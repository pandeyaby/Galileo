"""Streaming execution events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "graph_start",
    "graph_end",
    "graph_abort",
    "node_start",
    "node_end",
    "node_retry",
    "node_error",
    "node_skip",
    "checkpoint",
    "interrupt",
    "values",
]


@dataclass
class StreamEvent:
    type: EventType
    node_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] | None = None
    error: str | None = None
    attempt: int | None = None
    duration_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "node_id": self.node_id,
            "data": self.data,
            "state": self.state,
            "error": self.error,
            "attempt": self.attempt,
            "duration_s": self.duration_s,
        }
