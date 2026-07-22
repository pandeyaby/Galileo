# === dizzygraph/nodes.py ===
"""Node types: Atomic, Loop, SubGraph, Agent — graphs made of loops."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .state import State, merge_state

log = logging.getLogger("dizzygraph.nodes")

MakerFn = Callable[[State], "State | MappingUpdate"]
CheckerFn = Callable[[State], bool | float]
StopFn = Callable[[State], bool]
MappingUpdate = dict[str, Any]
AgentFn = Callable[[State], "State | MappingUpdate | Awaitable[State | MappingUpdate]"]


NODE_REGISTRY: dict[str, type["Node"]] = {}


def register_node_type(name: str):
    def deco(cls: type[Node]):
        NODE_REGISTRY[name] = cls
        cls.node_kind = name  # type: ignore[attr-defined]
        return cls

    return deco


class Node(BaseModel, ABC):
    """Base node — identity + typed execution hook."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    name: str = ""
    description: str = ""
    timeout_s: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    node_kind: ClassVar[str] = "base"

    def model_post_init(self, __context: Any) -> None:
        if not self.name:
            self.name = self.id

    @abstractmethod
    def run(self, state: State) -> State:
        ...

    async def arun(self, state: State) -> State:
        return await asyncio.to_thread(self.run, state)


@register_node_type("atomic")
class AtomicNode(Node):
    """Single callable step: State → State (or mapping patch)."""

    node_kind: ClassVar[str] = "atomic"
    fn: Callable[[State], State | MappingUpdate]

    def run(self, state: State) -> State:
        out = self.fn(state)
        if isinstance(out, State):
            return out
        return merge_state(state, out)


@register_node_type("loop")
class LoopNode(Node):
    """
    Loop engineering inside a graph node.

    maker  — think/act worker (updates state each iteration)
    checker — optional separate verifier (bool or score)
    stop_condition — hard exit on state (in addition to checker / max_iters)
    """

    node_kind: ClassVar[str] = "loop"
    maker: Callable[[State], State | MappingUpdate]
    checker: CheckerFn | None = None
    stop_condition: StopFn | None = None
    max_iters: int = 8
    score_threshold: float | None = None
    score_key: str = "quality"

    def run(self, state: State) -> State:
        current = state
        loop_count = 0
        scores: list[float] = []

        for i in range(1, self.max_iters + 1):
            loop_count = i
            t0 = time.perf_counter()
            patch = self.maker(current)
            current = patch if isinstance(patch, State) else merge_state(current, patch)
            elapsed = time.perf_counter() - t0

            score: float | None = None
            if self.checker is not None:
                verdict = self.checker(current)
                if isinstance(verdict, bool):
                    ok = verdict
                    score = 1.0 if verdict else 0.0
                else:
                    score = float(verdict)
                    ok = (
                        score >= self.score_threshold
                        if self.score_threshold is not None
                        else score >= 1.0
                    )
                scores.append(score)
                current = merge_state(
                    current,
                    {
                        "metrics": {
                            "loop_score": score,
                            "loop_ok": ok,
                            "loop_iter": i,
                            "loop_maker_s": round(elapsed, 4),
                        },
                        "data": {self.score_key: score},
                    },
                )
                if ok:
                    log.info("LoopNode %s passed checker at iter=%s score=%s", self.id, i, score)
                    break
            if self.stop_condition and self.stop_condition(current):
                log.info("LoopNode %s stop_condition at iter=%s", self.id, i)
                break

        return merge_state(
            current,
            {
                "metrics": {
                    "loop_count": loop_count,
                    "loop_scores": scores,
                    "loop_converged": bool(scores and self.score_threshold is not None and scores[-1] >= self.score_threshold)
                    if scores
                    else False,
                }
            },
        )


@register_node_type("subgraph")
class SubGraphNode(Node):
    """Nested Graph — hierarchy / recursion (executor injects run_fn)."""

    node_kind: ClassVar[str] = "subgraph"
    graph: Any  # Graph — avoid circular import at type time
    max_depth: int = 4
    _run_fn: Callable[[Any, State, int], State] | None = None

    def bind_runner(self, run_fn: Callable[[Any, State, int], State]) -> None:
        self._run_fn = run_fn

    def run(self, state: State) -> State:
        if self._run_fn is None:
            raise RuntimeError(f"SubGraphNode {self.id}: executor has not bound a runner")
        depth = int(state.data.get("_subgraph_depth", 0) or 0)
        if depth >= self.max_depth:
            return merge_state(state, {"error": f"SubGraph depth limit {self.max_depth} at {self.id}"})
        nested_in = merge_state(state, {"data": {"_subgraph_depth": depth + 1}})
        return self._run_fn(self.graph, nested_in, depth + 1)


@register_node_type("agent")
class AgentNode(Node):
    """
    LLM-ready step. Default uses a mock; swap `llm_fn` for litellm/OpenAI.

    Example real swap::

        async def call_llm(state):
            from litellm import acompletion
            r = await acompletion(model="gpt-4o-mini", messages=[...])
            return {"data": {"reply": r.choices[0].message.content}}
    """

    node_kind: ClassVar[str] = "agent"
    llm_fn: AgentFn | None = None
    system_prompt: str = "You are a careful agent. Be concise."
    mock_reply: str = "mock-agent-reply"

    def run(self, state: State) -> State:
        if self.llm_fn is None:
            prompt = state.get("query") or state.get("data", {}).get("prompt") or ""
            reply = f"{self.mock_reply}: {str(prompt)[:80]}"
            return merge_state(
                state,
                {
                    "messages": [{"role": "assistant", "content": reply}],
                    "data": {"agent_reply": reply},
                },
            )
        out = self.llm_fn(state)
        if asyncio.iscoroutine(out):
            out = asyncio.get_event_loop().run_until_complete(out)  # type: ignore[arg-type]
        if isinstance(out, State):
            return out
        return merge_state(state, out)  # type: ignore[arg-type]

    async def arun(self, state: State) -> State:
        if self.llm_fn is None:
            return self.run(state)
        out = self.llm_fn(state)
        if asyncio.iscoroutine(out) or isinstance(out, Awaitable):
            out = await out  # type: ignore[misc]
        if isinstance(out, State):
            return out
        return merge_state(state, out)  # type: ignore[arg-type]
