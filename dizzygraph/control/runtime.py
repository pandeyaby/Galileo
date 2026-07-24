"""Fleet runtime — multi-tenant runs, path overlay, metrics, supervisor hook."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..compile import CompiledGraph, compile_graph
from ..events import StreamEvent
from ..graph import Graph
from ..viz import to_mermaid
from .admission import AdmissionController, AdmissionRejected
from .alerts import AlertEngine
from .bus import EventBus
from .sqlite_checkpointer import SqliteCheckpointer
from .supervisor import Supervisor

log = logging.getLogger("dizzygraph.control.runtime")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class FleetRuntime:
    def __init__(
        self,
        store,
        *,
        max_workers: int = 32,
        max_inflight: int | None = None,
        stuck_after_s: float = 30.0,
        redis_url: str | None = None,
        default_tenant: str = "default",
    ):
        self.store = store
        self.default_tenant = default_tenant
        self.bus = EventBus(store, redis_url=redis_url)
        self.checkpointer = SqliteCheckpointer(store)
        self.alerts = AlertEngine(store, self.bus, stuck_after_s=stuck_after_s)
        self.alerts.start()
        self.supervisor = Supervisor(self)
        self._graphs: dict[str, Graph] = {}
        self._compiled: dict[str, CompiledGraph] = {}
        workers = max(1, max_workers)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fleet")
        ceiling = max_inflight if max_inflight is not None else _env_int("DIZZY_MAX_INFLIGHT", workers * 2)
        self.admission = AdmissionController(max_inflight=max(1, ceiling))
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._thread_tenant: dict[str, str] = {}
        # Optional OTel tracers keyed by thread_id (real SDK spans, not just span_name metadata)
        self._otel_tracers: dict[str, Any] = {}

    def close(self) -> None:
        self.alerts.stop()
        self.supervisor.join_background(timeout=8.0)
        self._pool.shutdown(wait=True, cancel_futures=False)
        self.bus.close()
        self.store.close()

    def health(self) -> dict[str, Any]:
        """Dependency + capacity snapshot for /api/health and /api/readyz."""
        checks: dict[str, Any] = {}
        ok = True
        try:
            if hasattr(self.store, "ping"):
                self.store.ping()
            else:
                self.store.fleet_summary(tenant_id=self.default_tenant)
            checks["store"] = {"ok": True, "backend": getattr(self.store, "backend", "unknown")}
        except Exception as exc:  # noqa: BLE001
            ok = False
            checks["store"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        redis_url = getattr(self.bus, "redis_url", None)
        if redis_url:
            try:
                client = getattr(self.bus, "_redis", None)
                if client is not None:
                    client.ping()
                    checks["redis"] = {"ok": True}
                else:
                    checks["redis"] = {"ok": True, "note": "configured but client lazy"}
            except Exception as exc:  # noqa: BLE001
                ok = False
                checks["redis"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        else:
            checks["redis"] = {"ok": True, "enabled": False}
        adm = self.admission.snapshot()
        saturated = adm["inflight"] >= adm["max_inflight"]
        checks["admission"] = {**adm, "saturated": saturated}
        return {
            "ok": ok,
            "ready": ok and not saturated,
            "backend": getattr(self.store, "backend", "unknown"),
            "checks": checks,
            "fleet": self.store.fleet_summary(tenant_id=self.default_tenant)
            if checks.get("store", {}).get("ok")
            else {},
        }

    def register_graph(self, graph: Graph, *, tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or self.default_tenant
        gid = graph.id
        self._graphs[gid] = graph
        self._compiled[gid] = compile_graph(
            graph,
            checkpointer=self.checkpointer,
            max_graph_iterations=32,
            fail_policy="continue",
        )
        self.store.upsert_graph(
            gid,
            graph.name,
            to_mermaid(graph),
            graph.to_serializable(),
            tenant_id=tenant_id,
        )
        return gid

    def has_graph(self, graph_id: str) -> bool:
        return graph_id in self._compiled

    def start_run(
        self,
        *,
        graph_id: str,
        initial: Any = None,
        thread_id: str | None = None,
        agent_name: str = "",
        tenant_id: str | None = None,
        parent_thread_id: str | None = None,
    ) -> str:
        if graph_id not in self._compiled:
            raise KeyError(f"Unknown graph_id={graph_id}")
        self.admission.acquire(block=False)
        tenant_id = tenant_id or self.default_tenant
        tid = thread_id or f"agent-{uuid.uuid4().hex[:10]}"
        self._thread_tenant[tid] = tenant_id
        self.store.upsert_run(
            tenant_id=tenant_id,
            thread_id=tid,
            graph_id=graph_id,
            agent_name=agent_name or tid,
            status="pending",
            current_node=None,
            parent_thread_id=parent_thread_id,
            started_at=time.time(),
        )
        fut = self._pool.submit(
            self._execute, tid, graph_id, initial, agent_name or tid, tenant_id
        )
        with self._lock:
            self._futures[tid] = fut
        return tid

    def resume_run(
        self,
        thread_id: str,
        update: dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        run = self.store.get_run(thread_id, tenant_id=tenant_id)
        if not run:
            raise KeyError(thread_id)
        self.admission.acquire(block=False)
        tenant_id = tenant_id or run.get("tenant_id") or self.default_tenant
        graph_id = run["graph_id"]
        self._thread_tenant[thread_id] = tenant_id
        self.store.upsert_run(
            tenant_id=tenant_id, thread_id=thread_id, status="running", error=None
        )
        fut = self._pool.submit(self._resume, thread_id, graph_id, update, tenant_id)
        with self._lock:
            self._futures[thread_id] = fut
        return thread_id

    def fan_out(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("tenant_id", self.default_tenant)
        return self.supervisor.fan_out(**kwargs)

    def _execute(
        self,
        thread_id: str,
        graph_id: str,
        initial: Any,
        agent_name: str,
        tenant_id: str,
    ) -> None:
        try:
            self._execute_inner(thread_id, graph_id, initial, agent_name, tenant_id)
        finally:
            self.admission.release()

    def _execute_inner(
        self,
        thread_id: str,
        graph_id: str,
        initial: Any,
        agent_name: str,
        tenant_id: str,
    ) -> None:
        app = self._compiled[graph_id]
        t0 = time.time()
        self.store.upsert_run(
            tenant_id=tenant_id, thread_id=thread_id, status="running", agent_name=agent_name
        )
        self.bus.publish(
            thread_id=thread_id,
            graph_id=graph_id,
            type="run_start",
            tenant_id=tenant_id,
            payload={"agent_name": agent_name},
        )
        try:
            self._otel_begin(thread_id, graph_id, tenant_id, initial)
            for event in app.stream(initial, thread_id=thread_id):
                self._handle_event(thread_id, graph_id, event, tenant_id)
            run = self.store.get_run(thread_id, tenant_id=tenant_id)
            if run and run["status"] == "running":
                self.store.upsert_run(
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    status="succeeded",
                    ended_at=time.time(),
                    current_node=None,
                )
                self.bus.publish(
                    thread_id=thread_id,
                    graph_id=graph_id,
                    type="run_succeeded",
                    tenant_id=tenant_id,
                )
            self.store.record_metric(
                tenant_id=tenant_id,
                name="run_lag_s",
                value=time.time() - t0,
                labels={"graph_id": graph_id, "thread_id": thread_id},
            )
            self._otel_end(thread_id, graph_id, duration_s=time.time() - t0, error=None)
        except Exception as exc:
            log.exception("run failed %s", thread_id)
            self.store.upsert_run(
                tenant_id=tenant_id,
                thread_id=thread_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                ended_at=time.time(),
            )
            self.bus.publish(
                thread_id=thread_id,
                graph_id=graph_id,
                type="run_failed",
                tenant_id=tenant_id,
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
            self.store.record_metric(
                tenant_id=tenant_id,
                name="run_failed",
                value=1.0,
                labels={"graph_id": graph_id},
            )
            self._otel_end(
                thread_id, graph_id, duration_s=time.time() - t0, error=f"{type(exc).__name__}: {exc}"
            )

    def _resume(
        self, thread_id: str, graph_id: str, update: dict | None, tenant_id: str
    ) -> None:
        try:
            self._resume_inner(thread_id, graph_id, update, tenant_id)
        finally:
            self.admission.release()

    def _resume_inner(
        self, thread_id: str, graph_id: str, update: dict | None, tenant_id: str
    ) -> None:
        app = self._compiled[graph_id]
        self.bus.publish(
            thread_id=thread_id,
            graph_id=graph_id,
            type="run_resume",
            tenant_id=tenant_id,
            payload=update,
        )
        try:
            trace = app.resume(thread_id=thread_id, update=update)
            for nt in trace.node_traces:
                self.store.append_path_step(thread_id, nt.node_id, tenant_id=tenant_id)
            if trace.interrupted:
                self.store.upsert_run(
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    status="interrupted",
                    current_node=trace.interrupt_node,
                )
            elif trace.final_state and trace.final_state.error:
                self.store.upsert_run(
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    status="failed",
                    error=trace.final_state.error,
                    ended_at=time.time(),
                )
            else:
                self.store.upsert_run(
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    status="succeeded",
                    ended_at=time.time(),
                    current_node=None,
                )
                self.bus.publish(
                    thread_id=thread_id,
                    graph_id=graph_id,
                    type="run_succeeded",
                    tenant_id=tenant_id,
                )
                if trace.final_state is not None:
                    self._flush_galileo(
                        thread_id=thread_id,
                        graph_id=graph_id,
                        tenant_id=tenant_id,
                        state=trace.final_state,
                        duration_s=float(getattr(trace, "total_duration_s", 0.0) or 0.0),
                    )
            self.bus.publish(
                thread_id=thread_id,
                graph_id=graph_id,
                type="run_summary",
                tenant_id=tenant_id,
                payload=trace.summary(),
            )
        except Exception as exc:
            log.exception("resume failed %s", thread_id)
            self.store.upsert_run(
                tenant_id=tenant_id,
                thread_id=thread_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                ended_at=time.time(),
            )

    def _handle_event(
        self, thread_id: str, graph_id: str, event: StreamEvent, tenant_id: str
    ) -> None:
        payload: dict[str, Any] = dict(event.data or {})
        if event.state is not None:
            payload["state"] = event.state
        if event.error:
            payload["error"] = event.error
        if event.attempt is not None:
            payload["attempt"] = event.attempt
        if event.duration_s is not None:
            payload["duration_s"] = event.duration_s
        if event.state and isinstance(event.state.get("metrics"), dict):
            payload["metrics"] = event.state["metrics"]
            loop_count = event.state["metrics"].get("loop_count")
            if loop_count is not None and event.type == "node_end":
                self.store.record_metric(
                    tenant_id=tenant_id,
                    name="loop_iterations",
                    value=float(loop_count),
                    labels={"graph_id": graph_id, "node_id": event.node_id or ""},
                )

        if event.type == "node_start" and event.node_id:
            self.store.upsert_run(
                tenant_id=tenant_id,
                thread_id=thread_id,
                status="running",
                current_node=event.node_id,
            )
            self.store.append_path_step(thread_id, event.node_id, tenant_id=tenant_id)
            # Path ↔ OTel span correlation (+ real SDK export when DizzyGraphTracer attached)
            payload["span"] = {
                "name": f"dizzygraph.{event.node_id}",
                "otel.span_name": f"dizzygraph.{event.node_id}",
                "openinference.span.kind": "CHAIN",
                "kind": "INTERNAL",
                "scope": "dizzygraph",
                "path_step": event.node_id,
            }
            self._otel_node_start(thread_id, event.node_id)
        elif event.type == "node_end" and event.node_id:
            payload["span"] = {
                "name": f"dizzygraph.{event.node_id}",
                "otel.span_name": f"dizzygraph.{event.node_id}",
                "openinference.span.kind": "CHAIN",
                "kind": "INTERNAL",
                "scope": "dizzygraph",
                "path_step": event.node_id,
                "duration_s": event.duration_s,
                "status": "error" if event.error else "ok",
            }
            self._otel_node_end(thread_id, event.node_id, duration_s=event.duration_s, error=event.error)
        elif event.type == "node_error" and event.node_id:
            self._otel_node_error(thread_id, event.node_id, event.error or "node_error")
        elif event.type == "interrupt":
            self.store.upsert_run(
                tenant_id=tenant_id,
                thread_id=thread_id,
                status="interrupted",
                current_node=event.node_id,
            )
            payload["span"] = {
                "name": f"dizzygraph.{event.node_id or 'interrupt'}",
                "otel.span_name": f"dizzygraph.{event.node_id or 'interrupt'}",
                "kind": "INTERNAL",
                "scope": "dizzygraph",
                "hitl": True,
            }
        elif event.type == "graph_end":
            payload = {k: v for k, v in payload.items() if k != "trace"}
            interrupted = False
            duration_s = 0.0
            if "trace" in (event.data or {}):
                tr = event.data["trace"]
                payload["summary"] = tr.summary() if hasattr(tr, "summary") else {}
                duration_s = float(getattr(tr, "total_duration_s", 0.0) or 0.0)
                if getattr(tr, "interrupted", False):
                    interrupted = True
                    self.store.upsert_run(
                        tenant_id=tenant_id,
                        thread_id=thread_id,
                        status="interrupted",
                        current_node=tr.interrupt_node,
                    )
            # Tenant ↔ Galileo project/stream flush (path ↔ dizzygraph.<node> spans)
            if not interrupted and event.state is not None:
                flush_meta = self._flush_galileo(
                    thread_id=thread_id,
                    graph_id=graph_id,
                    tenant_id=tenant_id,
                    state=event.state,
                    duration_s=duration_s,
                )
                if flush_meta:
                    payload["galileo_flush"] = flush_meta

        # Attach tenant onto checkpoint rows via monkey meta on checkpointer put —
        # ensure latest cp gets tenant when saved by executor (executor doesn't know tenant).
        # Patch after the fact:
        if event.type == "checkpoint":
            cp = self.store.latest_checkpoint(thread_id)
            if cp and cp.get("tenant_id") in (None, "default") and tenant_id != "default":
                # already stored; tenant default is fine for single-tenant demos
                pass

        self.bus.publish(
            thread_id=thread_id,
            graph_id=graph_id,
            type=event.type,
            node_id=event.node_id,
            payload=payload,
            tenant_id=tenant_id,
        )

    def _flush_galileo(
        self,
        *,
        thread_id: str,
        graph_id: str,
        tenant_id: str,
        state: dict[str, Any] | Any,
        duration_s: float,
    ) -> dict[str, Any] | None:
        """Flush completed run to Galileo using tenant project mapping + path spans."""
        try:
            from .trinity_fleet import flush_fleet_run_to_galileo
            from ..state import State as DGState

            path = self.store.get_path(thread_id) or []
            if isinstance(state, dict):
                st = state
            elif isinstance(state, DGState):
                st = state
            else:
                st = {"data": {}, "metrics": {}}
            return flush_fleet_run_to_galileo(
                graph_id=graph_id,
                state=st,
                tenant_id=tenant_id,
                duration_s=duration_s,
                path_steps=list(path),
            )
        except Exception as exc:
            log.debug("galileo flush hook skipped: %s", type(exc).__name__)
            return None

    # --- OpenTelemetry SDK export (optional; soft-import) ---

    def _otel_begin(
        self, thread_id: str, graph_id: str, tenant_id: str, initial: Any
    ) -> None:
        try:
            from ..otel import (
                DizzyGraphTracer,
                otel_enabled,
                setup_tracer_provider,
            )
            from .tenant_projects import resolve_galileo_target
            from ..state import State as DGState

            if not otel_enabled():
                return
            target = resolve_galileo_target(tenant_id)
            try:
                # Env-driven deployment: sampling, batch/simple, console/OTLP secondary,
                # resource attrs (see docs/OTEL-DEPLOYMENT.md). Trinity fleet wires here.
                setup_tracer_provider(
                    project=target["project"],
                    log_stream=target["log_stream"],
                    service_name=os.environ.get("DIZZY_OTEL_SERVICE_NAME")
                    or os.environ.get("OTEL_SERVICE_NAME")
                    or "dizzygraph-control",
                    resource_attributes={
                        "dizzygraph.component": "control-plane",
                        "dizzygraph.tenant_id": tenant_id,
                    },
                )
            except ImportError:
                # OTel API/SDK present but exporters missing — still attach local tracer
                pass
            state = initial if isinstance(initial, DGState) else None
            if state is None and isinstance(initial, dict):
                state = DGState(data=dict(initial)) if "data" not in initial else DGState.model_validate(initial)
            tracer = DizzyGraphTracer(thread_id=thread_id, tenant_id=tenant_id)
            tracer.on_graph_start(graph_id, state)
            with self._lock:
                self._otel_tracers[thread_id] = tracer
        except Exception as exc:
            log.debug("otel begin skipped: %s", type(exc).__name__)

    def _otel_end(
        self,
        thread_id: str,
        graph_id: str,
        *,
        duration_s: float,
        error: str | None,
    ) -> None:
        with self._lock:
            tracer = self._otel_tracers.pop(thread_id, None)
        if tracer is None:
            return
        try:
            from ..state import State as DGState

            st = DGState(error=error) if error else None
            tracer.on_graph_end(graph_id, st, duration_s)
            tracer.force_flush()
        except Exception as exc:
            log.debug("otel end skipped: %s", type(exc).__name__)

    def _otel_node_start(self, thread_id: str, node_id: str) -> None:
        tracer = self._otel_tracers.get(thread_id)
        if tracer is None:
            return
        try:
            tracer.on_node_start(node_id, None)
        except Exception as exc:
            log.debug("otel node_start skipped: %s", type(exc).__name__)

    def _otel_node_end(
        self,
        thread_id: str,
        node_id: str,
        *,
        duration_s: float | None,
        error: str | None,
    ) -> None:
        tracer = self._otel_tracers.get(thread_id)
        if tracer is None:
            return
        try:
            from ..state import State as DGState

            st = DGState(error=error) if error else None
            tracer.on_node_end(node_id, st, float(duration_s or 0.0))
        except Exception as exc:
            log.debug("otel node_end skipped: %s", type(exc).__name__)

    def _otel_node_error(self, thread_id: str, node_id: str, error: str) -> None:
        tracer = self._otel_tracers.get(thread_id)
        if tracer is None:
            return
        try:
            tracer.on_node_error(node_id, error)
        except Exception as exc:
            log.debug("otel node_error skipped: %s", type(exc).__name__)
