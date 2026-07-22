# === dizzygraph/persist.py ===
"""Save/load graph skeletons and state snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import Graph
from .state import State


def save_graph(graph: Graph, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(graph.to_serializable(), indent=2), encoding="utf-8")
    return path


def load_graph(path: str | Path) -> dict[str, Any]:
    """Load serializable skeleton. Re-bind callables when reconstructing runtime Graph."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_state(state: State, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_state(path: str | Path) -> State:
    return State.model_validate_json(Path(path).read_text(encoding="utf-8"))
