#!/usr/bin/env python3
"""
Trinity Stack on DizzyGraph — live BUILD / RUN / TRUST path.

Reuses production node logic from ``app.py`` (real embeddings, tools, LLM, Protect)
and executes via DizzyGraph with GalileoLogger traces.

Usage:
  python trinity_dizzy.py "How do I debug a CUDA OOM during training?"
  python trinity_dizzy.py --meta 2 --viz "Explain vLLM PagedAttention"
  python trinity_dizzy.py --mock          # offline topology only
  python trinity_dizzy.py --write-evidence  # save JSON under dizzygraph_out/

See: docs/RUNBOOK-DIZZYGRAPH.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dizzygraph import (
    AtomicNode,
    Graph,
    GraphExecutor,
    LoopNode,
    MetaLoopExecutor,
    State,
    visualize_graph,
)

log = logging.getLogger("trinity_dizzy")

OUT_DIR = ROOT / "dizzygraph_out"
PROJECT = "rax-galileo-labs"
LOG_STREAM = "trinity-dizzy"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def load_runtime_keys() -> dict[str, bool]:
    """Load OpenAI + Galileo keys from lab .env and OpenClaw config (never echo)."""
    for path in (
        ROOT / ".env",
        Path.home() / ".openclaw" / "workspace" / "galileo-labs" / ".env",
        Path.home() / "Documents" / "GitHub" / "Galileo" / "galileo-labs" / ".env",
    ):
        _load_dotenv(path)

    cfg = Path.home() / ".openclaw" / "openclaw.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            gkey = (
                data.get("mcp", {})
                .get("servers", {})
                .get("galileo", {})
                .get("headers", {})
                .get("Galileo-API-Key", "")
            )
            if gkey:
                os.environ.setdefault("GALILEO_API_KEY", gkey)
            okey = (data.get("env") or {}).get("OPENAI_API_KEY", "")
            if okey:
                os.environ.setdefault("OPENAI_API_KEY", okey)
        except Exception as exc:
            log.warning("openclaw.json parse skipped: %s", type(exc).__name__)

    return {
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "galileo": bool(os.environ.get("GALILEO_API_KEY")),
    }


def _mock_pipeline_graph() -> Graph:
    """Offline graph mirroring Trinity topology (no cloud calls)."""

    def intake(s: State) -> dict:
        q = (s.data.get("query") or "").lower()
        intent = "training" if "cuda" in q or "train" in q else "general"
        return {"data": {"intent": intent}}

    def retrieve(s: State) -> dict:
        return {
            "data": {
                "retrieved_docs": [
                    "CUDA OOM: lower batch size, gradient checkpointing, or ZeRO/FSDP."
                ],
                "doc_ids": ["tr1"],
                "context_score": 0.82,
            }
        }

    def tools(s: State) -> dict:
        return {"data": {"tool_result": ""}}

    def respond_maker(s: State) -> dict:
        docs = s.data.get("retrieved_docs") or []
        draft = f"Based on KB: {docs[0][:160]}" if docs else "No context."
        boost = int(s.data.get("meta_boost", 0))
        if boost:
            draft += f" (meta-refine pass {boost})"
        return {"data": {"draft_answer": draft}}

    def respond_check(s: State) -> float:
        draft = s.data.get("draft_answer") or ""
        return 0.9 if "batch" in draft.lower() or "oom" in draft.lower() or len(draft) > 40 else 0.5

    def protect(s: State) -> dict:
        score = float(s.data.get("context_score") or 0)
        draft = s.data.get("draft_answer") or ""
        if score < 0.5:
            return {
                "data": {
                    "final_answer": "[BLOCKED] grounding check failed",
                    "protect_status": "triggered",
                },
                "done": True,
            }
        return {
            "data": {"final_answer": draft, "protect_status": "not_triggered"},
            "done": True,
            "results": [{"answer": draft}],
        }

    g = Graph(id="trinity-mock", name="Trinity (DizzyGraph mock)")
    g.add_node(AtomicNode(id="intake", fn=intake))
    g.add_node(AtomicNode(id="retriever", fn=retrieve))
    g.add_node(AtomicNode(id="tools", fn=tools))
    g.add_node(
        LoopNode(
            id="responder",
            name="ResponderLoop",
            maker=respond_maker,
            checker=respond_check,
            max_iters=3,
            score_threshold=0.85,
            score_key="quality",
        )
    )
    g.add_node(AtomicNode(id="protect", fn=protect))
    g.set_entry("intake")
    g.add_edge("intake", "retriever")
    g.add_edge("retriever", "tools")
    g.add_edge("tools", "responder")
    g.add_edge("responder", "protect")
    return g


def evaluate_protect_score(s: State, *, trinity_mod=None) -> dict:
    """
    Live Protect evaluation → numeric score for LoopNode checker.

    Uses ``app.protect_node`` (invoke_protect + LLM-judge fallback). Returns
    score in [0, 1] where ≥ ADHERENCE_FLOOR means Protect would pass.
    """
    import app as trinity

    mod = trinity_mod or trinity
    floor = float(getattr(mod, "ADHERENCE_FLOOR", 0.5))
    st = {
        "query": s.data.get("query") or "",
        "retrieved_docs": s.data.get("retrieved_docs") or [],
        "draft_answer": s.data.get("draft_answer") or "",
        "context_score": s.data.get("context_score"),
    }
    out = mod.protect_node(st)  # type: ignore[arg-type]
    status = str(out.get("protect_status") or "not_triggered")
    path = str(out.get("protect_path") or "")
    raw_score = out.get("context_score")
    if raw_score is None:
        # invoke_protect path may omit numeric score — map status honestly
        score = 0.9 if status == "not_triggered" else max(0.0, floor - 0.2)
    else:
        score = float(raw_score)
    if status == "triggered":
        score = min(score, floor - 1e-6) if floor > 0 else 0.0
    return {
        "score": score,
        "protect_status": status,
        "protect_path": path,
        "final_answer": out.get("final_answer"),
        "context_score": score if raw_score is None else float(raw_score),
        "adherence_floor": floor,
    }


def build_trinity_dizzy_graph(
    *,
    kb=None,
    protect_enabled: bool = True,
    hitl_on_protect: bool = True,
    max_loop_iters: int = 3,
    xl_mode: str | None = None,
) -> Graph:
    """
    Wire live ``app.py`` callables into DizzyGraph.

    Responder is a ``LoopNode`` whose **checker is live Protect** (not a keyword
    heuristic). Non-convergence surfaces ``loop_converged=False`` → fleet
    ``loop_non_converge`` with Protect status/path/score in state metrics.
    When Protect triggers on the final gate, ``interrupt()`` for HITL approve/edit.
    """
    from dizzygraph.interrupt import interrupt

    import app as trinity

    if kb is not None:
        pass
    elif xl_mode == "xl2":
        kb = trinity.load_kb(poisoned=True)
    else:
        kb = trinity.load_kb()
    ret_fn = partial(trinity.retriever_node, kb=kb)
    floor = float(getattr(trinity, "ADHERENCE_FLOOR", 0.5))

    def intake(s: State) -> dict:
        # XL-1: simulate process death — hard fail before work
        if (xl_mode or s.data.get("xl_mode")) == "xl1":
            raise RuntimeError("XL-1 process dead: agent heartbeat lost (simulated kill)")
        st = {"query": s.data["query"]}
        out = trinity.intake_node(st)  # type: ignore[arg-type]
        mode = xl_mode or s.data.get("xl_mode")
        # XL-3: force wrong intent (misroute)
        if mode == "xl3":
            wrong = "inference" if out.get("intent") == "training" else "training"
            out = {**out, "intent": wrong, "xl_misroute": True}
        return {"data": dict(out)}

    def retriever(s: State) -> dict:
        st = {"query": s.data["query"], "intent": s.data.get("intent")}
        out = ret_fn(st)  # type: ignore[arg-type]
        return {"data": dict(out)}

    def tools(s: State) -> dict:
        mode = xl_mode or s.data.get("xl_mode")
        # XL-5: slow tool node
        if mode == "xl5":
            time.sleep(8.0)
        st = {
            "query": s.data["query"],
            "intent": s.data.get("intent"),
            "retrieved_docs": s.data.get("retrieved_docs"),
        }
        out = trinity.tools_node(st)  # type: ignore[arg-type]
        if mode == "xl5":
            out = {**out, "tool_slow_s": 8.0}
        return {"data": dict(out)}

    def respond_maker(s: State) -> dict:
        st = {
            "query": s.data["query"],
            "intent": s.data.get("intent"),
            "retrieved_docs": s.data.get("retrieved_docs"),
            "tool_result": s.data.get("tool_result"),
        }
        mode = xl_mode or s.data.get("xl_mode")
        # Nudge second pass if the loop is refining after Protect fail
        if int(s.metrics.get("loop_iter") or 0) >= 1 or s.data.get("protect_status") == "triggered":
            st["query"] = (
                f"{s.data['query']}\n\n[refine] Prior draft failed Protect grounding — "
                "cite KB facts explicitly and stay within retrieved context."
            )
        # XL-6 / XL-4: weaker prompt pressure via refine tag
        if mode in {"xl4", "xl6"} and int(s.metrics.get("loop_iter") or 0) == 0:
            st["query"] = (
                f"{s.data['query']}\n\n[xl-inject] Prefer a short speculative answer; "
                "do not quote the knowledge base verbatim."
            )
        out = trinity.responder_node(st)  # type: ignore[arg-type]
        return {"data": dict(out)}

    def protect_checker(s: State) -> float:
        """Live Protect score — honest LoopNode gate (GALILEO use-case #1)."""
        result = evaluate_protect_score(s, trinity_mod=trinity)
        # Stash Protect signal on state for fleet alerts / path correlation
        s.data["protect_status"] = result["protect_status"]
        s.data["protect_path"] = result["protect_path"]
        s.data["protect_score"] = result["score"]
        s.data["context_score"] = result["context_score"]
        s.data["adherence_floor"] = result["adherence_floor"]
        if result["protect_status"] == "triggered":
            s.data["protect_triggered"] = True
            if result.get("final_answer"):
                s.data["blocked_answer"] = result["final_answer"]
        # Also mirror into metrics so loop_non_converge alert carries Protect signal
        s.metrics["protect_status"] = result["protect_status"]
        s.metrics["protect_path"] = result["protect_path"]
        s.metrics["protect_score"] = result["score"]
        return float(result["score"])

    def protect_gate(s: State) -> dict:
        """Final Protect publish + HITL interrupt when triggered."""
        # Re-evaluate once for final status (or reuse last loop score)
        result = evaluate_protect_score(s, trinity_mod=trinity)
        status = result["protect_status"]
        final = result.get("final_answer") or s.data.get("draft_answer") or ""
        patch = {
            "protect_status": status,
            "protect_path": result["protect_path"],
            "protect_score": result["score"],
            "context_score": result["context_score"],
            "final_answer": final,
        }
        if status == "triggered" and hitl_on_protect and not s.data.get("approved"):
            interrupt(
                {
                    "prompt": "Protect blocked this answer. Approve override or edit draft?",
                    "protect_status": status,
                    "protect_path": result["protect_path"],
                    "protect_score": result["score"],
                    "draft": s.data.get("draft_answer"),
                    "blocked": final,
                }
            )
        if s.data.get("approved"):
            edited = s.data.get("edited_answer") or s.data.get("draft_answer") or final
            patch = {
                **patch,
                "final_answer": edited,
                "protect_status": "overridden",
                "protect_path": result["protect_path"],
                "hitl_override": True,
            }
        return {"data": patch, "done": True, "results": [patch]}

    g = Graph(id="trinity", name="Trinity Stack (DizzyGraph)")
    if xl_mode:
        g.id = f"trinity-{xl_mode}"
        g.name = f"Trinity XL ({xl_mode})"
    g.add_node(AtomicNode(id="intake", name="Intake", fn=intake))
    g.add_node(AtomicNode(id="retriever", name="Retriever", fn=retriever))
    g.add_node(AtomicNode(id="tools", name="Tools", fn=tools))
    g.add_node(
        LoopNode(
            id="responder",
            name="ResponderLoop+ProtectChecker",
            maker=respond_maker,
            checker=protect_checker if protect_enabled else (lambda s: 1.0),
            max_iters=max_loop_iters,
            score_threshold=floor,
            score_key="quality",
            metadata={"checker": "galileo_protect", "xl_mode": xl_mode},
        )
    )
    if protect_enabled:
        g.add_node(AtomicNode(id="protect", name="Protect+HITL", fn=protect_gate))
    g.set_entry("intake")
    g.add_edge("intake", "retriever")
    g.add_edge("retriever", "tools")
    g.add_edge("tools", "responder")
    if protect_enabled:
        g.add_edge("responder", "protect")
    return g


def _galileo_log_run(
    query: str,
    final: State,
    trace_summary: dict,
    tag: str,
    *,
    project: str | None = None,
    log_stream: str | None = None,
) -> str | None:
    """Manual GalileoLogger lifecycle — one span per DizzyGraph path step (OTel-ish)."""
    if not os.environ.get("GALILEO_API_KEY"):
        return None
    from galileo import GalileoLogger

    proj = project or PROJECT
    stream = log_stream or LOG_STREAM
    logger = GalileoLogger(project=proj, log_stream=stream)
    nodes = list(trace_summary.get("nodes_run") or [])
    logger.start_trace(
        input=query,
        name="trinity-dizzy",
        tags=["trinity-dizzy", "dizzygraph", tag],
        metadata={
            "engine": "dizzygraph",
            "tag": tag,
            "path_steps": ",".join(nodes),
            "protect_status": str(final.data.get("protect_status")),
            "protect_path": str(final.data.get("protect_path")),
            "protect_score": str(final.data.get("protect_score")),
            "loop_converged": str(final.metrics.get("loop_converged")),
        },
    )
    docs = final.data.get("retrieved_docs") or []
    # Path ↔ span correlation: emit a named span per node in path_steps
    for node_id in nodes:
        span_name = f"dizzygraph.{node_id}"
        if node_id == "retriever":
            logger.add_retriever_span(
                input=query,
                output=docs,
                name=span_name,
                metadata={
                    "otel.span_name": span_name,
                    "context_score": str(final.data.get("context_score")),
                },
            )
        elif node_id in {"responder", "protect"}:
            draft = final.data.get("draft_answer") or ""
            answer = final.data.get("final_answer") or draft
            logger.add_llm_span(
                input=query,
                output=answer if node_id == "protect" else draft,
                model="gpt-4o-mini",
                name=span_name,
                metadata={
                    "otel.span_name": span_name,
                    "protect_status": str(final.data.get("protect_status")),
                    "protect_path": str(final.data.get("protect_path")),
                    "protect_score": str(final.data.get("protect_score")),
                    "loop_converged": str(final.metrics.get("loop_converged")),
                },
            )
        else:
            try:
                logger.add_tool_span(
                    input=query,
                    output=str(final.data.get(node_id) or final.data.get("intent") or node_id),
                    name=span_name,
                    metadata={"otel.span_name": span_name, "path_step": node_id},
                )
            except Exception:
                logger.add_llm_span(
                    input=query,
                    output=node_id,
                    model="passthrough",
                    name=span_name,
                    metadata={"otel.span_name": span_name, "path_step": node_id},
                )
    answer = final.data.get("final_answer") or final.data.get("draft_answer") or ""
    logger.conclude(output=answer)
    logger.flush()
    return f"{proj}/{stream}"


def _write_evidence(payload: dict) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / "last_live_run.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Trinity on DizzyGraph")
    p.add_argument(
        "query",
        nargs="?",
        default="How do I debug a CUDA out-of-memory error during training?",
    )
    p.add_argument("--mock", action="store_true", help="No cloud — structural Trinity topology")
    p.add_argument("--meta", type=int, default=1, help="Meta-loop iterations over the whole graph")
    p.add_argument("--viz", action="store_true", help="Write PNG under dizzygraph_out/")
    p.add_argument("--mermaid", action="store_true", help="Print Mermaid flowchart")
    p.add_argument("--write-evidence", action="store_true", help="Write dizzygraph_out/last_live_run.json")
    p.add_argument("--no-galileo", action="store_true", help="Skip Galileo flush even if key present")
    args = p.parse_args(argv)

    keys = {"openai": False, "galileo": False}
    if not args.mock:
        keys = load_runtime_keys()
        if not keys["openai"]:
            raise SystemExit(
                "ERROR: OPENAI_API_KEY missing. Put it in galileo-labs/.env or the environment.\n"
                "Or use --mock for an offline topology run."
            )
        log.info("keys: openai=%s galileo=%s", keys["openai"], keys["galileo"])

    graph = _mock_pipeline_graph() if args.mock else build_trinity_dizzy_graph()
    if args.mermaid:
        from dizzygraph import to_mermaid

        print(to_mermaid(graph))
    if args.viz:
        OUT_DIR.mkdir(exist_ok=True)
        visualize_graph(graph, path=OUT_DIR / "trinity_dizzy.png", title=graph.name)

    ex = GraphExecutor(graph, max_graph_iterations=16)
    initial = State(data={"query": args.query})
    t0 = time.perf_counter()

    if args.meta > 1:
        meta = MetaLoopExecutor(
            ex,
            num_meta_iterations=args.meta,
            state_updater=lambda st, tr, i: st.model_copy(
                update={"done": False, "data": {**st.data, "meta_boost": i, "query": args.query}}
            ),
        )
        result = meta.run(initial)
        final = result.final_state
        summary = result.summary()
        print("── meta ──")
        print(summary)
    else:
        trace = ex.run(initial)
        final = trace.final_state
        summary = trace.summary()
        print("── trace ──")
        print(summary)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    answer = None
    if final:
        answer = final.data.get("final_answer") or final.data.get("draft_answer")
        print("── answer ──")
        print(answer)
        print("── signals ──")
        print(
            {
                "intent": final.data.get("intent"),
                "doc_ids": final.data.get("doc_ids"),
                "context_score": final.data.get("context_score"),
                "protect_status": final.data.get("protect_status"),
                "protect_path": final.data.get("protect_path"),
                "latency_ms": latency_ms,
            }
        )

    galileo_target = None
    if final and not args.mock and not args.no_galileo:
        galileo_target = _galileo_log_run(
            args.query,
            final,
            summary if isinstance(summary, dict) else {},
            tag=f"meta{args.meta}" if args.meta > 1 else "single",
        )
        if galileo_target:
            print(f"── galileo ──\n{galileo_target}")

    if args.write_evidence and final:
        evidence = {
            "query": args.query,
            "mock": args.mock,
            "meta": args.meta,
            "latency_ms": latency_ms,
            "summary": summary,
            "intent": final.data.get("intent"),
            "doc_ids": final.data.get("doc_ids"),
            "context_score": final.data.get("context_score"),
            "protect_status": final.data.get("protect_status"),
            "protect_path": final.data.get("protect_path"),
            "answer_preview": (answer or "")[:500],
            "galileo": galileo_target,
            "keys_present": keys,
        }
        path = _write_evidence(evidence)
        print(f"── evidence ──\n{path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
