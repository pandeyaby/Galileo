"""Tests for Protect LoopNode, HITL, tenant map, XL API, span correlation."""

from __future__ import annotations

import time

import pytest

from dizzygraph import AtomicNode, Graph, LoopNode, MemoryCheckpointer, State, interrupt
from dizzygraph.control.demo_graph import ensure_demo_graph
from dizzygraph.control.runtime import FleetRuntime
from dizzygraph.control.store import ControlStore
from dizzygraph.control.tenant_projects import list_tenant_mappings, resolve_galileo_target


@pytest.fixture
def runtime(tmp_path):
    store = ControlStore(tmp_path / "control.db")
    rt = FleetRuntime(store, stuck_after_s=2.0, max_workers=8)
    ensure_demo_graph(rt)
    yield rt
    rt.close()


def _wait_terminal(runtime, ids, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = [runtime.store.get_run(i) for i in ids]
        if all(r and r["status"] not in {"pending", "running"} for r in runs):
            return runs
        time.sleep(0.15)
    return [runtime.store.get_run(i) for i in ids]


def test_protect_as_loop_checker_non_converge_alert(runtime):
    """LoopNode checker returns Protect-like low scores → loop_non_converge + Protect payload."""

    def maker(s: State):
        return {"data": {"draft_answer": "ungrounded speculation"}}

    def protect_checker(s: State) -> float:
        # Simulate live Protect: triggered + low score
        s.data["protect_status"] = "triggered"
        s.data["protect_path"] = "invoke_protect"
        s.data["protect_score"] = 0.2
        s.metrics["protect_status"] = "triggered"
        s.metrics["protect_path"] = "invoke_protect"
        s.metrics["protect_score"] = 0.2
        return 0.2

    g = Graph(id="protect-loop", name="Protect Loop")
    g.add_node(
        LoopNode(
            id="responder",
            maker=maker,
            checker=protect_checker,
            max_iters=2,
            score_threshold=0.5,
            score_key="quality",
        )
    )
    g.add_node(AtomicNode(id="done", fn=lambda s: {"done": True}))
    g.set_entry("responder")
    g.add_edge("responder", "done")
    runtime.register_graph(g)

    tid = runtime.start_run(graph_id="protect-loop", initial={"query": "q"})
    _wait_terminal(runtime, [tid])
    alerts = runtime.store.list_alerts(open_only=False)
    hit = [a for a in alerts if a["rule"] == "loop_non_converge" and a["thread_id"] == tid]
    assert hit, f"expected loop_non_converge, got {[a['rule'] for a in alerts]}"
    assert hit[0]["payload"].get("protect_status") == "triggered"
    assert "Protect=" in hit[0]["message"]


def test_hitl_after_protect_trigger():
    def draft(s: State):
        return {"data": {"draft_answer": "maybe wrong", "protect_status": "triggered"}}

    def protect_gate(s: State):
        if s.data.get("protect_status") == "triggered" and not s.data.get("approved"):
            interrupt(
                {
                    "prompt": "Protect blocked",
                    "protect_status": "triggered",
                    "draft": s.data.get("draft_answer"),
                }
            )
        final = s.data.get("edited_answer") or s.data.get("draft_answer")
        status = "overridden" if s.data.get("approved") else s.data.get("protect_status")
        return {"data": {"final_answer": final, "protect_status": status}, "done": True}

    g = Graph(id="hitl-protect")
    g.add_node(AtomicNode(id="draft", fn=draft))
    g.add_node(AtomicNode(id="protect", fn=protect_gate))
    g.set_entry("draft")
    g.add_edge("draft", "protect")

    app = g.compile(checkpointer=MemoryCheckpointer())
    t1 = app.invoke(State(data={"query": "q"}), thread_id="hitl-protect-1")
    assert t1.interrupted is True
    assert t1.interrupt_node == "protect"
    assert t1.interrupt_value["protect_status"] == "triggered"

    t2 = app.resume(thread_id="hitl-protect-1", update={"data": {"approved": True}})
    assert t2.interrupted is False
    assert t2.final_state is not None
    assert t2.final_state.data.get("protect_status") == "overridden"
    assert t2.final_state.data.get("final_answer") == "maybe wrong"


def test_tenant_galileo_mapping(monkeypatch):
    monkeypatch.setenv(
        "DIZZY_TENANT_GALILEO",
        '{"acme":{"project":"acme-proj","log_stream":"acme-stream"}}',
    )
    target = resolve_galileo_target("acme")
    assert target["project"] == "acme-proj"
    assert target["log_stream"] == "acme-stream"
    table = list_tenant_mappings()
    assert "acme" in table["tenants"]


def test_path_span_correlation_in_events(runtime):
    tid = runtime.start_run(
        graph_id="fleet-demo",
        initial={"query": "span", "agent_ix": 1},
    )
    _wait_terminal(runtime, [tid])
    events = runtime.store.events_for_thread(tid, limit=200)
    starts = [e for e in events if e["type"] == "node_start"]
    assert starts
    span = (starts[0].get("payload") or {}).get("span")
    assert span is not None
    assert span["otel.span_name"].startswith("dizzygraph.")
    assert span["path_step"] == starts[0]["node_id"]


def test_xl_fanout_requires_keys(runtime, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dizzygraph.control import trinity_fleet
    from dizzygraph.control.api import create_app

    def _boom():
        raise trinity_fleet.TrinityKeysError("no keys", missing=["OPENAI_API_KEY"])

    monkeypatch.setattr(trinity_fleet, "require_live_keys", _boom)
    # xl_fanout imports require_live_keys at call time from trinity_fleet
    import dizzygraph.control.xl_fanout as xl_mod

    monkeypatch.setattr(xl_mod, "require_live_keys", _boom)

    app = create_app(runtime)
    client = TestClient(app)
    r = client.post("/api/trinity/xl-fanout", json={"wait": False})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.get("live_required") is True
    assert "OPENAI_API_KEY" in (detail.get("missing") or [])


def test_supervisor_aggregates_protect_status(runtime):
    def maker(s: State):
        return {"data": {"draft_answer": "x", "quality": 1.0}}

    def publish(s: State):
        return {
            "data": {
                "final_answer": "x",
                "protect_status": "triggered" if int(s.data.get("agent_ix") or 0) % 2 == 0 else "not_triggered",
                "protect_score": 0.1 if int(s.data.get("agent_ix") or 0) % 2 == 0 else 0.9,
            },
            "done": True,
        }

    g = Graph(id="prot-fan")
    g.add_node(AtomicNode(id="work", fn=maker))
    g.add_node(AtomicNode(id="protect", fn=publish))
    g.set_entry("work")
    g.add_edge("work", "protect")
    runtime.register_graph(g)

    result = runtime.fan_out(
        graph_id="prot-fan",
        items=[{"query": f"q{i}", "agent_ix": i} for i in range(4)],
        wait=True,
        timeout_s=20,
    )
    assert result["n"] == 4
    assert result.get("protect_triggered") == 2
    assert all("protect_status" in r for r in result["results"])


def test_tenant_galileo_api(runtime):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dizzygraph.control.api import create_app

    app = create_app(runtime)
    client = TestClient(app)
    r = client.get("/api/tenants/galileo", headers={"X-Tenant-Id": "default"})
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"]["project"]
    assert body["resolved"]["log_stream"]


def test_evaluate_protect_score_helper(monkeypatch):
    """Unit-level: evaluate_protect_score maps protect_node output → score."""
    from trinity_dizzy import evaluate_protect_score

    class FakeTrinity:
        ADHERENCE_FLOOR = 0.5

        @staticmethod
        def protect_node(st):
            return {
                "final_answer": "[BLOCKED]",
                "protect_status": "triggered",
                "context_score": 0.3,
                "protect_path": "llm_judge_fallback",
            }

    s = State(data={"query": "q", "draft_answer": "bad", "retrieved_docs": []})
    out = evaluate_protect_score(s, trinity_mod=FakeTrinity)
    assert out["protect_status"] == "triggered"
    assert out["score"] < 0.5
