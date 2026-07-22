# === dizzygraph/executor.py ===
"""Graph execution: DAG topo + cyclic iterative runs, traces, async branches."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .graph import Graph
from .nodes import SubGraphNode
from .state import State, merge_state

log = logging.getLogger("dizzygraph.executor")


@dataclass
class NodeTrace:
    node_id: str
    input_state: dict[str, Any]
    output_state: dict[str, Any]
    duration_s: float
    loop_count: int | None = None
    error: str | None = None


@dataclass
class ExecutionTrace:
    graph_id: str
    node_traces: list[NodeTrace] = field(default_factory=list)
    graph_iterations: int = 0
    total_duration_s: float = 0.0
    final_state: State | None = None
    cycle_visits: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "graph_iterations": self.graph_iterations,
            "total_duration_s": round(self.total_duration_s, 4),
            "nodes_run": [t.node_id for t in self.node_traces],
            "errors": [t.error for t in self.node_traces if t.error],
            "cycle_visits": self.cycle_visits,
        }


class GraphExecutor:
    """
    Runs a Graph from entry points.

    - Acyclic: topological layers (optional parallel within a layer).
    - Cyclic: iterative frontier expansion with max_graph_iterations.
    """

    def __init__(
        self,
        graph: Graph,
        *,
        max_graph_iterations: int = 32,
        parallel_branches: bool = False,
        checkpoint_fn=None,
    ):
        self.graph = graph
        self.max_graph_iterations = max_graph_iterations
        self.parallel_branches = parallel_branches
        self.checkpoint_fn = checkpoint_fn
        self._bind_subgraphs()

    def _bind_subgraphs(self) -> None:
        for node in self.graph.nodes.values():
            if isinstance(node, SubGraphNode):
                node.bind_runner(self._run_nested)

    def _run_nested(self, graph: Graph, state: State, depth: int) -> State:
        nested = GraphExecutor(
            graph,
            max_graph_iterations=self.max_graph_iterations,
            parallel_branches=self.parallel_branches,
        )
        trace = nested.run(state)
        return trace.final_state or state

    def run(self, state: State | None = None) -> ExecutionTrace:
        warnings = self.graph.validate(allow_cycles=True)
        for w in warnings:
            log.info("validate: %s", w)

        state = state or State()
        trace = ExecutionTrace(graph_id=self.graph.id)
        t0 = time.perf_counter()

        layers = self.graph.topological_layers()
        if layers is not None and not self.graph.detect_cycles():
            state = self._run_dag(state, layers, trace)
            trace.graph_iterations = 1
        else:
            state = self._run_cyclic(state, trace)

        trace.final_state = state
        trace.total_duration_s = time.perf_counter() - t0
        if self.checkpoint_fn:
            self.checkpoint_fn(state, trace)
        return trace

    async def arun(self, state: State | None = None) -> ExecutionTrace:
        return await asyncio.to_thread(self.run, state)

    def _run_node(self, node_id: str, state: State, trace: ExecutionTrace) -> State:
        node = self.graph.nodes[node_id]
        before = state.model_dump()
        t0 = time.perf_counter()
        err = None
        try:
            if node.timeout_s:
                # cooperative timeout via thread+wait would need extras; soft note only
                pass
            out = node.run(state)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            log.exception("Node %s failed", node_id)
            out = merge_state(state, {"error": err})
        dur = time.perf_counter() - t0
        loop_count = None
        if out.metrics.get("loop_count") is not None:
            loop_count = int(out.metrics["loop_count"])
        trace.node_traces.append(
            NodeTrace(
                node_id=node_id,
                input_state=before,
                output_state=out.model_dump(),
                duration_s=dur,
                loop_count=loop_count,
                error=err,
            )
        )
        trace.cycle_visits[node_id] = trace.cycle_visits.get(node_id, 0) + 1
        log.info("ran %-20s  %.3fs  loop=%s", node_id, dur, loop_count)
        return out

    def _run_dag(self, state: State, layers: list[list[str]], trace: ExecutionTrace) -> State:
        for layer in layers:
            # Parallel optional; default sequential for predictable traces.
            if self.parallel_branches and len(layer) > 1:
                try:
                    state = asyncio.run(self._parallel_layer(layer, state, trace))
                except RuntimeError:
                    for nid in layer:
                        state = self._run_node(nid, state, trace)
            else:
                for nid in layer:
                    state = self._run_node(nid, state, trace)
        return state

    async def _parallel_layer(self, layer: list[str], state: State, trace: ExecutionTrace) -> State:
        # Independent patches merged after; shared state copy-in
        async def one(nid: str) -> State:
            return await asyncio.to_thread(self._run_node, nid, state.model_copy(deep=True), trace)

        parts = await asyncio.gather(*[one(n) for n in layer])
        merged = state
        for p in parts:
            merged = merge_state(merged, p.model_dump(exclude_unset=False))
        return merged

    def _run_cyclic(self, state: State, trace: ExecutionTrace) -> State:
        """Frontier walk with visit budget — supports feedback edges."""
        frontier = list(self.graph.get_entry_nodes())
        scheduled: list[str] = []
        iters = 0
        while frontier and iters < self.max_graph_iterations:
            iters += 1
            node_id = frontier.pop(0)
            # soft revisit limit per node
            if trace.cycle_visits.get(node_id, 0) >= self.max_graph_iterations:
                log.warning("skip %s — visit budget exhausted", node_id)
                continue
            state = self._run_node(node_id, state, trace)
            if state.done:
                break
            for nxt in self.graph.successors(node_id, state):
                # allow re-queue for cycles, but avoid immediate infinite same-edge spam
                if trace.cycle_visits.get(nxt, 0) < self.max_graph_iterations:
                    frontier.append(nxt)
            scheduled.append(node_id)
        trace.graph_iterations = iters
        if frontier and not state.done:
            log.warning(
                "stopped after max_graph_iterations=%s (frontier left=%s)",
                self.max_graph_iterations,
                frontier,
            )
        return state
