"""CompiledGraph — the adoptable API: compile → invoke / stream / resume."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .callbacks import BaseCallbackHandler
from .checkpoint import Checkpointer
from .events import StreamEvent
from .executor import ExecutionTrace, GraphExecutor
from .graph import Graph
from .retry import RetryPolicy
from .state import State


class CompiledGraph:
    """Runnable graph with checkpoints, streaming, and HITL resume."""

    def __init__(self, executor: GraphExecutor):
        self.executor = executor
        self.graph = executor.graph

    def invoke(
        self,
        state: State | dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> ExecutionTrace:
        return self.executor.run(_coerce_state(state), thread_id=thread_id)

    def stream(
        self,
        state: State | dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        return self.executor.stream(_coerce_state(state), thread_id=thread_id)

    def resume(
        self,
        *,
        thread_id: str,
        update: dict[str, Any] | State | None = None,
    ) -> ExecutionTrace:
        return self.executor.resume(thread_id=thread_id, update=update)

    async def ainvoke(
        self,
        state: State | dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> ExecutionTrace:
        return await self.executor.arun(_coerce_state(state), thread_id=thread_id)

    def get_state(self, thread_id: str) -> State | None:
        cp = self.executor.checkpointer.get(thread_id) if self.executor.checkpointer else None
        return State.model_validate(cp.state) if cp else None

    def to_mermaid(self) -> str:
        from .viz import to_mermaid

        return to_mermaid(self.graph)


def _coerce_state(state: State | dict[str, Any] | None) -> State | None:
    if state is None or isinstance(state, State):
        return state
    if "data" in state or any(k in state for k in ("messages", "results", "metrics", "done", "error")):
        return State.model_validate(state)
    # Convenience: bare dict → State.data
    return State(data=dict(state))


def compile_graph(
    graph: Graph,
    *,
    checkpointer: Checkpointer | None = None,
    callbacks: list[BaseCallbackHandler] | None = None,
    default_retry: RetryPolicy | None = None,
    parallel_branches: bool = False,
    max_graph_iterations: int = 32,
    fail_fast: bool = False,
) -> CompiledGraph:
    ex = GraphExecutor(
        graph,
        checkpointer=checkpointer,
        callbacks=callbacks,
        default_retry=default_retry,
        parallel_branches=parallel_branches,
        max_graph_iterations=max_graph_iterations,
        fail_fast=fail_fast,
    )
    return CompiledGraph(ex)
