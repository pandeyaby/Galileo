"""Postgres durable store — drop-in for multi-worker deployments.

Requires: ``pip install 'dizzygraph[control-pg]'`` and ``DIZZY_DATABASE_URL``.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.parse import urlparse


PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS graphs (
  tenant_id TEXT NOT NULL DEFAULT 'default',
  graph_id TEXT NOT NULL,
  name TEXT NOT NULL,
  mermaid TEXT NOT NULL DEFAULT '',
  skeleton_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (tenant_id, graph_id)
);
CREATE TABLE IF NOT EXISTS runs (
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  agent_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  current_node TEXT,
  parent_thread_id TEXT,
  started_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  ended_at DOUBLE PRECISION,
  error TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_pg_runs_status ON runs(tenant_id, status);
CREATE TABLE IF NOT EXISTS checkpoints (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  checkpoint_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  state_json TEXT NOT NULL,
  next_nodes_json TEXT NOT NULL,
  visits_json TEXT NOT NULL,
  pending_interrupt_json TEXT,
  interrupt_node TEXT,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pg_cp_thread ON checkpoints(thread_id, id DESC);
CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  type TEXT NOT NULL,
  node_id TEXT,
  payload_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pg_ev_id ON events(id);
CREATE TABLE IF NOT EXISTS alerts (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  rule TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  acked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS path_steps (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  name TEXT NOT NULL,
  value DOUBLE PRECISION NOT NULL,
  labels_json TEXT NOT NULL DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL
);
"""


class PostgresStore:
    """Postgres implementation mirroring ControlStore API."""

    backend = "postgres"

    def __init__(self, dsn: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ImportError(
                "Postgres backend requires psycopg: pip install 'dizzygraph[control-pg]'"
            ) from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn
        self._lock = threading.RLock()
        self._conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(PG_SCHEMA)
            self._conn.commit()

    def ping(self) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_graph(self, graph_id, name, mermaid, skeleton, *, tenant_id="default"):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO graphs(tenant_id, graph_id, name, mermaid, skeleton_json, created_at)
                    VALUES(%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id, graph_id) DO UPDATE SET
                      name=EXCLUDED.name, mermaid=EXCLUDED.mermaid,
                      skeleton_json=EXCLUDED.skeleton_json
                    """,
                    (tenant_id, graph_id, name, mermaid, json.dumps(skeleton), time.time()),
                )
            self._conn.commit()

    def get_graph(self, graph_id, *, tenant_id="default"):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM graphs WHERE tenant_id=%s AND graph_id=%s",
                    (tenant_id, graph_id),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def list_graphs(self, *, tenant_id="default"):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT graph_id, name, created_at, tenant_id FROM graphs WHERE tenant_id=%s ORDER BY created_at DESC",
                    (tenant_id,),
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def upsert_run(self, **fields):
        thread_id = fields["thread_id"]
        tenant_id = fields.get("tenant_id", "default")
        now = time.time()
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_id FROM runs WHERE tenant_id=%s AND thread_id=%s",
                    (tenant_id, thread_id),
                )
                existing = cur.fetchone()
                if not existing:
                    cur.execute(
                        """
                        INSERT INTO runs(
                          tenant_id, thread_id, graph_id, agent_name, status, current_node,
                          parent_thread_id, started_at, updated_at, ended_at, error, meta_json
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            tenant_id,
                            thread_id,
                            fields.get("graph_id", ""),
                            fields.get("agent_name", ""),
                            fields.get("status", "pending"),
                            fields.get("current_node"),
                            fields.get("parent_thread_id"),
                            fields.get("started_at", now),
                            fields.get("updated_at", now),
                            fields.get("ended_at"),
                            fields.get("error"),
                            json.dumps(fields.get("meta") or {}),
                        ),
                    )
                else:
                    sets, vals = [], []
                    for key in (
                        "graph_id",
                        "agent_name",
                        "status",
                        "current_node",
                        "parent_thread_id",
                        "ended_at",
                        "error",
                    ):
                        if key in fields:
                            sets.append(f"{key}=%s")
                            vals.append(fields[key])
                    if "meta" in fields:
                        sets.append("meta_json=%s")
                        vals.append(json.dumps(fields["meta"]))
                    sets.append("updated_at=%s")
                    vals.append(fields.get("updated_at", now))
                    vals.extend([tenant_id, thread_id])
                    cur.execute(
                        f"UPDATE runs SET {', '.join(sets)} WHERE tenant_id=%s AND thread_id=%s",
                        vals,
                    )
            self._conn.commit()

    def get_run(self, thread_id, *, tenant_id=None):
        with self._lock:
            with self._conn.cursor() as cur:
                if tenant_id:
                    cur.execute(
                        "SELECT * FROM runs WHERE tenant_id=%s AND thread_id=%s",
                        (tenant_id, thread_id),
                    )
                else:
                    cur.execute("SELECT * FROM runs WHERE thread_id=%s LIMIT 1", (thread_id,))
                row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        return d

    def list_runs(self, *, tenant_id="default", status=None, parent_thread_id=None, limit=200):
        q = "SELECT * FROM runs WHERE tenant_id=%s"
        args: list[Any] = [tenant_id]
        if status:
            q += " AND status=%s"
            args.append(status)
        if parent_thread_id is not None:
            q += " AND parent_thread_id=%s"
            args.append(parent_thread_id)
        q += " ORDER BY updated_at DESC LIMIT %s"
        args.append(limit)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(q, args)
                rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
            out.append(d)
        return out

    def list_runs_all(self, *, limit=1000):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("SELECT * FROM runs ORDER BY updated_at DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
            out.append(d)
        return out

    def list_children(self, parent_thread_id, *, tenant_id="default"):
        return self.list_runs(tenant_id=tenant_id, parent_thread_id=parent_thread_id, limit=500)

    def fleet_summary(self, *, tenant_id="default"):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT status, COUNT(*) AS n FROM runs WHERE tenant_id=%s GROUP BY status",
                    (tenant_id,),
                )
                rows = cur.fetchall()
                cur.execute(
                    "SELECT COUNT(*) AS n FROM alerts WHERE tenant_id=%s AND acked=0",
                    (tenant_id,),
                )
                open_alerts = cur.fetchone()["n"]
        summary = {r["status"]: r["n"] for r in rows}
        summary["open_alerts"] = open_alerts
        summary["total"] = sum(v for k, v in summary.items() if k != "open_alerts")
        return summary

    def put_checkpoint(self, cp):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO checkpoints(
                      tenant_id, thread_id, checkpoint_id, graph_id, state_json, next_nodes_json,
                      visits_json, pending_interrupt_json, interrupt_node, created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        cp.get("tenant_id", "default"),
                        cp["thread_id"],
                        cp["checkpoint_id"],
                        cp["graph_id"],
                        json.dumps(cp["state"]),
                        json.dumps(cp["next_nodes"]),
                        json.dumps(cp.get("visits") or {}),
                        json.dumps(cp.get("pending_interrupt"), default=str)
                        if cp.get("pending_interrupt") is not None
                        else None,
                        cp.get("interrupt_node"),
                        cp.get("created_at", time.time()),
                    ),
                )
            self._conn.commit()

    def latest_checkpoint(self, thread_id):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=%s ORDER BY id DESC LIMIT 1",
                    (thread_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        return {
            "tenant_id": d.get("tenant_id", "default"),
            "thread_id": d["thread_id"],
            "checkpoint_id": d["checkpoint_id"],
            "graph_id": d["graph_id"],
            "state": json.loads(d["state_json"]),
            "next_nodes": json.loads(d["next_nodes_json"]),
            "visits": json.loads(d["visits_json"]),
            "pending_interrupt": json.loads(d["pending_interrupt_json"])
            if d["pending_interrupt_json"]
            else None,
            "interrupt_node": d["interrupt_node"],
            "created_at": d["created_at"],
        }


    def list_checkpoints(self, thread_id, *, limit: int = 50):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=%s ORDER BY id DESC LIMIT %s",
                    (thread_id, limit),
                )
                rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(row)
            out.append(
                {
                    "tenant_id": d.get("tenant_id", "default"),
                    "thread_id": d["thread_id"],
                    "checkpoint_id": d["checkpoint_id"],
                    "graph_id": d["graph_id"],
                    "state": json.loads(d["state_json"]),
                    "next_nodes": json.loads(d["next_nodes_json"]),
                    "visits": json.loads(d["visits_json"]),
                    "pending_interrupt": json.loads(d["pending_interrupt_json"])
                    if d["pending_interrupt_json"]
                    else None,
                    "interrupt_node": d["interrupt_node"],
                    "created_at": d["created_at"],
                }
            )
        return out

    def clear_checkpoints(self, thread_id) -> int:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM checkpoints WHERE thread_id=%s", (thread_id,))
                n = cur.rowcount
            self._conn.commit()
        return int(n or 0)


    def append_event(
        self,
        *,
        thread_id,
        graph_id,
        type,
        node_id=None,
        payload=None,
        created_at=None,
        tenant_id="default",
    ):
        ts = created_at or time.time()
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events(tenant_id, thread_id, graph_id, type, node_id, payload_json, created_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id
                    """,
                    (tenant_id, thread_id, graph_id, type, node_id, json.dumps(payload or {}), ts),
                )
                eid = cur.fetchone()["id"]
            self._conn.commit()
        return {
            "id": eid,
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "graph_id": graph_id,
            "type": type,
            "node_id": node_id,
            "payload": payload or {},
            "created_at": ts,
        }

    def events_since(self, last_id=0, *, tenant_id=None, limit=500):
        with self._lock:
            with self._conn.cursor() as cur:
                if tenant_id:
                    cur.execute(
                        "SELECT * FROM events WHERE id > %s AND tenant_id=%s ORDER BY id ASC LIMIT %s",
                        (last_id, tenant_id, limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM events WHERE id > %s ORDER BY id ASC LIMIT %s",
                        (last_id, limit),
                    )
                rows = cur.fetchall()
        return [self._event_row(r) for r in rows]

    def events_for_thread(self, thread_id, *, limit=200):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM events WHERE thread_id=%s ORDER BY id DESC LIMIT %s",
                    (thread_id, limit),
                )
                rows = list(reversed(cur.fetchall()))
        return [self._event_row(r) for r in rows]

    @staticmethod
    def _event_row(d):
        return {
            "id": d["id"],
            "tenant_id": d.get("tenant_id", "default"),
            "thread_id": d["thread_id"],
            "graph_id": d["graph_id"],
            "type": d["type"],
            "node_id": d["node_id"],
            "payload": json.loads(d["payload_json"]),
            "created_at": d["created_at"],
        }

    def add_alert(
        self,
        *,
        thread_id,
        graph_id,
        rule,
        severity,
        message,
        payload=None,
        tenant_id="default",
    ):
        ts = time.time()
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM alerts
                    WHERE tenant_id=%s AND thread_id=%s AND rule=%s AND acked=0
                    ORDER BY id DESC LIMIT 1
                    """,
                    (tenant_id, thread_id, rule),
                )
                existing = cur.fetchone()
                if existing:
                    return {"id": existing["id"], "deduped": True}
                cur.execute(
                    """
                    INSERT INTO alerts(
                      tenant_id, thread_id, graph_id, rule, severity, message, payload_json, created_at, acked
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,0) RETURNING id
                    """,
                    (
                        tenant_id,
                        thread_id,
                        graph_id,
                        rule,
                        severity,
                        message,
                        json.dumps(payload or {}),
                        ts,
                    ),
                )
                eid = cur.fetchone()["id"]
            self._conn.commit()
        return {
            "id": eid,
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "graph_id": graph_id,
            "rule": rule,
            "severity": severity,
            "message": message,
            "payload": payload or {},
            "created_at": ts,
            "acked": 0,
        }

    def list_alerts(self, *, tenant_id="default", open_only=True, limit=100):
        q = "SELECT * FROM alerts WHERE tenant_id=%s"
        args: list[Any] = [tenant_id]
        if open_only:
            q += " AND acked=0"
        q += " ORDER BY id DESC LIMIT %s"
        args.append(limit)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(q, args)
                rows = cur.fetchall()
        out = []
        for d in rows:
            out.append(
                {
                    "id": d["id"],
                    "tenant_id": d.get("tenant_id", "default"),
                    "thread_id": d["thread_id"],
                    "graph_id": d["graph_id"],
                    "rule": d["rule"],
                    "severity": d["severity"],
                    "message": d["message"],
                    "payload": json.loads(d["payload_json"]),
                    "created_at": d["created_at"],
                    "acked": d["acked"],
                }
            )
        return out

    def ack_alert(self, alert_id, *, tenant_id="default"):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE alerts SET acked=1 WHERE id=%s AND tenant_id=%s",
                    (alert_id, tenant_id),
                )
            self._conn.commit()

    def append_path_step(self, thread_id, node_id, *, tenant_id="default", ts=None):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO path_steps(tenant_id, thread_id, node_id, created_at) VALUES(%s,%s,%s,%s)",
                    (tenant_id, thread_id, node_id, ts or time.time()),
                )
            self._conn.commit()

    def get_path(self, thread_id):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT node_id FROM path_steps WHERE thread_id=%s ORDER BY id ASC",
                    (thread_id,),
                )
                rows = cur.fetchall()
        return [r["node_id"] for r in rows]

    def record_metric(self, *, tenant_id, name, value, labels=None, ts=None):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO metrics(tenant_id, name, value, labels_json, created_at) VALUES(%s,%s,%s,%s,%s)",
                    (tenant_id, name, float(value), json.dumps(labels or {}), ts or time.time()),
                )
            self._conn.commit()

    def query_metrics(self, *, tenant_id, name=None, since_s=3600):
        since = time.time() - since_s
        with self._lock:
            with self._conn.cursor() as cur:
                if name:
                    cur.execute(
                        """
                        SELECT * FROM metrics WHERE tenant_id=%s AND name=%s AND created_at>=%s
                        ORDER BY created_at ASC
                        """,
                        (tenant_id, name, since),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM metrics WHERE tenant_id=%s AND created_at>=%s
                        ORDER BY created_at ASC
                        """,
                        (tenant_id, since),
                    )
                rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "tenant_id": r["tenant_id"],
                "name": r["name"],
                "value": r["value"],
                "labels": json.loads(r["labels_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def metrics_rollup(self, *, tenant_id="default"):
        # Reuse same logic as sqlite via duck-typing list methods
        from .store import ControlStore

        # Inline copy of rollup math
        runs = self.list_runs(tenant_id=tenant_id, limit=1000)
        total = len(runs) or 1
        failed = sum(1 for r in runs if r["status"] == "failed")
        succeeded = sum(1 for r in runs if r["status"] == "succeeded")
        interrupted = sum(1 for r in runs if r["status"] == "interrupted")
        running = sum(1 for r in runs if r["status"] == "running")
        durations = [
            float(r["ended_at"]) - float(r["started_at"])
            for r in runs
            if r.get("ended_at") and r.get("started_at")
        ]
        lag_p50 = sorted(durations)[len(durations) // 2] if durations else 0.0
        lag_p95 = sorted(durations)[int(len(durations) * 0.95)] if durations else 0.0
        loop_samples = [
            m["value"]
            for m in self.query_metrics(tenant_id=tenant_id, name="loop_iterations", since_s=86400)
        ]
        stuck = len(
            [
                a
                for a in self.list_alerts(tenant_id=tenant_id, open_only=True, limit=500)
                if a["rule"] == "stuck_run"
            ]
        )
        return {
            "total_runs": len(runs),
            "running": running,
            "succeeded": succeeded,
            "failed": failed,
            "interrupted": interrupted,
            "fail_rate": round(failed / total, 4),
            "lag_p50_s": round(lag_p50, 4),
            "lag_p95_s": round(lag_p95, 4),
            "loop_iterations_avg": round(sum(loop_samples) / len(loop_samples), 3)
            if loop_samples
            else 0.0,
            "loop_iterations_max": max(loop_samples) if loop_samples else 0.0,
            "stuck_nodes": stuck,
            "open_alerts": self.fleet_summary(tenant_id=tenant_id).get("open_alerts", 0),
        }


def open_store(db_url: str | None = None, sqlite_path: str = "dizzygraph_out/control.db"):
    """Factory: postgres://… → PostgresStore, else SQLite ControlStore."""
    import os

    url = db_url or os.environ.get("DIZZY_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url and urlparse(url).scheme in {"postgres", "postgresql"}:
        return PostgresStore(url)
    from .store import ControlStore

    return ControlStore(sqlite_path)
