"""LangGraph + OpenInference / Galileo callback (real SDKs, no mock).

Prefers:
  1. Galileo LangChain callback (official)
  2. OpenInference LangChain instrumentor + Galileo OTel processor
  3. Manual GalileoLogger with openinference.span.kind metadata

Usage:
  pip install langgraph langchain-openai langchain-core galileo openai
  # optional OpenInference path:
  pip install openinference-instrumentation-langchain 'galileo[otel]'
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  python examples/integrations/openinference_langgraph_galileo.py
"""

from __future__ import annotations

import os
import sys
from typing import TypedDict

sys.path.insert(0, os.path.dirname(__file__))

from _common import load_keys, project_stream, require_openai


def main() -> int:
    err = require_openai()
    if err:
        return err
    try:
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from langgraph.graph import END, StateGraph
    except ImportError:
        print("ERROR: pip install langgraph langchain-openai langchain-core")
        return 2

    keys = load_keys()
    project, stream = project_stream("openinference-langgraph")
    query = "One sentence: what is ZeRO optimizer sharding?"
    os.environ.setdefault("GALILEO_PROJECT", project)
    os.environ.setdefault("GALILEO_LOG_STREAM", stream)

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

    mode = "manual"
    config: dict = {}

    if keys.get("galileo") or os.environ.get("GALILEO_API_KEY"):
        # 1) Official Galileo LangChain callback
        try:
            from galileo import GalileoLogger
            from galileo.handlers.langchain import GalileoCallback

            logger = GalileoLogger(project=project, log_stream=stream)
            cb = GalileoCallback(galileo_logger=logger, start_new_trace=True, flush_on_chain_end=True)
            config = {"callbacks": [cb]}
            mode = "galileo_langchain_callback"
            print(f"galileo: {mode} → {project}/{stream}")
        except ImportError:
            # 2) OpenInference + Galileo OTel
            try:
                from _common import setup_galileo_otel
                from openinference.instrumentation.langchain import LangChainInstrumentor

                provider = setup_galileo_otel(project=project, log_stream=stream)
                LangChainInstrumentor().instrument(tracer_provider=provider)
                mode = "openinference_otel"
                print(f"galileo: {mode} → {project}/{stream}")
            except ImportError:
                mode = "manual"
                print("galileo: falling back to manual GalileoLogger spans")

    result = app.invoke({"query": query, "answer": ""}, config=config or None)
    answer = result.get("answer") or ""

    if mode == "manual" and (keys.get("galileo") or os.environ.get("GALILEO_API_KEY")):
        from galileo import GalileoLogger

        logger = GalileoLogger(project=project, log_stream=stream)
        logger.start_trace(
            input=query,
            name="openinference-langgraph",
            tags=["integration", "openinference", "langgraph"],
            metadata={"framework": "langgraph", "otel.scope": "openinference-starter"},
        )
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
        print(f"galileo manual: {project}/{stream}")
    elif not (keys.get("galileo") or os.environ.get("GALILEO_API_KEY")):
        print("galileo: skipped (GALILEO_API_KEY missing)")

    print("── answer ──")
    print(answer[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
