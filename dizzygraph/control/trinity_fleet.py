"""Register live Trinity Stack into the DizzyGraph control-plane fleet.

No mock fallbacks. Requires real OPENAI_API_KEY (+ Galileo key for Console flush)
loaded via ``trinity_dizzy.load_runtime_keys`` / OpenClaw config.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from ..callbacks import BaseCallbackHandler
from ..state import State
from .runtime import FleetRuntime
from .tenant_projects import resolve_galileo_target

log = logging.getLogger("dizzygraph.control.trinity")

TRINITY_GRAPH_ID = "trinity"


class TrinityKeysError(RuntimeError):
    """Raised when live Trinity cannot start because keys are missing."""

    def __init__(self, message: str, *, missing: list[str]):
        super().__init__(message)
        self.missing = missing


GALILEO_USE_CASES = [
    {
        "id": "protect-as-loop-gate",
        "title": "Protect as LoopNode checker",
        "layer": "LoopNode",
        "why": "Maker drafts; Protect score is the checker — non-converge → fleet alert",
        "status": "shipped",
    },
    {
        "id": "xl-drill-fanout",
        "title": "XL drill fan-out under supervisor",
        "layer": "Supervisor",
        "why": "Run XL-1..XL-6 as child thread_ids; aggregate Protect outcomes in one parent",
        "status": "shipped",
    },
    {
        "id": "silent-regression-meta",
        "title": "Silent regression via MetaLoop",
        "layer": "MetaLoopExecutor",
        "why": "Outer loop re-runs Trinity; fleet metrics show lag/fail while scorers trend down",
        "status": "shipped",
    },
    {
        "id": "hitl-protect-override",
        "title": "HITL after Protect trigger",
        "layer": "interrupt()",
        "why": "When Protect blocks, pause for human approve/edit; resume continues to publish",
        "status": "shipped",
    },
    {
        "id": "multi-tenant-projects",
        "title": "Tenant ↔ Galileo project/stream",
        "layer": "Auth/tenants",
        "why": "Each tenant_id maps to a Galileo project/log stream for isolated Console views",
        "status": "shipped",
    },
    {
        "id": "otel-path-correlate",
        "title": "Path overlay ↔ OTel/Galileo spans",
        "layer": "path_steps + events + OTel SDK",
        "why": (
            "Fleet path_steps align to span names dizzygraph.<node>; "
            "optional DizzyGraphTracer exports real OTel spans (GalileoSpanProcessor); "
            "flush uses tenant project map"
        ),
        "status": "shipped",
    },
]


def flush_fleet_run_to_galileo(
    *,
    graph_id: str,
    state: State | dict[str, Any],
    tenant_id: str = "default",
    duration_s: float = 0.0,
    path_steps: list[str] | None = None,
    project: str | None = None,
    log_stream: str | None = None,
) -> dict[str, Any] | None:
    """
    Flush a completed fleet run into Galileo using tenant → project/stream mapping.

    Uses ``path_steps`` (from ControlStore) for ``dizzygraph.<node>`` span names so
    path overlay ↔ Console spans correlate. Returns flush metadata or None if skipped.
    """
    if not os.environ.get("GALILEO_API_KEY"):
        return None
    if isinstance(state, dict):
        data = state.get("data") or {}
        metrics = state.get("metrics") or {}
    else:
        data = state.data
        metrics = state.metrics
    target = resolve_galileo_target(tenant_id)
    project = project or target["project"]
    log_stream = log_stream or target["log_stream"]
    path = list(path_steps or data.get("path_steps") or [])
    try:
        from galileo import GalileoLogger

        query = data.get("query") or ""
        answer = data.get("final_answer") or data.get("draft_answer") or ""
        logger = GalileoLogger(project=project, log_stream=log_stream)
        logger.start_trace(
            input=query,
            name=f"fleet-{graph_id}",
            tags=["fleet", "dizzygraph", graph_id, "live", f"tenant:{tenant_id}"],
            metadata={
                "engine": "dizzygraph-fleet",
                "tenant_id": tenant_id,
                "duration_s": str(round(duration_s, 4)),
                "protect_status": str(data.get("protect_status")),
                "protect_score": str(data.get("protect_score")),
                "protect_path": str(data.get("protect_path")),
                "loop_converged": str(metrics.get("loop_converged")),
                "path_steps": ",".join(path) if path else "",
            },
        )
        for step in path or ["trinity-fleet"]:
            span_name = f"dizzygraph.{step}"
            logger.add_llm_span(
                input=query,
                output=answer if step in {"protect", "responder"} else step,
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                name=span_name,
                metadata={
                    "otel.span_name": span_name,
                    "openinference.span.kind": "CHAIN" if step not in {"responder", "protect"} else "LLM",
                    "path_step": step,
                    "protect_status": str(data.get("protect_status")),
                },
            )
        logger.conclude(output=answer)
        logger.flush()
        return {"project": project, "log_stream": log_stream, "spans": len(path) or 1}
    except Exception as exc:
        log.warning("Galileo fleet flush skipped: %s", type(exc).__name__)
        return None


class GalileoFleetCallback(BaseCallbackHandler):
    """Callback wrapper around ``flush_fleet_run_to_galileo`` (tenant mapping)."""

    def __init__(
        self,
        *,
        tenant_id: str = "default",
        project: str | None = None,
        log_stream: str | None = None,
        path_steps: list[str] | None = None,
    ):
        target = resolve_galileo_target(tenant_id)
        self.tenant_id = tenant_id
        self.project = project or target["project"]
        self.log_stream = log_stream or target["log_stream"]
        self.path_steps = path_steps

    def on_graph_end(self, graph_id: str, state: State, duration_s: float) -> None:
        flush_fleet_run_to_galileo(
            graph_id=graph_id,
            state=state,
            tenant_id=self.tenant_id,
            duration_s=duration_s,
            path_steps=self.path_steps,
            project=self.project,
            log_stream=self.log_stream,
        )


def require_live_keys() -> dict[str, bool]:
    """Load keys from env / OpenClaw; raise TrinityKeysError if OpenAI missing."""
    from trinity_dizzy import load_runtime_keys

    keys = load_runtime_keys()
    missing: list[str] = []
    if not keys.get("openai"):
        missing.append("OPENAI_API_KEY")
    if missing:
        raise TrinityKeysError(
            "Live Trinity requires API keys. Set OPENAI_API_KEY (and ideally GALILEO_API_KEY) "
            "in the environment, galileo-labs/.env, or OpenClaw config. No mock fallback.",
            missing=missing,
        )
    return keys


def ensure_trinity_graphs(
    runtime: FleetRuntime,
    *,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """
    Register the **live** Trinity graph only.

    Raises ``TrinityKeysError`` if keys are missing. Never registers a mock graph.
    """
    keys = require_live_keys()
    if not runtime.has_graph(TRINITY_GRAPH_ID):
        from trinity_dizzy import build_trinity_dizzy_graph

        g = build_trinity_dizzy_graph(protect_enabled=True, hitl_on_protect=True)
        g.id = TRINITY_GRAPH_ID
        g.name = "Trinity Stack (live · Protect LoopNode + HITL)"
        runtime.register_graph(g, tenant_id=tenant_id)
        log.info(
            "Registered live Trinity graph (openai=%s galileo=%s tenant=%s)",
            keys.get("openai"),
            keys.get("galileo"),
            tenant_id,
        )
    target = resolve_galileo_target(tenant_id)
    return {
        "live": TRINITY_GRAPH_ID,
        "keys": {"openai": bool(keys.get("openai")), "galileo": bool(keys.get("galileo"))},
        "galileo_target": target,
        "tenant_id": tenant_id,
    }


def spawn_trinity_fleet(
    runtime: FleetRuntime,
    *,
    queries: list[str],
    tenant_id: str = "default",
    agent_prefix: str = "trinity",
) -> dict[str, Any]:
    """Fan-out live Trinity queries as supervised child runs. No mock path."""
    if not queries:
        raise ValueError("queries must be non-empty")
    reg = ensure_trinity_graphs(runtime, tenant_id=tenant_id)
    items = [{"query": q, "agent_ix": i} for i, q in enumerate(queries)]
    result = runtime.fan_out(
        graph_id=TRINITY_GRAPH_ID,
        items=items,
        tenant_id=tenant_id,
        agent_prefix=agent_prefix,
        wait=False,
    )
    result["graph_id"] = TRINITY_GRAPH_ID
    result["live"] = True
    result["keys"] = reg.get("keys")
    result["galileo_target"] = reg.get("galileo_target")
    return result


def run_silent_regression_meta(
    runtime: FleetRuntime,
    *,
    query: str,
    tenant_id: str = "default",
    meta_iters: int = 3,
) -> dict[str, Any]:
    """
    MetaLoop over live Trinity — records protect/quality trend metrics to fleet.

    Runs synchronously in-process (not as child threads) then stores a parent run
    summarizing meta iterations for the ops UI.
    """
    import uuid

    from dizzygraph import GraphExecutor, MetaLoopExecutor, State
    from trinity_dizzy import build_trinity_dizzy_graph

    keys = require_live_keys()
    ensure_trinity_graphs(runtime, tenant_id=tenant_id)
    graph = build_trinity_dizzy_graph(protect_enabled=True, hitl_on_protect=False)
    ex = GraphExecutor(graph, max_graph_iterations=16)
    meta = MetaLoopExecutor(
        ex,
        num_meta_iterations=meta_iters,
        state_updater=lambda st, tr, i: st.model_copy(
            update={
                "done": False,
                "data": {
                    **st.data,
                    "meta_boost": i,
                    "query": query,
                    # Soft inject: nudge speculative answers on later iters (XL-6 shape)
                    "xl_mode": "xl6" if i >= 1 else st.data.get("xl_mode"),
                },
            }
        ),
    )
    t0 = time.perf_counter()
    result = meta.run(State(data={"query": query}))
    elapsed = time.perf_counter() - t0

    qualities: list[float] = []
    protect_statuses: list[str] = []
    for st in result.states:
        if st.data.get("quality") is not None:
            qualities.append(float(st.data["quality"]))
        elif st.data.get("protect_score") is not None:
            qualities.append(float(st.data["protect_score"]))
        protect_statuses.append(str(st.data.get("protect_status") or ""))

    trend = None
    if len(qualities) >= 2:
        trend = qualities[-1] - qualities[0]

    parent = f"meta-regression-{uuid.uuid4().hex[:10]}"
    runtime.store.upsert_run(
        tenant_id=tenant_id,
        thread_id=parent,
        graph_id=TRINITY_GRAPH_ID,
        agent_name="meta:silent-regression",
        status="succeeded",
        started_at=t0,
        ended_at=time.time(),
        meta={
            "role": "meta_loop",
            "kind": "silent-regression",
            "meta_iters": meta_iters,
            "qualities": qualities,
            "protect_statuses": protect_statuses,
            "quality_delta": trend,
            "elapsed_s": round(elapsed, 3),
        },
    )
    runtime.store.put_checkpoint(
        {
            "tenant_id": tenant_id,
            "thread_id": parent,
            "checkpoint_id": f"meta-{uuid.uuid4().hex[:8]}",
            "graph_id": TRINITY_GRAPH_ID,
            "state": State(
                data={
                    "query": query,
                    "meta_qualities": qualities,
                    "protect_statuses": protect_statuses,
                    "quality_delta": trend,
                    "protect_status": (result.final_state.data.get("protect_status") if result.final_state else None),
                    "final_answer": (
                        result.final_state.data.get("final_answer") if result.final_state else None
                    ),
                },
                done=True,
                metrics={"meta_converged": result.converged, "elapsed_s": elapsed},
            ).model_dump(),
            "next_nodes": [],
            "visits": {},
        }
    )
    for i, q in enumerate(qualities):
        runtime.store.record_metric(
            tenant_id=tenant_id,
            name="meta_protect_score",
            value=float(q),
            labels={"meta_iter": str(i + 1), "graph_id": TRINITY_GRAPH_ID},
        )
    if trend is not None:
        runtime.store.record_metric(
            tenant_id=tenant_id,
            name="meta_quality_delta",
            value=float(trend),
            labels={"graph_id": TRINITY_GRAPH_ID},
        )
    runtime.store.record_metric(
        tenant_id=tenant_id,
        name="run_lag_s",
        value=float(elapsed),
        labels={"graph_id": TRINITY_GRAPH_ID, "scope": "meta"},
    )
    runtime.bus.publish(
        thread_id=parent,
        graph_id=TRINITY_GRAPH_ID,
        type="meta_regression_done",
        tenant_id=tenant_id,
        payload={
            "qualities": qualities,
            "protect_statuses": protect_statuses,
            "quality_delta": trend,
            "elapsed_s": elapsed,
        },
    )
    target = resolve_galileo_target(tenant_id)
    return {
        "thread_id": parent,
        "meta_iters": meta_iters,
        "qualities": qualities,
        "protect_statuses": protect_statuses,
        "quality_delta": trend,
        "elapsed_s": round(elapsed, 3),
        "summary": result.summary(),
        "galileo_target": target,
        "keys": {"openai": bool(keys.get("openai")), "galileo": bool(keys.get("galileo"))},
        "live": True,
    }


DEFAULT_TRINITY_QUERIES = [
    "How do I debug a CUDA out-of-memory error during training?",
    "Explain vLLM PagedAttention in one paragraph.",
    "What is gradient checkpointing and when should I use it?",
    "How do I choose between FSDP and DeepSpeed ZeRO?",
]
