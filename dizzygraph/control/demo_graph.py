"""Demo graph for fleet — includes LoopNode that sometimes fails to converge."""

from __future__ import annotations

import random
import time

from ..graph import Graph
from ..interrupt import interrupt
from ..nodes import AtomicNode, LoopNode
from ..retry import RetryPolicy
from ..state import State
from .runtime import FleetRuntime


def build_demo_graph() -> Graph:
    def intake(s: State) -> dict:
        time.sleep(0.05 + random.random() * 0.1)
        return {"data": {"accepted": True, "seed": random.random()}}

    def work_maker(s: State) -> dict:
        time.sleep(0.05)
        ix = int(s.data.get("agent_ix") or 0)
        # Every 3rd agent is designed to struggle / not converge
        base = 0.35 if ix % 3 == 0 else 0.55
        q = float(s.data.get("quality") or base) + (0.15 if ix % 3 == 0 else 0.35)
        return {"data": {"quality": q, "draft": f"draft@{q:.2f}"}}

    def work_check(s: State) -> float:
        return float(s.data.get("quality") or 0)

    def gate(s: State) -> dict:
        ix = int(s.data.get("agent_ix") or 0)
        # Every 5th agent waits for human approval once
        if ix % 5 == 0 and not s.data.get("approved"):
            interrupt({"prompt": "approve publish", "draft": s.data.get("draft")})
        return {"data": {"gated": True}}

    def publish(s: State) -> dict:
        return {
            "data": {"final": s.data.get("draft"), "published": True},
            "done": True,
            "results": [s.data.get("draft")],
        }

    g = Graph(id="fleet-demo", name="Fleet Demo (loop + HITL + retries)")
    g.add_node(AtomicNode(id="intake", fn=intake))
    g.add_node(
        LoopNode(
            id="worker",
            name="WorkerLoop",
            maker=work_maker,
            checker=work_check,
            max_iters=3,
            score_threshold=0.9,
            score_key="quality",
            retry_policy=RetryPolicy(max_attempts=3, initial_interval_s=0.05, jitter=False),
        )
    )
    g.add_node(AtomicNode(id="gate", fn=gate))
    g.add_node(AtomicNode(id="publish", fn=publish))
    g.set_entry("intake")
    g.add_edge("intake", "worker")
    g.add_edge("worker", "gate")
    g.add_edge("gate", "publish")
    return g


def ensure_demo_graph(runtime: FleetRuntime, *, tenant_id: str = "default") -> str:
    if not runtime.has_graph("fleet-demo"):
        runtime.register_graph(build_demo_graph(), tenant_id=tenant_id)
    return "fleet-demo"
