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
        Path("/Users/abhinavpandey/Documents/GitHub/Galileo/galileo-labs/.env"),
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


def build_trinity_dizzy_graph(*, kb=None, protect_enabled: bool = True) -> Graph:
    """Wire live ``app.py`` callables into DizzyGraph nodes."""
    import app as trinity

    kb = kb if kb is not None else trinity.load_kb()
    ret_fn = partial(trinity.retriever_node, kb=kb)

    def intake(s: State) -> dict:
        st = {"query": s.data["query"]}
        out = trinity.intake_node(st)  # type: ignore[arg-type]
        return {"data": dict(out)}

    def retriever(s: State) -> dict:
        st = {"query": s.data["query"], "intent": s.data.get("intent")}
        out = ret_fn(st)  # type: ignore[arg-type]
        return {"data": dict(out)}

    def tools(s: State) -> dict:
        st = {
            "query": s.data["query"],
            "intent": s.data.get("intent"),
            "retrieved_docs": s.data.get("retrieved_docs"),
        }
        out = trinity.tools_node(st)  # type: ignore[arg-type]
        return {"data": dict(out)}

    def respond(s: State) -> dict:
        st = {
            "query": s.data["query"],
            "intent": s.data.get("intent"),
            "retrieved_docs": s.data.get("retrieved_docs"),
            "tool_result": s.data.get("tool_result"),
        }
        out = trinity.responder_node(st)  # type: ignore[arg-type]
        return {"data": dict(out)}

    def protect(s: State) -> dict:
        st = {
            "query": s.data["query"],
            "retrieved_docs": s.data.get("retrieved_docs"),
            "draft_answer": s.data.get("draft_answer"),
            "context_score": s.data.get("context_score"),
        }
        out = trinity.protect_node(st)  # type: ignore[arg-type]
        return {"data": dict(out), "done": True, "results": [out]}

    g = Graph(id="trinity", name="Trinity Stack (DizzyGraph)")
    g.add_node(AtomicNode(id="intake", name="Intake", fn=intake))
    g.add_node(AtomicNode(id="retriever", name="Retriever", fn=retriever))
    g.add_node(AtomicNode(id="tools", name="Tools", fn=tools))
    g.add_node(AtomicNode(id="responder", name="Responder", fn=respond))
    if protect_enabled:
        g.add_node(AtomicNode(id="protect", name="Protect", fn=protect))
    g.set_entry("intake")
    g.add_edge("intake", "retriever")
    g.add_edge("retriever", "tools")
    g.add_edge("tools", "responder")
    if protect_enabled:
        g.add_edge("responder", "protect")
    return g


def _galileo_log_run(query: str, final: State, trace_summary: dict, tag: str) -> str | None:
    """Manual GalileoLogger lifecycle for the DizzyGraph path (no LangChain callback)."""
    if not os.environ.get("GALILEO_API_KEY"):
        return None
    from galileo import GalileoLogger

    logger = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM)
    logger.start_trace(
        input=query,
        name="trinity-dizzy",
        tags=["trinity-dizzy", "dizzygraph", tag],
        metadata={"engine": "dizzygraph", "tag": tag},
    )
    docs = final.data.get("retrieved_docs") or []
    logger.add_retriever_span(
        input=query,
        output=docs,
        name="retriever",
        metadata={"context_score": str(final.data.get("context_score"))},
    )
    draft = final.data.get("draft_answer") or ""
    answer = final.data.get("final_answer") or draft
    logger.add_llm_span(
        input=query,
        output=answer,
        model="gpt-4o-mini",
        name="responder+protect",
        metadata={
            "protect_status": str(final.data.get("protect_status")),
            "protect_path": str(final.data.get("protect_path")),
            "nodes": ",".join(trace_summary.get("nodes_run") or []),
        },
    )
    logger.conclude(output=answer)
    logger.flush()
    return f"{PROJECT}/{LOG_STREAM}"


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
