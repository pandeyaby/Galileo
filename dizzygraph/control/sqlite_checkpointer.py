"""Durable checkpointer backed by the control-plane store (SQLite or Postgres)."""

from __future__ import annotations

from ..checkpoint import Checkpoint, Checkpointer


class SqliteCheckpointer(Checkpointer):
    """Name kept for compatibility — works with any store that has put/latest checkpoint.

    Multi-worker note: SQLite is single-process; use Postgres (``DIZZY_DATABASE_URL``)
    plus Redis event fan-out for multi-worker. See ``docs/RUNBOOK-MULTI-WORKER.md``.
    History: ``list()`` returns newest-first checkpoints when the store supports
    ``list_checkpoints``; otherwise falls back to latest-only.
    """

    def __init__(self, store):
        self.store = store

    def put(self, checkpoint: Checkpoint) -> Checkpoint:
        payload = checkpoint.to_dict()
        # Prefer tenant from meta if present
        if checkpoint.meta.get("tenant_id"):
            payload["tenant_id"] = checkpoint.meta["tenant_id"]
        self.store.put_checkpoint(payload)
        return checkpoint

    def get(self, thread_id: str) -> Checkpoint | None:
        raw = self.store.latest_checkpoint(thread_id)
        if not raw:
            return None
        return self._from_raw(raw)

    def list(self, thread_id: str) -> list[Checkpoint]:
        if hasattr(self.store, "list_checkpoints"):
            rows = self.store.list_checkpoints(thread_id, limit=100)
            return [self._from_raw(r) for r in rows]
        cp = self.get(thread_id)
        return [cp] if cp else []

    def clear(self, thread_id: str) -> None:
        if hasattr(self.store, "clear_checkpoints"):
            self.store.clear_checkpoints(thread_id)

    @staticmethod
    def _from_raw(raw: dict) -> Checkpoint:
        return Checkpoint(
            thread_id=raw["thread_id"],
            checkpoint_id=raw["checkpoint_id"],
            graph_id=raw["graph_id"],
            state=raw["state"],
            next_nodes=raw["next_nodes"],
            visits=raw["visits"],
            pending_interrupt=raw["pending_interrupt"],
            interrupt_node=raw["interrupt_node"],
            created_at=raw["created_at"],
            meta={"tenant_id": raw.get("tenant_id", "default")},
        )


# Clearer alias
StoreCheckpointer = SqliteCheckpointer
