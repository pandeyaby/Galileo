<!-- DRAFT — aligned to references/loop-engineering-thesis.md (2026-06-13).
     Numbers illustrative from the lab; re-verify against a real run before posting. -->

# X Thread — 14 Tweets

---

**Tweet 1 — HOOK**
"Loop engineering" is the shift everyone's posting about: stop prompting your agent by hand, design a loop that runs it for you.

Nobody's stress-testing the one part that actually matters.

A loop is only as good as its gate. 🧵

---

**Tweet 2 — the claim**
The gate is the objective check that can *fail the work* without you in the room.

No gate = the agent grading its own homework on repeat while the bill runs.

Geoffrey Huntley named it: the loop that says "done" early and fails quietly.

---

**Tweet 3 — the test**
So we built the loop right and tried to break the gate.

A production AI-platform engineering assistant (LangGraph agent over a real ML-infra corpus), wrapped in the loop, run through 6 failure modes.

Its own infra telemetry caught 1 of 6.

---

**Tweet 4 — the framing**
Two layers, two questions:

• Infra/RUN telemetry: "is it running?"
• Trust layer (Galileo): "is it RIGHT?"

Running ≠ working. 5 of the 6 failures were invisible to "is it running?"

---

**Tweet 5 — the scary one**
We "optimized" the model config. Cheaper, shorter context.

Infra telemetry: latency IMPROVED. Dashboards greener. 🎉

Reality: grounding dropped, citations dropped. Quality was dying.

The improvement was the disguise.

---

**Tweet 6 — why 100% traffic**
A judge on 100% of traffic caught it in single-digit queries.

5% sampling would've needed ~30x more before noticing.

By then the loop has shipped a week of degraded answers.

The gate has to see ALL of it, not a sample.

---

**Tweet 7 — the silent one**
We swapped the agent's knowledge index for an off-domain one.

Process: alive. Latency: fine. Errors: zero.

Every answer: confidently wrong.

Only a layer that scores grounding against the retrieved context caught it.

---

**Tweet 8 — the closing-the-loop one**
We injected a hallucination-prone prompt.

Looks great on fluency + completeness. Fails on grounding — the one metric that counts.

The eval caught it in dev. Then the SAME metric became a runtime guardrail in prod. Same check, no glue code.

---

**Tweet 9 — self-repairing harness**
This is the part the "your harness should repair itself" crowd is right about:

Every failure we debugged got locked as a regression test.

The harness gets HARDER to break each cycle. Trace → diagnose → gate → fix → lock → repeat.

---

**Tweet 10 — the others**
The rest of the 6:
• Kill the process → infra alarms, traces stop (correct)
• Break the router → identical span paths = the bug's fingerprint
• Slow tool → infra says "something's slow," spans say "this exact node"

---

**Tweet 11 — the table**
What fires, and where the fix lands:

Traces stop → RUN
Uniform span paths → BUILD
Bad retrieval, low grounding → TRUST
High fluency, low grounding → TRUST → Protect
Latency "improved", quality down → TRUST

---

**Tweet 12 — the honest take**
Most teams don't need a full loop yet.

Only when: the task repeats, verification is automated, the budget absorbs retries.

But the moment you let an agent run unattended, the gate is the whole game.

A "reviewer" agent with an opinion is not a gate.

---

**Tweet 13 — not a vacuum**
Opik, LangSmith, Arize all live here. Real space, real tools.

What moved the needle for us: one metric that scores dev evals AND gates prod traffic, on judges cheap enough to run at 100%.

The eval→guardrail→regression lifecycle, closed.

---

**Tweet 14 — CTA**
The leverage moved from prompting to designing loops.

Build the loop. Stay the engineer. Put a real gate on it.

Full write-up — 6 drills, runbooks, the decision table → [link]

What's the gate on your loop right now: a test, a judge, or an optimist?
