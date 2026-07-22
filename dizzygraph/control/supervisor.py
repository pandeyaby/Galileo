"""Cross-agent supervisor — fan-out child runs, wait, aggregate."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from ..state import State


class Supervisor:
    """
    Fan-out N child agents under a parent thread, wait for terminal states,
    aggregate results into parent checkpoint-friendly dict.
    """

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._bg_threads: list[threading.Thread] = []

    def join_background(self, timeout: float = 5.0) -> None:
        for t in list(self._bg_threads):
            t.join(timeout=timeout)
        self._bg_threads = [t for t in self._bg_threads if t.is_alive()]

    def fan_out(
        self,
        *,
        graph_id: str,
        items: list[dict[str, Any]],
        tenant_id: str = "default",
        parent_thread_id: str | None = None,
        agent_prefix: str = "worker",
        wait: bool = True,
        timeout_s: float = 120.0,
        poll_s: float = 0.25,
    ) -> dict[str, Any]:
        parent = parent_thread_id or f"supervisor-{uuid.uuid4().hex[:10]}"
        self.runtime.store.upsert_run(
            tenant_id=tenant_id,
            thread_id=parent,
            graph_id=graph_id,
            agent_name=f"supervisor:{agent_prefix}",
            status="running",
            meta={"role": "supervisor", "child_count": len(items)},
        )
        child_ids: list[str] = []
        for i, item in enumerate(items):
            payload = dict(item)
            payload.setdefault("agent_ix", i)
            tid = self.runtime.start_run(
                graph_id=graph_id,
                initial=payload,
                agent_name=f"{agent_prefix}-{i+1:02d}",
                tenant_id=tenant_id,
                parent_thread_id=parent,
            )
            child_ids.append(tid)

        self.runtime.bus.publish(
            thread_id=parent,
            graph_id=graph_id,
            type="supervisor_fanout",
            tenant_id=tenant_id,
            payload={"children": child_ids, "n": len(child_ids)},
        )

        if not wait:

            def _bg() -> None:
                try:
                    if getattr(self.runtime.store, "_conn", None) is None:
                        return
                    self._wait_and_aggregate(
                        parent=parent,
                        graph_id=graph_id,
                        child_ids=child_ids,
                        tenant_id=tenant_id,
                        timeout_s=timeout_s,
                        poll_s=poll_s,
                    )
                except Exception as exc:
                    try:
                        self.runtime.store.upsert_run(
                            tenant_id=tenant_id,
                            thread_id=parent,
                            status="failed",
                            error=f"supervisor background wait failed: {exc}",
                            ended_at=time.time(),
                        )
                    except Exception:
                        pass

            t = threading.Thread(target=_bg, name=f"sup-{parent}", daemon=True)
            self._bg_threads.append(t)
            t.start()
            return {"parent_thread_id": parent, "children": child_ids, "status": "running"}

        return self._wait_and_aggregate(
            parent=parent,
            graph_id=graph_id,
            child_ids=child_ids,
            tenant_id=tenant_id,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )

    def _wait_and_aggregate(
        self,
        *,
        parent: str,
        graph_id: str,
        child_ids: list[str],
        tenant_id: str,
        timeout_s: float,
        poll_s: float,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            children = [self.runtime.store.get_run(c, tenant_id=tenant_id) for c in child_ids]
            if all(c and c["status"] not in {"pending", "running"} for c in children):
                break
            time.sleep(poll_s)

        children = [self.runtime.store.get_run(c, tenant_id=tenant_id) for c in child_ids]
        results = []
        protect_triggered = 0
        for c, cid in zip(children, child_ids):
            cp = self.runtime.store.latest_checkpoint(cid)
            state = (cp or {}).get("state") or {}
            data = state.get("data") if isinstance(state, dict) else {}
            data = data or {}
            pstatus = data.get("protect_status")
            if pstatus == "triggered":
                protect_triggered += 1
            results.append(
                {
                    "thread_id": cid,
                    "status": (c or {}).get("status"),
                    "error": (c or {}).get("error"),
                    "state": state,
                    "path": self.runtime.store.get_path(cid),
                    "protect_status": pstatus,
                    "protect_score": data.get("protect_score"),
                    "protect_path": data.get("protect_path"),
                }
            )

        failed = sum(1 for r in results if r["status"] == "failed")
        interrupted = sum(1 for r in results if r["status"] == "interrupted")
        succeeded = sum(1 for r in results if r["status"] == "succeeded")
        status = "succeeded"
        if failed:
            status = "failed"
        elif interrupted:
            status = "interrupted"

        aggregate = {
            "parent_thread_id": parent,
            "children": child_ids,
            "n": len(child_ids),
            "succeeded": succeeded,
            "failed": failed,
            "interrupted": interrupted,
            "protect_triggered": protect_triggered,
            "results": results,
            "status": status,
        }
        self.runtime.store.upsert_run(
            tenant_id=tenant_id,
            thread_id=parent,
            status=status,
            ended_at=time.time(),
            meta={
                "role": "supervisor",
                **{
                    k: aggregate[k]
                    for k in ("n", "succeeded", "failed", "interrupted", "protect_triggered")
                },
            },
        )
        self.runtime.store.put_checkpoint(
            {
                "tenant_id": tenant_id,
                "thread_id": parent,
                "checkpoint_id": f"sup-{uuid.uuid4().hex[:8]}",
                "graph_id": graph_id,
                "state": State(data={"supervisor": aggregate}, done=True).model_dump(),
                "next_nodes": [],
                "visits": {},
            }
        )
        self.runtime.bus.publish(
            thread_id=parent,
            graph_id=graph_id,
            type="supervisor_done",
            tenant_id=tenant_id,
            payload=aggregate,
        )
        self.runtime.store.record_metric(
            tenant_id=tenant_id,
            name="supervisor_batch_size",
            value=float(len(child_ids)),
            labels={"graph_id": graph_id},
        )
        self.runtime.store.record_metric(
            tenant_id=tenant_id,
            name="fail_rate",
            value=failed / max(1, len(child_ids)),
            labels={"graph_id": graph_id, "scope": "supervisor"},
        )
        return aggregate
