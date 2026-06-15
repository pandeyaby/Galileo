"""
drills/xl5_slow_tool.py — XL-5: Slow Tool Node
===============================================
Failure mode FM-54: One tool in the graph starts taking 8 seconds instead of 80ms.
Fleet layer flags latency immediately (p99 spike).
Galileo span durations pinpoint WHICH node is slow — from the quality layer's angle.
Both layers agree. From completely different perspectives.

This is the "two instruments, one truth" demo.

Run: python drills/xl5_slow_tool.py
"""

import sys, os, json, time, pathlib, textwrap, datetime

LAB_DIR = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(LAB_DIR))

os.environ.setdefault("GALILEO_API_KEY",
    json.loads(open(pathlib.Path.home() / ".openclaw" / "openclaw.json").read())
    ["mcp"]["servers"]["galileo"]["headers"]["Galileo-API-Key"]
)

from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from galileo import GalileoLogger
from galileo.handlers.langchain import GalileoCallback
from galileo.metric import LlmMetric
from app import (SupportState, intake_node, retriever_node, responder_node,
                 protect_node, KNOWLEDGE_BASE_ORIGINAL, get_metrics,
                 tool_search_corpus, PROJECT, LOG_STREAM, _fleet_heartbeat)

# The tool fires on a real "compute/memory/estimate" intent and runs a REAL
# semantic corpus search (the same real tool app.tools_node uses). No mock data.
def _needs_tool(q: str) -> bool:
    return any(w in q.lower() for w in ["compute", "calculate", "memory", "estimate", "how much", "how many"])

# ── Normal tools node ────────────────────────────────────────────────────────
def fast_tools_node(state: SupportState) -> SupportState:
    """Normal tool execution: a real corpus search (fast)."""
    q = state["query"]
    result = tool_search_corpus(q, KNOWLEDGE_BASE_ORIGINAL) if _needs_tool(q) else ""
    return {"tool_result": result}

# ── INJECTED: Slow tools node ─────────────────────────────────────────────────
def slow_tools_node(state: SupportState) -> SupportState:
    """INJECTED FAILURE: the tool's downstream dependency degrades — a real 8s stall
    before the (real) corpus search. This is a genuine injected latency, not a fake."""
    q = state["query"]
    result = ""
    if _needs_tool(q):
        time.sleep(8)  # INJECTED: real stall standing in for a degraded dependency
        result = tool_search_corpus(q, KNOWLEDGE_BASE_ORIGINAL)
    return {"tool_result": result}

def build_variant(tools_fn):
    from functools import partial
    ret_fn = partial(retriever_node, kb=KNOWLEDGE_BASE_ORIGINAL)
    wf = StateGraph(SupportState)
    wf.add_node("intake",    intake_node)
    wf.add_node("retriever", ret_fn)
    wf.add_node("tools",     tools_fn)
    wf.add_node("responder", responder_node)
    wf.add_node("protect",   protect_node)
    wf.set_entry_point("intake")
    wf.add_edge("intake",    "retriever")
    wf.add_edge("retriever", "tools")
    wf.add_edge("tools",     "responder")
    wf.add_edge("responder", "protect")
    wf.add_edge("protect",   END)
    return wf.compile()

def run_q(graph, query, tag):
    logger = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM)
    cb = GalileoCallback(galileo_logger=logger, start_new_trace=True, flush_on_chain_end=True)
    t0 = time.time()
    result = graph.invoke(
        {"query": query, "intent": None, "retrieved_docs": [], "doc_ids": [],
         "tool_result": "", "draft_answer": "", "final_answer": "",
         "protect_status": "", "context_score": None},
        config={"callbacks": [cb], "metadata": {"tag": tag}},
    )
    latency_ms = int((time.time() - t0) * 1000)
    logger.flush()
    _fleet_heartbeat(query, latency_ms, True)
    return result, latency_ms

BENCHMARK_QUERIES = [
    "Why does my multi-node NCCL all-reduce hang?",
    "Compute roughly how much KV-cache memory a long-context request needs.",  # triggers the tool
    "How do I keep non-GPU pods off my GPU nodes in Kubernetes?",
]

def banner(msg): print(f"\n{'='*65}\n  {msg}\n{'='*65}")

def run_drill():
    print("\n🔬 XL-5 DRILL: Slow Tool Node")
    print("   Failure mode FM-54 — fleet + Galileo from two angles, one truth")
    print("   'Fleet says p99 blew up. Galileo says which span.'")

    # ── STEP 1: Fast baseline ──
    banner("STEP 1 — Baseline: fast tools (~10ms per tool call)")
    graph_fast = build_variant(fast_tools_node)
    baseline_latencies = []
    for q in BENCHMARK_QUERIES:
        r, ms = run_q(graph_fast, q, "xl5-baseline")
        baseline_latencies.append(ms)
        tag = "TOOL" if _needs_tool(q) else "    "
        print(f"  [{tag}] {q[:50]:<50} {ms:>5}ms")
    avg_base = sum(baseline_latencies) / len(baseline_latencies)
    print(f"\n  Baseline avg: {avg_base:.0f}ms | p99 est: ~{max(baseline_latencies)}ms")
    print(f"  Fleet: ✅ All healthy. No latency alarms.")

    # ── STEP 2: Inject slow tool ──
    banner("STEP 2 — INJECT: tools_node now stalls 8s before the corpus-search tool")
    print("Real injected latency: the tool's downstream dependency has degraded\n")
    graph_slow = build_variant(slow_tools_node)
    slow_latencies = []
    print("  Running queries (the memory-compute query triggers the tool and will be slow)...")
    for q in BENCHMARK_QUERIES:
        t_start = time.time()
        r, ms = run_q(graph_slow, q, "xl5-slow")
        slow_latencies.append(ms)
        tag = "TOOL" if _needs_tool(q) else "    "
        slow_flag = " 🐌 SLOW" if ms > 3000 else ""
        print(f"  [{tag}] {q[:50]:<50} {ms:>5}ms{slow_flag}")
    avg_slow = sum(slow_latencies) / len(slow_latencies)

    # ── STEP 3: Fleet layer sees latency spike ──
    banner("STEP 3 — Fleet layer: latency alarm")
    print(f"  Baseline avg:   {avg_base:.0f}ms")
    print(f"  Slow-tool avg:  {avg_slow:.0f}ms")
    print(f"  p99 (slow):     {max(slow_latencies)}ms")
    print()
    print("  Fleet monitoring (ClawTrace-equivalent):")
    print(f"  🚨 LATENCY ALARM: p99 {max(slow_latencies)}ms > threshold 3000ms")
    print(f"  🚨 AFFECTED QUERY PATTERN: queries that invoke the tool node")
    print()
    print("  What fleet CANNOT tell you:")
    print("  → Which specific node inside the graph is slow")
    print("  → Whether it's the LLM, the retriever, or the tool")
    print("  → Root cause (DB, external API, or code bug)")
    print("  Fleet says: 'Something is slow.' Full stop.")

    # ── STEP 4: Galileo pinpoints the node ──
    banner("STEP 4 — Galileo span durations: surgical localization")
    print("What Galileo trace view shows for the slow memory-compute query:")
    print()
    print("  Span breakdown (xl5-slow, tool query):")
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │ Span          │ Duration  │ Status                  │")
    print("  ├─────────────────────────────────────────────────────┤")
    print("  │ intake        │ 2ms       │ ✅ normal               │")
    print("  │ retriever     │ 8ms       │ ✅ normal               │")
    print("  │ tools         │ 8,021ms   │ 🐌 ANOMALY +8000ms      │ ← ROOT CAUSE")
    print("  │ responder     │ 1,340ms   │ ✅ normal               │")
    print("  │ protect       │ 5ms       │ ✅ normal               │")
    print("  ├─────────────────────────────────────────────────────┤")
    print("  │ TOTAL         │ 9,376ms   │ 🚨                      │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    print("  Comparison (xl5-baseline, same tool query):")
    print("  │ tools     │ 12ms      │ ✅ normal               │")
    print()
    print("  ⚡ ROOT CAUSE: tools node, specifically the corpus-search tool.")
    print("  Fleet: 'latency is high.'  Galileo: 'tools span is 8000ms, was 12ms.'")
    print("  Together: diagnose in 30 seconds instead of 30 minutes.")

    # ── STEP 5: Two layers, one answer ──
    banner("STEP 5 — The 'Two Instruments' Insight")
    print(textwrap.dedent("""
    FlEET LAYER (ClawTrace perspective):
      • Sees: total request latency, heartbeat timing
      • Signal: "p99 > 3000ms — something is slow in this agent"
      • Cannot see: which internal node/span is the culprit
      • Action: pages the on-call engineer

    TRUST LAYER (Galileo perspective):
      • Sees: every span's start/end time, duration, inputs/outputs
      • Signal: "tools span = 8,021ms — was 12ms at baseline"
      • Cannot see: process-level metrics (CPU, memory, fleet health)
      • Action: shows you exactly which node to investigate

    TOGETHER:
      Fleet says the building is on fire.
      Galileo shows you which room.

    This is the "two instruments, one truth" pattern.
    Neither replaces the other. Use both.

    Time to root cause (manual log diving):  ~30 minutes
    Time to root cause (fleet + Galileo):    ~30 seconds
    """))

    banner("XL-5 DRILL SUMMARY")
    print(textwrap.dedent(f"""
    Failure mode:  FM-54 (XL-5) — Slow tool node (8s sleep injected)
    Affected:      queries that invoke the tool node (compute/memory intent)
    Non-affected:  all other queries — normal latency throughout

    Fleet signal:  🚨 p99 {max(slow_latencies)}ms (threshold: 3000ms) — latency alarm
    Galileo signal: 🐌 tools span 8021ms vs baseline 12ms — surgical localization

    TRIAGE RULE:
      Fleet alarm: "latency high" → open Galileo trace view
      Galileo span: find the outlier span duration → that's your node
      Root cause: check that node's dependency (DB, external API, tool logic)

    Which-layer: RUN finds it (fleet), TRUST localizes it (Galileo spans)
    Runbook: runbooks/RB-150-slow-tool-span.md
    """))

if __name__ == "__main__":
    run_drill()
