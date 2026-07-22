"""SQLite-backed durable store — multi-tenant, path history, metrics."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS graphs (
  tenant_id TEXT NOT NULL DEFAULT 'default',
  graph_id TEXT NOT NULL,
  name TEXT NOT NULL,
  mermaid TEXT NOT NULL DEFAULT '',
  skeleton_json TEXT NOT NULL,
  created_at REAL NOT NULL,
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
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  ended_at REAL,
  error TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_tenant_status ON runs(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_thread_id);

CREATE TABLE IF NOT EXISTS checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  checkpoint_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  state_json TEXT NOT NULL,
  next_nodes_json TEXT NOT NULL,
  visits_json TEXT NOT NULL,
  pending_interrupt_json TEXT,
  interrupt_node TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cp_thread ON checkpoints(thread_id, id DESC);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  type TEXT NOT NULL,
  node_id TEXT,
  payload_json TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_ev_tenant ON events(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_ev_thread ON events(thread_id, id);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  graph_id TEXT NOT NULL,
  rule TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  acked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(tenant_id, acked, id DESC);

CREATE TABLE IF NOT EXISTS path_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  thread_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_path_thread ON path_steps(thread_id, id);

CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  name TEXT NOT NULL,
  value REAL NOT NULL,
  labels_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(tenant_id, name, created_at);
"""


class ControlStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = "sqlite"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for older DBs created before tenants/path/metrics."""
        cols = {
            "runs": {r[1] for r in self._conn.execute("PRAGMA table_info(runs)").fetchall()},
            "events": {r[1] for r in self._conn.execute("PRAGMA table_info(events)").fetchall()},
            "alerts": {r[1] for r in self._conn.execute("PRAGMA table_info(alerts)").fetchall()},
            "checkpoints": {
                r[1] for r in self._conn.execute("PRAGMA table_info(checkpoints)").fetchall()
            },
            "graphs": {r[1] for r in self._conn.execute("PRAGMA table_info(graphs)").fetchall()},
        }
        # Old single-PK graphs/runs — leave as-is if already new schema; skip destructive migrate
        if "runs" in cols and "parent_thread_id" not in cols["runs"]:
            try:
                self._conn.execute("ALTER TABLE runs ADD COLUMN parent_thread_id TEXT")
            except sqlite3.OperationalError:
                pass
        if "runs" in cols and "tenant_id" not in cols["runs"]:
            for table in ("runs", "events", "alerts", "checkpoints", "graphs"):
                if table in cols and "tenant_id" not in cols[table]:
                    try:
                        self._conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
                        )
                    except sqlite3.OperationalError:
                        pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── graphs ────────────────────────────────────────────────────────────

    def upsert_graph(
        self,
        graph_id: str,
        name: str,
        mermaid: str,
        skeleton: dict,
        *,
        tenant_id: str = "default",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO graphs(tenant_id, graph_id, name, mermaid, skeleton_json, created_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(tenant_id, graph_id) DO UPDATE SET
                  name=excluded.name,
                  mermaid=excluded.mermaid,
                  skeleton_json=excluded.skeleton_json
                """,
                (tenant_id, graph_id, name, mermaid, json.dumps(skeleton), time.time()),
            )
            self._conn.commit()

    def get_graph(self, graph_id: str, *, tenant_id: str = "default") -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM graphs WHERE tenant_id=? AND graph_id=?",
                (tenant_id, graph_id),
            ).fetchone()
            if not row:
                row = self._conn.execute(
                    "SELECT * FROM graphs WHERE graph_id=? LIMIT 1", (graph_id,)
                ).fetchone()
        return dict(row) if row else None

    def list_graphs(self, *, tenant_id: str = "default") -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT graph_id, name, created_at, tenant_id FROM graphs
                WHERE tenant_id=? ORDER BY created_at DESC
                """,
                (tenant_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── runs ──────────────────────────────────────────────────────────────

    def upsert_run(self, **fields: Any) -> None:
        thread_id = fields["thread_id"]
        tenant_id = fields.get("tenant_id", "default")
        now = time.time()
        with self._lock:
            existing = self._conn.execute(
                "SELECT thread_id FROM runs WHERE tenant_id=? AND thread_id=?",
                (tenant_id, thread_id),
            ).fetchone()
            if not existing:
                self._conn.execute(
                    """
                    INSERT INTO runs(
                      tenant_id, thread_id, graph_id, agent_name, status, current_node,
                      parent_thread_id, started_at, updated_at, ended_at, error, meta_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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
                sets = []
                vals: list[Any] = []
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
                        sets.append(f"{key}=?")
                        vals.append(fields[key])
                if "meta" in fields:
                    sets.append("meta_json=?")
                    vals.append(json.dumps(fields["meta"]))
                sets.append("updated_at=?")
                vals.append(fields.get("updated_at", now))
                vals.extend([tenant_id, thread_id])
                self._conn.execute(
                    f"UPDATE runs SET {', '.join(sets)} WHERE tenant_id=? AND thread_id=?",
                    vals,
                )
            self._conn.commit()

    def get_run(self, thread_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if tenant_id:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE tenant_id=? AND thread_id=?",
                    (tenant_id, thread_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE thread_id=? LIMIT 1", (thread_id,)
                ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        return d

    def list_runs_all(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
            out.append(d)
        return out

    def list_runs(
        self,
        *,
        tenant_id: str = "default",
        status: str | None = None,
        parent_thread_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM runs WHERE tenant_id=?"
        args: list[Any] = [tenant_id]
        if status:
            q += " AND status=?"
            args.append(status)
        if parent_thread_id is not None:
            q += " AND parent_thread_id=?"
            args.append(parent_thread_id)
        q += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
            out.append(d)
        return out

    def list_children(self, parent_thread_id: str, *, tenant_id: str = "default") -> list[dict[str, Any]]:
        return self.list_runs(tenant_id=tenant_id, parent_thread_id=parent_thread_id, limit=500)

    def fleet_summary(self, *, tenant_id: str = "default") -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM runs WHERE tenant_id=? GROUP BY status",
                (tenant_id,),
            ).fetchall()
            open_alerts = self._conn.execute(
                "SELECT COUNT(*) AS n FROM alerts WHERE tenant_id=? AND acked=0",
                (tenant_id,),
            ).fetchone()["n"]
        summary = {r["status"]: r["n"] for r in rows}
        summary["open_alerts"] = open_alerts
        summary["total"] = sum(v for k, v in summary.items() if k != "open_alerts")
        return summary

    # ── checkpoints ───────────────────────────────────────────────────────

    def put_checkpoint(self, cp: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO checkpoints(
                  tenant_id, thread_id, checkpoint_id, graph_id, state_json, next_nodes_json,
                  visits_json, pending_interrupt_json, interrupt_node, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
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

    def latest_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM checkpoints WHERE thread_id=?
                ORDER BY id DESC LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
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

    # ── events ────────────────────────────────────────────────────────────

    def append_event(
        self,
        *,
        thread_id: str,
        graph_id: str,
        type: str,
        node_id: str | None = None,
        payload: dict | None = None,
        created_at: float | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        ts = created_at or time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO events(tenant_id, thread_id, graph_id, type, node_id, payload_json, created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (tenant_id, thread_id, graph_id, type, node_id, json.dumps(payload or {}), ts),
            )
            self._conn.commit()
            eid = cur.lastrowid
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

    def events_since(
        self,
        last_id: int = 0,
        *,
        tenant_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if tenant_id:
                rows = self._conn.execute(
                    """
                    SELECT * FROM events WHERE id > ? AND tenant_id=?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (last_id, tenant_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (last_id, limit),
                ).fetchall()
        return [self._event_row(r) for r in rows]

    def events_for_thread(self, thread_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM events WHERE thread_id=? ORDER BY id DESC LIMIT ?
                """,
                (thread_id, limit),
            ).fetchall()
        return [self._event_row(r) for r in reversed(list(rows))]

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
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

    # ── alerts ────────────────────────────────────────────────────────────

    def add_alert(
        self,
        *,
        thread_id: str,
        graph_id: str,
        rule: str,
        severity: str,
        message: str,
        payload: dict | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        ts = time.time()
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT id FROM alerts
                WHERE tenant_id=? AND thread_id=? AND rule=? AND acked=0
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_id, thread_id, rule),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "deduped": True}
            cur = self._conn.execute(
                """
                INSERT INTO alerts(
                  tenant_id, thread_id, graph_id, rule, severity, message, payload_json, created_at, acked
                ) VALUES(?,?,?,?,?,?,?,?,0)
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
            self._conn.commit()
            eid = cur.lastrowid
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

    def list_alerts(
        self,
        *,
        tenant_id: str = "default",
        open_only: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM alerts WHERE tenant_id=?"
        args: list[Any] = [tenant_id]
        if open_only:
            q += " AND acked=0"
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for row in rows:
            d = dict(row)
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

    def ack_alert(self, alert_id: int, *, tenant_id: str = "default") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE alerts SET acked=1 WHERE id=? AND tenant_id=?",
                (alert_id, tenant_id),
            )
            self._conn.commit()

    # ── path overlay ──────────────────────────────────────────────────────

    def append_path_step(
        self,
        thread_id: str,
        node_id: str,
        *,
        tenant_id: str = "default",
        ts: float | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO path_steps(tenant_id, thread_id, node_id, created_at)
                VALUES(?,?,?,?)
                """,
                (tenant_id, thread_id, node_id, ts or time.time()),
            )
            self._conn.commit()

    def get_path(self, thread_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id FROM path_steps WHERE thread_id=? ORDER BY id ASC",
                (thread_id,),
            ).fetchall()
        return [r["node_id"] for r in rows]

    # ── metrics ───────────────────────────────────────────────────────────

    def record_metric(
        self,
        *,
        tenant_id: str,
        name: str,
        value: float,
        labels: dict | None = None,
        ts: float | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO metrics(tenant_id, name, value, labels_json, created_at)
                VALUES(?,?,?,?,?)
                """,
                (tenant_id, name, float(value), json.dumps(labels or {}), ts or time.time()),
            )
            self._conn.commit()

    def query_metrics(
        self,
        *,
        tenant_id: str,
        name: str | None = None,
        since_s: float = 3600,
    ) -> list[dict[str, Any]]:
        since = time.time() - since_s
        with self._lock:
            if name:
                rows = self._conn.execute(
                    """
                    SELECT * FROM metrics
                    WHERE tenant_id=? AND name=? AND created_at>=?
                    ORDER BY created_at ASC
                    """,
                    (tenant_id, name, since),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM metrics
                    WHERE tenant_id=? AND created_at>=?
                    ORDER BY created_at ASC
                    """,
                    (tenant_id, since),
                ).fetchall()
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

    def metrics_rollup(self, *, tenant_id: str = "default") -> dict[str, Any]:
        """Lag / fail rate / loop iters / stuck — computed from runs + metrics + alerts."""
        runs = self.list_runs(tenant_id=tenant_id, limit=1000)
        total = len(runs) or 1
        failed = sum(1 for r in runs if r["status"] == "failed")
        succeeded = sum(1 for r in runs if r["status"] == "succeeded")
        interrupted = sum(1 for r in runs if r["status"] == "interrupted")
        running = sum(1 for r in runs if r["status"] == "running")
        durations = []
        for r in runs:
            if r.get("ended_at") and r.get("started_at"):
                durations.append(float(r["ended_at"]) - float(r["started_at"]))
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
