"""Elasticsearch + LangGraph RAG → Galileo (real SDKs, no mock).

LangGraph graph: retrieve (Elasticsearch kNN / match) → generate (ChatOpenAI).
Logs via Galileo LangChain callback when available.

Usage:
  pip install elasticsearch langgraph langchain-openai langchain-core galileo openai
  export ELASTIC_URL=https://...:443          # or ELASTIC_CLOUD_ID=...
  export ELASTIC_API_KEY=...                  # or ELASTIC_USER + ELASTIC_PASSWORD
  export ELASTIC_INDEX=galileo_rag_chunks
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=elasticsearch-langgraph-rag
  python examples/integrations/elasticsearch_langgraph_rag_galileo.py

Fail-loud: missing ES/OpenAI/Galileo credentials exit 2. No fake success.
"""

from __future__ import annotations

import os
import sys
from typing import Any, TypedDict

sys.path.insert(0, os.path.dirname(__file__))

from _common import (
    load_keys,
    project_stream,
    require_any_env,
    require_galileo,
    require_openai,
)


def _es_client():
    from elasticsearch import Elasticsearch

    url = (os.environ.get("ELASTIC_URL") or "").strip()
    cloud_id = (os.environ.get("ELASTIC_CLOUD_ID") or "").strip()
    api_key = (os.environ.get("ELASTIC_API_KEY") or "").strip()
    user = (os.environ.get("ELASTIC_USER") or "").strip()
    password = (os.environ.get("ELASTIC_PASSWORD") or "").strip()

    kwargs: dict[str, Any] = {"request_timeout": 30}
    if api_key:
        kwargs["api_key"] = api_key
    elif user and password:
        kwargs["basic_auth"] = (user, password)
    else:
        raise RuntimeError(
            "Set ELASTIC_API_KEY or ELASTIC_USER+ELASTIC_PASSWORD (no anonymous mock)."
        )

    if cloud_id:
        return Elasticsearch(cloud_id=cloud_id, **kwargs)
    if url:
        return Elasticsearch(url, **kwargs)
    raise RuntimeError("Set ELASTIC_URL or ELASTIC_CLOUD_ID.")


def _embed(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI()
    model = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    resp = client.embeddings.create(model=model, input=text)
    return list(resp.data[0].embedding)


def retrieve_es(query: str) -> list[dict[str, Any]]:
    index = os.environ.get("ELASTIC_INDEX", "galileo_rag_chunks")
    text_field = os.environ.get("ELASTIC_TEXT_FIELD", "text")
    vector_field = os.environ.get("ELASTIC_VECTOR_FIELD", "embedding")
    top_k = int(os.environ.get("ELASTIC_TOP_K", "4"))
    client = _es_client()

    try:
        info = client.info()
    except Exception as exc:
        raise RuntimeError(
            f"Elasticsearch connection failed ({type(exc).__name__}: {exc}). "
            "Check ELASTIC_URL / ELASTIC_CLOUD_ID and credentials."
        ) from exc
    _ = info

    if not client.indices.exists(index=index):
        raise RuntimeError(
            f"Index `{index}` does not exist. Create it and index docs with "
            f"`{text_field}` (+ optional dense_vector `{vector_field}`)."
        )

    # Prefer kNN when vector field present; fall back to BM25 match
    query_vec = _embed(query)
    knn_body = {
        "knn": {
            "field": vector_field,
            "query_vector": query_vec,
            "k": top_k,
            "num_candidates": max(top_k * 20, 50),
        },
        "_source": [text_field, "source"],
        "size": top_k,
    }
    try:
        resp = client.search(index=index, body=knn_body)
        hits = resp.get("hits", {}).get("hits", [])
        if hits:
            return [
                {
                    "text": str((h.get("_source") or {}).get(text_field) or ""),
                    "score": float(h.get("_score") or 0.0),
                    "source": (h.get("_source") or {}).get("source"),
                }
                for h in hits
                if (h.get("_source") or {}).get(text_field)
            ]
    except Exception as exc:
        print(
            f"WARN: kNN search failed ({type(exc).__name__}: {exc}); "
            f"falling back to BM25 match on `{text_field}`."
        )

    resp = client.search(
        index=index,
        body={
            "query": {"match": {text_field: query}},
            "_source": [text_field, "source"],
            "size": top_k,
        },
    )
    hits = resp.get("hits", {}).get("hits", [])
    out = [
        {
            "text": str((h.get("_source") or {}).get(text_field) or ""),
            "score": float(h.get("_score") or 0.0),
            "source": (h.get("_source") or {}).get("source"),
        }
        for h in hits
        if (h.get("_source") or {}).get(text_field)
    ]
    if not out:
        raise RuntimeError(
            f"No hits in `{index}` for query. Index documents or adjust ELASTIC_TEXT_FIELD."
        )
    return out


def main() -> int:
    err = (
        require_openai()
        or require_galileo()
        or require_any_env(("ELASTIC_URL", "ELASTIC_CLOUD_ID"))
        or require_any_env(("ELASTIC_API_KEY", "ELASTIC_USER"))
    )
    if err:
        return err
    if (os.environ.get("ELASTIC_USER") or "").strip() and not (
        os.environ.get("ELASTIC_PASSWORD") or ""
    ).strip() and not (os.environ.get("ELASTIC_API_KEY") or "").strip():
        print("ERROR: ELASTIC_PASSWORD required when using ELASTIC_USER (no mock).")
        return 2

    try:
        from elasticsearch import Elasticsearch  # noqa: F401
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
        from langgraph.graph import END, StateGraph
    except ImportError:
        print(
            "ERROR: pip install elasticsearch langgraph langchain-openai langchain-core"
        )
        return 2

    keys = load_keys()
    project, stream = project_stream("elasticsearch-langgraph-rag")
    os.environ.setdefault("GALILEO_PROJECT", project)
    os.environ.setdefault("GALILEO_LOG_STREAM", stream)

    query = os.environ.get(
        "RAG_QUERY",
        "Summarize what the indexed documents say about retrieval quality.",
    )

    class S(TypedDict):
        query: str
        context: str
        answer: str
        hits: int

    llm = ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)

    def retrieve_node(state: S) -> S:
        hits = retrieve_es(state["query"])
        ctx = "\n\n".join(f"[{i+1}] {h['text']}" for i, h in enumerate(hits))
        return {
            "query": state["query"],
            "context": ctx,
            "answer": "",
            "hits": len(hits),
        }

    def generate_node(state: S) -> S:
        msgs = [
            SystemMessage(
                content="Answer using ONLY the provided context. If insufficient, say you don't know."
            ),
            HumanMessage(
                content=f"Context:\n{state['context']}\n\nQuestion: {state['query']}"
            ),
        ]
        msg = llm.invoke(msgs)
        return {
            "query": state["query"],
            "context": state["context"],
            "answer": str(msg.content),
            "hits": state["hits"],
        }

    g = StateGraph(S)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    app = g.compile()

    config: dict = {}
    mode = "manual"
    if keys.get("galileo") or os.environ.get("GALILEO_API_KEY"):
        try:
            from galileo import GalileoLogger
            from galileo.handlers.langchain import GalileoCallback

            logger = GalileoLogger(project=project, log_stream=stream)
            cb = GalileoCallback(
                galileo_logger=logger, start_new_trace=True, flush_on_chain_end=True
            )
            config = {"callbacks": [cb]}
            mode = "galileo_langchain_callback"
            print(f"galileo: {mode} → {project}/{stream}")
        except ImportError:
            mode = "manual"

    try:
        result = app.invoke(
            {"query": query, "context": "", "answer": "", "hits": 0},
            config=config or None,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: LangGraph RAG failed: {type(exc).__name__}: {exc}")
        return 2

    answer = result.get("answer") or ""
    if mode == "manual":
        try:
            from galileo import GalileoLogger

            logger = GalileoLogger(project=project, log_stream=stream)
            logger.start_trace(
                input=query,
                name="elasticsearch-langgraph-rag",
                tags=["integration", "elasticsearch", "langgraph", "rag"],
                metadata={"hits": result.get("hits")},
            )
            logger.add_retriever_span(
                input=query,
                output=[result.get("context") or ""],
                name="elasticsearch.retrieve",
                metadata={"otel.span_name": "langgraph.retrieve"},
            )
            logger.add_llm_span(
                input=query,
                output=answer,
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                name="langgraph.generate",
                metadata={"otel.span_name": "langgraph.generate"},
            )
            logger.conclude(output=answer)
            logger.flush()
            print(f"galileo manual → {project}/{stream}")
        except ImportError:
            print("ERROR: pip install galileo")
            return 2

    print(f"retrieved hits={result.get('hits')}")
    print("── answer ──")
    print(answer[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
