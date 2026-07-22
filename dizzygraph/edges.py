# === dizzygraph/edges.py ===
"""Directed edges with optional predicates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .state import State

ConditionFn = Callable[[State], bool]


class Edge(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_id: str
    target_id: str
    condition: ConditionFn | None = None
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_active(self, state: State) -> bool:
        if self.condition is None:
            return True
        return bool(self.condition(state))
