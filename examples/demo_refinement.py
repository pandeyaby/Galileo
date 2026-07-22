#!/usr/bin/env python3
"""
DizzyGraph demos — runnable without API keys.

  python -m examples.demo_refinement
  python examples/demo_refinement.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dizzygraph import (
    AtomicNode,
    Graph,
    GraphExecutor,
    LoopNode,
    MetaLoopExecutor,
    State,
    visualize_graph,
    save_graph,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("demo")


def build_refinement_graph() -> Graph:
    """
    Atomic intake → LoopNode (maker/checker) → optional feedback cycle → aggregate.

    Graph-level cycle: if quality still low, edge back to refine (capped by executor).
    """

    def intake(state: State) -> dict:
        draft = state.data.get("seed", "The system uses loops.")
        return {"data": {"draft": draft, "query": state.data.get("query", "Improve this")}}

    def maker(state: State) -> dict:
        draft = state.data.get("draft", "")
        # Deterministic "improvement": append a clarifying clause each pass
        improved = (draft + " It converges via explicit checkers and exit conditions.").strip()
        # Slightly shorten runaway growth
        if len(improved) > 220:
            improved = improved[:217] + "..."
        return {"data": {"draft": improved}, "messages": [{"role": "maker", "content": improved}]}

    def checker(state: State) -> float:
        draft = state.data.get("draft", "")
        # Score: length + keyword coverage (mock quality)
        keys = ("converges", "checkers", "exit")
        hit = sum(1 for k in keys if k in draft.lower())
        score = min(1.0, 0.25 * hit + min(len(draft), 120) / 200.0)
        return score

    def aggregate(state: State) -> dict:
        return {
            "results": [{"final_draft": state.data.get("draft"), "quality": state.data.get("quality")}],
            "done": True,
        }

    g = Graph(id="refine", name="Iterative Refinement Graph")
    g.add_node(AtomicNode(id="intake", name="Intake", fn=intake))
    g.add_node(
        LoopNode(
            id="refine",
            name="RefineLoop",
            description="maker improves draft; checker scores until threshold",
            maker=maker,
            checker=checker,
            max_iters=5,
            score_threshold=0.85,
            score_key="quality",
        )
    )
    g.add_node(AtomicNode(id="aggregate", name="Aggregate", fn=aggregate))
    g.set_entry("intake")
    g.add_edge("intake", "refine")
    # Feedback edge: if still weak, cycle (graph-level loop over the LoopNode)
    g.add_edge(
        "refine",
        "refine",
        condition=lambda s: float(s.data.get("quality") or 0) < 0.85 and not s.done,
        label="retry-if-weak",
    )
    g.add_edge(
        "refine",
        "aggregate",
        condition=lambda s: float(s.data.get("quality") or 0) >= 0.85 or s.metrics.get("loop_count", 0) >= 5,
        label="accept",
    )
    return g


def main() -> None:
    graph = build_refinement_graph()
    warnings = graph.validate(allow_cycles=True)
    log.info("validation: %s", warnings or "ok")
    log.info("cycles: %s", graph.detect_cycles())

    out_dir = ROOT / "dizzygraph_out"
    out_dir.mkdir(exist_ok=True)
    save_graph(graph, out_dir / "refine_graph.json")
    visualize_graph(graph, path=out_dir / "refine_graph.png", title="Refinement (LoopNode + cycle)")

    ex = GraphExecutor(graph, max_graph_iterations=12)
    meta = MetaLoopExecutor(
        ex,
        num_meta_iterations=4,
        convergence_check=lambda prev, cur: abs(
            float(prev.data.get("quality") or 0) - float(cur.data.get("quality") or 0)
        )
        < 0.01
        and float(cur.data.get("quality") or 0) >= 0.85,
        state_updater=lambda st, tr, i: st.model_copy(
            update={
                "done": False,
                "data": {
                    **st.data,
                    "seed": st.data.get("draft", st.data.get("seed", "")),
                    "meta_boost": i,
                },
            }
        ),
    )

    initial = State(data={"seed": "Agents need structure.", "query": "Make this production-ready"})
    result = meta.run(initial)

    log.info("── Meta summary ──")
    log.info("%s", result.summary())
    log.info("Final draft: %s", (result.final_state.data.get("draft") if result.final_state else None))
    log.info("Artifacts in %s", out_dir)


if __name__ == "__main__":
    main()
