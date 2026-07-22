#!/usr/bin/env python3
"""
Autonomous-agent style demo: AgentNode + LoopNode + meta self-improvement.

  python examples/demo_agent.py

Swap mock LLM → real:
  from litellm import completion
  def llm_fn(state):
      r = completion(model="gpt-4o-mini", messages=[
          {"role": "user", "content": state.data["goal"]}
      ])
      return {"data": {"plan": r.choices[0].message.content}}
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dizzygraph import (
    AgentNode,
    AtomicNode,
    Graph,
    GraphExecutor,
    LoopNode,
    MetaLoopExecutor,
    State,
    SubGraphNode,
    visualize_graph,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("demo_agent")


def mock_llm(state: State) -> dict:
    goal = state.data.get("goal", "")
    step = int(state.data.get("plan_step", 1))
    plan = f"Step {step}: address '{goal[:60]}' with a verifiable check."
    return {"data": {"plan": plan, "plan_step": step}, "messages": [{"role": "assistant", "content": plan}]}


def build_inner_skill_graph() -> Graph:
    """Small nested graph used inside SubGraphNode."""

    def skill(state: State) -> dict:
        plan = state.data.get("plan", "")
        return {"data": {"skill_note": f"executed skill on: {plan[:80]}"}}

    g = Graph(id="skill", name="Skill SubGraph")
    g.add_node(AtomicNode(id="skill_run", fn=skill))
    g.set_entry("skill_run")
    return g


def build_agent_graph() -> Graph:
    def critique(state: State) -> float:
        plan = state.data.get("plan", "")
        # Prefer plans that mention verify/check
        score = 0.4
        if "verifiable" in plan.lower() or "check" in plan.lower():
            score += 0.4
        if len(plan) > 20:
            score += 0.2
        return min(1.0, score)

    def improve(state: State) -> dict:
        step = int(state.data.get("plan_step", 1)) + 1
        base = state.data.get("plan", "")
        return {
            "data": {
                "plan_step": step,
                "plan": base + " Refine with an explicit exit condition.",
            }
        }

    def finish(state: State) -> dict:
        return {"done": True, "results": [{"plan": state.data.get("plan"), "skill": state.data.get("skill_note")}]}

    g = Graph(id="agent", name="Agent Self-Improve Graph")
    g.add_node(
        AgentNode(
            id="propose",
            name="Propose",
            llm_fn=mock_llm,
            description="Mock LLM planner — swap llm_fn for litellm",
        )
    )
    g.add_node(
        LoopNode(
            id="self_critique",
            name="SelfCritiqueLoop",
            maker=improve,
            checker=critique,
            max_iters=4,
            score_threshold=0.9,
            score_key="quality",
        )
    )
    g.add_node(SubGraphNode(id="skills", name="Skills", graph=build_inner_skill_graph()))
    g.add_node(AtomicNode(id="finish", fn=finish))
    g.set_entry("propose")
    g.add_edge("propose", "self_critique")
    g.add_edge("self_critique", "skills")
    g.add_edge("skills", "finish")
    return g


def main() -> None:
    graph = build_agent_graph()
    out = ROOT / "dizzygraph_out"
    out.mkdir(exist_ok=True)
    visualize_graph(graph, path=out / "agent_graph.png", title="Agent + Loop + SubGraph")

    ex = GraphExecutor(graph, max_graph_iterations=16)
    meta = MetaLoopExecutor(ex, num_meta_iterations=3)
    result = meta.run(State(data={"goal": "Ship a safe agent loop with a checker"}))
    log.info("meta: %s", result.summary())
    log.info("final: %s", result.final_state.results if result.final_state else None)


if __name__ == "__main__":
    main()
