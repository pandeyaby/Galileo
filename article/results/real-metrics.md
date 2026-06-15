# Real Metrics — Trinity-Stack Drill Results

**Date:** 2026-06-14  
**Stream:** `rax-galileo-labs` → `trinity-stack`  
**Traces:** 96 total (79 new from this run + 17 pre-existing)  
**Spans:** 527  
**Scorer:** gpt-4o-mini LLM-as-judge (244 evaluation calls)  
**Method:** Traces ingested to Galileo via GalileoCallback, metrics computed locally
via LLM-as-judge against actual trace input/context/output

---

## Baseline (10 queries)

| Metric             | Value |
|--------------------|-------|
| context_adherence  | 1.000 |
| completeness       | 0.900 |
| cites_kb_source    | 0.900 |
| latency (mean)     | 4207ms |

---

## XL-2: Poisoned Retriever (Retrieval Corpus Swap)

The money demo. Fleet says green; Galileo says the building is on fire.

| Metric             | xl2-baseline (3q) | xl2-poisoned (6q) | Δ       |
|--------------------|-------------------|--------------------|---------|
| context_adherence  | **1.000**         | **0.000**          | -1.000 🚨 |
| completeness       | 1.000             | 0.567              | -0.433 🚨 |
| cites_kb_source    | **1.000**         | **0.000**          | -1.000 🚨 |
| latency (mean)     | 3054ms            | 1797ms             | (faster — poison is smaller corpus) |

**Key insight:** context_adherence craters from 1.0 → 0.0. The agent still answers confidently
(completeness 0.567), but every claim is unsupported by context. Fleet sees nothing wrong.
Only Galileo's context_adherence catches it.

---

## XL-3: LangGraph Misrouting (Router Always Returns "infra")

| Metric             | xl3-baseline (12q) | xl3-misrouted (12q) | Δ       |
|--------------------|---------------------|----------------------|---------|
| routing_accuracy   | **1.000**           | **0.333**            | -0.667 🚨 |
| context_adherence  | 1.000               | 1.000                | 0.000   |
| completeness       | 1.000               | 1.000                | 0.000   |
| cites_kb_source    | 1.000               | 1.000                | 0.000   |
| latency (mean)     | 2713ms              | 2818ms               | +4%     |

**Key insight:** routing_accuracy drops from 1.0 → 0.333 (only the 4/12 genuinely-infra queries
score correct). Context adherence stays high because the KB is small enough that even wrong-category
docs sometimes contain useful content — but the intent mislabeling is a clear signal in the trace
view (every span path shows "infra" regardless of query).

---

## XL-4: Eval → Protect Guardrail

| Phase                    | Traces | Protect Status   | context_adherence | completeness | cites_kb |
|--------------------------|--------|------------------|-------------------|--------------|----------|
| A: Dev eval (bad prompt) | 7      | skipped          | 1.000             | 1.000        | 1.000    |
| B: Prod, no Protect      | 3      | skipped          | 1.000             | 1.000        | 1.000    |
| C: Prod + Protect rule   | 5      | not_triggered    | 1.000             | 1.000        | 1.000    |

**Note:** The hallucination-prone prompt (`"Give confident, detailed answers. Never say I don't know."`)
did NOT produce low-adherence responses in this run — the KB's content is specific enough that even a
bad system prompt grounded answers in the retrieved docs. Protect correctly **did not fire** (no false
positives). For a stronger demo, the prompt would need to be paired with an out-of-scope query set
that forces fabrication.

The Protect stage infrastructure IS operational (real `invoke_protect` calls return `not_triggered`
with 200 OK). The eval→guardrail pipeline is wired end-to-end.

---

## XL-5: Slow Tool Node (8s Injected Stall)

| Query Type    | xl5-baseline | xl5-slow  | Δ          |
|---------------|-------------|-----------|------------|
| Non-tool (NCCL hang)       | 2977ms | 2659ms  | -11% (noise) |
| **Tool (KV-cache compute)**| **2674ms** | **11004ms** | **+8330ms (+312%) 🐌** |
| Non-tool (K8s GPU pods)    | 2319ms | 1998ms  | -14% (noise) |
| **Mean**      | 2657ms      | 5221ms    | **+96%**   |

**Key insight:** Fleet sees a p99 spike (11s). Galileo's span durations pinpoint the `tools` node
as the outlier (+8.3 seconds, matching the injected 8s stall). Non-tool queries are unaffected.
Two instruments, one truth.

---

## XL-6: Silent Quality Regression (Model Config Degradation)

| Metric             | xl6-baseline (8q) | xl6-degraded (8q) | Δ       |
|--------------------|---------------------|--------------------|---------|
| context_adherence  | **1.000**           | **0.875**          | -0.125 ⚠️ |
| completeness       | **1.000**           | **0.838**          | -0.163 ⚠️ |
| cites_kb_source    | **1.000**           | **0.250**          | -0.750 🚨 |
| latency (mean)     | 2575ms              | 2091ms             | -19% (faster!) |

**Key insight:** Fleet shows **improvement** (lower latency). Quality is quietly dying.
cites_kb_source craters from 1.0 → 0.25 — the degraded config produces shorter, less-cited
answers. context_adherence dips to 0.875 (some claims ungrounded). This is the regression that
kills user trust over weeks. Luna-2 at 100% traffic catches it after 8 queries; 5% sampling
would need ~160 queries.

---

## Cross-Drill Summary

| Drill | What Broke          | Fleet Signal | Galileo Signal                  | Detection Time |
|-------|---------------------|--------------|----------------------------------|----------------|
| XL-2  | Wrong corpus        | ✅ Green     | 🚨 adherence 1.0 → 0.0          | Immediate      |
| XL-3  | Router hardcoded    | ✅ Green     | 🚨 routing 1.0 → 0.33           | Immediate      |
| XL-4  | Bad prompt deployed | ✅ Green     | ✅ Protect wired (no false pos) | Pipeline ready  |
| XL-5  | Tool stall 8s       | 🚨 Latency  | 🐌 tools span +8330ms           | Immediate      |
| XL-6  | Quality regression  | ✅ Improved! | 🚨 cites 1.0 → 0.25             | 8 queries       |

---

## Data Provenance

- **96 traces** in Galileo Console (`rax-galileo-labs` → `trinity-stack`)
- **244 LLM-as-judge calls** (gpt-4o-mini, temp=0) for metric computation
- All numbers derived from actual trace input/context/output — zero fabrication
- Trace tags: `baseline`, `xl2-baseline`, `xl2-poisoned`, `xl3-baseline`,
  `xl3-misrouted`, `xl4-dev-eval`, `xl4-prod-no-protect`, `xl4-prod-with-protect`,
  `xl5-baseline`, `xl5-slow`, `xl6-baseline`, `xl6-degraded`
- Raw data: `article/results/all_traces.json`, `article/results/metric_scores.json`
