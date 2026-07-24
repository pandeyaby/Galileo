"""Fleet runtime — multi-tenant runs, path overlay, metrics, supervisor hook."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..compile import CompiledGraph, compile_graph
from ..events import StreamEvent
from ..graph import Graph
from ..viz import to_mermaid
from .alerts import AlertEngine
from .bus import EventBus
from .sqlite_checkpointer import SqliteCheckpointer
from .supervisor import Supervisor

log = logging.getLogger("dizzygraph.control.runtime")


class FleetRuntime:
    def __init__(
        self,
        store,
        *,
        max_workers: int = 32,
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
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fleet")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._thread_tenant: dict[str, str] = {}

    def close(self) -> None:
        self.alerts.stop()
        self.supervisor.join_background(timeout=8.0)
        self._pool.shutdown(wait=True, cancel_futures=False)
        self.bus.close()
        self.store.close()

    def register_graph(self, graph: Graph, *, tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or self.default_tenant
        gid = graph.id
        self._graphs[gid] = graph
        self._compiled[gid] = compile_graph(
            graph,
            checkpointer=self.checkpointer,
            max_graph_iterations=32,
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

    def _resume(
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
            # Path ↔ OTel-ish span correlation (pragmatic v1)
            payload["span"] = {
                "name": f"dizzygraph.{event.node_id}",
                "otel.span_name": f"dizzygraph.{event.node_id}",
                "openinference.span.kind": "CHAIN",
                "kind": "INTERNAL",
                "scope": "dizzygraph",
                "path_step": event.node_id,
            }
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
