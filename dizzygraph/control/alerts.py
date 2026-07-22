"""Alert engine — loop non-convergence, errors, stuck runs, retries, aging HITL."""

from __future__ import annotations

import threading
import time
from typing import Any

from .bus import EventBus


class AlertEngine:
    def __init__(
        self,
        store,
        bus: EventBus,
        *,
        stuck_after_s: float = 30.0,
        interrupt_age_s: float = 60.0,
        retry_threshold: int = 3,
        tick_s: float = 5.0,
    ):
        self.store = store
        self.bus = bus
        self.stuck_after_s = stuck_after_s
        self.interrupt_age_s = interrupt_age_s
        self.retry_threshold = retry_threshold
        self.tick_s = tick_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.bus.subscribe(self.on_event)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="alert-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def on_event(self, event: dict[str, Any]) -> None:
        et = event.get("type")
        thread_id = event["thread_id"]
        graph_id = event["graph_id"]
        tenant_id = event.get("tenant_id") or "default"
        payload = event.get("payload") or {}
        node_id = event.get("node_id")

        if et == "node_error":
            self._raise(
                thread_id,
                graph_id,
                tenant_id=tenant_id,
                rule="node_error",
                severity="critical",
                message=f"Node {node_id} failed: {payload.get('error') or payload}",
                payload=payload,
            )

        if et == "node_retry":
            attempt = int(payload.get("attempt") or 0)
            if attempt >= self.retry_threshold:
                self._raise(
                    thread_id,
                    graph_id,
                    tenant_id=tenant_id,
                    rule="high_retries",
                    severity="warning",
                    message=f"Node {node_id} retry attempt {attempt}",
                    payload=payload,
                )

        if et == "node_end":
            metrics = payload.get("metrics") or {}
            state = payload.get("state") if isinstance(payload, dict) else None
            state_data = {}
            if isinstance(state, dict):
                metrics = {**(state.get("metrics") or {}), **metrics}
                state_data = state.get("data") or {}
            if metrics.get("loop_count") is not None and metrics.get("loop_converged") is False:
                protect_status = (
                    metrics.get("protect_status")
                    or state_data.get("protect_status")
                    or (payload.get("data") or {}).get("protect_status")
                )
                protect_score = metrics.get("protect_score") or state_data.get("protect_score")
                protect_path = metrics.get("protect_path") or state_data.get("protect_path")
                msg = (
                    f"LoopNode {node_id} did not converge "
                    f"(iters={metrics.get('loop_count')}, scores={metrics.get('loop_scores')})"
                )
                if protect_status is not None:
                    msg += (
                        f" · Protect={protect_status}"
                        f" score={protect_score} path={protect_path}"
                    )
                self._raise(
                    thread_id,
                    graph_id,
                    tenant_id=tenant_id,
                    rule="loop_non_converge",
                    severity="warning",
                    message=msg,
                    payload={
                        "node_id": node_id,
                        "metrics": metrics,
                        "protect_status": protect_status,
                        "protect_score": protect_score,
                        "protect_path": protect_path,
                    },
                )

        if et == "interrupt":
            self._raise(
                thread_id,
                graph_id,
                tenant_id=tenant_id,
                rule="hitl_interrupt",
                severity="info",
                message=f"HITL interrupt at {node_id}",
                payload=payload,
            )

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_s):
            self.scan_stuck()

    def scan_stuck(self) -> None:
        now = time.time()
        lister = getattr(self.store, "list_runs_all", None)
        runs = lister(limit=1000) if callable(lister) else self.store.list_runs(limit=1000)
        for run in runs:
            status = run["status"]
            updated = float(run["updated_at"] or 0)
            thread_id = run["thread_id"]
            graph_id = run["graph_id"]
            tenant_id = run.get("tenant_id") or "default"

            if status == "running" and (now - updated) >= self.stuck_after_s:
                self._raise(
                    thread_id,
                    graph_id,
                    tenant_id=tenant_id,
                    rule="stuck_run",
                    severity="critical",
                    message=(
                        f"Run stuck — no progress for {int(now - updated)}s "
                        f"(current={run.get('current_node')})"
                    ),
                    payload={"updated_at": updated, "current_node": run.get("current_node")},
                )
                self.store.record_metric(
                    tenant_id=tenant_id,
                    name="stuck_nodes",
                    value=1.0,
                    labels={"thread_id": thread_id, "node_id": run.get("current_node") or ""},
                )

            if status == "interrupted" and (now - updated) >= self.interrupt_age_s:
                self._raise(
                    thread_id,
                    graph_id,
                    tenant_id=tenant_id,
                    rule="interrupt_aging",
                    severity="warning",
                    message=f"HITL waiting {int(now - updated)}s at {run.get('current_node')}",
                    payload={"updated_at": updated},
                )

    def _raise(
        self,
        thread_id: str,
        graph_id: str,
        *,
        tenant_id: str,
        rule: str,
        severity: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        alert = self.store.add_alert(
            thread_id=thread_id,
            graph_id=graph_id,
            rule=rule,
            severity=severity,
            message=message,
            payload=payload,
            tenant_id=tenant_id,
        )
        if alert.get("deduped"):
            return
        self.bus.publish(
            thread_id=thread_id,
            graph_id=graph_id,
            type="alert",
            tenant_id=tenant_id,
            payload=alert,
        )
