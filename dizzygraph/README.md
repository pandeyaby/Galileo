# DizzyGraph

**Graphs made of loops. Graphs that loop. Loops over those graphs.**

A small runtime for loop engineering — **not** a LangGraph clone.
`LoopNode` and `MetaLoopExecutor` are first-class. Checkpoints, streaming, HITL, reducers,
retries, Mermaid, and (v0.5+) a multi-agent **control plane** with admission control,
fail policies, and real health/ready probes are real.

```bash
pip install -e ".[dev,viz]"
pytest
python examples/demo_hitl.py
python examples/demo_refinement.py
python trinity_dizzy.py --mock --mermaid
```

## Control plane (fleet ops) — v0.5+

Many `thread_id`s → SQLite/Postgres checkpointer → event bus (optional Redis) → UI
(graph + path overlay + state + metrics + alerts) + supervisor fan-out + API-key tenants.

```bash
pip install -e ".[control,dev]"
# optional backends: pip install -e ".[control-full]"
python -m dizzygraph.control --demo 8 --fanout 4
# → http://127.0.0.1:8787

# Live Trinity fleet (requires OPENAI_API_KEY; fails loudly if missing — no mock)
python -m dizzygraph.control --trinity 4 --port 8800
```

**Ops endpoints:** `/api/health` (deps + admission), `/api/livez`, `/api/readyz`,
`/api/metrics/prometheus`. Backpressure via `DIZZY_MAX_INFLIGHT` (HTTP 429 when saturated).

Multi-worker topology (Postgres + Redis) and honest gaps:
[`docs/RUNBOOK-MULTI-WORKER.md`](../docs/RUNBOOK-MULTI-WORKER.md).

Goals / DoD: [`control/GOALS.md`](control/GOALS.md).  
Galileo × DizzyGraph use cases: [`docs/GALILEO-DIZZYGRAPH-USE-CASES.md`](../docs/GALILEO-DIZZYGRAPH-USE-CASES.md).  
Live Trinity runbook: [`docs/RUNBOOK-DIZZYGRAPH.md`](../docs/RUNBOOK-DIZZYGRAPH.md).

### Galileo use cases (shipped)

| Use case | Notes |
|----------|--------|
| Protect as `LoopNode` checker | Live responder loop; non-converge → `loop_non_converge` |
| XL drill fan-out | Supervisor children XL-1..XL-6; parent aggregates Protect |
| Silent regression meta-loop | `MetaLoopExecutor` + fleet metrics / Protect trend |
| HITL after Protect trigger | `interrupt()` → resume via UI / `POST /api/runs/{id}/resume` |
| Tenant ↔ Galileo project/stream | `DIZZY_TENANT_GALILEO` / `X-Tenant-Id` |
| Path ↔ span correlation | `otel.span_name=dizzygraph.<node>` on events + optional OTel SDK exporter (`dizzygraph.otel`) + tenant-mapped Galileo flush |

Third-party starters (fail-loud, no mock success): [`examples/integrations/`](../examples/integrations/).

**DizzyGraph OTel exporter:** `pip install 'dizzygraph[otel]'`. Auto-attaches on fleet runs when `GALILEO_API_KEY` is set (disable with `DIZZY_OTEL=0`).

---

## Why it exists

Most agent graphs treat “retry until good” as an afterthought edge. DizzyGraph makes the
**loop** the unit of composition:

| Layer | Primitive | Job |
|-------|-----------|-----|
| Inner | `LoopNode` | maker + checker + max_iters inside one node |
| Graph | `Graph` + edges | DAGs **or** cycles with visit budgets |
| Nested | `SubGraphNode` | graphs inside nodes (inherits checkpointer when nested) |
| Outer | `MetaLoopExecutor` | loop over whole graph runs |
| Trust | your gate | Protect / human `interrupt()` / scorer |
| Fleet | `dizzygraph.control` | many agents, alerts, metrics, tenants, admission |

## Quick start

```python
from dizzygraph import AtomicNode, FailPolicy, Graph, LoopNode, MemoryCheckpointer, State, interrupt

def draft(s):
    return {"data": {"text": "v1"}}

def refine(s):
    q = float(s.data.get("quality") or 0) + 0.5
    return {"data": {"quality": q, "text": f"v{q}"}}

def approve(s):
    if not s.data.get("ok"):
        interrupt({"draft": s.data.get("text")})
    return {"done": True}

g = Graph(id="demo")
g.add_node(AtomicNode(id="draft", fn=draft))
g.add_node(LoopNode(id="refine", maker=refine, checker=lambda s: s.data["quality"],
                    max_iters=4, score_threshold=0.9))
g.add_node(AtomicNode(id="gate", fn=approve))
g.set_entry("draft").add_edge("draft", "refine").add_edge("refine", "gate")

app = g.compile(checkpointer=MemoryCheckpointer(), fail_policy=FailPolicy.CONTINUE)
print(app.get_config())
print(app.to_mermaid())

t1 = app.invoke(State(), thread_id="t1")          # interrupts at gate
t2 = app.resume(thread_id="t1", update={"data": {"ok": True}})
```

## Runtime contract (honest)

| Feature | Behavior |
|---------|----------|
| **Reducers** | `messages`/`results` append; `data.*` **replace** by default. Register `unique_append` etc. when you need it. |
| **Checkpoints** | After every node when `checkpointer` + `thread_id` are set. Memory / File; control plane adds SQLite/Postgres with history `list`/`clear`. |
| **Stream** | `app.stream()` → `graph_start` / `node_*` / `checkpoint` / `interrupt` / `graph_abort` / `graph_end`. |
| **HITL** | `interrupt(value)` inside a node → pause → `resume(update=...)`. Re-enters the interrupted node. |
| **Fail policies** | `abort` (legacy `fail_fast=True`), `continue` (default), `skip` successors. |
| **Retries / timeouts** | `RetryPolicy` on a node or `default_retry` on compile; real `timeout_s`. |
| **Observability** | Callbacks + optional `on_event` hook; fleet metrics + Prometheus text; optional OTel. |
| **Fleet backpressure** | `AdmissionController` / `DIZZY_MAX_INFLIGHT` → HTTP 429 when saturated. |
| **Viz / persist** | `to_mermaid`; skeleton JSON does not round-trip callables. |

## Production-oriented — what this is / isn’t

**Shipped and useful for lab → small production fleets**

- Loop-first graphs with durable checkpoints and HITL
- Fail policies, compile `get_config()`, nested checkpointer inheritance
- Control-plane health/ready, admission, metrics rollup + Prometheus scrape text
- Postgres + Redis scaffolding for multi-worker **state + event fan-out**

**Still not LangGraph / still incomplete**

- No Pregel channel scheduler or LangChain ecosystem surface
- No global run leases (duplicate `thread_id` starts can race across workers)
- Postgres store is single-connection (not a pooled high-QPS backend yet)
- Auth defaults **off** for local demos — turn on `DIZZY_AUTH_REQUIRED=1` outside demos
- Live Trinity paths **never mock**; offline topology uses `--mock` / `--demo` only

If you need Pregel + full LangChain production surface — use LangGraph.
If you want **named loop layers**, a readable Mermaid story, a small control plane, and a runtime you can finish reading in an afternoon — use DizzyGraph.

## Architecture

```
MetaLoop ──► CompiledGraph.invoke/stream
                 │
                 ├─ LoopNode (inner loop engineering)
                 ├─ MapNode  (fan-out over items)
                 ├─ SubGraphNode → nested executor
                 └─ feedback Edge (graph cycles)

Fleet ──► control plane (store + bus + UI + supervisor + admission)
```

## Trinity

| Command | Notes |
|---------|--------|
| `python trinity_dizzy.py "..."` | Live DizzyGraph Trinity — Protect as LoopNode checker, then Protect+HITL gate |
| `python trinity_dizzy.py --mock` | Same topology, offline local checker only |
| `python -m dizzygraph.control --trinity N` | Live Trinity agents in the fleet UI (keys required) |
| `python app.py "..."` | Original LangGraph Trinity / XL drills |

Corpus: lab-scale ML-platform KB (~1000 chunks) via `corpus/generate_ml_corpus.py` — see `corpus/STATS.md`.
