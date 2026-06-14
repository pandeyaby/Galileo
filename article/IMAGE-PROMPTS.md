# Article image prompts — "A Loop Is Only as Good as Its Gate"

Ready-to-paste prompts for an image model (Midjourney / DALL·E / Galileo imagegen / Ideogram).
These are **conceptual illustrations + clean diagrams** to make the article lively — they are
NOT data charts. The real metric charts (XL-2 adherence crater, XL-6 regression) come from
**screenshots of the live Galileo Console** after the drills run (see the publish runbook);
do not generate fake-looking charts to stand in for those.

**Shared style block** (append to any prompt for consistency):
> Editorial tech illustration, dark "cockpit" aesthetic: near-black background #0a0a0f, surfaces
> #12121a, thin #1e1e2e borders. Typeface vibe: Inter + JetBrains Mono. Restrained semantic color
> only — green #34a853 (healthy), red #ff6b6b (failure), amber #f5a623 (warning), blue #88aaff
> (accent). Flat, precise, high signal, zero decoration. No stock-photo people, no 3D gloss, no
> clichéd glowing-brain/robot. Crisp vector feel. Negative space. Ideogram/DALL·E: render any text
> labels sharply and spelled correctly.

Aspect ratios: Medium hero **1400×787 (16:9)**; LinkedIn **1200×627**; X **1600×900**.

---

## 1. HERO — "A loop is only as good as its gate"
> A clean circular loop diagram drawn as a closed pipeline of four nodes — "FIND WORK → AGENT
> ACTS → CHECK → DECIDE" — arrows flowing clockwise. At the bottom of the loop, one node is a
> literal **gate / turnstile** labeled "GATE", drawn larger and in sharp focus, with a red ✕ and
> a green ✓ lane. A document labeled "BAD ANSWER" is being stopped at the red lane; a "SHIP"
> token passes the green lane. The rest of the loop is slightly dimmed so the gate is the hero.
> Caption space lower-left. Mood: an autonomous system that polices itself. [shared style block]

Alt hook for the same idea (more conceptual):
> A relay race baton being handed in a continuous loop, but one runner is a referee holding a
> red flag — the only one who can stop the race. Minimal, dark, editorial. [shared style block]

---

## 2. THE THREE-LAYER STACK (BUILD / RUN / TRUST)
> A clean vertical stack of three labeled horizontal layers. Top: "BUILD — LangGraph — did we
> construct it right?" Middle: "RUN — fleet telemetry — is it running?" Bottom (highlighted,
> drawn as a gate): "TRUST — Galileo — is it RIGHT?". A single agent request is a glowing line
> passing down through all three. Beside each layer, a small eye icon showing what it can SEE;
> the RUN layer's eye is half-closed (blind to quality), the TRUST layer's eye is open. Diagram,
> not art. Labels crisp. [shared style block]

---

## 3. XL-6 — THE IMPROVEMENT DISGUISE (the money visual)
> Split-panel, side by side. LEFT panel titled "WHAT THE DASHBOARD SHOWED": a latency line
> trending DOWN with a green ✓ and the words "LATENCY IMPROVED 🎉". RIGHT panel titled "WHAT WAS
> ACTUALLY HAPPENING": a quality/grounding line trending DOWN in red with "CONTEXT ADHERENCE
> FALLING". Same time axis on both. The unsettling point: both lines go down, but down means
> "good" on the left and "disaster" on the right. A thin vertical marker labeled "model swapped
> here". Editorial, dark, precise. [shared style block]

---

## 4. XL-2 — CONFIDENTLY WRONG (silent retrieval failure)
> A speech bubble from a sleek agent UI giving a confident, authoritative answer — but the answer
> text is visibly off-domain nonsense (e.g. a plumbing/gardening tip) while the surrounding chrome
> shows "✅ healthy · latency 0.9s · errors 0%". Big amber magnifier over a single metric reading
> "context_adherence ▼ crater". The contrast: perfect-looking system, confidently wrong output.
> [shared style block]

---

## 5. THE FLYWHEEL — eval → guardrail → regression
> A flywheel / closed loop of four stages with arrows: "EVAL catches it (dev)" → "SAME METRIC
> becomes PROTECT rule (prod)" → "bad output BLOCKED + escalated" → "failure LOCKED as regression
> test" → back to EVAL. Each cycle the ring is drawn slightly thicker/stronger with a small label
> "harder to break each cycle". One metric token (`context_adherence`) visibly travels the whole
> ring to show it's the *same* check doing all three jobs. Clean infographic. [shared style block]

---

## 6. (Optional) THE RALPH WIGGUM LOOP — quiet failure
> A loop that has quietly derailed: the "CHECK" node is replaced by a smiley "looks good to me 👍"
> rubber stamp instead of a real gate, and a stack of half-finished work piles up unnoticed while
> a token-cost meter climbs in the corner. Slightly absurd but technical. Caption space for
> "a reviewer with an opinion is not a gate." [shared style block]

---

## Placement suggestion
- Medium: #1 hero up top · #2 after "What We Built" · #4 in the XL-2 section (paired with the real
  Console screenshot) · #3 in the XL-6 section (paired with the real Console screenshot) · #5 in
  "Why Galileo Is the Gate".
- LinkedIn: #1 or #3 (the improvement-disguise reads great cold).
- X thread: #3 on the XL-6 tweet, #5 on the flywheel tweet, #1 as the lead image.

## Real charts (NOT generated — screenshot these)
- XL-2: Galileo Console → project `rax-galileo-labs` → stream `trinity-stack` → context_adherence
  over tags `xl2-baseline` vs `xl2-poisoned` (the crater).
- XL-6: same project → context_adherence + cites_kb_source trend over `xl6-baseline` vs
  `xl6-degraded` (quality down) alongside the latency drop.
