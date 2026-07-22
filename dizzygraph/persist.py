"""Save/load topology skeletons and state snapshots.

Callables cannot round-trip through JSON. Use ``save_graph_skeleton`` /
``load_graph_skeleton`` for honest naming. ``load_graph`` is an alias that
returns the skeleton dict (not a runnable Graph).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import Graph
from .state import State


def save_graph_skeleton(graph: Graph, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(graph.to_serializable(), indent=2), encoding="utf-8")
    return path


def load_graph_skeleton(path: str | Path) -> dict[str, Any]:
    """Load topology JSON. Re-bind node callables yourself to rebuild a Graph."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Back-compat aliases (honest docs; same behavior)
save_graph = save_graph_skeleton
load_graph = load_graph_skeleton


def save_state(state: State, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_state(path: str | Path) -> State:
    return State.model_validate_json(Path(path).read_text(encoding="utf-8"))
