# === dizzygraph/state.py ===
"""Flexible shared state with deep-merge updates."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class State(BaseModel):
    """Shared graph state — extend via subclass or free-form `data`."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    messages: list[Any] = Field(default_factory=list)
    results: list[Any] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    done: bool = False
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.model_fields_set or hasattr(self, key):
            try:
                return getattr(self, key)
            except AttributeError:
                pass
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> "State":
        if key in type(self).model_fields and key != "data":
            return self.model_copy(update={key: value})
        new_data = dict(self.data)
        new_data[key] = value
        return self.model_copy(update={"data": new_data})


def _deep_merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, Mapping):
        out = dict(a)
        for k, v in b.items():
            out[k] = _deep_merge(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b if b is not None else a


def merge_state(base: State, update: State | Mapping[str, Any] | None) -> State:
    """Deep-merge an update into base. Mappings become field/`data` patches."""
    if update is None:
        return base
    if isinstance(update, State):
        patch = update.model_dump(exclude_unset=True)
    else:
        patch = dict(update)

    known = set(type(base).model_fields)
    field_patch: dict[str, Any] = {}
    data_patch: dict[str, Any] = {}
    for k, v in patch.items():
        if k == "data" and isinstance(v, Mapping):
            data_patch.update(v)
        elif k in known:
            cur = getattr(base, k)
            field_patch[k] = _deep_merge(cur, v)
        else:
            data_patch[k] = v

    if data_patch:
        field_patch["data"] = _deep_merge(dict(base.data), data_patch)
    return base.model_copy(update=field_patch)
