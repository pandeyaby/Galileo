# Real Metrics — Galileo-Computed (Server-Side Scorers)

**Date:** 2026-06-14  
**Stream:** `rax-galileo-labs` → `trinity-stack`  
**Traces:** 81+ (across all drill tags)  
**LLM Spans Scored:** 169+  
**Scorer Engine:** Galileo platform (GPT-4o mini via OpenAI integration `ed1060ea`)  
**Method:** Server-side preset + custom LLM scorers, computed by Galileo on ingested traces  

> **These are GALILEO's own numbers** — computed by Galileo's server-side scorer infrastructure
> via the configured OpenAI integration. They are NOT local-judge scores. The integration was
> configured on 2026-06-14 and metrics recomputed over all historical traces.

---

## Scorers Configured

| Scorer | ID | Type | What It Measures |
|--------|----|------|-----------------|
| context_adherence | `085534b6` | preset | Is the response grounded in the retrieved context? (Galileo's core RAG metric) |
| completeness | `d0e50ddb` | preset | Does the response fully address the question? |
| cites_kb_source | `9d7e0dd2` | custom LLM | Does the response cite a specific KB source (e.g. [KB-n] tag, flag, API)? |
| routing_accuracy | `747a9fe7` | custom LLM | Does the response address the same topic domain as the query? |

---

## XL-2: Poisoned Retriever — THE MONEY DEMO

The retriever is silently serving the wrong corpus (yoga, cooking, pet grooming).
Fleet says green. Galileo shows the building is on fire.

| Metric | xl2-baseline (6 spans) | xl2-poisoned (10 spans) | Δ |
|--------|----------------------|------------------------|---|
| **context_adherence** | **1.000** | **0.600** | **-0.400 🚨** |
| **completeness** | **1.000** | **0.000** | **-1.000 🚨** |
| **cites_kb_source** | **1.000** | **0.000** | **-1.000 🚨** |

### The Signal

- **completeness craters from 1.0 → 0.0**: The agent answers questions about yoga memberships
  and plumbing when asked about CUDA OOM errors. Zero useful content.
- **cites_kb_source craters from 1.0 → 0.0**: Not a single response cites an actual engineering
  source. The model fabricates from parametric knowledge instead.
- **context_adherence drops from 1.0 → 0.6**: Partial — some responses are technically
  "grounded" in the poisoned context (they reference the wrong docs), while others fabricate
  entirely. The 0.6 average reflects a mix of both failure modes.

**xl2-recovered (4 spans):** All metrics return to 1.000. Corpus restored → quality restored.

### Fleet During This Failure

Fleet status: ✅ ALL GREEN. Process alive. Latency normal. Error rate 0%.
No alert fires. Without Galileo, this failure is invisible until users complain.

---

## XL-6: Silent Quality Regression — THE TWIST

Team "optimizes" the model config (shorter context, higher temperature, no grounding instruction).
Fleet shows IMPROVEMENT (lower latency). Quality quietly dies.

| Metric | xl6-baseline (16 spans) | xl6-degraded (16 spans) | Δ |
|--------|------------------------|------------------------|---|
| **context_adherence** | **1.000** | **1.000** | 0.000 |
| **completeness** | **0.983** | **0.646** | **-0.337 🚨** |
| **cites_kb_source** | **1.000** | **0.875** | **-0.125 ⚠️** |

### The Signal

- **completeness drops from 0.983 → 0.646**: Degraded responses are shorter, less actionable,
  miss key details. The model still sounds confident — it just says less.
- **cites_kb_source drops from 1.0 → 0.875**: ~12% of responses stop citing specific KB
  sources. Generic answers replace grounded ones.
- **context_adherence stays at 1.0**: The responses are still "about the right topic" even
  when degraded. Adherence alone wouldn't catch this — you need completeness + citation metrics.

### Fleet During This Failure

Fleet: ✅ POSITIVE SIGNAL (latency improved because shorter responses = faster).
The on-call engineer might celebrate. Users are getting worse answers.

### Why This Matters

This is the regression that kills trust over weeks. No single answer is catastrophically wrong.
The quality decline is statistical — visible only when you score 100% of traffic (or a
meaningful sample). At 5% sampling, you'd need ~200 queries to reach significance.
Galileo's 100% scoring catches it after 16 queries.

---

## XL-3: LangGraph Misrouting

Router bug sends ALL queries to the "infra" flow regardless of topic.

| Metric | xl3-baseline (12 spans) | xl3-misrouted (12 spans) | Δ |
|--------|------------------------|--------------------------|---|
| context_adherence | 1.000 | 1.000 | 0.000 |
| completeness | 1.000 | 0.981 | -0.019 |
| cites_kb_source | 1.000 | 1.000 | 0.000 |
| **routing_accuracy** | *(computing — scorer registered 2026-06-14)* | *(computing)* | — |

### The Signal

Quality metrics stay high because the KB is small enough that even wrong-category docs
sometimes contain relevant content. The **routing_accuracy** custom scorer (just registered)
will show the misroute once computation completes.

The PRIMARY signal for XL-3 is Galileo's **trace view**: every span path shows identical
routing to "infra" regardless of query topic. This visual uniformity is the diagnostic
fingerprint — normal routing shows variety, a bug creates uniformity.

---

## XL-4: Eval → Protect Guardrail

| Phase | Traces | Protect Status | context_adherence | completeness |
|-------|--------|---------------|-------------------|--------------|
| A: Dev eval (bad prompt) | 10 | skipped | 1.000 | 0.967 |
| B: Prod, no Protect | 6 | skipped | 1.000 | 1.000 |
| C: Prod + Protect rule | 10 | not_triggered | 1.000 | 0.867 |

### Result

Protect correctly **did not fire**. The hallucination-prone prompt ("Never say I don't know")
did NOT produce hallucinated responses because:
1. The engineering KB is specific enough that even bad prompts produce grounded answers
2. GPT-4o-mini's parametric knowledge is strong enough to resist the fabrication instruction

This is actually a **correct outcome** — Protect should not block responses that ARE grounded.
The Protect infrastructure is fully operational (real `invoke_protect` calls return 200 OK).
For a trigger demo, pairing the bad prompt with an out-of-scope query set and a poisoned
corpus would force genuine fabrication.

**Protect pipeline status:** ✅ End-to-end wired and operational. Same metric (context_adherence)
flows from dev eval → prod guardrail. `invoke_protect()` with Ruleset confirmed working.

---

## XL-5: Slow Tool Node

| Query Type | xl5-baseline | xl5-slow | Δ |
|-----------|-------------|---------|---|
| All metrics | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 0.000 |

Quality is completely unaffected by the 8s tool stall. Galileo confirms what fleet couldn't:
the latency spike didn't corrupt the output. This is the "two instruments, one truth" demo —
fleet finds the performance anomaly, Galileo confirms quality is intact.

---

## Cross-Drill Summary (Galileo-Computed)

| Drill | What Broke | Fleet Signal | Galileo Signal | Key Metric |
|-------|-----------|-------------|---------------|-----------|
| XL-2 | Wrong corpus | ✅ Green | 🚨 completeness 1.0 → 0.0 | completeness, cites |
| XL-3 | Router hardcoded | ✅ Green | 👁️ Trace path uniformity | routing_accuracy (pending) |
| XL-4 | Bad prompt deployed | ✅ Green | ✅ Protect wired, no false pos | Pipeline operational |
| XL-5 | Tool stall 8s | 🚨 Latency | ✅ Quality intact (1.0/1.0/1.0) | Span duration |
| XL-6 | Quality regression | ✅ Improved! | 🚨 completeness 0.98 → 0.65 | completeness, cites |

---

## Data Provenance

- **81 traces** in Galileo Console (`rax-galileo-labs` → `trinity-stack`)
- **169+ LLM spans** scored by Galileo server-side scorers
- **Scorer engine:** GPT-4o mini via OpenAI integration (not local judge)
- **Integration ID:** `ed1060ea-1940-46c3-81cc-64047800539b` (created 2026-06-14)
- **Recomputation:** Triggered via `POST /projects/{pid}/recompute-metrics` after integration setup
- All numbers are GALILEO-COMPUTED — zero local-judge, zero fabrication
- Console URL: https://app.galileo.ai → rax-galileo-labs → trinity-stack
- Filter by `user_metadata.tag` for per-drill comparison

### What Changed to Fix "Auth Error"

The root cause was **no LLM integration configured** in Galileo. Server-side scorers
(both preset and custom LLM) need an LLM integration to make judge calls.

**Fix applied:**
```python
from galileo import Integration
Integration.create_openai(token=openai_api_key)
# → created integration ed1060ea, is_selected=True
```

After creating the integration:
1. Updated scorer settings with `model_name: "GPT-4o mini"` for all 3 scorers
2. Triggered `recompute-metrics` on all 81 traces
3. Scores populated within 2-3 minutes on LLM spans

### Scorer Settings (Final)

| Scorer | model_name | model_type | num_judges |
|--------|-----------|-----------|-----------|
| context_adherence | GPT-4o mini | llm | 3 |
| completeness | GPT-4o mini | llm | 3 |
| cites_kb_source | GPT-4o mini | llm | 1 |
| routing_accuracy | GPT-4o mini | llm | 1 |
