"""OpenTelemetry instrumentation for DizzyGraph node runs.

Exports real spans named ``dizzygraph.<node>`` (and a parent ``dizzygraph.<graph>``
run span) via the OpenTelemetry SDK. When ``galileo[otel]`` is installed and
``GALILEO_API_KEY`` is set, spans are batched to Galileo's OTLP endpoint through
``GalileoSpanProcessor``.

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
from typing import Any

from .callbacks import BaseCallbackHandler
from .events import StreamEvent
from .state import State

log = logging.getLogger("dizzygraph.otel")

_PROVIDER_LOCK = threading.Lock()
_PROVIDER_READY = False


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


def setup_galileo_tracer_provider(
    *,
    project: str | None = None,
    log_stream: str | None = None,
    force: bool = False,
) -> Any:
    """
    Configure a global TracerProvider with GalileoSpanProcessor (idempotent).

    Raises ImportError if OTel / galileo.otel extras are missing.
    """
    global _PROVIDER_READY
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    if project:
        os.environ.setdefault("GALILEO_PROJECT", project)
    if log_stream:
        os.environ.setdefault("GALILEO_LOG_STREAM", log_stream)

    with _PROVIDER_LOCK:
        existing = trace.get_tracer_provider()
        if _PROVIDER_READY and not force and isinstance(existing, TracerProvider):
            return existing

        try:
            from galileo.otel import GalileoSpanProcessor, add_galileo_span_processor
        except ImportError as exc:
            raise ImportError(
                "Install Galileo OTel extras: pip install 'galileo[otel]' "
                "opentelemetry-sdk opentelemetry-api"
            ) from exc

        provider = TracerProvider()
        add_galileo_span_processor(provider, GalileoSpanProcessor())
        trace.set_tracer_provider(provider)
        _PROVIDER_READY = True
        log.info(
            "DizzyGraph OTel → Galileo project=%s stream=%s",
            os.environ.get("GALILEO_PROJECT"),
            os.environ.get("GALILEO_LOG_STREAM"),
        )
        return provider


def get_tracer(name: str = "dizzygraph"):
    from opentelemetry import trace

    return trace.get_tracer(name, "0.4.0")


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
        if os.environ.get("GALILEO_API_KEY"):
            setup_galileo_tracer_provider(project=project, log_stream=log_stream)
        return OpenTelemetryCallback(thread_id=thread_id, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("OTel callback skipped: %s", type(exc).__name__)
        return None
