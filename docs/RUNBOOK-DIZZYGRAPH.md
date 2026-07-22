# Implementation Runbook — DizzyGraph × Trinity (live)

**Purpose:** Run the Trinity engineering assistant on **DizzyGraph** with real embeddings, real LLM calls, real Protect, and real Galileo traces — then verify Console evidence.

**Engines**

| Entry | Runtime | When to use |
|-------|---------|-------------|
| `python app.py "..."` | LangGraph | XL drills / original baseline |
| `python trinity_dizzy.py "..."` | DizzyGraph | Loop/graph + meta-loop path |
| `python trinity_dizzy.py --mock` | DizzyGraph offline | CI / no keys |

---

## 0. Prerequisites

```bash
cd /path/to/Galileo-public   # this repo
python3 -m venv .venv && source .venv/bin/activate   # recommended
pip install -r requirements.txt
# If system Python blocks installs: pip install --break-system-packages -r requirements.txt
```

**Keys (never commit):**

| Variable | Source |
|----------|--------|
| `OPENAI_API_KEY` | `~/.openclaw/workspace/galileo-labs/.env` (or export in shell) |
| `GALILEO_API_KEY` | OpenClaw `mcp.servers.galileo` header, or export |

`trinity_dizzy.py` loads both automatically via `load_runtime_keys()`.

**Optional viz:**

```bash
pip install networkx matplotlib
```

---

## 1. Smoke (offline)

```bash
pip install -e ".[dev,viz]"
pytest
python examples/demo_hitl.py
python examples/demo_refinement.py
python examples/demo_agent.py
python trinity_dizzy.py --mock --viz --mermaid --write-evidence
```

Expect: pytest green, HITL interrupt→resume, node trace printed, Mermaid flowchart, optional `dizzygraph_out/trinity_dizzy.png`.

**Runtime notes (v0.4+):**

- Live **responder is a `LoopNode`** whose **checker is live Galileo Protect**
  (`invoke_protect` / LLM-judge fallback) — not a keyword heuristic.
  Non-convergence → fleet `loop_non_converge` with Protect status/score/path.
- When Protect triggers on the final gate, `interrupt()` pauses for HITL
  approve/edit; `POST /api/runs/{id}/resume` continues.
- Mock (`--mock`) still uses a cheap local checker for offline topology only.
- `data.*` channels **replace** by default (no silent `doc_ids` concat).
- Real per-node checkpoints when you pass `checkpointer=` + `thread_id`.
- `interrupt()` / `resume()` for human gates; `to_mermaid()` for docs.
- Control plane: `python -m dizzygraph.control` (default port **8787**). Live Trinity fleet: `--trinity N` (keys required, no mock). Use-case status: [`GALILEO-DIZZYGRAPH-USE-CASES.md`](GALILEO-DIZZYGRAPH-USE-CASES.md).

---

## 2. Live single run (real data)

```bash
python trinity_dizzy.py --viz --write-evidence \
  "How do I debug a CUDA out-of-memory error during training?"
```

**What is real**

- Dense retrieval: OpenAI `text-embedding-3-small` + cosine index over the engineering KB  
- Responder: `gpt-4o-mini`  
- Protect: `invoke_protect` (or LLM-judge fallback if Protect metric unavailable)  
- Telemetry path: fleet helpers still available from `app.py` / `fleet/`  
- Trust: GalileoLogger flush → project `rax-galileo-labs` / stream `trinity-dizzy`

**Pass criteria**

- Printed `── answer ──` is grounded and cites KB-style content  
- `doc_ids` non-empty; `context_score` > 0  
- `protect_status` in `{not_triggered, triggered}`  
- `── galileo ── rax-galileo-labs/trinity-dizzy` printed  
- Console: [app.galileo.ai](https://app.galileo.ai) → `rax-galileo-labs` → `trinity-dizzy` shows a new trace tagged `trinity-dizzy` / `dizzygraph`

Evidence file: `dizzygraph_out/last_live_run.json` (gitignored).

---

## 3. Meta-loop (loop over the whole Trinity graph)

```bash
python trinity_dizzy.py --meta 2 --write-evidence \
  "How does vLLM PagedAttention improve inference throughput?"
```

Expect: `── meta ──` with `n_runs: 2`, two graph executions, final answer from last (or converged) state, one Galileo trace for the final flush (current logger wraps the final state).

---

## 4. Compare LangGraph vs DizzyGraph

```bash
# LangGraph (original stream: trinity-stack)
python app.py "How do I debug a CUDA out-of-memory error during training?"

# DizzyGraph (stream: trinity-dizzy)
python trinity_dizzy.py --write-evidence \
  "How do I debug a CUDA out-of-memory error during training?"
```

Compare answers and Protect signals; streams differ so Console filters stay clean.

---

## 5. Topology (graphs made of loops)

Live graph (default):

`intake → retriever → tools → responder → protect`

Mock graph uses a **LoopNode** on `responder` (cheap local checker) for offline
topology only. Live Trinity uses **Protect as the LoopNode checker**, then a
Protect+HITL gate node.

Standalone demos:

- `examples/demo_refinement.py` — LoopNode + graph-level feedback edge + MetaLoopExecutor  
- `examples/demo_agent.py` — AgentNode + LoopNode + SubGraphNode + meta  

Package docs: [`dizzygraph/README.md`](../dizzygraph/README.md) · control goals: [`dizzygraph/control/GOALS.md`](../dizzygraph/control/GOALS.md)

### Control plane (optional)

```bash
pip install -e ".[control,dev]"
python -m dizzygraph.control --demo 8 --fanout 4          # http://127.0.0.1:8787
python -m dizzygraph.control --trinity 4 --port 8800      # live Trinity agents
```

UI: fleet list, Mermaid + path overlay, state, metrics (lag / fail / loop / stuck), alerts.
`--trinity` and `POST /api/trinity/*` are **live-only** (400 / exit if keys missing).

---

## 6. Failure triage

| Symptom | Fix |
|---------|-----|
| `OPENAI_API_KEY missing` | Export key or fix lab `.env`; or `--mock` |
| Embeddings / ChatOpenAI auth error | Rotate OpenAI key; confirm no quotes/spaces in `.env` |
| Galileo flush auth error | Confirm `GALILEO_API_KEY` via OpenClaw or env |
| Empty Console on `trinity-stack` | Wrong stream — Dizzy live uses **`trinity-dizzy`** |
| Protect always `llm_judge_fallback` | Expected on tiers without Protect LLM metric; judge path is still real |
| `networkx/matplotlib not installed` | Optional; install for PNG viz |

---

## 7. Definition of done

- [ ] Offline demos pass  
- [ ] Live query returns grounded answer + `doc_ids` / `context_score`  
- [ ] Trace visible under `rax-galileo-labs` / `trinity-dizzy`  
- [ ] `dizzygraph_out/last_live_run.json` written for the live run  
- [ ] Meta-loop (`--meta 2`) completes without crash  

---

## 9. Verified live run (evidence)

Captured from this environment (keys present; no secrets in the artifact):

| Field | Value |
|-------|-------|
| Query | How do I debug a CUDA out-of-memory error during training? |
| Nodes | intake → retriever → tools → responder → protect |
| Latency | ~8.0s |
| Intent | `training` |
| Context score | `1.0` |
| Protect | `not_triggered` via `llm_judge_fallback` |
| Galileo | `rax-galileo-labs` / `trinity-dizzy` |
| Answer | Grounded CUDA OOM guidance with `[KB-1]` cite |
| Artifact | `dizzygraph_out/last_live_run.json` (gitignored) |
| Graph PNG | `dizzygraph_out/trinity_dizzy.png` (gitignored) |

HTTP confirmed during the run: OpenAI embeddings + chat 200, Galileo protect/invoke + ingest traces 200.

**Note:** The first evidence JSON showed repeated `doc_ids` because state merges concatenated lists when wrappers re-spread prior `data`. Wrappers now return **patches only**; expect unique ids like `tr1`, `in3`, `tr2` on the next live run.
