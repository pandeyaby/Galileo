"""Durable (enough) checkpoints — resume after crash or HITL interrupt."""

from __future__ import annotations

import json
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .state import State


@dataclass
class Checkpoint:
    thread_id: str
    checkpoint_id: str
    graph_id: str
    state: dict[str, Any]
    next_nodes: list[str]
    visits: dict[str, int] = field(default_factory=dict)
    pending_interrupt: Any = None
    interrupt_node: str | None = None
    created_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Checkpoint":
        return cls(**raw)


class Checkpointer(ABC):
    @abstractmethod
    def put(self, checkpoint: Checkpoint) -> Checkpoint: ...

    @abstractmethod
    def get(self, thread_id: str) -> Checkpoint | None: ...

    @abstractmethod
    def list(self, thread_id: str) -> list[Checkpoint]: ...

    def clear(self, thread_id: str) -> None:
        raise NotImplementedError


class MemoryCheckpointer(Checkpointer):
    """In-process checkpointer — perfect for demos and tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, list[Checkpoint]] = {}

    def put(self, checkpoint: Checkpoint) -> Checkpoint:
        with self._lock:
            self._store.setdefault(checkpoint.thread_id, []).append(checkpoint)
        return checkpoint

    def get(self, thread_id: str) -> Checkpoint | None:
        with self._lock:
            hist = self._store.get(thread_id) or []
            return hist[-1] if hist else None

    def list(self, thread_id: str) -> list[Checkpoint]:
        with self._lock:
            return list(self._store.get(thread_id) or [])

    def clear(self, thread_id: str) -> None:
        with self._lock:
            self._store.pop(thread_id, None)


class FileCheckpointer(Checkpointer):
    """JSON files under a directory — survives process restart."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, thread_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in thread_id)
        return self.root / f"{safe}.json"

    def put(self, checkpoint: Checkpoint) -> Checkpoint:
        with self._lock:
            path = self._path(checkpoint.thread_id)
            hist: list[dict[str, Any]] = []
            if path.exists():
                hist = json.loads(path.read_text(encoding="utf-8"))
            hist.append(checkpoint.to_dict())
            path.write_text(json.dumps(hist, indent=2, default=str), encoding="utf-8")
        return checkpoint

    def get(self, thread_id: str) -> Checkpoint | None:
        hist = self.list(thread_id)
        return hist[-1] if hist else None

    def list(self, thread_id: str) -> list[Checkpoint]:
        path = self._path(thread_id)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Checkpoint.from_dict(x) for x in raw]

    def clear(self, thread_id: str) -> None:
        path = self._path(thread_id)
        if path.exists():
            path.unlink()


def new_checkpoint_id() -> str:
    return uuid.uuid4().hex[:12]


def checkpoint_from_state(
    *,
    thread_id: str,
    graph_id: str,
    state: State,
    next_nodes: list[str],
    visits: dict[str, int] | None = None,
    pending_interrupt: Any = None,
    interrupt_node: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Checkpoint:
    return Checkpoint(
        thread_id=thread_id,
        checkpoint_id=new_checkpoint_id(),
        graph_id=graph_id,
        state=state.model_dump(),
        next_nodes=list(next_nodes),
        visits=dict(visits or {}),
        pending_interrupt=pending_interrupt,
        interrupt_node=interrupt_node,
        meta=meta or {},
    )
