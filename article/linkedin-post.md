<!-- DRAFT — aligned to references/loop-engineering-thesis.md (2026-06-13).
     Metric numbers are illustrative from the lab; re-verify against a real
     trinity-stack run before publishing (no invented numbers in the final). -->

# LinkedIn Post

---

Everyone's talking about "loop engineering" — moving from prompting your agent by hand to designing a loop that runs the agent for you: it finds the work, does it, checks it, and decides the next move while you sleep.

Here's the part the threads gloss over: **a loop is only as good as its gate.**

Without an objective check that can *fail the work*, you don't have an autonomous loop. You have an agent grading its own homework, on repeat, while the bill runs. Geoffrey Huntley named the failure mode — the loop that emits "done" early and fails quietly.

So we built the loop properly and tried to break the gate.

We took a production-grade AI-platform engineering assistant (a LangGraph agent over a real ML-infra corpus), wrapped it in the loop, and ran it through 6 failure modes. The agent's own infrastructure telemetry — process health, latency, error rate — caught **1 of 6**.

The trust layer (Galileo) caught all 6. Here's the one that should scare you:

**We "optimized" the model config** — shorter context, cheaper settings. Infra telemetry showed *improvement*: latency dropped. Every ops dashboard went greener. Meanwhile answer quality was quietly dying — fewer citations, lower grounding. A judge running on **100% of traffic** caught the regression in single-digit queries. 5% sampling would have needed ~30x more before it noticed. By then the loop has shipped a week of degraded work.

That's the thesis in one drill: **the loop's gate has to see quality, not just liveness — and it has to see all of it, not a sample.**

The other failure that matters is the one that closes the loop. We injected a hallucination-prone prompt. The eval caught it in dev (high fluency, low grounding — looks great on every metric except the one that counts). Then the *same metric* became a runtime guardrail in prod: low-grounding answers blocked and escalated automatically. Same check, dev to prod, no glue code. And every failure we debugged got locked as a regression test — so the harness gets **harder to break each cycle**. That's a self-repairing harness, not a dashboard.

The honest version: most teams don't need a full loop yet — not until the task repeats, verification is automated, and the budget absorbs the retries. Observability that stops at the trace was fine when agents were simple. But the moment you let an agent run unattended, **the gate is the whole game** — and "a second agent asked to review" is just a second optimist.

This isn't a vacuum — Opik, LangSmith, Arize and others live here too. What made the difference in our lab was the specific lifecycle: one metric that scores dev evals *and* gates prod traffic, on judges cheap enough to run at 100%.

Build the loop. Stay the engineer. But put a real gate on it.

Full write-up — the 6 drills, the runbooks, the decision table for which layer fires and where the fix lands → [link]

What's the gate on your agent loop right now — a test, a judge, or an optimist?

#AI #LLMOps #AgentEngineering #AIObservability #Evals #Guardrails #Galileo #LangGraph
