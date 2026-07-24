"""CompiledGraph — the adoptable API: compile → invoke / stream / resume."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .callbacks import BaseCallbackHandler
from .checkpoint import Checkpointer
from .events import StreamEvent
from .executor import ExecutionTrace, GraphExecutor
from .fail_policy import FailPolicy, coerce_fail_policy
from .graph import Graph
from .retry import RetryPolicy
from .state import State


class CompiledGraph:
    """Runnable graph with checkpoints, streaming, HITL resume, and fail policies.

    Compile once, then:
      ``invoke`` / ``ainvoke`` — run to completion (or interrupt)
      ``stream`` / ``astream`` — observe ``StreamEvent``s
      ``resume`` — continue after HITL
      ``get_state`` / ``get_config`` — inspect durability + compile options
    """

    def __init__(self, executor: GraphExecutor):
        self.executor = executor
        self.graph = executor.graph

    def get_config(self) -> dict[str, Any]:
        """Stable compile-time options for ops / debugging."""
        return {
            "graph_id": self.graph.id,
            "max_graph_iterations": self.executor.max_graph_iterations,
            "parallel_branches": self.executor.parallel_branches,
            "fail_policy": self.executor.fail_policy.value,
            "fail_fast": self.executor.fail_fast,
            "has_checkpointer": self.executor.checkpointer is not None,
            "nest_checkpointer": self.executor.nest_checkpointer,
            "default_retry": (
                None
                if self.executor.default_retry is None
                else {
                    "max_attempts": self.executor.default_retry.max_attempts,
                    "initial_interval_s": self.executor.default_retry.initial_interval_s,
                }
            ),
        }

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

    async def astream(
        self,
        state: State | dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ):
        """Async generator over stream events (thread offload per event batch)."""
        import asyncio

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        def _produce() -> None:
            try:
                for ev in self.executor.stream(_coerce_state(state), thread_id=thread_id):
                    loop.call_soon_threadsafe(queue.put_nowait, ev)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        loop.run_in_executor(None, _produce)
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            yield item

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
    fail_fast: bool | None = None,
    fail_policy: FailPolicy | str | bool | None = None,
    on_event=None,
    nest_checkpointer: bool = True,
) -> CompiledGraph:
    """Compile a Graph into an invokable ``CompiledGraph``.

    Prefer ``fail_policy='abort'|'continue'|'skip'``. Legacy ``fail_fast=True``
    maps to ``abort``.
    """
    if fail_policy is None and fail_fast is not None:
        fail_policy = fail_fast
    coerce_fail_policy(fail_policy)  # validate early
    ex = GraphExecutor(
        graph,
        checkpointer=checkpointer,
        callbacks=callbacks,
        default_retry=default_retry,
        parallel_branches=parallel_branches,
        max_graph_iterations=max_graph_iterations,
        fail_policy=fail_policy,
        on_event=on_event,
        nest_checkpointer=nest_checkpointer,
    )
    return CompiledGraph(ex)
