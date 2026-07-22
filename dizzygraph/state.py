"""Shared graph state with explicit channel reducers."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .reducers import DATA_REDUCERS, FIELD_REDUCERS, replace


class State(BaseModel):
    """
    Shared graph state.

    Top-level fields use ``FIELD_REDUCERS``. Keys inside ``data`` default to
    **replace** (safe); register ``register_data_reducer`` when you need append.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    messages: list[Any] = Field(default_factory=list)
    results: list[Any] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    done: bool = False
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields and key != "data":
            return getattr(self, key, default)
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> "State":
        if key in type(self).model_fields and key != "data":
            return self.model_copy(update={key: value})
        new_data = dict(self.data)
        new_data[key] = value
        return self.model_copy(update={"data": new_data})

    def dump(self) -> dict[str, Any]:
        return self.model_dump()


def _reduce_data(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        reducer = DATA_REDUCERS.get(k, replace)
        out[k] = reducer(out.get(k), v)
    return out


def merge_state(base: State, update: State | Mapping[str, Any] | None) -> State:
    """Merge an update into base using channel reducers."""
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
            reducer = FIELD_REDUCERS.get(k, replace)
            field_patch[k] = reducer(getattr(base, k), v)
        else:
            data_patch[k] = v

    if data_patch:
        field_patch["data"] = _reduce_data(dict(base.data), data_patch)
    return base.model_copy(update=field_patch)


def apply_values(base: State, values: Mapping[str, Any]) -> State:
    """Apply a resume/HITL patch (same reducer rules as merge_state)."""
    return merge_state(base, values)
