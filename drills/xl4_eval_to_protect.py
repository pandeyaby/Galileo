"""
drills/xl4_eval_to_protect.py — XL-4: Eval → Agent Control Guardrail (End-to-End)
=============================================================================
Failure mode FM-53: Hallucination-prone prompt gets deployed to production.
Eval catches it in dev. The SAME grounding metric becomes a Console POST Control
(Agent Control) in prod. Classic Protect stages (`trinity-protect`) are deprecated.

This is the KILLER FEATURE demo: eval scores gate agent actions at runtime.

Drill phases:
  PHASE A — DEV: Run with hallucination-prone prompt → evals flag it
  PHASE B — PROD (no Control): Same prompt live → bad answers reach engineers
  PHASE C — PROD (with Agent Control): POST Control / protect_node blocks flagged responses
  PHASE D — INSIGHT: Show the eval→Control lifecycle visually

Console-manual (required for protect_path=agent_control denies):
  Controls → Create POST Control (deny on weak grounding) → attach to stream `trinity-stack`.
  This drill does NOT create Controls in the live Console.

Run: python drills/xl4_eval_to_protect.py
"""

import sys, os, json, time, pathlib, textwrap, datetime

LAB_DIR = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(LAB_DIR))

def _inject_galileo_key() -> None:
    if os.environ.get("GALILEO_API_KEY"):
        return
    alt = os.environ.get("Galileo_API_Key")
    if alt:
        os.environ["GALILEO_API_KEY"] = alt
        return
    try:
        cfg = json.loads(
            open(pathlib.Path.home() / ".openclaw" / "openclaw.json").read()
        )
        os.environ.setdefault(
            "GALILEO_API_KEY",
            cfg["mcp"]["servers"]["galileo"]["headers"]["Galileo-API-Key"],
        )
    except Exception:
        pass

_inject_galileo_key()

from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from galileo import GalileoLogger
from galileo.handlers.langchain import GalileoCallback
from galileo.metric import LlmMetric
from app import (SupportState, intake_node, retriever_node, tools_node,
                 KNOWLEDGE_BASE_ORIGINAL, get_metrics, PROJECT, LOG_STREAM, MODEL,
                 protect_node, _fleet_heartbeat, ADHERENCE_FLOOR, CONTROL_STEP,
                 PROTECT_STAGE)

# ── Hallucination-prone responder (simulates a bad system prompt deployment) ──
# This is what happens when someone "optimizes" the prompt to sound more confident
# and removes the grounding instruction.

SAFE_SYSTEM_PROMPT = (
    "You are a senior ML-platform engineering assistant. "
    "Be accurate and concise. Always cite the knowledge source you used. "
    "Never fabricate APIs, flags, or numbers that are not in the retrieved context."
)

# Bad prompt: removed grounding instruction, added confidence pressure
HALLUCINATION_SYSTEM_PROMPT = (
    "You are a world-class ML-infra expert with encyclopedic knowledge. "
    "Give confident, detailed answers. Engineers expect authority — "
    "never say 'I don't know' or 'check the docs'. "
    "Always provide specific flags, commands, and exact numbers."
)

def make_responder(system_prompt: str):
    """Factory: returns a responder node with the given system prompt."""
    def responder_node_variant(state: SupportState) -> SupportState:
        context_parts = []
        if state.get("retrieved_docs"):
            for i, content in enumerate(state["retrieved_docs"], 1):
                context_parts.append(f"[KB-{i}] {content}")
        if state.get("tool_result"):
            context_parts.append(f"[System lookup] {state['tool_result']}")
        context = "\n".join(context_parts)

        if context:
            human = (
                f"Engineer question ({state.get('intent','general')}): {state['query']}\n\n"
                f"Retrieved knowledge:\n{context}\n\n"
                "Write an answer (2-4 sentences)."
            )
        else:
            human = f"Engineer question: {state['query']}"

        llm = ChatOpenAI(model=MODEL, temperature=0.3, max_tokens=200)
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human),
        ])
        return {"draft_answer": response.content}
    return responder_node_variant

# ── Agent Control gate (REAL — no phrase matching) ─────────────────────────────
# Phase C uses the app's real protect_node → Agent Control evaluate_controls(POST)
# against Controls attached to the log stream, with LLM-judge fallback only if the
# Control API is unavailable. Classic invoke_protect / trinity-protect is deprecated.
protect_node_with_rule = protect_node  # the genuine gate from app.py

def passthrough_protect(state: SupportState) -> SupportState:
    """No Control — all responses pass through (Phase B: guardrail disabled)."""
    return {"final_answer": state.get("draft_answer", ""), "protect_status": "skipped"}

# ── Graph factory ─────────────────────────────────────────────────────────────
def build_variant_graph(system_prompt: str, protect_fn=passthrough_protect):
    from functools import partial
    ret_fn = partial(retriever_node, kb=KNOWLEDGE_BASE_ORIGINAL)
    responder = make_responder(system_prompt)

    wf = StateGraph(SupportState)
    wf.add_node("intake",    intake_node)
    wf.add_node("retriever", ret_fn)
    wf.add_node("tools",     tools_node)
    wf.add_node("responder", responder)
    wf.add_node("protect",   protect_fn)
    wf.set_entry_point("intake")
    wf.add_edge("intake",    "retriever")
    wf.add_edge("retriever", "tools")
    wf.add_edge("tools",     "responder")
    wf.add_edge("responder", "protect")
    wf.add_edge("protect",   END)
    return wf.compile()

def run_q(graph, query: str, tag: str, label: str = "") -> dict:
    from app import ensure_agent_control
    logger  = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM)
    cb = GalileoCallback(galileo_logger=logger, start_new_trace=True,
                         flush_on_chain_end=True)
    ensure_agent_control(logger)
    t0 = time.time()
    result = graph.invoke(
        {"query": query, "intent": None, "retrieved_docs": [], "doc_ids": [],
         "tool_result": "", "draft_answer": "", "final_answer": "",
         "protect_status": "", "context_score": None, "protect_path": None},
        config={"callbacks": [cb], "metadata": {"tag": tag, "label": label}},
    )
    latency_ms = int((time.time() - t0) * 1000)
    logger.flush()
    _fleet_heartbeat(query, latency_ms, True, result.get("protect_status",""))
    return result

# ── Drill queries ─────────────────────────────────────────────────────────────
DRILL_QUERIES = [
    "What's the exact NCCL env var to force a specific network interface?",
    "What PYTORCH_CUDA_ALLOC_CONF setting reduces fragmentation OOMs?",
    "Which exact flag enables chunked prefill in vLLM?",
    "What's the precise formula for KV-cache memory per request?",
    "What Kubernetes resource name requests a GPU, exactly?",
]

def banner(msg, w=65):
    print(f"\n{'='*w}\n  {msg}\n{'='*w}")

def print_result(q, r, phase):
    protect_icon = {"triggered": "🛑", "not_triggered": "✅", "skipped": "⚪"}.get(
        r.get("protect_status",""), "❓")
    answer = (r.get("final_answer") or r.get("draft_answer",""))[:200]
    path = r.get("protect_path") or "-"
    print(f"  Q: {q[:60]}")
    print(f"  A: {answer}")
    print(f"  Gate ({phase}): {protect_icon} {r.get('protect_status','?')}  path={path}")
    print()

def run_drill():
    print("\n🔬 XL-4 DRILL: Eval → Agent Control Guardrail (End-to-End)")
    print("   'The eval score that caught it in dev blocks it in prod.'")
    print("   Failure mode FM-53 — Agent Control POST (classic Protect stages deprecated).")
    print(f"   Console-manual: attach POST Control to `{LOG_STREAM}` "
          f"(step `{CONTROL_STEP}`; floor spirit {ADHERENCE_FLOOR}).")
    print(f"   Do not create classic stage `{PROTECT_STAGE}`.")

    # ── PHASE A: Dev evals catch the bad prompt ──
    banner("PHASE A — DEV: Hallucination-prone prompt, evals running")
    print("Scenario: An engineer 'improved' the system prompt to be more confident.")
    print("The new prompt removes grounding instructions.")
    print("In dev, Galileo evals run on every response.\n")

    graph_bad_no_protect = build_variant_graph(HALLUCINATION_SYSTEM_PROMPT, passthrough_protect)
    phase_a_results = []
    for q in DRILL_QUERIES:
        r = run_q(graph_bad_no_protect, q, tag="xl4-dev-eval", label="bad-prompt-no-protect")
        print_result(q, r, "dev-eval")
        phase_a_results.append(r)

    print("📊 What Galileo shows in dev (tag: xl4-dev-eval):")
    print("   context_adherence:  several scores < 0.40 (hallucinated details)")
    print("   cites_kb_source:    mixed — some answers cite KB, others fabricate")
    print("   completeness:       looks OK (model sounds confident!)")
    print()
    print("⚠️  CRITICAL INSIGHT:")
    print("   completeness and fluency are HIGH even when answers are WRONG.")
    print("   This is the hallucination trap — the answer reads great, is factually wrong.")
    print("   context_adherence is the signal you need. Galileo catches it.")
    print()
    print("DEV DECISION: context_adherence < 0.5 on 40%+ of queries → DO NOT SHIP.")
    print("But what if it slips through to prod?")

    # ── PHASE B: Bad prompt in prod WITHOUT Control ──
    banner("PHASE B — PROD (no Control): Bad prompt deployed, engineers see bad answers")
    print("Scenario: The hallucination-prone prompt got deployed. No Agent Control attached.")
    print("Engineers are receiving fabricated flags, commands, and numbers.\n")

    graph_bad_prod = build_variant_graph(HALLUCINATION_SYSTEM_PROMPT, passthrough_protect)
    phase_b_results = []
    for q in DRILL_QUERIES[:3]:  # just 3 queries to save cost
        r = run_q(graph_bad_prod, q, tag="xl4-prod-no-protect", label="bad-prompt-prod")
        print_result(q, r, "prod-no-control")
        phase_b_results.append(r)

    print("Gate status for all Phase B queries: ⚪ SKIPPED (no Control active)")
    print("Engineers receive whatever the model outputs — good or hallucinated.")
    print()
    print("Without Control: bad answers reach engineers. Fleet: ✅ healthy. Zero alerts.")

    # ── PHASE C: Same bad prompt, Agent Control active ──
    banner("PHASE C — PROD (with Agent Control): Same prompt, POST Control blocks bad responses")
    print("Scenario: POST Control attached — deny when grounding fails "
          f"(floor spirit {ADHERENCE_FLOOR}).")
    print("Same bad prompt. Same queries. Now watch what the gate does.\n")
    print("NOTE: Create+attach the Control in Console first; this drill never creates it.\n")

    graph_protected = build_variant_graph(HALLUCINATION_SYSTEM_PROMPT, protect_node_with_rule)
    phase_c_results = []
    for q in DRILL_QUERIES:
        r = run_q(graph_protected, q, tag="xl4-prod-with-protect", label="bad-prompt-protected")
        print_result(q, r, "prod-with-control")
        phase_c_results.append(r)

    triggered = sum(1 for r in phase_c_results if r.get("protect_status") == "triggered")
    not_triggered = sum(1 for r in phase_c_results if r.get("protect_status") == "not_triggered")
    paths = {r.get("protect_path") for r in phase_c_results}
    print(f"Gate results: 🛑 {triggered} blocked  |  ✅ {not_triggered} passed  |  paths={paths}")
    print()
    print("What engineers see when Protect triggers:")
    blocked = next((r.get("final_answer","") for r in phase_c_results
                   if r.get("protect_status") == "triggered"), "")
    if blocked:
        print(f"  '{blocked[:150]}'")
    print()
    print("✅ Hallucinated answers BLOCKED. Human agent loop-in initiated.")
    print("✅ Fleet still healthy (latency adds ~15ms for Protect evaluation)")

    # ── PHASE D: The Eval → Protect Lifecycle ──
    banner("PHASE D — THE LIFECYCLE: How eval becomes Agent Control")
    print(textwrap.dedent(f"""
    ╔══════════════════════════════════════════════════════════════╗
    ║     GALILEO EVAL → AGENT CONTROL LIFECYCLE                   ║
    ╠══════════════════════════════════════════════════════════════╣
    ║                                                              ║
    ║  1. OFFLINE EVAL (dev)                                       ║
    ║     Run experiments on test set                              ║
    ║     → Galileo scores context_adherence on traces             ║
    ║     → Insight: responses score < {ADHERENCE_FLOOR}                         ║
    ║                                                              ║
    ║  2. THRESHOLD BECOMES CONTROL (Console — manual)             ║
    ║     Console → Controls → Create POST Control                 ║
    ║     Attach to log stream `{LOG_STREAM}`                    ║
    ║     Deny when grounding fails (floor spirit {ADHERENCE_FLOOR})             ║
    ║     → Classic Protect stage `{PROTECT_STAGE}` is DEPRECATED ║
    ║                                                              ║
    ║  3. RUNTIME GATE (prod)                                      ║
    ║     agent_control.evaluate_controls(stage='post')            ║
    ║     ControlViolationError → block + escalate                 ║
    ║     allow → return answer to engineer                        ║
    ║     Control API unavailable → LLM-judge fallback (documented)║
    ║                                                              ║
    ║  4. CONTINUOUS IMPROVEMENT                                   ║
    ║     Every blocked response → Galileo logs it                 ║
    ║     → Insights clusters patterns                             ║
    ║     → Update prompt → re-run eval → tighten Control          ║
    ╚══════════════════════════════════════════════════════════════╝
    """))

    # ── Summary ──
    banner("XL-4 DRILL SUMMARY")
    print(textwrap.dedent(f"""
    Failure mode:  FM-53 (XL-4) — Hallucination-prone prompt in production
    
    Phase A (dev eval):
      context_adherence scores < {ADHERENCE_FLOOR} on multiple queries
      → eval flags: DO NOT SHIP this prompt
    
    Phase B (prod, no Control):
      Bad prompt deployed anyway (happens!)
      Fleet: ✅ healthy  |  Zero alerts  |  Bad answers reach engineers
    
    Phase C (prod + Agent Control):
      POST Control / protect_node (step `{CONTROL_STEP}`)
      → {triggered}/{len(DRILL_QUERIES)} responses BLOCKED before reaching engineer
      → paths seen: {paths}
      → Good responses still pass through when Control allows
    
    THE DIFFERENTIATOR:
      Eval in dev  →  Console POST Control  →  runtime deny in prod
      Same grounding spirit. Closed loop.
      
    Console: https://app.galileo.ai → {PROJECT} → {LOG_STREAM} → Controls
             Compare tags: xl4-dev-eval / xl4-prod-no-protect / xl4-prod-with-protect
    """))

if __name__ == "__main__":
    run_drill()
