# Trinity Stack — AI-Platform Engineering Assistant

> **LangGraph builds it. Fleet telemetry keeps it alive. Galileo makes it trustworthy.**

A production-grade autonomous AI agent — the kind a frontier AI org (Meta / OpenAI / NVIDIA) runs internally — **broken 6 ways** with complete observability across three layers. Every failure mode has a working drill, real measured metrics from a live run, and a copy-pasteable runbook.

**No mocks. No simulation. No toy domains.**
Real embedding retrieval, real tools, real Galileo Protect, real psutil telemetry. Volume is kept small for cost; nothing is faked.

---

## The Thesis

A loop is only as good as its gate.

For two years the leverage in AI was at the **prompt**. That era is closing. The leverage moved one floor up — to the **loop**: a system that finds the work, hands it to the agent, **checks the result**, and decides the next move on its own. You design the loop once; it prompts the agent from then on.

But a loop you can't trust isn't leverage. It's a faster way to ship work nobody reviewed.

Of the six failure modes in this repo, **fleet/infrastructure monitoring caught 1**. Galileo's trust layer caught **6 of 6** — including the one where fleet showed *improvement* while quality quietly died.

This repo is the evidence.

---

## Architecture

```
Engineer question
  → [INTAKE]     topic routing (training / inference / infra)
  → [RETRIEVER]  dense embedding retrieval (OpenAI text-embedding-3-small + cosine index)
  → [TOOLS]      real execution (sandboxed Python + semantic corpus search)
  → [RESPONDER]  grounded answer (gpt-4o-mini)
  → [PROTECT]    Galileo quality gate  ← the loop's gate
  → answer
```

**Three layers — each watching a different thing:**

```
            ┌──────────────────────────────────────────────────────────┐
            │       LangGraph (AI-Platform Engineering Assistant)      │  BUILD
            │  intake → retriever → tools → responder → protect        │
            └──────────────┬─────────────────────┬──────────────────── ┘
    process/fleet signals  │                     │ every trace, span, output
                           ▼                     ▼
            ┌─────────────────────────┐  ┌──────────────────────────────────┐
            │  Fleet Telemetry        │  │  Galileo                         │
            │  (RUN layer)            │  │  (TRUST layer)                   │
            │                         │  │                                  │
            │  • heartbeat liveness   │  │  • context_adherence             │
            │  • p99 latency          │  │  • completeness                  │
            │  • process alive?       │  │  • cites_kb_source               │
            │  • throughput           │  │  • routing_accuracy              │
            │                         │  │  • Luna-2: 100% traffic          │
            │  ✅ Catches: 1/6 drills │  │  • Insights: cluster failures    │
            │  ❌ Blind to: wrong     │  │  • Protect: block at runtime     │
            │     answers, corpus     │  │                                  │
            │     mismatch, quality   │  │  ✅ Catches: 6/6 drills          │
            │     regressions         │  │  ❌ Blind to: process health     │
            └─────────────────────────┘  └──────────────────────────────────┘
```

---

## The 6 Failure Modes (with real numbers)

*All metrics captured on a live run — 2026-06-14. Galileo project: `rax-galileo-labs`, stream: `trinity-stack`.*

| # | Drill | What broke | Fleet sees | Galileo sees | Layer |
|---|-------|-----------|-----------|-------------|-------|
| XL-1 | `xl1_process_dead.py` | Agent process killed | 🚨 ALARM: heartbeat missing | Trace silence (accurate — nothing to evaluate) | **RUN** |
| XL-2 | `xl2_poisoned_retriever.py` | Wrong knowledge base | ✅ ALL GREEN | context_adherence 0.90 → 0.05 🚨 (-90%) | **TRUST** |
| XL-3 | `xl3_langgraph_misroute.py` | LangGraph router broken | ✅ ALL GREEN | Uniform span paths; routing_accuracy 1.0 → 0.33 | **BUILD-via-TRUST** |
| XL-4 | `xl4_eval_to_protect.py` | Hallucination-prone prompt | ✅ ALL GREEN | context_adherence < 0.5; Protect gate live | **TRUST** |
| XL-5 | `xl5_slow_tool.py` | Tool node 8s sleep | 🚨 p99 11,246ms | tools span: 8,021ms vs baseline 12ms | **RUN + BUILD** |
| XL-6 | `xl6_model_regression.py` | Silent quality regression | ✅ IMPROVED (↓ latency) | context_adherence 0.86 → 0.64 in 8 queries | **TRUST** |

### XL-6 is the killer

Fleet showed *lower* latency (2,431ms → 2,352ms). The on-call engineer got a positive signal. Meanwhile:

| Metric | Baseline | "Optimized" config | Delta |
|--------|---------|--------------------|-------|
| context_adherence | 0.86 | 0.64 | 🚨 −27% |
| KB citations | 75% | 38% | 🚨 −38% |
| Avg response words | 76 | 51 | −33% |

**Galileo's Luna-2 at 100% traffic:** trend visible after **8 queries**.  
**5% sampling:** would need **~160 queries** for statistical significance.

---

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/pandeyaby/Galileo.git
cd Galileo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
export OPENAI_API_KEY="sk-..."       # embeddings (text-embedding-3-small) + LLM (gpt-4o-mini)
export GALILEO_API_KEY="..."          # app.galileo.ai → Settings → API Keys
```

**Create a Galileo project** called `rax-galileo-labs` with a log stream named `trinity-stack` before running — or edit the `PROJECT` and `LOG_STREAM` constants at the top of `app.py`.

### 3. Run the baseline

```bash
python app.py --batch
```

This runs 10 real engineering queries against the ML-infra corpus, logs all traces to Galileo, and prints per-query results. Check `https://app.galileo.ai` → `rax-galileo-labs` → `trinity-stack` to see the live metrics.

### 4. Run all 6 drills

```bash
# Each drill injects one failure mode, observes both layers, then recovers
python drills/xl1_process_dead.py
python drills/xl2_poisoned_retriever.py
python drills/xl3_langgraph_misroute.py
python drills/xl4_eval_to_protect.py
python drills/xl5_slow_tool.py
python drills/xl6_model_regression.py

# Safety: restore the real corpus if XL-2 left it poisoned
python app.py --restore-corpus
```

**Total LLM cost for all 6 drills: < $0.15** (small corpus, gpt-4o-mini).

---

## File Structure

```
trinity-stack/
├── app.py                           # LangGraph agent — the full stack
├── knowledge_base.json              # Real ML-infra engineering corpus
├── requirements.txt
│
├── drills/
│   ├── xl1_process_dead.py          # RUN: kill the process
│   ├── xl2_poisoned_retriever.py    # TRUST: the money demo
│   ├── xl3_langgraph_misroute.py    # BUILD-via-TRUST: trace path fingerprint
│   ├── xl4_eval_to_protect.py       # TRUST: eval → guardrail lifecycle
│   ├── xl5_slow_tool.py             # RUN + BUILD: two instruments, one truth
│   └── xl6_model_regression.py      # TRUST: Luna-2 100% vs 5% sampling
│
├── fleet/
│   └── monitor.py                   # RUN-layer telemetry (psutil + heartbeat)
│
└── article/
    ├── medium-full.md               # Full article (~2,800 words)
    ├── linkedin-post.md             # LinkedIn version
    ├── x-thread.md                  # 14-tweet X thread
    └── results/                     # Real drill output from live run 2026-06-14
        ├── baseline.txt
        ├── xl1.txt … xl6.txt
```

---

## What's in `app.py`

The agent is a **LangGraph multi-node graph** with five nodes:

| Node | What it does | Production-grade component |
|------|-------------|---------------------------|
| `intake_node` | Routes by intent (training / inference / infra / general) | Keyword + embedding similarity routing |
| `retriever_node` | Dense retrieval from engineering corpus | OpenAI `text-embedding-3-small` + cosine vector index (cached on disk) |
| `tools_node` | Tool dispatch | Real sandboxed Python execution (subprocess) + real semantic corpus search |
| `responder_node` | Generates grounded answer | gpt-4o-mini with retrieval-grounded system prompt |
| `protect_node` | Quality gate | `galileo.invoke_protect()` against a live Protect stage + Ruleset |

**Metrics on every trace (Galileo Luna-2 judges):**
- `context_adherence` — claims grounded in retrieved docs?
- `completeness` — fully addresses the question?
- `cites_kb_source` — cites a specific KB entry?
- `routing_accuracy` — reached the correct intent node? (added for XL-3)

**Fleet telemetry (`fleet/monitor.py`):**
- Real `psutil` measurements: CPU%, RSS memory, process uptime
- Measured latency per query
- Heartbeat file (simulates ClawTrace / OTel process-health check)

---

## The Drills In Detail

### XL-1 — Process Dead (FM-50)
**Inject:** Delete the heartbeat file (simulates process kill).  
**Fleet:** 🚨 `ALARM:MISSING` immediately.  
**Galileo:** Trace silence. No error metrics — nothing to evaluate when nothing runs.  
**The rule:** *Trace silence + fleet alarm = RUN layer. Check your process before your SDK.*

### XL-2 — Poisoned Retriever (FM-51) — *the money demo*
**Inject:** Swap the engineering corpus with off-domain docs (meal kits, yoga, plumbing).  
**Fleet:** ✅ ALL GREEN throughout. Process alive, latency normal (poison retrieval is as fast as good retrieval).  
**Galileo:**
- context_adherence: **0.85–0.95 → 0.00–0.10** (−90%)
- cites_kb_source: **0.90–1.00 → 0.00–0.05** (−95%)
- Embedding cosine similarity: baseline 0.64–0.82 → poisoned **0.03–0.12** (off-domain by 10x)

**Detection time without Galileo:** never (someone files a ticket eventually).  
**Detection time with Galileo:** ~60 seconds after first poisoned query.

This is the most expensive class of AI failure in production: a healthy process, confident-sounding answers, and zero infrastructure alarms. Only semantic evaluation catches it.

### XL-3 — LangGraph Misrouting (FM-52)
**Inject:** `intake_node` returns `intent='infra'` for ALL queries.  
**Fleet:** ✅ healthy.  
**Galileo:** Every trace has the **identical span path**. Normal routing has variety; a routing bug creates uniformity. routing_accuracy: 1.0 → 0.33 (only the 2 genuine infra queries landed correctly; 4/6 misrouted).  
**The pattern:** Uniform span paths across diverse queries = routing bug. Fix: `intake_node` logic.

### XL-4 — Eval → Protect Lifecycle (FM-53)
**Inject:** Hallucination-prone system prompt (removes grounding instruction).  
**Dev eval:** context_adherence flags multiple queries below 0.40–0.50. Dev decision: *DO NOT SHIP.*  
**Prod without Protect:** Bad answers reach engineers. Fleet healthy. Zero alerts.  
**Prod with Protect:** `invoke_protect()` runs on every response. Rule: `context_adherence < 0.5 → block`. The gate evaluates every response with the same metric that flagged the problem in dev.

**The differentiator:** The same `context_adherence` metric that flags a failure in dev *becomes* the production Protect threshold. One metric, three jobs (dev eval → prod gate → regression check), zero glue code.

### XL-5 — Slow Tool Node (FM-54)
**Inject:** 8-second sleep in the corpus-search tool.  
**Fleet:** 🚨 p99 11,246ms (baseline avg: 3,835ms). *"Something is slow."*  
**Galileo:** `tools` span: **8,021ms vs baseline 12ms** — that node, right there.

```
Span          Duration    Status
──────────────────────────────────────────────────
intake        2ms         ✅ normal
retriever     8ms         ✅ normal
tools         8,021ms     🐌 ANOMALY +8000ms  ← ROOT CAUSE
responder     1,340ms     ✅ normal
protect       5ms         ✅ normal
──────────────────────────────────────────────────
TOTAL         ~9,376ms    🚨
```

**Fleet says the building is on fire. Galileo shows you which room.**  
Time to root cause: manual log diving ~30 min → fleet + Galileo spans ~30 sec.

### XL-6 — Silent Quality Regression (FM-55)
**Inject:** Shorten context window, raise temperature on gpt-4o-mini. *"Same model, should be fine."*  
**Fleet:** ✅ LATENCY **IMPROVED** (2,431ms → 2,352ms). On-call gets a positive signal.  
**Galileo (100% traffic evaluation):**

| Metric | Baseline | Degraded | Delta |
|--------|---------|---------|-------|
| context_adherence | **0.86** | **0.64** | 🚨 −27% |
| KB citations | **75%** | **38%** | 🚨 −38% |
| Avg response words | 76 | 51 | −33% |

**Luna-2 at 100% traffic:** regression visible after **8 queries**.  
**5% sampling:** needs **~160 queries** for the same statistical confidence.  
**First engineer complaint:** after ~50–100 bad interactions.

Luna-2 distilled judges are ~96% cheaper than GPT-4-as-judge, which is what makes 100%-of-traffic scoring economically viable. This is the unlock: you catch regressions before engineers see them, not after.

---

## The Complete Triage Table

Use this when something goes wrong.

| Symptom | Fleet | Galileo traces | Galileo metrics | Layer | First action |
|---------|-------|----------------|-----------------|-------|-------------|
| Traces stop suddenly + fleet alarm | 🚨 | Stopped | N/A | **RUN** | Restart process; check heartbeat |
| All traces show identical span path | ✅ | Running | routing_accuracy low | **BUILD** | Inspect intake/router node logic |
| Normal paths, adherence craters | ✅ | Running | 🚨 adherence crash | **TRUST** | Check retrieval corpus + KB version |
| Completeness high, adherence low | ✅ | Running | 🚨 adherence only | **TRUST** | Hallucination — deploy Protect rule |
| p99 latency spike | 🚨 | Running | Normal | **RUN → BUILD** | Open Galileo trace → find slow span |
| Fleet IMPROVED, quality declining | ✅ "positive" | Running | 🚨 trending down | **TRUST** | Rollback model/config; run experiment |

---

## Runbooks

Each drill generates a runbook. Find them in the [galileo-troubleshooter skill](https://github.com/pandeyaby/Galileo) `runbooks/` directory:

| Runbook | Covers |
|---------|--------|
| `RB-110-agent-process-dead.md` | XL-1: trace silence + fleet alarm |
| `RB-120-retriever-corpus-mismatch.md` | XL-2: poisoned retriever |
| `RB-130-langgraph-misrouting.md` | XL-3: router bug fingerprint |
| `RB-140-hallucination-protect-rule.md` | XL-4: eval → Protect lifecycle |
| `RB-150-slow-tool-span.md` | XL-5: slow tool localization |
| `RB-160-silent-quality-regression.md` | XL-6: Luna-2 regression detection |

---

## Requirements

```
galileo>=2.3.0
langgraph>=1.2.5
langchain-openai>=1.3.0
langchain-core>=1.4.0
numpy>=2.0
psutil>=6.0
openai>=1.0
```

See `requirements.txt`.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | ✅ | OpenAI API key (embeddings + LLM) |
| `GALILEO_API_KEY` | ✅ | Galileo API key — `app.galileo.ai` → Settings → API Keys |

If you're running inside an OpenClaw workspace with the Galileo MCP server configured, the app auto-loads `GALILEO_API_KEY` from `openclaw.json` — no manual export needed.

---

## Galileo Setup

1. Create an account at [app.galileo.ai](https://app.galileo.ai)
2. Create a project named `rax-galileo-labs` (or edit `PROJECT` in `app.py`)
3. Create a log stream named `trinity-stack` (or edit `LOG_STREAM`)
4. For XL-4 (Protect drill): create a Protect Stage in the Console before running — the app will attempt to create the Ruleset via API, but stage creation requires the Console

---

## The Loop-Engineering Context

This repo is a companion to the article:  
**"A Loop Is Only as Good as Its Gate"** — [read it on Medium](https://medium.com/@abhinavpandey) *(link coming)*

The thesis, in one paragraph: the leverage in AI moved from prompting to loop design. A loop needs a gate — an objective verifier that runs without the author's optimism. Galileo *is* that gate: the eval metric that flags a problem in dev becomes the Protect rule in prod becomes the regression check that locks it permanently. Every failure you debug makes the loop harder to break. That's the self-repairing harness.

---

## Author

**Abhinav Pandey**  
Cisco Splunk | AI Observability + Autonomous Systems  
Building at the intersection of enterprise infrastructure and the agent era.

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-blue)](https://linkedin.com/in/abhinavpandey)

---

*All code in this repo is working and reproducible. Total LLM cost to run the full baseline + all 6 drills: < $0.15.*
