"""DizzyGraph core tests — reducers, LoopNode, stream, checkpoint, HITL."""

from __future__ import annotations

import pytest

from dizzygraph import (
    AtomicNode,
    Graph,
    LoopNode,
    MemoryCheckpointer,
    RetryPolicy,
    State,
    interrupt,
    merge_state,
    register_data_reducer,
    clear_data_reducers,
    unique_append,
    to_mermaid,
)


@pytest.fixture(autouse=True)
def _clean_reducers():
    clear_data_reducers()
    yield
    clear_data_reducers()


def test_data_lists_replace_by_default():
    s = State(data={"doc_ids": ["a", "b"]})
    s2 = merge_state(s, {"data": {"doc_ids": ["c"]}})
    assert s2.data["doc_ids"] == ["c"]


def test_unique_append_reducer():
    register_data_reducer("doc_ids", unique_append)
    s = State(data={"doc_ids": ["a", "b"]})
    s2 = merge_state(s, {"data": {"doc_ids": ["b", "c"]}})
    assert s2.data["doc_ids"] == ["a", "b", "c"]


def test_messages_append():
    s = State(messages=[{"role": "user", "content": "hi"}])
    s2 = merge_state(s, {"messages": [{"role": "assistant", "content": "yo"}]})
    assert len(s2.messages) == 2


def test_loop_node_converges():
    def maker(s: State):
        q = float(s.data.get("quality") or 0) + 0.4
        return {"data": {"quality": q, "draft": f"v{q:.1f}"}}

    g = Graph(id="t")
    g.add_node(
        LoopNode(
            id="refine",
            maker=maker,
            checker=lambda s: float(s.data.get("quality") or 0),
            max_iters=5,
            score_threshold=0.85,
        )
    )
    g.set_entry("refine")
    app = g.compile()
    trace = app.invoke(State())
    assert trace.final_state is not None
    assert trace.final_state.metrics["loop_converged"] is True
    assert trace.final_state.metrics["loop_count"] == 3


def test_stream_events():
    g = Graph(id="s")
    g.add_node(AtomicNode(id="a", fn=lambda s: {"data": {"x": 1}}))
    g.add_node(AtomicNode(id="b", fn=lambda s: {"data": {"y": 2}, "done": True}))
    g.set_entry("a")
    g.add_edge("a", "b")
    types = [e.type for e in g.compile().stream(State())]
    assert types[0] == "graph_start"
    assert "node_start" in types
    assert "node_end" in types
    assert types[-1] == "graph_end"


def test_checkpoint_and_resume_hitl(tmp_path=None):
    def draft(s: State):
        return {"data": {"draft": "needs review"}}

    def gate(s: State):
        if not s.data.get("approved"):
            interrupt({"prompt": "Approve draft?", "draft": s.data.get("draft")})
        return {"data": {"final": s.data["draft"]}, "done": True}

    g = Graph(id="hitl")
    g.add_node(AtomicNode(id="draft", fn=draft))
    g.add_node(AtomicNode(id="gate", fn=gate))
    g.set_entry("draft")
    g.add_edge("draft", "gate")

    cp = MemoryCheckpointer()
    app = g.compile(checkpointer=cp)
    t1 = app.invoke(State(), thread_id="u1")
    assert t1.interrupted is True
    assert t1.interrupt_node == "gate"

    t2 = app.resume(thread_id="u1", update={"data": {"approved": True}})
    assert t2.interrupted is False
    assert t2.final_state is not None
    assert t2.final_state.data.get("final") == "needs review"


def test_retry_policy():
    box = {"n": 0}

    def flaky(s: State):
        box["n"] += 1
        if box["n"] < 3:
            raise RuntimeError("boom")
        return {"data": {"ok": True}, "done": True}

    g = Graph(id="retry")
    g.add_node(
        AtomicNode(
            id="flaky",
            fn=flaky,
            retry_policy=RetryPolicy(max_attempts=3, initial_interval_s=0.01, jitter=False),
        )
    )
    g.set_entry("flaky")
    trace = g.compile().invoke(State())
    assert trace.final_state.data["ok"] is True
    assert box["n"] == 3


def test_timeout():
    import time

    def slow(s: State):
        time.sleep(0.5)
        return {"done": True}

    g = Graph(id="to")
    g.add_node(AtomicNode(id="slow", fn=slow, timeout_s=0.05))
    g.set_entry("slow")
    trace = g.compile().invoke(State())
    assert trace.final_state is not None
    assert trace.final_state.error
    assert "Timeout" in (trace.final_state.error or "")


def test_mermaid_contains_nodes():
    g = Graph(id="m")
    g.add_node(AtomicNode(id="a", fn=lambda s: s))
    g.add_node(LoopNode(id="L", maker=lambda s: s, max_iters=1))
    g.set_entry("a")
    g.add_edge("a", "L")
    text = to_mermaid(g)
    assert "flowchart TD" in text
    assert "a" in text
