"""OpenInference / OTel-shaped spans on a tiny LangGraph → Galileo.

Pragmatic starter: emit OpenInference-style span names via GalileoLogger
metadata (``otel.span_name`` / ``openinference.span.kind``). Full OTel exporter
wiring is optional; DizzyGraph fleet already correlates ``path_steps`` to
``dizzygraph.<node>`` span names.

Usage:
  pip install langgraph langchain-openai galileo openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  python examples/integrations/openinference_langgraph_galileo.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_keys() -> dict[str, bool]:
    try:
        from trinity_dizzy import load_runtime_keys

        return load_runtime_keys()
    except Exception:
        return {
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "galileo": bool(os.environ.get("GALILEO_API_KEY")),
        }


def main() -> int:
    keys = _load_keys()
    if not keys.get("openai"):
        print("ERROR: OPENAI_API_KEY required (no mock).")
        return 2
    try:
        from langgraph.graph import END, StateGraph
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
    except ImportError:
        print("ERROR: pip install langgraph langchain-openai langchain-core")
        return 2

    project = os.environ.get("GALILEO_PROJECT", "rax-galileo-labs")
    stream = os.environ.get("GALILEO_LOG_STREAM", "openinference-langgraph")
    query = "One sentence: what is ZeRO optimizer sharding?"

    class S(TypedDict):
        query: str
        answer: str

    llm = ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)

    def respond(state: S) -> S:
        msg = llm.invoke([HumanMessage(content=state["query"])])
        return {"query": state["query"], "answer": str(msg.content)}

    g = StateGraph(S)
    g.add_node("respond", respond)
    g.set_entry_point("respond")
    g.add_edge("respond", END)
    app = g.compile()

    logger = None
    if keys.get("galileo"):
        from galileo import GalileoLogger

        logger = GalileoLogger(project=project, log_stream=stream)
        logger.start_trace(
            input=query,
            name="openinference-langgraph",
            tags=["integration", "openinference", "langgraph"],
            metadata={"framework": "langgraph", "otel.scope": "openinference-starter"},
        )

    result = app.invoke({"query": query, "answer": ""})
    answer = result.get("answer") or ""

    if logger is not None:
        # OpenInference-shaped span metadata (pragmatic v1 — no full OTel SDK)
        logger.add_llm_span(
            input=query,
            output=answer,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            name="langgraph.respond",
            metadata={
                "otel.span_name": "langgraph.respond",
                "openinference.span.kind": "LLM",
                "path_step": "respond",
            },
        )
        logger.conclude(output=answer)
        logger.flush()
        print(f"galileo: {project}/{stream}")
    else:
        print("galileo: skipped (GALILEO_API_KEY missing)")

    print("── answer ──")
    print(answer[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
