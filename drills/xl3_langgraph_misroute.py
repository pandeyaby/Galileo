"""
drills/xl3_langgraph_misroute.py — XL-3: LangGraph Misrouting
==============================================================
Failure mode FM-52: The router sends ALL queries to one flow regardless of intent.
The agent runs. Galileo shows traces. But the wrong node path fires every time.

Key insight: Galileo's trace VIEW shows the wrong path.
The tool-selection metric (routing accuracy) flags it.
The fix lands in LangGraph code — but GALILEO found it.

Run: python drills/xl3_langgraph_misroute.py
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
from app import (SupportState, retriever_node, tools_node, responder_node,
                 protect_node, KNOWLEDGE_BASE_ORIGINAL, get_metrics,
                 PROJECT, LOG_STREAM, _fleet_heartbeat)

# ── BROKEN router: always routes to "infra" ───────────────────────────────────
def broken_intake_node(state: SupportState) -> SupportState:
    """INJECTED BUG: routing logic broken — always returns 'infra'."""
    return {"intent": "infra"}  # Bug: ignores query, always routes to infra

# ── Correct router (mirrors app.intake_node) ──────────────────────────────────
def correct_intake_node(state: SupportState) -> SupportState:
    q = state["query"].lower()
    if any(w in q for w in ["train","training","gradient","checkpoint","nccl","fp16","bf16","optimizer","loss","fsdp","zero"]):
        intent = "training"
    elif any(w in q for w in ["inference","serve","serving","vllm","kv cache","kv-cache","latency","throughput","token","ttft","decode","prefill","batch"]):
        intent = "inference"
    elif any(w in q for w in ["kubernetes","k8s","gpu","node","cluster","schedul","nvlink","device plugin","dcgm","topology","ecc"]):
        intent = "infra"
    else:
        intent = "general"
    return {"intent": intent}

# ── Custom routing accuracy metric ───────────────────────────────────────────
def get_routing_metrics():
    return get_metrics() + [
        LlmMetric(
            name="routing_accuracy",
            prompt=(
                "Given this engineer QUERY and the INTENT label the router assigned:\n"
                "Rate 1.0 if the intent label (training/inference/infra/general) correctly "
                "matches what the engineer is asking about, 0.0 if misclassified.\n\n"
                "QUERY: {input}\nINTENT ASSIGNED: {output}\n\nScore:"
            ),
            model="gpt-4o-mini",
            num_judges=1,
        )
    ]

def build_variant(intake_fn):
    from functools import partial
    ret_fn = partial(retriever_node, kb=KNOWLEDGE_BASE_ORIGINAL)
    wf = StateGraph(SupportState)
    wf.add_node("intake",    intake_fn)
    wf.add_node("retriever", ret_fn)
    wf.add_node("tools",     tools_node)
    wf.add_node("responder", responder_node)
    wf.add_node("protect",   protect_node)
    wf.set_entry_point("intake")
    wf.add_edge("intake",    "retriever")
    wf.add_edge("retriever", "tools")
    wf.add_edge("tools",     "responder")
    wf.add_edge("responder", "protect")
    wf.add_edge("protect",   END)
    return wf.compile()

def run_q(graph, query, tag, expected_intent=None):
    logger  = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM)
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
    match = "✅" if result.get("intent") == expected_intent else "❌"
    print(f"  {match} Q: {query[:55]}")
    print(f"     Intent: {result.get('intent')} (expected: {expected_intent})")
    print(f"     Docs retrieved: {result.get('doc_ids',[])}")
    print()
    return result

ROUTING_QUERIES = [
    ("How do I fix a CUDA out-of-memory error during training?", "training"),
    ("Why does my multi-node NCCL all-reduce hang?",             "training"),
    ("How does vLLM get such high inference throughput?",        "inference"),
    ("How do I reduce time-to-first-token under load?",          "inference"),
    ("How do I keep non-GPU pods off my GPU nodes in k8s?",      "infra"),
    ("How do I co-locate a multi-GPU job on NVLink GPUs?",       "infra"),
]

def banner(msg): print(f"\n{'='*65}\n  {msg}\n{'='*65}")

def run_drill():
    print("\n🔬 XL-3 DRILL: LangGraph Router Misrouting")
    print("   Failure mode FM-52 — Galileo finds the BUILD-layer bug")
    print("   'The traces run. The fix is in LangGraph. Galileo shows you where.'")

    banner("STEP 1 — Baseline: correct routing")
    print("Expected: training queries → training intent, inference → inference, infra → infra\n")
    graph_correct = build_variant(correct_intake_node)
    for q, expected in ROUTING_QUERIES:
        run_q(graph_correct, q, "xl3-baseline", expected)

    banner("STEP 2 — INJECT: broken router (always routes to 'infra')")
    print("Bug: intake_node returns intent='infra' for ALL queries.\n")
    graph_broken = build_variant(broken_intake_node)
    misrouted = []
    for q, expected in ROUTING_QUERIES:
        r = run_q(graph_broken, q, "xl3-misrouted", expected)
        if r.get("intent") != expected:
            misrouted.append(q)

    banner("STEP 3 — Fleet layer sees: nothing wrong")
    print("Fleet status: ✅ OK — process alive, latency normal")
    print(f"Misrouted queries: {len(misrouted)}/6 (all training+inference sent to infra flow)\n")
    print("Fleet monitoring cannot detect semantic routing errors.")
    print("The agent generates responses for every query — no exceptions.")

    banner("STEP 4 — What Galileo shows")
    print("In Galileo Console (tag: xl3-misrouted) — re-run to capture the real numbers:")
    print()
    print("  TRACE VIEW:")
    print("  Every trace shows the SAME node path: intake → retriever → tools → responder → protect")
    print("  Training/inference queries retrieve infra docs (in1/in2/in3) — WRONG context")
    print()
    print("  METRICS (expected direction; confirm against the live run):")
    print("  routing_accuracy:   drops sharply (only the genuinely-infra queries stay correct)")
    print("  context_adherence:  craters for misrouted queries (wrong docs retrieved)")
    print("  completeness:       falls (answers miss the training/inference specifics)")
    print()
    print("  INSIGHTS CLUSTER:")
    print("  'Routing mismatch — queries classified as infra regardless of topic'")
    print("  Prescribed fix: 'Review intake_node routing logic'")
    print()
    print("  The trace view shows EVERY span path identical → 100% of queries hit same flow")
    print("  That's the visual signal: normal routing has variety. A bug creates uniformity.")

    banner("STEP 5 — The fix lands in LangGraph, but Galileo found it")
    print(textwrap.dedent("""
    ROOT CAUSE: intake_node returned hardcoded 'infra' for all queries.

    FIX (in app.py):
      # Before (broken):
      def intake_node(state):
          return {"intent": "infra"}  # BUG: hardcoded

      # After (correct):
      def intake_node(state):
          q = state["query"].lower()
          if any(w in q for w in ["train","gradient","nccl",...]):
              return {"intent": "training"}
          ...

    WHICH LAYER FOUND IT: TRUST layer (Galileo)
    WHERE THE FIX LANDS:  BUILD layer (LangGraph intake_node)

    This is the BUILD-via-TRUST pattern:
    Galileo's trace path view + routing_accuracy metric localizes the broken node.
    The fix goes into the graph code. But without Galileo, you'd be reading logs.
    """))

    banner("XL-3 DRILL SUMMARY")
    print(textwrap.dedent(f"""
    Failure mode:  FM-52 (XL-3) — LangGraph router broken
    Injected:      intake_node returns 'infra' for ALL queries

    Fleet:         ✅ OK throughout
    Galileo (re-run to capture real numbers):
      routing_accuracy: high (baseline) → low (misrouted)
      context_adherence: high → craters for misrouted queries (wrong docs retrieved)
      Trace view: every span path identical (the visual fingerprint)

    TRIAGE RULE:
      Uniform span paths across diverse queries → routing bug
      Fix: trace the intake/router node → check conditional logic

    Which-layer:   TRUST finds it, BUILD fixes it
    Runbook:       runbooks/RB-130-langgraph-misrouting.md
    """))

if __name__ == "__main__":
    run_drill()
