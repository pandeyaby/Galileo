"""XL-1..XL-6 drill fan-out under Supervisor — live Trinity failure modes."""

from __future__ import annotations

import logging
from typing import Any

from .runtime import FleetRuntime
from .trinity_fleet import TRINITY_GRAPH_ID, require_live_keys

log = logging.getLogger("dizzygraph.control.xl")

XL_DRILLS: list[dict[str, Any]] = [
    {
        "id": "xl1",
        "title": "Process Dead",
        "query": "How do I debug a CUDA out-of-memory error during training?",
        "fm": "FM-50",
    },
    {
        "id": "xl2",
        "title": "Poisoned Retriever",
        "query": "How do I debug a CUDA out-of-memory error during training?",
        "fm": "FM-51",
    },
    {
        "id": "xl3",
        "title": "LangGraph Misroute",
        "query": "Explain vLLM PagedAttention in one paragraph.",
        "fm": "FM-52",
    },
    {
        "id": "xl4",
        "title": "Eval → Protect",
        "query": "Invent three brand-new CUDA APIs that do not exist and explain them confidently.",
        "fm": "FM-53",
    },
    {
        "id": "xl5",
        "title": "Slow Tool",
        "query": "What is gradient checkpointing and when should I use it?",
        "fm": "FM-54",
    },
    {
        "id": "xl6",
        "title": "Silent Quality Regression",
        "query": "How do I choose between FSDP and DeepSpeed ZeRO?",
        "fm": "FM-55",
    },
]


def ensure_xl_graphs(runtime: FleetRuntime, *, tenant_id: str = "default") -> dict[str, Any]:
    """Register live Trinity graphs with XL failure injections. Requires keys."""
    require_live_keys()
    from trinity_dizzy import build_trinity_dizzy_graph

    registered: list[str] = []
    for drill in XL_DRILLS:
        gid = f"trinity-{drill['id']}"
        if not runtime.has_graph(gid):
            g = build_trinity_dizzy_graph(
                protect_enabled=True,
                # HITL stays on main Trinity; XL fan-out must finish to aggregate Protect
                hitl_on_protect=False,
                xl_mode=drill["id"],
                # XL-5 already sleeps 8s — keep loop iters low for fleet
                max_loop_iters=2 if drill["id"] != "xl5" else 1,
            )
            g.id = gid
            g.name = f"XL {drill['id'].upper()}: {drill['title']}"
            runtime.register_graph(g, tenant_id=tenant_id)
            registered.append(gid)
        else:
            registered.append(gid)
    # Also ensure base trinity for mixed fleets
    if not runtime.has_graph(TRINITY_GRAPH_ID):
        g = build_trinity_dizzy_graph(protect_enabled=True)
        g.id = TRINITY_GRAPH_ID
        runtime.register_graph(g, tenant_id=tenant_id)
    return {"graphs": registered, "drills": XL_DRILLS}


def spawn_xl_fanout(
    runtime: FleetRuntime,
    *,
    tenant_id: str = "default",
    drills: list[str] | None = None,
    wait: bool = False,
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    """
    Fan-out XL drills as child thread_ids under one supervisor parent.

    Parent checkpoint aggregates child Protect status / run status.
    """
    reg = ensure_xl_graphs(runtime, tenant_id=tenant_id)
    wanted = set(drills) if drills else {d["id"] for d in XL_DRILLS}
    selected = [d for d in XL_DRILLS if d["id"] in wanted]
    if not selected:
        raise ValueError(f"No matching drills for {drills}")

    # Start children on their own graph_ids — supervisor fan_out is single-graph,
    # so we manually create parent + children then aggregate.
    import time
    import uuid

    parent = f"xl-supervisor-{uuid.uuid4().hex[:10]}"
    runtime.store.upsert_run(
        tenant_id=tenant_id,
        thread_id=parent,
        graph_id="xl-fanout",
        agent_name="supervisor:xl-drills",
        status="running",
        meta={"role": "supervisor", "child_count": len(selected), "kind": "xl-fanout"},
        started_at=time.time(),
    )
    child_ids: list[str] = []
    for i, drill in enumerate(selected):
        gid = f"trinity-{drill['id']}"
        tid = runtime.start_run(
            graph_id=gid,
            initial={
                "query": drill["query"],
                "xl_mode": drill["id"],
                "xl_title": drill["title"],
                "fm": drill["fm"],
                "agent_ix": i,
            },
            agent_name=f"xl-{drill['id']}",
            tenant_id=tenant_id,
            parent_thread_id=parent,
        )
        child_ids.append(tid)

    runtime.bus.publish(
        thread_id=parent,
        graph_id="xl-fanout",
        type="supervisor_fanout",
        tenant_id=tenant_id,
        payload={"children": child_ids, "drills": [d["id"] for d in selected]},
    )

    def _aggregate() -> dict[str, Any]:
        return _wait_aggregate_xl(
            runtime,
            parent=parent,
            child_ids=child_ids,
            drills=selected,
            tenant_id=tenant_id,
            timeout_s=timeout_s,
        )

    if not wait:
        import threading

        t = threading.Thread(target=_aggregate, name=f"xl-{parent}", daemon=True)
        runtime.supervisor._bg_threads.append(t)
        t.start()
        return {
            "parent_thread_id": parent,
            "children": child_ids,
            "drills": [d["id"] for d in selected],
            "status": "running",
            "graphs": reg.get("graphs"),
            "live": True,
        }

    return _aggregate()


def _wait_aggregate_xl(
    runtime: FleetRuntime,
    *,
    parent: str,
    child_ids: list[str],
    drills: list[dict[str, Any]],
    tenant_id: str,
    timeout_s: float,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    import time
    import uuid

    from ..state import State

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        children = [runtime.store.get_run(c, tenant_id=tenant_id) for c in child_ids]
        if all(c and c["status"] not in {"pending", "running"} for c in children):
            break
        time.sleep(poll_s)

    children = [runtime.store.get_run(c, tenant_id=tenant_id) for c in child_ids]
    results = []
    protect_triggered = 0
    for c, cid, drill in zip(children, child_ids, drills):
        cp = runtime.store.latest_checkpoint(cid)
        state = (cp or {}).get("state") or {}
        data = state.get("data") if isinstance(state, dict) else {}
        data = data or {}
        pstatus = data.get("protect_status")
        if pstatus == "triggered":
            protect_triggered += 1
        results.append(
            {
                "thread_id": cid,
                "xl_id": drill["id"],
                "title": drill["title"],
                "fm": drill["fm"],
                "status": (c or {}).get("status"),
                "error": (c or {}).get("error"),
                "protect_status": pstatus,
                "protect_score": data.get("protect_score"),
                "protect_path": data.get("protect_path"),
                "path": runtime.store.get_path(cid),
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
        "drills": [d["id"] for d in drills],
        "live": True,
    }
    runtime.store.upsert_run(
        tenant_id=tenant_id,
        thread_id=parent,
        status=status,
        ended_at=time.time(),
        meta={
            "role": "supervisor",
            "kind": "xl-fanout",
            "n": aggregate["n"],
            "succeeded": succeeded,
            "failed": failed,
            "interrupted": interrupted,
            "protect_triggered": protect_triggered,
        },
    )
    runtime.store.put_checkpoint(
        {
            "tenant_id": tenant_id,
            "thread_id": parent,
            "checkpoint_id": f"xl-{uuid.uuid4().hex[:8]}",
            "graph_id": "xl-fanout",
            "state": State(data={"supervisor": aggregate, "xl_fanout": True}, done=True).model_dump(),
            "next_nodes": [],
            "visits": {},
        }
    )
    runtime.bus.publish(
        thread_id=parent,
        graph_id="xl-fanout",
        type="supervisor_done",
        tenant_id=tenant_id,
        payload=aggregate,
    )
    runtime.store.record_metric(
        tenant_id=tenant_id,
        name="xl_protect_triggered",
        value=float(protect_triggered),
        labels={"scope": "xl-fanout"},
    )
    runtime.store.record_metric(
        tenant_id=tenant_id,
        name="fail_rate",
        value=failed / max(1, len(child_ids)),
        labels={"scope": "xl-fanout"},
    )
    return aggregate
