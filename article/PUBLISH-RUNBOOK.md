# Publish runbook + readiness gate — trinity-stack article

Status as of 2026-06-13 (Cowork). Owner of live steps: **RAX** (runs on the Mac; Cowork can't
run the venv/keys). Owner of content edits: Cowork/Abhi.

## What's DONE (no live run needed)
- ✅ Articles realigned to the loop-engineering thesis (`references/loop-engineering-thesis.md`).
- ✅ `linkedin-post.md`, `x-thread.md` rewritten + re-skinned to the AI-platform engineering domain.
- ✅ `medium-full.md` reframed: new title, voice-draft intro (Abhi's style), "Why Galileo Is the
  Gate", thesis conclusion + sign-off.
- ✅ `IMAGE-PROMPTS.md` — image-gen prompts for hero + diagrams + flywheel.

## What's BLOCKED on a live drill run (RAX executes)

### Step 1 — Re-run the drills with the de-mocked build, capture REAL numbers
```bash
cd ~/.openclaw/workspace/galileo-labs/trinity-stack
source .venv/bin/activate                 # the lab's python3.14 venv
pip install numpy psutil                   # de-mocked build needs these (confirm langgraph,
                                           # langchain-openai, galileo already present)
# keys must be in env: OPENAI_API_KEY (embeddings+LLM) and GALILEO_API_KEY (from MCP config)
mkdir -p article/results article/screenshots

python app.py --batch            2>&1 | tee article/results/baseline.txt
python drills/xl1_process_dead.py        2>&1 | tee article/results/xl1.txt
python drills/xl2_poisoned_retriever.py  2>&1 | tee article/results/xl2.txt
python drills/xl3_langgraph_misroute.py  2>&1 | tee article/results/xl3.txt
python drills/xl4_eval_to_protect.py     2>&1 | tee article/results/xl4.txt
python drills/xl5_slow_tool.py           2>&1 | tee article/results/xl5.txt
python drills/xl6_model_regression.py    2>&1 | tee article/results/xl6.txt
python app.py --restore-corpus           # safety: restore real KB if any drill left it poisoned
```
Then pull the REAL values from the result files (these replace the illustrative numbers):
- XL-2: context_adherence baseline → poisoned (the crater)
- XL-3: routing_accuracy baseline → misrouted
- XL-4: how many responses Protect blocked; the dev adherence on the bad prompt
- XL-5: tools span ms baseline vs slow; fleet p99
- XL-6: context_adherence + cites_kb_source baseline → degraded; latency delta (the twist)

### Step 4 — Screenshot the Galileo Console (real charts)
Galileo Console (app.galileo.ai) → project `rax-galileo-labs` → log stream `trinity-stack`:
- **XL-2 chart:** context_adherence over tags `xl2-baseline` vs `xl2-poisoned` (the crater).
  Save → `article/screenshots/xl2-context-adherence-crater.png`
- **XL-6 chart:** context_adherence + cites_kb_source over `xl6-baseline` vs `xl6-degraded`,
  alongside the latency drop (the improvement-disguise). Save → `article/screenshots/xl6-regression.png`

### Step 2 — Update the Medium body with the real outputs
In `medium-full.md`, per-drill sections (Failure 1–6): replace any remaining "CloudMetrics" /
support-domain wording with the AI-platform engineering domain, and swap illustrative numbers for
the REAL ones from `article/results/*.txt`. Embed the two screenshots in the XL-2 and XL-6 sections.
(RAX can do this, or hand the `results/` files to Cowork and I'll fold them in precisely.)

## Publish-readiness gate (Step 7) — ready to publish when ALL are ✅
- [ ] Drills re-run on the de-mocked build; `article/results/*.txt` captured
- [ ] Real numbers folded into `medium-full.md` (and linkedin/x where specific numbers appear)
- [ ] Galileo Console screenshots saved + embedded (XL-2 crater, XL-6 twist)
- [ ] Abhi approved/edited the voice-draft intro + sign-off
- [ ] Hero + diagram images generated from `IMAGE-PROMPTS.md` and placed
- [ ] All `[link]` placeholders filled (repo link, cross-links between the 3 pieces)
- [ ] Final read-through; no "illustrative/pending" caveats or `<!-- DRAFT -->` comments left
- [ ] (Optional) Medium publication chosen (e.g. Towards Data Science / Level Up Coding)

When every box is checked, it's ready. Cowork will confirm the gate on request.
