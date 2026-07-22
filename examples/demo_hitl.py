#!/usr/bin/env python3
"""HITL + checkpoint demo — pause for approval, resume, finish."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dizzygraph import (
    AtomicNode,
    Graph,
    LoggingCallback,
    MemoryCheckpointer,
    State,
    interrupt,
    to_mermaid,
)


def main() -> None:
    def research(s: State) -> dict:
        return {"data": {"draft": f"Answer draft for: {s.data.get('query')}"}}

    def human_gate(s: State) -> dict:
        if not s.data.get("approved"):
            interrupt(
                {
                    "action": "approve_or_edit",
                    "draft": s.data.get("draft"),
                }
            )
        draft = s.data.get("edited_draft") or s.data.get("draft")
        return {"data": {"final": draft}}

    def publish(s: State) -> dict:
        return {"data": {"published": True}, "done": True, "results": [s.data.get("final")]}

    g = Graph(id="hitl-demo", name="Approve-then-publish")
    g.add_node(AtomicNode(id="research", fn=research))
    g.add_node(AtomicNode(id="gate", fn=human_gate))
    g.add_node(AtomicNode(id="publish", fn=publish))
    g.set_entry("research")
    g.add_edge("research", "gate")
    g.add_edge("gate", "publish")

    print("── mermaid ──")
    print(to_mermaid(g))

    app = g.compile(checkpointer=MemoryCheckpointer(), callbacks=[LoggingCallback()])
    print("\n── invoke (will interrupt) ──")
    t1 = app.invoke({"query": "Why do loops need gates?"}, thread_id="demo")
    print("interrupted:", t1.interrupted, "at", t1.interrupt_node)
    print("value:", t1.interrupt_value)

    print("\n── resume with approval ──")
    t2 = app.resume(thread_id="demo", update={"data": {"approved": True}})
    print("nodes:", t2.summary()["nodes_run"])
    print("final:", t2.final_state.data if t2.final_state else None)


if __name__ == "__main__":
    main()
