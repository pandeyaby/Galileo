# === dizzygraph/meta.py ===
"""Meta-loops over entire graph executions — looping over graphs made of loops."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .executor import ExecutionTrace, GraphExecutor
from .state import State, merge_state

log = logging.getLogger("dizzygraph.meta")

ConvergenceFn = Callable[[State | None, State], bool]
StateUpdaterFn = Callable[[State, ExecutionTrace, int], State]
AggregatorFn = Callable[[list[State], list[ExecutionTrace]], Any]


@dataclass
class MetaLoopResult:
    num_meta_iterations: int
    history: list[ExecutionTrace] = field(default_factory=list)
    states: list[State] = field(default_factory=list)
    aggregated: Any = None
    converged: bool = False
    final_state: State | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "num_meta_iterations": self.num_meta_iterations,
            "converged": self.converged,
            "runs": [t.summary() for t in self.history],
            "aggregated": self.aggregated,
        }


class MetaLoopExecutor:
    """
    Run GraphExecutor repeatedly with cross-run state evolution.

    This is the outer loop in Pau's formulation:
      loop { execute(graph_of_loops) ; update ; check convergence }
    """

    def __init__(
        self,
        executor: GraphExecutor,
        *,
        num_meta_iterations: int = 3,
        convergence_check: ConvergenceFn | None = None,
        state_updater: StateUpdaterFn | None = None,
        aggregator: AggregatorFn | None = None,
        feedback_loop: Callable[[State, list[ExecutionTrace]], State] | None = None,
    ):
        self.executor = executor
        self.num_meta_iterations = num_meta_iterations
        self.convergence_check = convergence_check
        self.state_updater = state_updater or self._default_updater
        self.aggregator = aggregator or self._default_aggregator
        self.feedback_loop = feedback_loop

    @staticmethod
    def _default_updater(state: State, trace: ExecutionTrace, meta_i: int) -> State:
        """Carry forward results + bump meta counter; keep best quality if present."""
        quality = state.data.get("quality")
        hist = list(state.data.get("meta_qualities", []))
        if quality is not None:
            hist.append(quality)
        best = max(hist) if hist else quality
        return merge_state(
            state,
            {
                "results": [{"meta_iter": meta_i, "summary": trace.summary()}],
                "data": {
                    "meta_iter": meta_i,
                    "meta_qualities": hist,
                    "best_quality": best,
                },
                "done": False,
            },
        )

    @staticmethod
    def _default_aggregator(states: list[State], traces: list[ExecutionTrace]) -> dict[str, Any]:
        qualities = [s.data.get("quality") for s in states if s.data.get("quality") is not None]
        return {
            "n_runs": len(traces),
            "qualities": qualities,
            "best_quality": max(qualities) if qualities else None,
            "last_metrics": states[-1].metrics if states else {},
        }

    def run(self, initial: State | None = None) -> MetaLoopResult:
        state = initial or State()
        prev: State | None = None
        result = MetaLoopResult(num_meta_iterations=self.num_meta_iterations)

        for i in range(1, self.num_meta_iterations + 1):
            log.info("══ Meta-loop iteration %s/%s ══", i, self.num_meta_iterations)
            trace = self.executor.run(state)
            state = trace.final_state or state
            result.history.append(trace)
            result.states.append(state.model_copy(deep=True))

            if self.convergence_check and prev is not None and self.convergence_check(prev, state):
                result.converged = True
                log.info("Meta-loop converged at iteration %s", i)
                break

            if self.feedback_loop:
                state = self.feedback_loop(state, result.history)

            prev = state.model_copy(deep=True)
            if i < self.num_meta_iterations:
                state = self.state_updater(state, trace, i)

        result.aggregated = self.aggregator(result.states, result.history)
        result.final_state = state
        return result
