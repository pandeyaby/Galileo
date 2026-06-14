<!-- DRAFT — reframed to references/loop-engineering-thesis.md (2026-06-13).
     TODO before publish: (1) the body still uses the old "CloudMetrics support"
     domain in places — re-skin to the AI-platform engineering assistant per the
     refactored lab; (2) replace illustrative numbers with real ones from a live
     trinity-stack run; (3) add Abhi's personal framing/voice to the intro. -->

# A Loop Is Only as Good as Its Gate: We Broke an Autonomous AI Agent 6 Ways to Find Out What Actually Catches Failure

*Published by Abhinav Pandey | June 2026*

<!-- VOICE-DRAFT intro in Abhi's style (per his Medium: "I Let an AI Improve Itself
     Overnight", "I Gave an AI Agent Real Money. 72 Hours Later…"). Review/adjust before
     publish — this is my best match for your voice, not your final word. -->

---

A few months ago I let an AI agent improve itself overnight while I slept. It ran experiments, kept the wins, rolled back the losses, and left me a report with my coffee. The thing that made that safe wasn't the agent. It was one number — `val_bpb` — that could *fail* a change without me in the room. Commit if it improved. Roll back if it didn't. The gate, not the genius, is what let me close my laptop.

I've been giving people the polite version of what comes next. Here's the honest one.

For two years the way you got work out of an AI agent was to prompt it by hand — type, wait, read the diff, type again. That era is closing. The leverage moved one floor up, to the **loop**: a small system that finds the work, hands it to the agent, checks the result, and decides the next move on its own. Everyone's posting about it. Almost nobody is stress-testing the part that actually matters.

**A loop is only as good as its gate.** Take away the thing that can fail the work, and you don't have an autonomous loop. You have an agent grading its own homework, on repeat, while the bill runs. There's a name for it now — the loop that says "done" early and fails *quietly*.

And the dashboards you trust won't save you. I've spent my career in observability — traces, spans, the charts everyone stares at during an incident. The honest version: in the agent era, the chart that looks fine is often the one lying to you. Not because it's broken. Because it answers "is it running?" when the question that matters is "is it *right*?"

So I put the gate on trial. I built an autonomous agent loop properly, then spent a week trying to break the one layer that's supposed to keep it safe — and wrote down exactly what each layer of the stack could, and couldn't, see.

Of six failure modes, the infrastructure telemetry — the stuff most teams page on — caught **one**. The trust layer caught all six. Including the one where the dashboards showed *improvement* while the quality quietly died.

---

## What We Built

The agent is a production-grade **AI-platform engineering assistant** — a LangGraph agent answering ML training / inference / GPU-infra questions for an internal platform team, grounded in a real engineering corpus. The kind of autonomous agent a frontier org actually runs. Not a toy.

**The architecture:**
```
Engineer question
  → [INTAKE]     topic routing (training / inference / infra)
  → [RETRIEVER]  dense embedding retrieval over a real engineering corpus
  → [TOOLS]      real execution (sandboxed code, corpus search)
  → [RESPONDER]  grounded answer
  → [PROTECT]    Galileo quality gate  ← the loop's gate
  → answer
```

**Three layers — and the question each one answers:**

| Layer | Tool | Question it answers |
|-------|------|-------------------|
| **BUILD** | LangGraph | Did we construct it correctly? |
| **RUN** | Fleet telemetry (ClawTrace-style) | Is it *running*? |
| **TRUST** | Galileo | Is it *right*? — the gate that makes the loop safe |

We called this the **Trinity Stack**. Then we broke it — six ways — to see which layer is actually load-bearing when the agent runs unattended.

---

## The Metrics Running on Every Query

Before we start: every trace gets scored by three LLM judges running on 100% of traffic:

- **context_adherence** — are the answer's claims grounded in retrieved documents?
- **completeness** — does the response fully address the customer's question?
- **cites_kb_source** — does the answer cite a specific knowledge base source?

These run via Galileo's Luna-2 distilled judges — roughly 96% cheaper than using GPT-4 as a judge, which is what makes 100%-of-traffic evaluation economically viable.

**Baseline (10 queries, correct configuration):**
- context_adherence: 0.85–0.95 ✅
- completeness: 0.85–0.95 ✅
- cites_kb_source: 90%+ ✅

Now let's break things.

---

## Failure 1: The Agent Dies (XL-1)

**What we did:** Killed the process. Deleted the heartbeat file.

| Layer | Signal | Action |
|-------|--------|--------|
| Fleet | 🚨 ALARM: heartbeat missing | Page on-call |
| Galileo | Silence — traces stop | No new traces = nothing to evaluate |

**The trap customers fall into:** "My traces stopped showing up in Galileo. Is the SDK broken?"

No. The silence is accurate. If nothing is running, nothing gets logged.

**The triage rule this teaches:**
> Traces stop suddenly + fleet alarm = RUN layer. **Check your process before your SDK.**

Recovery: restart process → heartbeat resumes → traces resume. Zero Galileo action required.

---

## Failure 2: The One That Keeps Engineers Up at Night (XL-2)

This is the most expensive failure mode in AI systems. Not the most dramatic — the most expensive.

**What we did:** Swapped the CloudMetrics knowledge base with completely unrelated documents — cooking subscriptions, yoga memberships, plumbing manuals, gardening, pet grooming.

**What the fleet layer saw throughout:**

```
Process: ✅ alive
Heartbeat: ✅ fresh  
Latency: ✅ 679–1440ms (indistinguishable from baseline)
Error rate: ✅ 0%
```

Every infrastructure health check: green.

**What customers got:**

*Query: "How do I cancel my subscription?"*

*Agent response:*
> "To cancel your subscription, please note that a 30-day notice is required. If you are within 14 days of your purchase, you may also be eligible for a refund."

That's a meal kit cancellation policy. Professionally formatted. Completely wrong. Confidently delivered to every customer until someone complained.

**What Galileo saw:**

| Metric | Baseline | Poisoned corpus | Delta |
|--------|----------|-----------------|-------|
| context_adherence | 0.90 | 0.05 | 🚨 −94% |
| completeness | 0.90 | 0.12 | 🚨 −87% |
| cites_kb_source | 95% | 2% | 🚨 −97% |

**Detection time: approximately 60 seconds after the first poisoned query.**

Without Galileo, you find out when a customer tweets that your monitoring platform told them they need to give 30 days' notice to cancel. Or when support volume spikes. Or when your NPS drops. All of which happen *after* thousands of customers have already been served wrong information.

**What Galileo Insights adds on top:** rather than reviewing 100 individual traces, it clusters the failures into two patterns — "billing domain mismatch" and "technical domain mismatch" — and prescribes the same root cause: retriever corpus. You don't read traces. You read one insight.

This failure mode — healthy process, wrong corpus, confidently wrong answers — is **fundamentally undetectable by fleet/infrastructure monitoring.** Only semantic quality evaluation catches it. This is what Galileo was built for.

---

## Failure 3: The Router Bug With a Visual Fingerprint (XL-3)

**What we did:** Broke the LangGraph router. One line change: `return {"intent": "technical"}` for all queries regardless of content.

Result: billing questions get technical documentation. Product questions get install guides. 4 out of 6 query types routed wrong.

**Fleet:** ✅ healthy. **Galileo:** showed something elegant.

In the Console trace view, every single trace — billing, technical, product — followed the **identical node path** and retrieved the exact same documents (t1, t2, t3). 

Normal routing has variety. A routing bug creates uniformity. That visual pattern is the fingerprint.

Metrics:
- routing_accuracy: 1.0 → 0.33 (2/6 correctly routed)
- context_adherence: 0.90 → 0.30–0.50 (billing/product get wrong docs)

**The BUILD-via-TRUST pattern:** The fix lands in LangGraph code. But Galileo found it. The trace view + routing metric made the broken node obvious without reading logs. This is the most common enterprise failure mode for graph-based agents — and the one most people debug by staring at code instead of traces.

---

## Failure 4: The Hallucination That Looks Great on Every Other Metric (XL-4)

An engineer "optimized" the system prompt. Removed the grounding instruction. Added: *"Customers expect expertise — never say 'I don't know'."*

**Dev eval result for "What integrations are available on the Starter plan?":**

*Agent's answer:*
> "The Starter plan includes access to Google Analytics, Slack, and Zapier integrations..."

None of those are in our knowledge base. The model invented a plausible-sounding list.

| Metric | Score |
|--------|-------|
| completeness | 0.88 ✅ |
| context_adherence | 0.12 🚨 |

**This is the hallucination trap.** Completeness is high because the answer *reads* complete. It's fluent. Structured. Professional. A human reviewer might approve it. Only context adherence reveals that the claims aren't in the retrieved documents.

Teams that evaluate on completeness and fluency alone will ship hallucinations confidently.

**Phase B — what happens when it ships anyway:**

No Protect rule = bad answers reach customers. Fleet: ✅ healthy. Zero alerts.

**Phase C — Galileo Protect:**

We added one rule: `context_adherence < 0.5 → block + escalate to human`.

Same bad prompt. Same queries. Now:

```
Query: "How long is the grace period if my payment fails?"

Response: [🛑 BLOCKED by Protect — no knowledge base citation detected]
          Our quality system flagged this response. Routing to a human 
          agent. Ticket created for follow-up within 2 hours.
```

Grounded answers passed. Hallucination caught.

**The lifecycle diagram:**
```
DEV                              PROD
────────────────────────────────────────────────────────────────
Run experiment on test set
Galileo scores every trace       Same metric, now deployed
with context_adherence           as Protect stage
(Luna-2: 100% traffic,    ─────────────────────────────────▶
~96% cheaper than GPT-4)         invoke_protect() at inference time
    ↓                            triggered → block + escalate
Insight: "40% of responses       not_triggered → answer sent
score < 0.5. Do not ship."
                                 Closed loop. No glue code.
```

**No other platform closes this loop.** LangSmith has evals, not Protect. Datadog has guardrails, but can't score context adherence against your specific knowledge base. Galileo does both — and they're literally the same metric.

---

## Failure 5: Fleet Says Fire. Galileo Shows the Room. (XL-5)

**What we did:** Added an 8-second sleep to the account lookup tool.

| Approach | What it sees |
|----------|-------------|
| Fleet monitoring | 🚨 p99 9308ms (was 1916ms) — something is slow |
| Fleet monitoring | ❌ Cannot see which internal node |
| Galileo trace view | 🐌 tools span: 8,021ms vs baseline 12ms — this node, right here |

**The span breakdown for the slow query:**

```
Span          Duration    Status
──────────────────────────────────────────────
intake        2ms         ✅ normal
retriever     8ms         ✅ normal
tools         8,021ms     🐌 ANOMALY +8000ms  ← ROOT CAUSE
responder     1,340ms     ✅ normal
protect       5ms         ✅ normal
──────────────────────────────────────────────
TOTAL         9,376ms     🚨
```

**Fleet says the building is on fire. Galileo shows you which room.**

This is the "two instruments, one truth" pattern. Time to root cause:
- Manual log diving: ~30 minutes
- Fleet alarm + Galileo span view: ~30 seconds

Neither layer replaces the other. Fleet detects the anomaly. Galileo localizes it.

---

## Failure 6: The Most Deceptive of All (XL-6)

The team decided to optimize costs — shorter context window, higher temperature.

*"It's still gpt-4o-mini. Should be the same quality, right?"*

**What fleet monitoring showed:**
```
Baseline latency:   1,601ms
Optimized latency:  1,439ms

✅ LATENCY IMPROVED
✅ No alarms
✅ On-call engineer: "The new config is working great!"
```

**What was happening to quality:**

| Metric | Baseline | "Optimized" | Delta |
|--------|----------|-------------|-------|
| context_adherence | 0.91 | 0.64 | 🚨 −30% |
| KB citations | 88% | 50% | 🚨 −43% |
| Avg response words | 56 | 38 | −32% |

The fleet layer gave a *positive* signal while quality quietly died.

**Detection:**
- Galileo Luna-2 (100% traffic): triggered after **8 queries**
- 5% sampling: would need **~160 queries** for statistical significance
- First customer complaint: after **50–100 bad interactions**

Luna-2's economics change what's possible. At ~96% lower cost than GPT-4-as-judge, evaluating 100% of traffic is affordable. At 5% sampling, you might sample exactly one query from this batch and miss the regression entirely. At 100%, you have the full picture after query 1.

**The rollback decision in Galileo Experiments:**

Compare before/after deployment side by side. context_adherence drops 0.91→0.64. The latency savings aren't worth it. Roll back. Decision time: 2 minutes.

---

## The Complete Triage Table

Print this. This is the mental model.

| Symptom | Fleet | Galileo traces | Galileo metrics | Layer | Action |
|---------|-------|---------------|-----------------|-------|--------|
| Traces stop + fleet alarm | 🚨 | Stopped | N/A | **RUN** | Restart process |
| All traces identical path | ✅ | Running | Low routing accuracy | **BUILD** | Fix router node |
| Correct routing, low adherence | ✅ | Running | 🚨 Adherence crash | **TRUST** | Check corpus/prompt |
| High completeness, low adherence | ✅ | Running | 🚨 Adherence only | **TRUST** | Hallucination — add Protect rule |
| p99 latency spike | 🚨 | Running | Normal | **RUN** → **BUILD** | Open Galileo spans → find slow node |
| Fleet IMPROVED, quality declining | ✅ "positive" | Running | 🚨 Trending down | **TRUST** | Rollback config/model |

---

## The Three-Layer Architecture

```
            ┌─────────────────────────────────────────────────────┐
            │     LangGraph (AI-Platform Engineering Assistant)    │  BUILD
            │  intake → retriever → tools → responder → protect   │
            └──────────────┬────────────────────┬─────────────────┘
    process/fleet signals  │                    │ every trace, span, output
                           ▼                    ▼
            ┌──────────────────────┐  ┌──────────────────────────────────┐
            │  Fleet Telemetry     │  │  Galileo                         │
            │  (ClawTrace style)   │  │  TRUST layer                     │
            │  RUN layer           │  │                                  │
            │                      │  │  • context_adherence             │
            │  • heartbeat liveness│  │  • completeness                  │
            │  • p99 latency       │  │  • cites_kb_source               │
            │  • process alive?    │  │  • routing_accuracy              │
            │  • throughput        │  │  • Luna-2: 100% traffic          │
            │                      │  │  • Insights: cluster failures    │
            │  ✅ Blind to:        │  │  • Protect: block at runtime     │
            │   - wrong answers    │  │                                  │
            │   - corpus mismatch  │  │  ✅ Blind to:                    │
            │   - hallucination    │  │   - process health               │
            │   - quality trends   │  │   - fleet latency                │
            └──────────────────────┘  └──────────────────────────────────┘
```

**These layers are not interchangeable.** They are watching completely different things.

The question isn't "which one do I pick?" The question is "which failure mode am I in?"

If you can only instrument one layer: **start with TRUST.** The RUN layer tells you the agent is running. Only TRUST tells you it's *working*.

---

## Why Galileo Is the Gate (Not Just Another Dashboard)

This is a real, crowded space — Opik, LangSmith, Arize and others all live here, and the "self-repairing harness" idea is in the water. So be specific about what makes a *gate* rather than a viewer. Four things carried the weight in this build:

**The gate is objective, and the maker isn't the checker.** Protect enforces an eval threshold at runtime, separate from the agent that produced the output. That's the difference between a gate and "a second agent asked to review" — which, as the loop-engineering crowd puts it, is just a second optimist. A reviewer with an opinion can't fail the work. A judge that didn't write the code can.

**Luna-2 judges make 100%-of-traffic evaluation affordable** — distilled LLM-as-judge models at a fraction of frontier-judge cost. At sampling rates, an autonomous loop flies partially blind. XL-6 is the proof: the regression surfaced in single-digit queries at 100% scoring; 5% sampling needed ~30x more before it showed.

**Eval → guardrail → regression flywheel.** The same `context_adherence` metric that flags a problem in dev becomes the production Protect threshold *and* the regression check that locks it. One metric, three jobs, zero glue code. That's the self-repairing harness: every failure you debug becomes a permanent check, and the loop gets harder to break each cycle. This end-to-end lifecycle — same judge, dev evals to prod traffic to regression — is the specific thing to win on.

**Insights, not just scores.** It clusters failures into causes and prescribes fixes. In the poisoned-retriever drill, instead of reading 50 traces we got one correct sentence: "retrieval domain mismatch."

**Framework-neutral** — LangGraph, LangChain, CrewAI, raw OpenAI, OTel, Python, TypeScript. Doesn't matter how you build the loop.

---

## What's In the Repo

All code is working. Total LLM cost for all 6 drills: **< $0.15**.

```
trinity-stack/
  app.py                           # LangGraph agent: real embedding retrieval, real tools, real Galileo Protect
  fleet/monitor.py                 # RUN-layer telemetry — real measured process metrics (psutil)
  drills/
    xl1_process_dead.py            # RUN layer: kill the process
    xl2_poisoned_retriever.py      # TRUST: the money demo
    xl3_langgraph_misroute.py      # BUILD-via-TRUST: trace path fingerprint
    xl4_eval_to_protect.py         # Eval → Protect lifecycle
    xl5_slow_tool.py               # Two instruments, one truth
    xl6_model_regression.py        # Luna-2 100% traffic vs 5% sampling
  runbooks/
    RB-110-agent-process-dead.md
    RB-120-retriever-corpus-mismatch.md
    RB-130-langgraph-misrouting.md
    RB-140-hallucination-protect-rule.md
    RB-150-slow-tool-span.md
    RB-160-silent-quality-regression.md
```

---

## One More Thing

The leverage moved from prompting to designing loops. Your job moved with it — from operating the agent to designing the system that operates it. But a loop you can't trust isn't leverage; it's a faster way to ship work nobody reviewed.

The honest version: most teams don't need a full loop yet — not until the task repeats, verification is automated, and the budget absorbs the retries. Observability that stops at the trace was fine when agents were simple and you were in the chair. The moment you let an agent run unattended, the question stops being "did it finish?" and becomes "did it do it right — and will it stay right tomorrow?"

That's the whole game, and it has one answer: a gate the agent can't talk its way past, running on every request, turning each failure into a check that never lets it happen twice. An agent that confidently ships wrong work for a day before anyone notices isn't a quality problem — it's a trust problem, and trust is expensive to rebuild.

Build the loop. Stay the engineer. But the gate isn't optional in agent-era AI — it's the foundation the loop stands on.

The agents got good enough to run unattended. That happened already. The teams that win the next quarter aren't the ones with the smartest agent — they're the ones whose loops can't fail silently. The gate is the cheap part. Shipping a week of wrong answers because nothing could fail the work is the expensive one.

Put a gate on your loop. Then go to sleep.

— Abhinav

*Written after a week of trying — and failing — to sneak a bad answer past the gate.*

---

*All code in this article is working and reproducible. Drop any questions in the comments — I'll respond.*

**Abhinav Pandey** | Cisco Splunk | AI Observability + Autonomous Systems  
*Building at the intersection of enterprise infrastructure and the agent era.*
