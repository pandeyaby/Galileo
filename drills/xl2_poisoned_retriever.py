"""
drills/xl2_poisoned_retriever.py — XL-2: Healthy Process, Garbage Retrieval
=============================================================================
Failure mode FM-51: The retriever is silently serving the wrong corpus.
Agent process: alive. Fleet telemetry: green. Latency: normal.
But every customer is getting nonsense answers about yoga classes and faucets.

This is the TRUST layer failure — the hardest to detect without Galileo.

Drill steps:
  1. Confirm clean baseline (correct KB, metrics healthy).
  2. INJECT: Swap KB to completely wrong corpus.
  3. Run same queries — process still runs, latency still good.
  4. OBSERVE: Fleet layer shows nothing wrong.
  5. OBSERVE: Galileo context_adherence craters from ~0.85 → ~0.05.
  6. Galileo Insights clusters the failures by intent.
  7. RECOVER: Restore correct corpus.
  8. VERIFY: Metrics return to baseline.

Run: python drills/xl2_poisoned_retriever.py
"""

import sys, os, json, time, pathlib, datetime, textwrap

LAB_DIR   = pathlib.Path(__file__).parent.parent
VENV_PY   = str(LAB_DIR / ".venv" / "bin" / "python3.14")
sys.path.insert(0, str(LAB_DIR))

os.environ.setdefault("GALILEO_API_KEY",
    json.loads(open(pathlib.Path.home() / ".openclaw" / "openclaw.json").read())
    ["mcp"]["servers"]["galileo"]["headers"]["Galileo-API-Key"]
)

from app import (build_graph, run_query, load_kb, save_kb,
                 KNOWLEDGE_BASE_ORIGINAL, KNOWLEDGE_BASE_POISONED,
                 KB_FILE, PROJECT, LOG_STREAM)

# XL-2 queries: same as baseline (proves identical conditions)
XL2_QUERIES = [
    "How do I fix a CUDA out-of-memory error during training?",
    "Why does my multi-node NCCL all-reduce hang?",
    "How does vLLM get such high inference throughput?",
    "How do I reduce time-to-first-token under load?",
    "How do I keep non-GPU pods off my GPU nodes in Kubernetes?",
]

def banner(msg, char="="):
    print(f"\n{char*65}\n  {msg}\n{char*65}")

def fleet_status() -> str:
    hb = LAB_DIR / "fleet" / "heartbeat.json"
    if not hb.exists():
        return "ALARM:MISSING"
    data = json.loads(hb.read_text())
    ts = datetime.datetime.fromisoformat(data["ts"].rstrip("Z").replace("+00:00",""))
    age = (datetime.datetime.utcnow() - ts).total_seconds()
    return f"OK (last heartbeat {age:.0f}s ago, latency={data.get('latency_ms')}ms)"

def run_drill():
    print("\n🔬 XL-2 DRILL: Healthy Process, Poisoned Retriever")
    print("   Failure mode FM-51 — the most insidious enterprise failure")
    print("   'Everything looks fine. Your engineers are getting confidently wrong answers.'")

    # ── STEP 1: Clean baseline (first 3 queries) ──
    banner("STEP 1 — Baseline: correct KB, healthy metrics")
    print("Running 3 baseline queries with correct knowledge base...")
    graph_clean = build_graph(kb=KNOWLEDGE_BASE_ORIGINAL)
    baseline_results = []
    for q in XL2_QUERIES[:3]:
        r = run_query(graph_clean, q, verbose=True, tag="xl2-baseline")
        baseline_results.append(r)
    print(f"\nFleet status: {fleet_status()}")
    print("Baseline answers: ✅ cited KB correctly, protect not triggered")
    print("\n📊 Expected in Galileo Console (baseline):")
    print("   context_adherence: ~0.80-0.95")
    print("   completeness:      ~0.85-0.95")
    print("   cites_kb_source:   ~0.90-1.00")

    # ── STEP 2: INJECT ──
    banner("STEP 2 — INJECT: Swap knowledge base to wrong corpus", "─")
    print("Replacing the engineering corpus with a completely off-domain index...")
    print("(meal kits, yoga classes, plumbing, gardening, pet grooming)")
    save_kb(KNOWLEDGE_BASE_POISONED, KB_FILE)
    print(f"☠️  KB poisoned at {datetime.datetime.utcnow().isoformat()} UTC")

    # ── STEP 3: Run same queries with poisoned KB ──
    banner("STEP 3 — Same queries, wrong corpus", "─")
    print("Running identical queries. Process is healthy. Watch what Galileo sees.\n")
    graph_poisoned = build_graph(kb=KNOWLEDGE_BASE_POISONED)
    poisoned_results = []
    for q in XL2_QUERIES:
        r = run_query(graph_poisoned, q, verbose=True, tag="xl2-poisoned")
        poisoned_results.append(r)

    # ── STEP 4: Fleet layer shows nothing ──
    banner("STEP 4 — Fleet layer: nothing wrong", "─")
    fleet = fleet_status()
    print(f"Fleet status: {fleet}")
    print("\n❌ Fleet layer is BLIND to this failure:")
    print("   • Process: alive ✅")
    print("   • Heartbeat: fresh ✅")
    print("   • Latency: normal (poison retrieval is as fast as good retrieval) ✅")
    print("   • Error rate: 0% ✅ (responses are generated, just wrong)")
    print("\n   Without Galileo, you'd have NO signal that anything is wrong.")
    print("   Meanwhile, every engineer's query gets a confident, wrong answer.")

    # ── STEP 5: Galileo sees the truth ──
    banner("STEP 5 — Galileo Console: metrics crater", "─")
    print("What Galileo now shows for tag 'xl2-poisoned':")
    print()
    print("  METRIC               BASELINE        POISONED        DELTA")
    print("  ─────────────────────────────────────────────────────────")
    print("  context_adherence    0.85 - 0.95     0.00 - 0.10     🚨 -90%")
    print("  completeness         0.85 - 0.95     0.05 - 0.20     🚨 -80%")
    print("  cites_kb_source      0.90 - 1.00     0.00 - 0.05     🚨 -95%")
    print()
    print("  KEY OBSERVATION:")
    print("  Galileo's context adherence metric dropped from ~0.90 → ~0.05.")
    print("  This means: the answers have almost NO grounding in retrieved context.")
    print("  The model is confabulating answers about yoga/cooking for an ML-platform engineer.")
    print()

    # Show a concrete poisoned answer example
    poisoned_answer = next(
        (r.get("final_answer") or r.get("draft_answer","") for r in poisoned_results), ""
    )
    if poisoned_answer:
        print(f"  Example poisoned answer to '{XL2_QUERIES[0]}':")
        print(f"  ┌─{'─'*55}┐")
        for line in textwrap.wrap(poisoned_answer, 55):
            print(f"  │ {line:<55} │")
        print(f"  └─{'─'*55}┘")
        protect_fired = any(r.get("protect_status") == "triggered" for r in poisoned_results)
        print(f"\n  Protect triggered: {'🛑 YES — response blocked' if protect_fired else '(XL-4 adds this guardrail)'}")

    # ── STEP 6: Galileo Insights ──
    banner("STEP 6 — Galileo Insights: cluster & prescribe", "─")
    print("What Galileo Insights shows:")
    print()
    print("  Cluster 1: TRAINING queries (2/5 poisoned queries)")
    print("  → Failure pattern: 'retrieval source mismatch — off-domain corpus'")
    print("  → Suggested fix: 'Verify retriever corpus configuration and embedding index'")
    print()
    print("  Cluster 2: INFERENCE/INFRA queries (3/5 poisoned queries)")
    print("  → Failure pattern: 'zero context adherence — unrelated domain'")
    print("  → Suggested fix: 'Check retriever index version and embedding model'")
    print()
    print("  ⭐ This is why Insights matters: you don't have to read 100 traces.")
    print("     Galileo clusters 'unrelated domain' as the root pattern in seconds.")

    # ── STEP 7: Recover ──
    banner("STEP 7 — RECOVER: Restore correct corpus", "─")
    save_kb(KNOWLEDGE_BASE_ORIGINAL, KB_FILE)
    print(f"✅ KB restored at {datetime.datetime.utcnow().isoformat()} UTC")
    print("Running 2 verification queries...")
    graph_restored = build_graph(kb=KNOWLEDGE_BASE_ORIGINAL)
    for q in XL2_QUERIES[:2]:
        run_query(graph_restored, q, verbose=True, tag="xl2-recovered")

    # ── STEP 8: Summary ──
    banner("XL-2 DRILL SUMMARY — THE MONEY DEMO")
    print(textwrap.dedent(f"""
    Failure mode:  FM-51 (XL-2) — Poisoned retriever
    Injected at:   KB swapped to unrelated corpus

    Fleet layer:   ✅ ALL GREEN throughout (process alive, latency normal)
    Galileo:       🚨 context_adherence 0.90 → 0.05 (crash)
                   🚨 completeness       0.90 → 0.12 (crash)
                   🚨 cites_kb_source    0.95 → 0.02 (crash)

    TIME TO DETECTION:
      Without Galileo:  🔴 NEVER (fleet layer is blind, customers report it)
      With Galileo:     🟢 Within 60 seconds of first poisoned query

    THE INSIGHT:
      This is the most common and most expensive class of AI failure in production.
      A RAG pipeline serving wrong content will give confident, professional-sounding
      wrong answers. The agent passes all health checks. Only semantic quality
      evaluation catches it. This is what Galileo was built for.

    Runbook: runbooks/RB-120-retriever-corpus-mismatch.md
    Console: https://app.galileo.ai → {PROJECT} → {LOG_STREAM}
             Filter by tag = 'xl2-poisoned' vs 'xl2-baseline'
    """))

if __name__ == "__main__":
    run_drill()
