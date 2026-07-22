"""Durable checkpointer backed by the control-plane store (SQLite or Postgres)."""

from __future__ import annotations

from ..checkpoint import Checkpoint, Checkpointer


class SqliteCheckpointer(Checkpointer):
    """Name kept for compatibility — works with any store that has put/latest checkpoint."""

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

    def list(self, thread_id: str) -> list[Checkpoint]:
        cp = self.get(thread_id)
        return [cp] if cp else []

    def clear(self, thread_id: str) -> None:
        pass


# Clearer alias
StoreCheckpointer = SqliteCheckpointer
