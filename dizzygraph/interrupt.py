"""Human-in-the-loop: pause a graph, wait for approval/edit, resume."""

from __future__ import annotations

from typing import Any


class GraphInterrupt(Exception):
    """Raised inside a node to pause execution until ``CompiledGraph.resume``."""

    def __init__(self, value: Any = None, *, node_id: str | None = None):
        self.value = value
        self.node_id = node_id
        super().__init__(f"GraphInterrupt(node={node_id!r}, value={value!r})")


def interrupt(value: Any = None) -> Any:
    """
    Call from a node to pause the graph.

    The executor checkpoints state, surfaces ``value`` to the caller via a
    stream event / InterruptedRun, and waits for ``resume(update=...)``.
    """
    raise GraphInterrupt(value)
