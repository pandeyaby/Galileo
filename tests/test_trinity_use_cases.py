"""Tests for Protect LoopNode, HITL, tenant map, XL API, span correlation."""

from __future__ import annotations

import time
from pathlib import Path

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
    assert span.get("openinference.span.kind") == "CHAIN"


def test_xl_fanout_requires_keys(runtime, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dizzygraph.control import trinity_fleet
    from dizzygraph.control.api import create_app

    def _boom():
        raise trinity_fleet.TrinityKeysError("no keys", missing=["OPENAI_API_KEY"])

    monkeypatch.setattr(trinity_fleet, "require_live_keys", _boom)
    import dizzygraph.control.xl_fanout as xl_mod

    monkeypatch.setattr(xl_mod, "require_live_keys", _boom)

    app = create_app(runtime)
    client = TestClient(app)
    r = client.post("/api/trinity/xl-fanout", json={"wait": False})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.get("live_required") is True
    assert "OPENAI_API_KEY" in (detail.get("missing") or [])


def test_meta_regression_requires_keys(runtime, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dizzygraph.control import trinity_fleet
    from dizzygraph.control.api import create_app

    def _boom():
        raise trinity_fleet.TrinityKeysError("no keys", missing=["OPENAI_API_KEY"])

    monkeypatch.setattr(trinity_fleet, "require_live_keys", _boom)
    app = create_app(runtime)
    client = TestClient(app)
    r = client.post("/api/trinity/meta-regression", json={"meta_iters": 2})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.get("live_required") is True


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


def test_trinity_graph_wires_protect_checker_metadata():
    """build_trinity_dizzy_graph marks LoopNode checker as galileo_protect (not heuristic)."""
    from trinity_dizzy import build_trinity_dizzy_graph

    g = build_trinity_dizzy_graph(protect_enabled=True, hitl_on_protect=False, max_loop_iters=1)
    responder = g.nodes["responder"]
    assert responder.metadata.get("checker") == "galileo_protect"
    assert callable(responder.checker)


def test_flush_fleet_uses_tenant_mapping(monkeypatch):
    """Galileo flush resolves tenant → project/stream and emits dizzygraph.<node> spans."""
    monkeypatch.setenv("GALILEO_API_KEY", "test-key")
    monkeypatch.setenv(
        "DIZZY_TENANT_GALILEO",
        '{"acme":{"project":"acme-proj","log_stream":"acme-stream"}}',
    )

    spans: list[str] = []
    flushed: dict = {}

    class FakeLogger:
        def __init__(self, project, log_stream):
            flushed["project"] = project
            flushed["log_stream"] = log_stream

        def start_trace(self, **kwargs):
            flushed["trace"] = kwargs

        def add_llm_span(self, **kwargs):
            spans.append(kwargs.get("name") or "")

        def conclude(self, **kwargs):
            flushed["conclude"] = kwargs

        def flush(self):
            flushed["flushed"] = True

    import sys
    import types

    fake_galileo = types.ModuleType("galileo")
    fake_galileo.GalileoLogger = FakeLogger
    monkeypatch.setitem(sys.modules, "galileo", fake_galileo)

    from dizzygraph.control.trinity_fleet import flush_fleet_run_to_galileo

    meta = flush_fleet_run_to_galileo(
        graph_id="trinity",
        state=State(data={"query": "q", "final_answer": "a", "protect_status": "not_triggered"}),
        tenant_id="acme",
        duration_s=1.2,
        path_steps=["intake", "responder", "protect"],
    )
    assert meta is not None
    assert flushed["project"] == "acme-proj"
    assert flushed["log_stream"] == "acme-stream"
    assert flushed.get("flushed") is True
    assert spans == ["dizzygraph.intake", "dizzygraph.responder", "dizzygraph.protect"]


def test_otel_callback_exports_node_spans(monkeypatch):
    """OpenTelemetryCallback emits real SDK spans named dizzygraph.<node>."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from dizzygraph.otel import OpenTelemetryCallback

    monkeypatch.setenv("DIZZY_OTEL", "1")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    g = Graph(id="otel-demo", name="otel")
    g.add_node(AtomicNode(id="alpha", fn=lambda s: {"data": {"x": 1}}))
    g.add_node(AtomicNode(id="beta", fn=lambda s: {"done": True}))
    g.set_entry("alpha")
    g.add_edge("alpha", "beta")
    app = g.compile(callbacks=[OpenTelemetryCallback(thread_id="t-otel")])
    trace_result = app.invoke(State(data={"query": "otel"}))
    assert not trace_result.interrupted

    spans = exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert "dizzygraph.otel-demo" in names
    assert "dizzygraph.alpha" in names
    assert "dizzygraph.beta" in names
    alpha = next(s for s in spans if s.name == "dizzygraph.alpha")
    assert alpha.attributes.get("otel.span_name") == "dizzygraph.alpha"
    assert alpha.attributes.get("dizzygraph.path_step") == "alpha"


def test_otel_module_soft_import():
    """dizzygraph.otel imports without requiring OTel packages at module load."""
    from dizzygraph import otel as otel_mod

    assert hasattr(otel_mod, "OpenTelemetryCallback")
    assert hasattr(otel_mod, "otel_available")


def test_integration_starters_fail_without_keys():
    """Starters must exit non-zero when required keys are missing (no fake success)."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    starters = [
        ("crewai_galileo.py", {}),
        ("a2a_galileo.py", {}),
        ("bedrock_galileo.py", {"GALILEO_API_KEY": "x"}),  # still needs AWS
    ]
    drop = {
        "OPENAI_API_KEY",
        "GALILEO_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_PROFILE",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    }
    env_base = {k: v for k, v in os.environ.items() if k not in drop}
    env_base["DIZZY_SKIP_DOTENV"] = "1"  # don't reload OpenClaw keys in fail-loud checks
    for name, extra in starters:
        env = {**env_base, **extra}
        proc = subprocess.run(
            [sys.executable, str(root / "examples" / "integrations" / name)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 2, (
            f"{name} should exit 2, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
        )
        assert "ERROR" in (proc.stdout + proc.stderr)
