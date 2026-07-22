# DizzyGraph

**Graphs made of loops. Graphs that loop. Loops over those graphs.**

This is the July 2026 framing in code: Steinberger’s “loops → graphs” shift, and Pau Labarta Bajo’s nested version — *looping over graphs that are made of loops*.

See: [`docs/RUNBOOK-DIZZYGRAPH.md`](../docs/RUNBOOK-DIZZYGRAPH.md) for live key setup, Console verification, and a captured real run.

## Architecture

| Piece | Role |
|-------|------|
| `AtomicNode` | One step: `State → State` |
| `LoopNode` | **Loop engineering inside a node** — maker + optional checker + `max_iters` / stop |
| `SubGraphNode` | **Graph inside a node** — hierarchy / recursion (depth-limited) |
| `AgentNode` | LLM-ready step (mock by default; swap `llm_fn` for litellm/OpenAI) |
| `Edge` | Conditional transitions (enables **graph-level cycles**) |
| `GraphExecutor` | DAG topo *or* cyclic frontier walk with visit budgets + traces |
| `MetaLoopExecutor` | **Meta-loop** — run the whole graph N times with updater / convergence / aggregator |

```
MetaLoop ──► GraphExecutor ──► nodes
                 │
                 ├─ LoopNode (inner loop engineering)
                 ├─ SubGraphNode → nested GraphExecutor
                 └─ feedback Edge (graph cycles)
```

That nesting is intentional. It feels “dizzy” until the names click — then you get momentum: every layer has the same verbs (`run`, `State`, `trace`).

## Run demos (no API keys)

```bash
python examples/demo_refinement.py   # LoopNode + graph cycle + meta-loop
python examples/demo_agent.py        # AgentNode + LoopNode + SubGraphNode + meta
python trinity_dizzy.py --mock --meta 3 --viz
```

## Trinity

| Command | Engine |
|---------|--------|
| `python app.py "..."` | LangGraph (existing XL drills) |
| `python trinity_dizzy.py "..."` | DizzyGraph + live Trinity nodes |
| `python trinity_dizzy.py --mock` | Same topology, offline |

## Extension

```python
from dizzygraph import AtomicNode, register_node_type, Node, State

@register_node_type("my_node")
class MyNode(Node):
    def run(self, state: State) -> State:
        ...
```

- Persist skeleton: `save_graph` / `load_graph` (re-bind callables after load).
- Checkpoints: pass `checkpoint_fn` into `GraphExecutor`.
- Real LLM: set `AgentNode(llm_fn=...)` — see `examples/demo_agent.py`.

## Why this gives momentum

Loop engineering alone is a single while-loop with a gate. Graph engineering alone is a flowchart. Together — and with a meta-loop — you get **controllable recursion**: refine inside a node, branch across the graph, then improve the *whole run* across meta-iterations. Same `State` object the whole way down. That is the unstoppable part.
