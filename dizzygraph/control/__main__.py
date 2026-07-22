"""DizzyGraph Control Plane — ``python -m dizzygraph.control``."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .api import create_app
from .auth import AuthRegistry
from .demo_graph import ensure_demo_graph
from .postgres_store import open_store
from .runtime import FleetRuntime


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="DizzyGraph multi-agent control plane")
    p.add_argument("--db", default="dizzygraph_out/control.db", help="SQLite path (if no DSN)")
    p.add_argument("--database-url", default=os.environ.get("DIZZY_DATABASE_URL"), help="postgres://…")
    p.add_argument("--redis-url", default=os.environ.get("DIZZY_REDIS_URL"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--demo", type=int, default=0, help="Spawn N offline fleet-demo agents on boot")
    p.add_argument("--fanout", type=int, default=0, help="Supervisor fan-out N fleet-demo children on boot")
    p.add_argument("--stuck-after", type=float, default=30.0)
    p.add_argument("--auth-required", action="store_true")
    p.add_argument("--tenant", default="default")
    p.add_argument(
        "--trinity",
        type=int,
        default=0,
        help="Spawn N **live** Trinity agents on boot (requires OPENAI_API_KEY)",
    )
    args = p.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = open_store(args.database_url, sqlite_path=args.db)
    runtime = FleetRuntime(
        store,
        stuck_after_s=args.stuck_after,
        redis_url=args.redis_url,
        default_tenant=args.tenant,
    )
    ensure_demo_graph(runtime, tenant_id=args.tenant)

    # Live Trinity only — fail loudly if --trinity requested without keys
    if args.trinity > 0:
        from .trinity_fleet import DEFAULT_TRINITY_QUERIES, TrinityKeysError, spawn_trinity_fleet

        try:
            t = spawn_trinity_fleet(
                runtime,
                queries=DEFAULT_TRINITY_QUERIES[: args.trinity],
                tenant_id=args.tenant,
            )
            logging.info(
                "Trinity LIVE fleet parent=%s children=%s",
                t.get("parent_thread_id"),
                len(t.get("children") or []),
            )
        except TrinityKeysError as exc:
            raise SystemExit(f"ERROR: {exc}\nMissing: {exc.missing}") from exc
    else:
        try:
            from .trinity_fleet import TrinityKeysError, ensure_trinity_graphs

            reg = ensure_trinity_graphs(runtime, tenant_id=args.tenant)
            logging.info("Trinity live graph ready: %s", reg)
        except TrinityKeysError as exc:
            logging.warning("Trinity live not registered yet (keys missing): %s", exc.missing)
        except Exception as exc:
            logging.warning("Trinity register deferred: %s", exc)

    auth = AuthRegistry(required=args.auth_required or None)
    if args.auth_required and not auth._key_to_tenant:
        key = auth.issue_demo_key(args.tenant)
        logging.info("Issued demo API key for tenant=%s: %s", args.tenant, key)

    if args.demo > 0:
        for i in range(args.demo):
            runtime.start_run(
                graph_id="fleet-demo",
                agent_name=f"agent-{i+1:02d}",
                initial={"query": f"boot-task-{i+1}", "agent_ix": i},
                tenant_id=args.tenant,
            )
        logging.info("spawned %s demo agents", args.demo)

    if args.fanout > 0:
        result = runtime.fan_out(
            graph_id="fleet-demo",
            items=[{"query": f"fan-{i+1}", "agent_ix": i} for i in range(args.fanout)],
            tenant_id=args.tenant,
            wait=False,
        )
        logging.info(
            "supervisor fan-out parent=%s children=%s",
            result["parent_thread_id"],
            len(result["children"]),
        )

    app = create_app(runtime, auth=auth)

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Install control deps: pip install -e '.[control]'\n"
            f"Missing: {exc}"
        ) from exc

    logging.info(
        "Control plane → http://%s:%s  backend=%s redis=%s",
        args.host,
        args.port,
        getattr(store, "backend", "sqlite"),
        bool(args.redis_url),
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
