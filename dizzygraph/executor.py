"""Graph execution: DAG / cyclic, stream, checkpoints, HITL, retries, fail policies."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .callbacks import BaseCallbackHandler, FanoutCallbacks
from .checkpoint import Checkpoint, Checkpointer, checkpoint_from_state
from .events import StreamEvent
from .fail_policy import FailPolicy, coerce_fail_policy
from .graph import Graph
from .interrupt import GraphInterrupt
from .nodes import SubGraphNode
from .retry import RetryPolicy, run_with_retry
from .state import State, apply_values, merge_state

log = logging.getLogger("dizzygraph.executor")


@dataclass
class NodeTrace:
    node_id: str
    input_state: dict[str, Any]
    output_state: dict[str, Any]
    duration_s: float
    loop_count: int | None = None
    error: str | None = None
    attempts: int = 1


@dataclass
class ExecutionTrace:
    graph_id: str
    node_traces: list[NodeTrace] = field(default_factory=list)
    graph_iterations: int = 0
    total_duration_s: float = 0.0
    final_state: State | None = None
    cycle_visits: dict[str, int] = field(default_factory=dict)
    interrupted: bool = False
    interrupt_value: Any = None
    interrupt_node: str | None = None
    checkpoint_id: str | None = None
    thread_id: str | None = None
    fail_policy: str = FailPolicy.CONTINUE.value
    aborted: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "graph_iterations": self.graph_iterations,
            "total_duration_s": round(self.total_duration_s, 4),
            "nodes_run": [t.node_id for t in self.node_traces],
            "errors": [t.error for t in self.node_traces if t.error],
            "cycle_visits": self.cycle_visits,
            "interrupted": self.interrupted,
            "interrupt_node": self.interrupt_node,
            "checkpoint_id": self.checkpoint_id,
            "thread_id": self.thread_id,
            "fail_policy": self.fail_policy,
            "aborted": self.aborted,
        }


class GraphExecutor:
    """
    Runs a Graph from entry points (or a checkpoint frontier).

    - Acyclic: topological layers (optional parallel within a layer).
    - Cyclic: iterative frontier expansion with visit budgets.
    - Checkpoints after every node when a Checkpointer + thread_id are set.
    - ``stream()`` yields ``StreamEvent``s; ``run()`` drains the stream.
    - ``fail_policy``: abort | continue | skip after node errors.
    """

    def __init__(
        self,
        graph: Graph,
        *,
        max_graph_iterations: int = 32,
        parallel_branches: bool = False,
        checkpointer: Checkpointer | None = None,
        callbacks: list[BaseCallbackHandler] | None = None,
        default_retry: RetryPolicy | None = None,
        fail_fast: bool | None = None,
        fail_policy: FailPolicy | str | bool | None = None,
        on_event=None,
        # legacy — called after each successful node if no Checkpointer
        checkpoint_fn=None,
        nest_checkpointer: bool = True,
    ):
        self.graph = graph
        self.max_graph_iterations = max_graph_iterations
        self.parallel_branches = parallel_branches
        self.checkpointer = checkpointer
        self.callbacks = FanoutCallbacks(list(callbacks or []))
        self.default_retry = default_retry
        if fail_policy is None and fail_fast is not None:
            fail_policy = fail_fast
        self.fail_policy = coerce_fail_policy(fail_policy)
        self.fail_fast = self.fail_policy is FailPolicy.ABORT
        self.on_event = on_event
        self.checkpoint_fn = checkpoint_fn
        self.nest_checkpointer = nest_checkpointer
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
            checkpointer=self.checkpointer if self.nest_checkpointer else None,
            callbacks=list(self.callbacks.handlers) if self.callbacks.handlers else None,
            default_retry=self.default_retry,
            fail_policy=self.fail_policy,
            on_event=self.on_event,
            nest_checkpointer=self.nest_checkpointer,
        )
        thread_id = None
        if self.nest_checkpointer and isinstance(state.metrics, dict):
            thread_id = state.metrics.get("thread_id")
        trace = nested.run(state, thread_id=thread_id)
        return trace.final_state or state

    def run(
        self,
        state: State | None = None,
        *,
        thread_id: str | None = None,
    ) -> ExecutionTrace:
        final: ExecutionTrace | None = None
        for event in self.stream(state, thread_id=thread_id):
            if event.type == "graph_end" and event.data.get("trace") is not None:
                final = event.data["trace"]
        if final is None:
            raise RuntimeError("stream ended without graph_end")
        return final

    async def arun(self, state: State | None = None, *, thread_id: str | None = None) -> ExecutionTrace:
        import asyncio

        return await asyncio.to_thread(self.run, state, thread_id=thread_id)

    def stream(
        self,
        state: State | None = None,
        *,
        thread_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        warnings = self.graph.validate(allow_cycles=True)
        for w in warnings:
            log.info("validate: %s", w)

        trace = ExecutionTrace(graph_id=self.graph.id, thread_id=thread_id, fail_policy=self.fail_policy.value)
        t0 = time.perf_counter()

        frontier: list[str]
        visits: dict[str, int] = {}
        pending_cp: Checkpoint | None = None

        if thread_id and self.checkpointer:
            pending_cp = self.checkpointer.get(thread_id)
            if pending_cp and pending_cp.pending_interrupt is not None:
                # Caller must use resume() — surface interrupt again
                state = State.model_validate(pending_cp.state)
                yield self._emit(
                    StreamEvent(
                        type="interrupt",
                        node_id=pending_cp.interrupt_node,
                        data={"value": pending_cp.pending_interrupt, "hint": "call resume()"},
                        state=state.model_dump(),
                    )
                )
                trace.interrupted = True
                trace.interrupt_value = pending_cp.pending_interrupt
                trace.interrupt_node = pending_cp.interrupt_node
                trace.checkpoint_id = pending_cp.checkpoint_id
                trace.final_state = state
                trace.total_duration_s = time.perf_counter() - t0
                yield self._emit(
                    StreamEvent(type="graph_end", data={"trace": trace}, state=state.model_dump())
                )
                return
            if pending_cp and pending_cp.next_nodes:
                state = State.model_validate(pending_cp.state)
                frontier = list(pending_cp.next_nodes)
                visits = dict(pending_cp.visits)
            else:
                state = state or State()
                frontier = list(self.graph.get_entry_nodes())
        else:
            state = state or State()
            frontier = list(self.graph.get_entry_nodes())

        if thread_id:
            state = merge_state(state, {"metrics": {"thread_id": thread_id}})

        self.callbacks.on_graph_start(self.graph.id, state)
        yield self._emit(StreamEvent(type="graph_start", state=state.model_dump()))

        layers = self.graph.topological_layers()
        use_dag = layers is not None and not self.graph.detect_cycles() and not pending_cp

        try:
            if use_dag:
                assert layers is not None
                for event in self._stream_dag(state, layers, trace, thread_id, visits):
                    if event.type == "values":
                        state = State.model_validate(event.state or {})
                    yield event
                    if event.type == "interrupt":
                        trace.interrupted = True
                        break
                    if event.type == "graph_abort":
                        trace.aborted = True
                        break
                trace.graph_iterations = 1
            else:
                for event in self._stream_cyclic(state, frontier, trace, thread_id, visits):
                    if event.type == "values":
                        state = State.model_validate(event.state or {})
                    yield event
                    if event.type == "interrupt":
                        trace.interrupted = True
                        break
                    if event.type == "graph_abort":
                        trace.aborted = True
                        break
        finally:
            trace.final_state = state
            trace.total_duration_s = time.perf_counter() - t0
            self.callbacks.on_graph_end(self.graph.id, state, trace.total_duration_s)

        yield self._emit(
            StreamEvent(
                type="graph_end",
                data={"trace": trace},
                state=state.model_dump() if state else None,
            )
        )

    def resume(
        self,
        *,
        thread_id: str,
        update: dict[str, Any] | State | None = None,
    ) -> ExecutionTrace:
        """Continue after HITL interrupt — merge ``update`` and clear pending interrupt."""
        if not self.checkpointer:
            raise RuntimeError("resume() requires a checkpointer")
        cp = self.checkpointer.get(thread_id)
        if cp is None:
            raise RuntimeError(f"No checkpoint for thread_id={thread_id!r}")
        state = State.model_validate(cp.state)
        if update is not None:
            state = apply_values(state, update if isinstance(update, dict) else update.model_dump())
        # Clear interrupt; keep frontier
        cleared = checkpoint_from_state(
            thread_id=thread_id,
            graph_id=self.graph.id,
            state=state,
            next_nodes=cp.next_nodes,
            visits=cp.visits,
            pending_interrupt=None,
            interrupt_node=None,
            meta={"resumed_from": cp.checkpoint_id},
        )
        self.checkpointer.put(cleared)
        return self.run(state, thread_id=thread_id)

    # ── internals ──────────────────────────────────────────────────────────

    def _emit(self, event: StreamEvent) -> StreamEvent:
        self.callbacks.on_event(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 — hooks must not kill the run
                log.exception("on_event hook failed")
        return event

    def _save_cp(
        self,
        thread_id: str | None,
        state: State,
        next_nodes: list[str],
        visits: dict[str, int],
        *,
        pending_interrupt: Any = None,
        interrupt_node: str | None = None,
    ) -> Checkpoint | None:
        if not thread_id or not self.checkpointer:
            if self.checkpoint_fn and pending_interrupt is None:
                # legacy end-hook style — only used if explicitly provided
                pass
            return None
        cp = checkpoint_from_state(
            thread_id=thread_id,
            graph_id=self.graph.id,
            state=state,
            next_nodes=next_nodes,
            visits=visits,
            pending_interrupt=pending_interrupt,
            interrupt_node=interrupt_node,
        )
        self.checkpointer.put(cp)
        return cp

    def _run_node(
        self,
        node_id: str,
        state: State,
        trace: ExecutionTrace,
    ) -> tuple[State, list[StreamEvent], GraphInterrupt | None, bool]:
        """Returns (state, events, interrupt|None, skip_successors)."""
        node = self.graph.nodes[node_id]
        before = state.model_dump()
        events: list[StreamEvent] = []
        self.callbacks.on_node_start(node_id, state)
        events.append(self._emit(StreamEvent(type="node_start", node_id=node_id, state=before)))

        policy: RetryPolicy | None = getattr(node, "retry_policy", None) or self.default_retry
        timeout_s = getattr(node, "timeout_s", None)
        attempts = 0
        err: str | None = None
        out = state
        skip_successors = False
        t0 = time.perf_counter()

        def once() -> State:
            nonlocal attempts
            attempts += 1
            if timeout_s:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(node.run, state)
                    try:
                        return fut.result(timeout=timeout_s)
                    except concurrent.futures.TimeoutError as exc:
                        raise TimeoutError(f"Node {node_id} exceeded timeout_s={timeout_s}") from exc
            return node.run(state)

        try:
            out = run_with_retry(
                once,
                policy,
                on_retry=lambda attempt, exc: events.append(
                    self._emit(
                        StreamEvent(
                            type="node_retry",
                            node_id=node_id,
                            attempt=attempt,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                ),
            )
        except GraphInterrupt as gi:
            dur = time.perf_counter() - t0
            gi.node_id = gi.node_id or node_id
            events.append(
                self._emit(
                    StreamEvent(
                        type="interrupt",
                        node_id=node_id,
                        data={"value": gi.value},
                        state=before,
                        duration_s=dur,
                    )
                )
            )
            return state, events, gi, False
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            log.exception("Node %s failed", node_id)
            self.callbacks.on_node_error(node_id, err)
            events.append(
                self._emit(StreamEvent(type="node_error", node_id=node_id, error=err))
            )
            if self.fail_policy is FailPolicy.ABORT:
                events.append(
                    self._emit(
                        StreamEvent(
                            type="graph_abort",
                            node_id=node_id,
                            error=err,
                            data={"fail_policy": self.fail_policy.value},
                        )
                    )
                )
                raise
            out = merge_state(state, {"error": err})
            if self.fail_policy is FailPolicy.SKIP:
                skip_successors = True
                events.append(
                    self._emit(
                        StreamEvent(
                            type="node_skip",
                            node_id=node_id,
                            error=err,
                            data={"fail_policy": "skip"},
                        )
                    )
                )

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
                attempts=max(1, attempts),
            )
        )
        trace.cycle_visits[node_id] = trace.cycle_visits.get(node_id, 0) + 1
        self.callbacks.on_node_end(node_id, out, dur)
        events.append(
            self._emit(
                StreamEvent(
                    type="node_end",
                    node_id=node_id,
                    state=out.model_dump(),
                    duration_s=dur,
                    attempt=attempts,
                )
            )
        )
        events.append(StreamEvent(type="values", node_id=node_id, state=out.model_dump()))
        log.info("ran %-20s  %.3fs  loop=%s attempts=%s", node_id, dur, loop_count, attempts)
        return out, events, None, skip_successors

    def _stream_dag(
        self,
        state: State,
        layers: list[list[str]],
        trace: ExecutionTrace,
        thread_id: str | None,
        visits: dict[str, int],
    ) -> Iterator[StreamEvent]:
        remaining = [nid for layer in layers for nid in layer]
        for layer in layers:
            if self.parallel_branches and len(layer) > 1:
                yield from self._parallel_layer_events(layer, state, trace)
                # re-read last values merge
                if trace.node_traces:
                    state = State.model_validate(trace.node_traces[-1].output_state)
                    # Proper merge of parallel outputs:
                    patches = [
                        t.output_state
                        for t in trace.node_traces[-len(layer) :]
                        if t.node_id in layer
                    ]
                    merged = state
                    # start from pre-layer — reconstruct from first of layer input
                    if patches:
                        merged = State.model_validate(trace.node_traces[-len(layer)].input_state)
                        for p in patches:
                            merged = merge_state(merged, p)
                        state = merged
                        yield StreamEvent(type="values", state=state.model_dump())
            else:
                for nid in layer:
                    state, events, gi, skip = self._run_node(nid, state, trace)
                    if gi is not None:
                        trace.interrupted = True
                        trace.interrupt_value = gi.value
                        trace.interrupt_node = nid
                        next_nodes = [nid]
                        cp = self._save_cp(
                            thread_id,
                            state,
                            next_nodes,
                            dict(trace.cycle_visits),
                            pending_interrupt=gi.value,
                            interrupt_node=nid,
                        )
                        if cp:
                            trace.checkpoint_id = cp.checkpoint_id
                            events.append(
                                self._emit(
                                    StreamEvent(
                                        type="checkpoint",
                                        node_id=nid,
                                        data={"checkpoint_id": cp.checkpoint_id},
                                    )
                                )
                            )
                        for ev in events:
                            yield ev
                        return
                    for ev in events:
                        yield ev
                        if ev.type == "graph_abort":
                            return
                    remaining = [n for n in remaining if n != nid]
                    visits[nid] = visits.get(nid, 0) + 1
                    cp = self._save_cp(thread_id, state, remaining, dict(trace.cycle_visits))
                    if cp:
                        trace.checkpoint_id = cp.checkpoint_id
                        yield self._emit(
                            StreamEvent(
                                type="checkpoint",
                                node_id=nid,
                                data={"checkpoint_id": cp.checkpoint_id},
                            )
                        )
                    if state.done or skip:
                        return
        if self.checkpoint_fn:
            self.checkpoint_fn(state, trace)

    def _parallel_layer_events(
        self,
        layer: list[str],
        state: State,
        trace: ExecutionTrace,
    ) -> Iterator[StreamEvent]:
        """Run independent layer nodes in threads; each gets a deep copy; merge after."""

        def one(nid: str) -> tuple[str, State, list[StreamEvent], NodeTrace | None, Exception | None]:
            local_trace = ExecutionTrace(graph_id=self.graph.id)
            try:
                out, events, gi, _skip = self._run_node(nid, state.model_copy(deep=True), local_trace)
                if gi is not None:
                    return nid, out, events, None, gi
                nt = local_trace.node_traces[-1] if local_trace.node_traces else None
                return nid, out, events, nt, None
            except Exception as exc:
                return nid, state, [], None, exc

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(layer)) as pool:
            futs = [pool.submit(one, nid) for nid in layer]
            for fut in concurrent.futures.as_completed(futs):
                nid, out, events, nt, exc = fut.result()
                if exc:
                    raise exc
                for ev in events:
                    yield ev
                if nt:
                    trace.node_traces.append(nt)
                    trace.cycle_visits[nid] = trace.cycle_visits.get(nid, 0) + 1

    def _stream_cyclic(
        self,
        state: State,
        frontier: list[str],
        trace: ExecutionTrace,
        thread_id: str | None,
        visits: dict[str, int],
    ) -> Iterator[StreamEvent]:
        iters = 0
        while frontier and iters < self.max_graph_iterations:
            iters += 1
            node_id = frontier.pop(0)
            if trace.cycle_visits.get(node_id, 0) >= self.max_graph_iterations:
                log.warning("skip %s — visit budget exhausted", node_id)
                continue
            state, events, gi, skip = self._run_node(node_id, state, trace)
            if gi is not None:
                trace.interrupted = True
                trace.interrupt_value = gi.value
                trace.interrupt_node = node_id
                trace.graph_iterations = iters
                cp = self._save_cp(
                    thread_id,
                    state,
                    [node_id],  # re-enter interrupted node after resume
                    dict(trace.cycle_visits),
                    pending_interrupt=gi.value,
                    interrupt_node=node_id,
                )
                if cp:
                    trace.checkpoint_id = cp.checkpoint_id
                    events.append(
                        self._emit(
                            StreamEvent(
                                type="checkpoint",
                                node_id=node_id,
                                data={"checkpoint_id": cp.checkpoint_id},
                            )
                        )
                    )
                for ev in events:
                    yield ev
                return
            for ev in events:
                yield ev
                if ev.type == "graph_abort":
                    return
            if state.done:
                break
            if not skip:
                for nxt in self.graph.successors(node_id, state):
                    if trace.cycle_visits.get(nxt, 0) < self.max_graph_iterations:
                        frontier.append(nxt)
            cp = self._save_cp(thread_id, state, list(frontier), dict(trace.cycle_visits))
            if cp:
                trace.checkpoint_id = cp.checkpoint_id
                yield self._emit(
                    StreamEvent(
                        type="checkpoint",
                        node_id=node_id,
                        data={"checkpoint_id": cp.checkpoint_id},
                    )
                )
        trace.graph_iterations = iters
        if frontier and not state.done:
            log.warning(
                "stopped after max_graph_iterations=%s (frontier left=%s)",
                self.max_graph_iterations,
                frontier,
            )
        if self.checkpoint_fn:
            self.checkpoint_fn(state, trace)
