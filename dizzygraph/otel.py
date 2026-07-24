"""OpenTelemetry instrumentation for DizzyGraph node runs.

Exports real spans named ``dizzygraph.<node>`` (and a parent ``dizzygraph.<graph>``
run span) via the OpenTelemetry SDK. When ``galileo[otel]`` is installed and
``GALILEO_API_KEY`` is set, spans are batched to Galileo's OTLP endpoint through
``GalileoSpanProcessor``.

Deployment patterns (see ``docs/OTEL-DEPLOYMENT.md``):

- **Sampling** — ``always_on`` / ``always_off`` / ``traceidratio`` /
  ``parentbased_*`` via ``DIZZY_OTEL_SAMPLER`` or standard ``OTEL_TRACES_SAMPLER``
- **Multi-backend** — Galileo + optional console exporter + secondary OTLP
- **Processors** — batch (default) vs simple via ``DIZZY_OTEL_PROCESSOR``
- **Resource** — ``service.name`` + extra attrs via ``OTEL_SERVICE_NAME`` /
  ``DIZZY_OTEL_SERVICE_NAME`` / ``OTEL_RESOURCE_ATTRIBUTES``
- **Env config** — ``OTEL_*`` and ``DIZZY_OTEL_*`` (see ``OtelConfig``)

Soft-imports: DizzyGraph core does not require OTel packages. Enable via:

  export DIZZY_OTEL=1          # or leave unset — auto-on when GALILEO_API_KEY + otel present
  pip install 'galileo[otel]' opentelemetry-sdk opentelemetry-api

Wire as a ``GraphExecutor`` callback::

    from dizzygraph.otel import OpenTelemetryCallback, setup_galileo_tracer_provider
    setup_galileo_tracer_provider(project=..., log_stream=...)
    app = compile_graph(g, callbacks=[OpenTelemetryCallback(thread_id=\"t1\")])

Or let the fleet runtime attach a tracer automatically (see ``FleetRuntime``).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from .callbacks import BaseCallbackHandler
from .events import StreamEvent
from .state import State

log = logging.getLogger("dizzygraph.otel")

_PROVIDER_LOCK = threading.Lock()
_PROVIDER_READY = False

# Sampler names accepted by resolve_sampler / OtelConfig
SAMPLER_ALWAYS_ON = "always_on"
SAMPLER_ALWAYS_OFF = "always_off"
SAMPLER_TRACEIDRATIO = "traceidratio"
SAMPLER_PARENTBASED_ALWAYS_ON = "parentbased_always_on"
SAMPLER_PARENTBASED_ALWAYS_OFF = "parentbased_always_off"
SAMPLER_PARENTBASED_TRACEIDRATIO = "parentbased_traceidratio"

KNOWN_SAMPLERS = frozenset(
    {
        SAMPLER_ALWAYS_ON,
        SAMPLER_ALWAYS_OFF,
        SAMPLER_TRACEIDRATIO,
        SAMPLER_PARENTBASED_ALWAYS_ON,
        SAMPLER_PARENTBASED_ALWAYS_OFF,
        SAMPLER_PARENTBASED_TRACEIDRATIO,
        # OTel env aliases
        "parentbased",
        "parentbased_traceidratio",
    }
)

PROCESSOR_BATCH = "batch"
PROCESSOR_SIMPLE = "simple"


def otel_available() -> bool:
    try:
        from opentelemetry import trace  # noqa: F401
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401

        return True
    except ImportError:
        return False


def otel_enabled() -> bool:
    """True when OTel should attach (explicit DIZZY_OTEL or auto with Galileo key)."""
    flag = (os.environ.get("DIZZY_OTEL") or "").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return False
    if flag in {"1", "true", "on", "yes"}:
        return otel_available()
    # Auto: GALILEO_API_KEY set and packages present
    if os.environ.get("GALILEO_API_KEY") and otel_available():
        return True
    return False


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "on", "yes"}


def _parse_resource_attrs(raw: str | None) -> dict[str, str]:
    """Parse ``key=value,key2=value2`` (OTEL_RESOURCE_ATTRIBUTES style)."""
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            out[k] = v
    return out


def _normalize_sampler_name(name: str) -> str:
    n = (name or "").strip().lower().replace("-", "_")
    aliases = {
        "alwayson": SAMPLER_ALWAYS_ON,
        "alwaysoff": SAMPLER_ALWAYS_OFF,
        "parentbased": SAMPLER_PARENTBASED_TRACEIDRATIO,
        "parent_based": SAMPLER_PARENTBASED_TRACEIDRATIO,
        "parentbased_trace_id_ratio": SAMPLER_PARENTBASED_TRACEIDRATIO,
        "trace_id_ratio": SAMPLER_TRACEIDRATIO,
        "ratio": SAMPLER_TRACEIDRATIO,
    }
    return aliases.get(n, n)


def resolve_sampler_name(
    *,
    sampler: str | None = None,
    default: str = SAMPLER_PARENTBASED_ALWAYS_ON,
) -> str:
    """Pick sampler name: explicit arg → DIZZY_OTEL_SAMPLER → OTEL_TRACES_SAMPLER → default."""
    if sampler and sampler.strip():
        return _normalize_sampler_name(sampler)
    dizzy = (os.environ.get("DIZZY_OTEL_SAMPLER") or "").strip()
    if dizzy:
        return _normalize_sampler_name(dizzy)
    otel = (os.environ.get("OTEL_TRACES_SAMPLER") or "").strip()
    if otel:
        return _normalize_sampler_name(otel)
    return _normalize_sampler_name(default)


def resolve_sampler_arg(*, ratio: float | None = None, default: float = 1.0) -> float:
    """Pick ratio: explicit → DIZZY_OTEL_SAMPLER_ARG → OTEL_TRACES_SAMPLER_ARG → default."""
    if ratio is not None:
        return max(0.0, min(1.0, float(ratio)))
    for key in ("DIZZY_OTEL_SAMPLER_ARG", "OTEL_TRACES_SAMPLER_ARG"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            try:
                return max(0.0, min(1.0, float(raw)))
            except ValueError:
                log.warning("Invalid %s=%r; using %s", key, raw, default)
    return max(0.0, min(1.0, float(default)))


def build_sampler(name: str | None = None, *, ratio: float | None = None) -> Any:
    """
    Construct an OTel Sampler from a normalized name + optional ratio.

    Raises ImportError if the SDK is missing; ValueError for unknown names.
    """
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )

    resolved = resolve_sampler_name(sampler=name)
    r = resolve_sampler_arg(ratio=ratio)

    if resolved == SAMPLER_ALWAYS_ON:
        return ALWAYS_ON
    if resolved == SAMPLER_ALWAYS_OFF:
        return ALWAYS_OFF
    if resolved == SAMPLER_TRACEIDRATIO:
        return TraceIdRatioBased(r)
    if resolved == SAMPLER_PARENTBASED_ALWAYS_ON:
        return ParentBased(ALWAYS_ON)
    if resolved == SAMPLER_PARENTBASED_ALWAYS_OFF:
        return ParentBased(ALWAYS_OFF)
    if resolved == SAMPLER_PARENTBASED_TRACEIDRATIO:
        return ParentBased(TraceIdRatioBased(r))
    raise ValueError(
        f"Unknown sampler {resolved!r}. Expected one of: "
        f"{', '.join(sorted(KNOWN_SAMPLERS - {'parentbased'}))}"
    )


def resolve_processor_kind(*, processor: str | None = None) -> str:
    """``batch`` (default) or ``simple`` from arg / DIZZY_OTEL_PROCESSOR."""
    raw = (processor or os.environ.get("DIZZY_OTEL_PROCESSOR") or PROCESSOR_BATCH).strip().lower()
    if raw in {PROCESSOR_BATCH, PROCESSOR_SIMPLE}:
        return raw
    log.warning("Unknown DIZZY_OTEL_PROCESSOR=%r; using batch", raw)
    return PROCESSOR_BATCH


def resolve_service_name(*, service_name: str | None = None) -> str:
    if service_name and service_name.strip():
        return service_name.strip()
    for key in ("DIZZY_OTEL_SERVICE_NAME", "OTEL_SERVICE_NAME"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return "dizzygraph"


def build_resource(
    *,
    service_name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Any:
    """Build an OTel Resource with service.name + env/extra attributes."""
    from opentelemetry.sdk.resources import Resource

    attrs: dict[str, Any] = {
        "service.name": resolve_service_name(service_name=service_name),
    }
    attrs.update(_parse_resource_attrs(os.environ.get("OTEL_RESOURCE_ATTRIBUTES")))
    attrs.update(_parse_resource_attrs(os.environ.get("DIZZY_OTEL_RESOURCE_ATTRS")))
    if attributes:
        attrs.update({k: v for k, v in attributes.items() if v is not None})
    return Resource.create(attrs)


@dataclass
class OtelConfig:
    """Resolved deployment config (env + overrides). Pure data — no SDK side effects."""

    enabled: bool = False
    sampler: str = SAMPLER_PARENTBASED_ALWAYS_ON
    sampler_arg: float = 1.0
    processor: str = PROCESSOR_BATCH
    service_name: str = "dizzygraph"
    resource_attributes: dict[str, str] = field(default_factory=dict)
    galileo_export: bool = True
    console_export: bool = False
    otlp_endpoint: str | None = None  # secondary / alternate OTLP HTTP traces URL
    project: str | None = None
    log_stream: str | None = None

    @classmethod
    def from_env(
        cls,
        *,
        project: str | None = None,
        log_stream: str | None = None,
        service_name: str | None = None,
        sampler: str | None = None,
        sampler_arg: float | None = None,
        processor: str | None = None,
        console: bool | None = None,
        otlp_endpoint: str | None = None,
        galileo: bool | None = None,
        resource_attributes: dict[str, Any] | None = None,
    ) -> OtelConfig:
        name = resolve_sampler_name(sampler=sampler)
        ratio = resolve_sampler_arg(ratio=sampler_arg)
        proc = resolve_processor_kind(processor=processor)
        svc = resolve_service_name(service_name=service_name)
        attrs = _parse_resource_attrs(os.environ.get("OTEL_RESOURCE_ATTRIBUTES"))
        attrs.update(_parse_resource_attrs(os.environ.get("DIZZY_OTEL_RESOURCE_ATTRS")))
        if resource_attributes:
            attrs.update({str(k): str(v) for k, v in resource_attributes.items()})

        secondary = otlp_endpoint
        if secondary is None:
            secondary = (
                (os.environ.get("DIZZY_OTEL_OTLP_ENDPOINT") or "").strip()
                or (os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or "").strip()
                or (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
                or None
            )
            # If only base OTEL_EXPORTER_OTLP_ENDPOINT is set, append traces path when needed
            if secondary and secondary.rstrip("/").endswith(":4318"):
                secondary = secondary.rstrip("/") + "/v1/traces"
            elif (
                secondary
                and not secondary.rstrip("/").endswith("/v1/traces")
                and "galileo" not in secondary
                and "/otel/traces" not in secondary
            ):
                # Keep as-is for full URLs; only auto-append for bare collector roots
                if secondary.rstrip("/").endswith(":4317"):
                    pass  # gRPC — leave for docs; HTTP exporter needs :4318
                elif "/v1/" not in secondary and secondary.count("/") <= 2:
                    secondary = secondary.rstrip("/") + "/v1/traces"

        console_on = (
            bool(console)
            if console is not None
            else _env_truthy("DIZZY_OTEL_CONSOLE") or _env_truthy("OTEL_CONSOLE_EXPORTER")
        )
        galileo_on = (
            bool(galileo)
            if galileo is not None
            else (
                not _env_truthy("DIZZY_OTEL_NO_GALILEO")
                and bool(os.environ.get("GALILEO_API_KEY"))
            )
        )

        return cls(
            enabled=otel_enabled(),
            sampler=name,
            sampler_arg=ratio,
            processor=proc,
            service_name=svc,
            resource_attributes=attrs,
            galileo_export=galileo_on,
            console_export=console_on,
            otlp_endpoint=secondary,
            project=project or os.environ.get("GALILEO_PROJECT"),
            log_stream=log_stream or os.environ.get("GALILEO_LOG_STREAM"),
        )


def _make_span_processor(kind: str, exporter: Any) -> Any:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    if kind == PROCESSOR_SIMPLE:
        return SimpleSpanProcessor(exporter)
    return BatchSpanProcessor(exporter)


def setup_tracer_provider(
    *,
    project: str | None = None,
    log_stream: str | None = None,
    force: bool = False,
    config: OtelConfig | None = None,
    service_name: str | None = None,
    sampler: str | None = None,
    sampler_arg: float | None = None,
    processor: str | None = None,
    console: bool | None = None,
    otlp_endpoint: str | None = None,
    galileo: bool | None = None,
    resource_attributes: dict[str, Any] | None = None,
) -> Any:
    """
    Configure a global TracerProvider with sampling, resource, and exporters.

    Exporters (any combination):
      - GalileoSpanProcessor when ``galileo`` and ``GALILEO_API_KEY``
      - ConsoleSpanExporter when ``console`` / ``DIZZY_OTEL_CONSOLE=1``
      - OTLP HTTP secondary when ``otlp_endpoint`` / ``DIZZY_OTEL_OTLP_ENDPOINT`` /
        ``OTEL_EXPORTER_OTLP_*``

    Idempotent unless ``force=True``.
    """
    global _PROVIDER_READY
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    cfg = config or OtelConfig.from_env(
        project=project,
        log_stream=log_stream,
        service_name=service_name,
        sampler=sampler,
        sampler_arg=sampler_arg,
        processor=processor,
        console=console,
        otlp_endpoint=otlp_endpoint,
        galileo=galileo,
        resource_attributes=resource_attributes,
    )
    if project:
        os.environ.setdefault("GALILEO_PROJECT", project)
        cfg.project = project
    if log_stream:
        os.environ.setdefault("GALILEO_LOG_STREAM", log_stream)
        cfg.log_stream = log_stream

    with _PROVIDER_LOCK:
        existing = trace.get_tracer_provider()
        if _PROVIDER_READY and not force and isinstance(existing, TracerProvider):
            return existing

        if force:
            # Allow replacing the global provider (tests / reconfigure).
            _reset_otel_provider_state_unlocked()

        resource = build_resource(
            service_name=cfg.service_name,
            attributes=cfg.resource_attributes,
        )
        sampler_obj = build_sampler(cfg.sampler, ratio=cfg.sampler_arg)
        provider = TracerProvider(resource=resource, sampler=sampler_obj)

        exporters_attached: list[str] = []

        if cfg.galileo_export:
            try:
                from galileo.otel import GalileoSpanProcessor, add_galileo_span_processor

                # GalileoSpanProcessor registers its own BatchSpanProcessor internally
                add_galileo_span_processor(provider, GalileoSpanProcessor())
                exporters_attached.append("galileo")
            except ImportError as exc:
                if galileo is True or (
                    galileo is None and os.environ.get("GALILEO_API_KEY") and not cfg.console_export and not cfg.otlp_endpoint
                ):
                    raise ImportError(
                        "Install Galileo OTel extras: pip install 'galileo[otel]' "
                        "opentelemetry-sdk opentelemetry-api"
                    ) from exc
                log.warning("Galileo OTel processor unavailable: %s", exc)

        if cfg.console_export:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            provider.add_span_processor(
                _make_span_processor(cfg.processor, ConsoleSpanExporter())
            )
            exporters_attached.append("console")

        if cfg.otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
            except ImportError as exc:
                raise ImportError(
                    "Secondary OTLP export requires: "
                    "pip install opentelemetry-exporter-otlp-proto-http"
                ) from exc
            headers: dict[str, str] = {}
            hdr_raw = (os.environ.get("OTEL_EXPORTER_OTLP_HEADERS") or "").strip()
            if hdr_raw:
                for part in hdr_raw.split(","):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        headers[k.strip()] = v.strip()
            exporter = OTLPSpanExporter(endpoint=cfg.otlp_endpoint, headers=headers or None)
            provider.add_span_processor(_make_span_processor(cfg.processor, exporter))
            exporters_attached.append(f"otlp:{cfg.otlp_endpoint}")

        if not exporters_attached:
            # Still install provider so local InMemory / tests can add processors
            log.info(
                "DizzyGraph OTel provider ready (no exporters); sampler=%s processor=%s service=%s",
                cfg.sampler,
                cfg.processor,
                cfg.service_name,
            )
        else:
            log.info(
                "DizzyGraph OTel → exporters=%s sampler=%s arg=%s processor=%s "
                "service=%s project=%s stream=%s",
                ",".join(exporters_attached),
                cfg.sampler,
                cfg.sampler_arg,
                cfg.processor,
                cfg.service_name,
                cfg.project,
                cfg.log_stream,
            )

        trace.set_tracer_provider(provider)
        _PROVIDER_READY = True
        return provider


def setup_galileo_tracer_provider(
    *,
    project: str | None = None,
    log_stream: str | None = None,
    force: bool = False,
    **kwargs: Any,
) -> Any:
    """
    Configure a global TracerProvider with GalileoSpanProcessor (idempotent).

    Accepts the same keyword overrides as ``setup_tracer_provider`` (sampler,
    processor, console, otlp_endpoint, …). Raises ImportError if Galileo OTel
    extras are missing when Galileo export is required and no alternate exporter
    is configured.
    """
    return setup_tracer_provider(
        project=project,
        log_stream=log_stream,
        force=force,
        **kwargs,
    )


def _reset_otel_provider_state_unlocked() -> None:
    """Clear idempotency + OTel Once latch. Caller must hold ``_PROVIDER_LOCK`` or be single-threaded."""
    global _PROVIDER_READY
    _PROVIDER_READY = False
    try:
        from opentelemetry import trace as trace_api
        from opentelemetry.util._once import Once

        # OTel SDK refuses a second set_tracer_provider unless the Once latch is reset.
        trace_api._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        trace_api._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
    except Exception:
        pass


def reset_otel_provider_state() -> None:
    """Test helper: clear idempotency flag and allow a new global TracerProvider."""
    with _PROVIDER_LOCK:
        _reset_otel_provider_state_unlocked()


def get_tracer(name: str = "dizzygraph"):
    from opentelemetry import trace

    return trace.get_tracer(name, "0.4.1")


class DizzyGraphTracer:
    """
    Manages nested OTel spans for a graph run.

    Span names:
      - graph: ``dizzygraph.<graph_id>``
      - node:  ``dizzygraph.<node_id>``
    """

    def __init__(
        self,
        *,
        thread_id: str | None = None,
        tenant_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ):
        if not otel_available():
            raise ImportError(
                "OpenTelemetry SDK required: pip install opentelemetry-sdk opentelemetry-api"
            )
        self.thread_id = thread_id
        self.tenant_id = tenant_id
        self.attributes = dict(attributes or {})
        self._tracer = get_tracer()
        self._graph_cm: Any = None
        self._graph_span: Any = None
        self._node_cms: dict[str, Any] = {}
        self._node_spans: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _base_attrs(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "dizzygraph.scope": "dizzygraph",
            "openinference.span.kind": "CHAIN",
            **self.attributes,
        }
        if self.thread_id:
            attrs["dizzygraph.thread_id"] = self.thread_id
        if self.tenant_id:
            attrs["dizzygraph.tenant_id"] = self.tenant_id
        return attrs

    def on_graph_start(self, graph_id: str, state: State | None = None) -> None:
        from opentelemetry import trace

        name = f"dizzygraph.{graph_id}"
        attrs = self._base_attrs()
        attrs["dizzygraph.graph_id"] = graph_id
        attrs["otel.span_name"] = name
        if state is not None and isinstance(getattr(state, "data", None), dict):
            q = state.data.get("query")
            if q is not None:
                attrs["dizzygraph.query"] = str(q)[:200]
        cm = self._tracer.start_as_current_span(name, kind=trace.SpanKind.INTERNAL, attributes=attrs)
        span = cm.__enter__()
        with self._lock:
            self._graph_cm = cm
            self._graph_span = span

    def on_graph_end(self, graph_id: str, state: State | None = None, duration_s: float = 0.0) -> None:
        from opentelemetry import trace

        with self._lock:
            span = self._graph_span
            cm = self._graph_cm
            self._graph_span = None
            self._graph_cm = None
        if span is None:
            return
        span.set_attribute("dizzygraph.duration_s", float(duration_s or 0.0))
        if state is not None and getattr(state, "error", None):
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(state.error)[:200]))
            span.record_exception(RuntimeError(str(state.error)))
        else:
            span.set_status(trace.Status(trace.StatusCode.OK))
        if cm is not None:
            cm.__exit__(None, None, None)

    def on_node_start(self, node_id: str, state: State | None = None) -> None:
        from opentelemetry import trace

        name = f"dizzygraph.{node_id}"
        attrs = self._base_attrs()
        attrs.update(
            {
                "dizzygraph.node_id": node_id,
                "dizzygraph.path_step": node_id,
                "otel.span_name": name,
            }
        )
        cm = self._tracer.start_as_current_span(name, kind=trace.SpanKind.INTERNAL, attributes=attrs)
        span = cm.__enter__()
        with self._lock:
            self._node_cms[node_id] = cm
            self._node_spans[node_id] = span

    def on_node_end(self, node_id: str, state: State | None = None, duration_s: float = 0.0) -> None:
        from opentelemetry import trace

        with self._lock:
            span = self._node_spans.pop(node_id, None)
            cm = self._node_cms.pop(node_id, None)
        if span is None:
            return
        span.set_attribute("dizzygraph.duration_s", float(duration_s or 0.0))
        err = getattr(state, "error", None) if state is not None else None
        if err:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(err)[:200]))
        else:
            span.set_status(trace.Status(trace.StatusCode.OK))
        if cm is not None:
            cm.__exit__(None, None, None)

    def on_node_error(self, node_id: str, error: str) -> None:
        from opentelemetry import trace

        with self._lock:
            span = self._node_spans.get(node_id)
        if span is None:
            return
        span.set_status(trace.Status(trace.StatusCode.ERROR, error[:200]))
        span.record_exception(RuntimeError(error))

    def force_flush(self, timeout_millis: int = 5000) -> None:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush(timeout_millis)


class OpenTelemetryCallback(BaseCallbackHandler):
    """GraphExecutor callback that exports DizzyGraph node spans via OTel."""

    def __init__(
        self,
        *,
        thread_id: str | None = None,
        tenant_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        tracer: DizzyGraphTracer | None = None,
    ):
        self._dg = tracer or DizzyGraphTracer(
            thread_id=thread_id,
            tenant_id=tenant_id,
            attributes=attributes,
        )

    def on_graph_start(self, graph_id: str, state: State) -> None:
        self._dg.on_graph_start(graph_id, state)

    def on_graph_end(self, graph_id: str, state: State, duration_s: float) -> None:
        self._dg.on_graph_end(graph_id, state, duration_s)
        self._dg.force_flush()

    def on_node_start(self, node_id: str, state: State) -> None:
        self._dg.on_node_start(node_id, state)

    def on_node_end(self, node_id: str, state: State, duration_s: float) -> None:
        self._dg.on_node_end(node_id, state, duration_s)

    def on_node_error(self, node_id: str, error: str) -> None:
        self._dg.on_node_error(node_id, error)

    def on_event(self, event: StreamEvent) -> None:
        # Spans are driven by lifecycle hooks; events are already correlated
        # via otel.span_name attributes on the fleet bus.
        return


def maybe_open_telemetry_callback(
    *,
    thread_id: str | None = None,
    tenant_id: str | None = None,
    project: str | None = None,
    log_stream: str | None = None,
) -> OpenTelemetryCallback | None:
    """Return an OTel callback when enabled + packages present; else None."""
    if not otel_enabled():
        return None
    try:
        if os.environ.get("GALILEO_API_KEY") or _env_truthy("DIZZY_OTEL_CONSOLE") or (
            os.environ.get("DIZZY_OTEL_OTLP_ENDPOINT")
            or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        ):
            setup_tracer_provider(project=project, log_stream=log_stream)
        return OpenTelemetryCallback(thread_id=thread_id, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("OTel callback skipped: %s", type(exc).__name__)
        return None
