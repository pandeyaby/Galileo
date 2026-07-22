"""FastAPI control plane — fleet REST + SSE + metrics + supervisor + auth."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import AuthRegistry, bind_auth, tenant_from_request
from .runtime import FleetRuntime

STATIC_DIR = Path(__file__).parent / "static"


class StartRunRequest(BaseModel):
    graph_id: str
    thread_id: str | None = None
    agent_name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    update: dict[str, Any] = Field(default_factory=dict)


class SpawnDemoRequest(BaseModel):
    n: int = 8
    graph_id: str = "fleet-demo"


class FanOutRequest(BaseModel):
    graph_id: str = "fleet-demo"
    items: list[dict[str, Any]] = Field(default_factory=list)
    n: int | None = None
    agent_prefix: str = "worker"
    wait: bool = True
    timeout_s: float = 120.0


class SpawnTrinityRequest(BaseModel):
    queries: list[str] = Field(default_factory=list)
    n: int | None = 4
    # live is always true for Trinity fleet — kept for API clarity / forward compat
    live: bool = True


class XlFanOutRequest(BaseModel):
    drills: list[str] = Field(default_factory=list)  # empty → all XL-1..XL-6
    wait: bool = False
    timeout_s: float = 300.0


class MetaRegressionRequest(BaseModel):
    query: str = "How do I choose between FSDP and DeepSpeed ZeRO?"
    meta_iters: int = 3


def create_app(runtime: FleetRuntime, *, auth: AuthRegistry | None = None) -> FastAPI:
    app = FastAPI(title="DizzyGraph Control Plane", version="0.4.0")
    registry = auth or AuthRegistry()
    bind_auth(app, registry)
    store = runtime.store

    def tid(request: Request) -> str:
        return tenant_from_request(request).tenant_id

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "backend": getattr(store, "backend", "sqlite"),
            "auth_required": registry.required,
            "fleet": store.fleet_summary(tenant_id="default"),
        }

    @app.get("/api/fleet")
    def fleet(request: Request) -> dict[str, Any]:
        tenant = tid(request)
        return {
            "tenant_id": tenant,
            "summary": store.fleet_summary(tenant_id=tenant),
            "metrics": store.metrics_rollup(tenant_id=tenant),
            "runs": store.list_runs(tenant_id=tenant, limit=300),
            "alerts": store.list_alerts(tenant_id=tenant, open_only=True, limit=50),
        }

    @app.get("/api/metrics")
    def metrics(request: Request, since_s: float = 3600) -> dict[str, Any]:
        tenant = tid(request)
        return {
            "tenant_id": tenant,
            "rollup": store.metrics_rollup(tenant_id=tenant),
            "series": {
                "run_lag_s": store.query_metrics(
                    tenant_id=tenant, name="run_lag_s", since_s=since_s
                ),
                "loop_iterations": store.query_metrics(
                    tenant_id=tenant, name="loop_iterations", since_s=since_s
                ),
                "fail_rate": store.query_metrics(
                    tenant_id=tenant, name="fail_rate", since_s=since_s
                ),
                "stuck_nodes": store.query_metrics(
                    tenant_id=tenant, name="stuck_nodes", since_s=since_s
                ),
            },
        }

    @app.get("/api/graphs")
    def graphs(request: Request) -> list[dict[str, Any]]:
        return store.list_graphs(tenant_id=tid(request))

    @app.get("/api/graphs/{graph_id}")
    def graph_detail(graph_id: str, request: Request) -> dict[str, Any]:
        g = store.get_graph(graph_id, tenant_id=tid(request))
        if not g:
            raise HTTPException(404, "graph not found")
        g = dict(g)
        g["skeleton"] = json.loads(g.pop("skeleton_json"))
        return g

    @app.get("/api/runs")
    def runs(request: Request, status: str | None = None) -> list[dict[str, Any]]:
        return store.list_runs(tenant_id=tid(request), status=status)

    @app.get("/api/runs/{thread_id}")
    def run_detail(thread_id: str, request: Request) -> dict[str, Any]:
        tenant = tid(request)
        run = store.get_run(thread_id, tenant_id=tenant)
        if not run:
            raise HTTPException(404, "run not found")
        cp = store.latest_checkpoint(thread_id)
        events = store.events_for_thread(thread_id, limit=100)
        graph = store.get_graph(run["graph_id"], tenant_id=tenant)
        children = store.list_children(thread_id, tenant_id=tenant)
        path = store.get_path(thread_id)
        return {
            "run": run,
            "checkpoint": cp,
            "events": events,
            "path": path,
            "children": children,
            "mermaid": (graph or {}).get("mermaid", ""),
            "graph_name": (graph or {}).get("name", ""),
        }

    @app.post("/api/runs")
    def start_run(body: StartRunRequest, request: Request) -> dict[str, str]:
        try:
            thread = runtime.start_run(
                graph_id=body.graph_id,
                initial=body.input or None,
                thread_id=body.thread_id,
                agent_name=body.agent_name,
                tenant_id=tid(request),
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"thread_id": thread}

    @app.post("/api/runs/{thread_id}/resume")
    def resume_run(thread_id: str, body: ResumeRequest, request: Request) -> dict[str, str]:
        try:
            runtime.resume_run(thread_id, update=body.update, tenant_id=tid(request))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"thread_id": thread_id}

    @app.post("/api/supervisor/fanout")
    def fanout(body: FanOutRequest, request: Request) -> dict[str, Any]:
        items = body.items
        if body.n and not items:
            items = [{"query": f"task-{i+1}", "agent_ix": i} for i in range(body.n)]
        if not items:
            raise HTTPException(400, "items or n required")
        return runtime.fan_out(
            graph_id=body.graph_id,
            items=items,
            tenant_id=tid(request),
            agent_prefix=body.agent_prefix,
            wait=body.wait,
            timeout_s=body.timeout_s,
        )

    @app.get("/api/alerts")
    def alerts(request: Request, open_only: bool = True) -> list[dict[str, Any]]:
        return store.list_alerts(tenant_id=tid(request), open_only=open_only)

    @app.post("/api/alerts/{alert_id}/ack")
    def ack_alert(alert_id: int, request: Request) -> dict[str, str]:
        store.ack_alert(alert_id, tenant_id=tid(request))
        return {"status": "acked"}

    @app.get("/api/events")
    def events(request: Request, since: int = Query(0), limit: int = 200) -> list[dict[str, Any]]:
        return store.events_since(since, tenant_id=tid(request), limit=limit)

    @app.get("/api/events/stream")
    async def event_stream(request: Request, since: int = Query(0)):
        tenant = tid(request)

        async def gen():
            last = since
            while True:
                if await request.is_disconnected():
                    break
                batch = await asyncio.to_thread(
                    runtime.bus.poll, last, tenant_id=tenant, limit=100
                )
                if batch:
                    for ev in batch:
                        last = ev["id"]
                        yield f"data: {json.dumps(ev, default=str)}\n\n"
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.4)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/demo/spawn")
    def spawn_demo(body: SpawnDemoRequest, request: Request) -> dict[str, Any]:
        from .demo_graph import ensure_demo_graph

        ensure_demo_graph(runtime, tenant_id=tid(request))
        ids = []
        for i in range(body.n):
            thread = runtime.start_run(
                graph_id=body.graph_id,
                agent_name=f"agent-{i+1:02d}",
                initial={"query": f"task-{i+1}", "agent_ix": i},
                tenant_id=tid(request),
            )
            ids.append(thread)
        return {"thread_ids": ids, "n": len(ids), "tenant_id": tid(request)}

    @app.post("/api/trinity/register")
    def trinity_register(request: Request) -> dict[str, Any]:
        from .trinity_fleet import TrinityKeysError, ensure_trinity_graphs

        try:
            return ensure_trinity_graphs(runtime, tenant_id=tid(request))
        except TrinityKeysError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": str(exc), "missing": exc.missing, "live_required": True},
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Trinity register failed: {exc}") from exc

    @app.post("/api/trinity/spawn")
    def trinity_spawn(body: SpawnTrinityRequest, request: Request) -> dict[str, Any]:
        from .trinity_fleet import (
            DEFAULT_TRINITY_QUERIES,
            TrinityKeysError,
            spawn_trinity_fleet,
        )

        queries = list(body.queries) if body.queries else list(DEFAULT_TRINITY_QUERIES)
        n = body.n if body.n is not None else 4
        if n < 1:
            raise HTTPException(status_code=400, detail="n must be >= 1")
        queries = queries[:n]
        try:
            return spawn_trinity_fleet(
                runtime,
                queries=queries,
                tenant_id=tid(request),
            )
        except TrinityKeysError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": str(exc), "missing": exc.missing, "live_required": True},
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Trinity spawn failed: {exc}") from exc

    @app.get("/api/trinity/use-cases")
    def trinity_use_cases() -> dict[str, Any]:
        from .trinity_fleet import GALILEO_USE_CASES

        return {"use_cases": GALILEO_USE_CASES}

    @app.post("/api/trinity/xl-fanout")
    def trinity_xl_fanout(body: XlFanOutRequest, request: Request) -> dict[str, Any]:
        from .trinity_fleet import TrinityKeysError
        from .xl_fanout import spawn_xl_fanout

        try:
            return spawn_xl_fanout(
                runtime,
                tenant_id=tid(request),
                drills=body.drills or None,
                wait=body.wait,
                timeout_s=body.timeout_s,
            )
        except TrinityKeysError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": str(exc), "missing": exc.missing, "live_required": True},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"XL fan-out failed: {exc}") from exc

    @app.post("/api/trinity/meta-regression")
    def trinity_meta_regression(body: MetaRegressionRequest, request: Request) -> dict[str, Any]:
        from .trinity_fleet import TrinityKeysError, run_silent_regression_meta

        if body.meta_iters < 1 or body.meta_iters > 8:
            raise HTTPException(400, "meta_iters must be 1..8")
        try:
            return run_silent_regression_meta(
                runtime,
                query=body.query,
                tenant_id=tid(request),
                meta_iters=body.meta_iters,
            )
        except TrinityKeysError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": str(exc), "missing": exc.missing, "live_required": True},
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Meta regression failed: {exc}") from exc

    @app.get("/api/tenants/galileo")
    def tenant_galileo_map(request: Request) -> dict[str, Any]:
        from .tenant_projects import list_tenant_mappings, resolve_galileo_target

        tenant = tid(request)
        return {
            "tenant_id": tenant,
            "resolved": resolve_galileo_target(tenant),
            "table": list_tenant_mappings(),
        }

    if STATIC_DIR.is_dir():

        @app.get("/", response_class=HTMLResponse)
        def index() -> HTMLResponse:
            return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
