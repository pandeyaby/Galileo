"""Control plane v0.4 — tenants, path, metrics, supervisor, auth."""

from __future__ import annotations

import time

import pytest

from dizzygraph.control.auth import AuthRegistry
from dizzygraph.control.demo_graph import ensure_demo_graph
from dizzygraph.control.runtime import FleetRuntime
from dizzygraph.control.store import ControlStore


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


def test_spawn_many_agents_and_persist(runtime):
    ids = [
        runtime.start_run(
            graph_id="fleet-demo",
            agent_name=f"a{i}",
            initial={"query": f"q{i}", "agent_ix": i},
        )
        for i in range(6)
    ]
    runs = _wait_terminal(runtime, ids)
    assert all(r is not None for r in runs)
    for r in runs:
        if r["status"] in {"succeeded", "interrupted"}:
            assert runtime.store.latest_checkpoint(r["thread_id"]) is not None
            assert runtime.store.get_path(r["thread_id"])


def test_loop_non_converge_alert(runtime):
    tid = runtime.start_run(
        graph_id="fleet-demo",
        agent_name="slow-loop",
        initial={"query": "x", "agent_ix": 3},
    )
    _wait_terminal(runtime, [tid])
    alerts = runtime.store.list_alerts(open_only=False)
    assert any(a["rule"] == "loop_non_converge" and a["thread_id"] == tid for a in alerts)


def test_path_overlay_recorded(runtime):
    tid = runtime.start_run(
        graph_id="fleet-demo",
        initial={"query": "p", "agent_ix": 1},
    )
    _wait_terminal(runtime, [tid])
    path = runtime.store.get_path(tid)
    assert "intake" in path
    assert "worker" in path


def test_metrics_rollup(runtime):
    ids = [
        runtime.start_run(
            graph_id="fleet-demo",
            initial={"query": f"m{i}", "agent_ix": i},
        )
        for i in range(4)
    ]
    _wait_terminal(runtime, ids)
    rollup = runtime.store.metrics_rollup()
    assert rollup["total_runs"] >= 4
    assert "fail_rate" in rollup
    assert "lag_p50_s" in rollup
    assert "loop_iterations_avg" in rollup


def test_supervisor_fanout(runtime):
    result = runtime.fan_out(
        graph_id="fleet-demo",
        items=[{"query": f"s{i}", "agent_ix": i} for i in range(4)],
        wait=True,
        timeout_s=20,
    )
    assert result["n"] == 4
    assert len(result["children"]) == 4
    parent = runtime.store.get_run(result["parent_thread_id"])
    assert parent is not None
    assert parent["meta"].get("role") == "supervisor"
    children = runtime.store.list_children(result["parent_thread_id"])
    assert len(children) == 4


def test_tenant_isolation(runtime):
    a = runtime.start_run(
        graph_id="fleet-demo",
        tenant_id="alpha",
        agent_name="alpha-1",
        initial={"query": "a", "agent_ix": 1},
    )
    b = runtime.start_run(
        graph_id="fleet-demo",
        tenant_id="beta",
        agent_name="beta-1",
        initial={"query": "b", "agent_ix": 1},
    )
    _wait_terminal(runtime, [a, b])
    alpha_runs = runtime.store.list_runs(tenant_id="alpha")
    beta_runs = runtime.store.list_runs(tenant_id="beta")
    assert all(r["tenant_id"] == "alpha" for r in alpha_runs)
    assert all(r["tenant_id"] == "beta" for r in beta_runs)
    assert a in {r["thread_id"] for r in alpha_runs}
    assert b not in {r["thread_id"] for r in alpha_runs}


def test_auth_required():
    reg = AuthRegistry(mapping={"acme": "secret-key"}, required=True)
    with pytest.raises(Exception):
        reg.resolve(api_key=None, tenant_header=None)
    ctx = reg.resolve(api_key="secret-key", tenant_header=None)
    assert ctx.tenant_id == "acme"


def test_api_fleet_metrics_fanout(runtime):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dizzygraph.control.api import create_app

    app = create_app(runtime)
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"] is True

    spawned = client.post("/api/demo/spawn", json={"n": 3})
    assert spawned.status_code == 200
    time.sleep(1.2)
    fleet = client.get("/api/fleet").json()
    assert fleet["summary"]["total"] >= 3
    assert "metrics" in fleet
    assert "fail_rate" in fleet["metrics"]

    metrics = client.get("/api/metrics").json()
    assert "rollup" in metrics
    assert "series" in metrics

    fan = client.post("/api/supervisor/fanout", json={"n": 3, "wait": True, "timeout_s": 20})
    assert fan.status_code == 200
    assert fan.json()["parent_thread_id"]
    detail = client.get(f"/api/runs/{fan.json()['parent_thread_id']}").json()
    assert "path" in detail
    assert detail["run"]["status"] in {"succeeded", "interrupted", "failed"}