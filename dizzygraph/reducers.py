"""Channel reducers — how state patches merge (LangGraph-style, Dizzy-simple).

Default for ``State.data`` keys is **replace** (not list-concat). That kills the
silent ``doc_ids`` duplication bug. Register an explicit reducer when you want
append / unique-append / dict-merge semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

T = TypeVar("T")
Reducer = Callable[[Any, Any], Any]


def replace(left: Any, right: Any) -> Any:
    """Last write wins (None does not erase)."""
    return left if right is None else right


def append(left: Any, right: Any) -> list[Any]:
    """Concatenate sequences; wrap scalars."""
    a = [] if left is None else (list(left) if isinstance(left, (list, tuple)) else [left])
    if right is None:
        return a
    if isinstance(right, (list, tuple)):
        return a + list(right)
    return a + [right]


def unique_append(left: Any, right: Any) -> list[Any]:
    """Append preserving order, dropping duplicates (hashable items)."""
    a = append(left, None)
    seen = set(a)
    for item in append(None, right):
        if item in seen:
            continue
        seen.add(item)
        a.append(item)
    return a


def merge_dicts(left: Any, right: Any) -> dict[str, Any]:
    """Shallow dict merge; nested dicts recurse; other values replace."""
    out: dict[str, Any] = dict(left) if isinstance(left, Mapping) else {}
    if not isinstance(right, Mapping):
        return out if right is None else dict(right) if isinstance(right, Mapping) else out
    for k, v in right.items():
        if k in out and isinstance(out[k], Mapping) and isinstance(v, Mapping):
            out[k] = merge_dicts(out[k], v)
        else:
            out[k] = v
    return out


def max_value(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def min_value(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


# Built-in field reducers for State top-level keys
FIELD_REDUCERS: dict[str, Reducer] = {
    "messages": append,
    "results": append,
    "metrics": merge_dicts,
    "done": replace,
    "error": replace,
}

# Per-key reducers inside State.data (default = replace)
DATA_REDUCERS: dict[str, Reducer] = {}


def register_data_reducer(key: str, reducer: Reducer) -> None:
    """Register merge semantics for a ``state.data[key]`` channel."""
    DATA_REDUCERS[key] = reducer


def clear_data_reducers() -> None:
    DATA_REDUCERS.clear()
