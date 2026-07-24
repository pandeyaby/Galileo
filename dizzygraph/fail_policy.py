"""Typed fail policies for graph execution after a node exhausts retries."""

from __future__ import annotations

from enum import Enum
from typing import Any


class FailPolicy(str, Enum):
    """What to do when a node fails after retries (and is not a GraphInterrupt)."""

    ABORT = "abort"  # re-raise; stop the graph (same as fail_fast=True)
    CONTINUE = "continue"  # record state.error and keep walking successors
    SKIP = "skip"  # record error, skip remaining successors from this node
    # Alias kept for compile(fail_fast=...) compatibility
    FAIL_FAST = "abort"


def coerce_fail_policy(value: FailPolicy | str | bool | None) -> FailPolicy:
    """Accept FailPolicy, string name, or legacy bool ``fail_fast``."""
    if value is None:
        return FailPolicy.CONTINUE
    if isinstance(value, bool):
        return FailPolicy.ABORT if value else FailPolicy.CONTINUE
    if isinstance(value, FailPolicy):
        return value
    key = str(value).strip().lower()
    if key in ("fail_fast", "abort", "raise"):
        return FailPolicy.ABORT
    if key in ("continue", "cont"):
        return FailPolicy.CONTINUE
    if key in ("skip",):
        return FailPolicy.SKIP
    raise ValueError(f"Unknown fail_policy={value!r}; use abort|continue|skip")


def fail_policy_as_dict(policy: FailPolicy) -> dict[str, Any]:
    return {"fail_policy": policy.value, "abort_on_error": policy is FailPolicy.ABORT}
