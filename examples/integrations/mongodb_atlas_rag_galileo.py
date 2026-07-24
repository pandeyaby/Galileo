"""MongoDB Atlas Vector Search RAG → Galileo (real SDKs, no mock).

Retrieves via Atlas `$vectorSearch` (or falls back to embedding + cosine over a
small collection sample), then answers with OpenAI. Traces go to Galileo via the
official LangChain callback or OTel.

Usage:
  pip install pymongo openai galileo langchain-openai langchain-core
  # optional OTel path: pip install 'galileo[otel]' opentelemetry-sdk
  export MONGODB_URI='mongodb+srv://...'
  export MONGODB_DB=galileo_rag          # optional, default galileo_rag
  export MONGODB_COLLECTION=chunks      # optional, default chunks
  export MONGODB_VECTOR_INDEX=vector_index  # Atlas Search index name
  export MONGODB_EMBEDDING_PATH=embedding   # field holding float vectors
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=mongodb-atlas-rag
  python examples/integrations/mongodb_atlas_rag_galileo.py

Fail-loud: missing URI/keys/packages exit 2. No fake success.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

from _common import (
    load_keys,
    project_stream,
    require_env,
    require_galileo,
    require_openai,
)


def _embed(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI()
    model = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    resp = client.embeddings.create(model=model, input=text)
    return list(resp.data[0].embedding)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def retrieve_atlas(query: str) -> list[dict[str, Any]]:
    from pymongo import MongoClient

    uri = os.environ["MONGODB_URI"]
    db_name = os.environ.get("MONGODB_DB", "galileo_rag")
    coll_name = os.environ.get("MONGODB_COLLECTION", "chunks")
    index = os.environ.get("MONGODB_VECTOR_INDEX", "vector_index")
    path = os.environ.get("MONGODB_EMBEDDING_PATH", "embedding")
    text_field = os.environ.get("MONGODB_TEXT_FIELD", "text")
    top_k = int(os.environ.get("MONGODB_TOP_K", "4"))

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
    except Exception as exc:
        raise RuntimeError(
            f"MongoDB Atlas connection failed ({type(exc).__name__}: {exc}). "
            "Check MONGODB_URI (and IP allowlist / credentials)."
        ) from exc

    coll = client[db_name][coll_name]
    query_vec = _embed(query)

    # Prefer Atlas Vector Search aggregation
    pipeline = [
        {
            "$vectorSearch": {
                "index": index,
                "path": path,
                "queryVector": query_vec,
                "numCandidates": max(top_k * 20, 40),
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                text_field: 1,
                "score": {"$meta": "vectorSearchScore"},
                "source": 1,
            }
        },
    ]
    try:
        docs = list(coll.aggregate(pipeline))
        if docs:
            return [
                {
                    "text": str(d.get(text_field) or d.get("text") or ""),
                    "score": float(d.get("score") or 0.0),
                    "source": d.get("source"),
                }
                for d in docs
                if (d.get(text_field) or d.get("text"))
            ]
    except Exception as exc:
        print(
            f"WARN: $vectorSearch failed ({type(exc).__name__}: {exc}); "
            f"falling back to local cosine over up to 200 docs in {db_name}.{coll_name}. "
            f"Ensure Atlas Search index `{index}` exists on path `{path}`."
        )

    # Fallback: sample + cosine (still real Mongo + real embeddings — not a mock answer)
    sample = list(coll.find({}, {text_field: 1, path: 1, "text": 1, "source": 1}).limit(200))
    if not sample:
        raise RuntimeError(
            f"Collection {db_name}.{coll_name} is empty. "
            "Insert documents with text + embedding fields, and create an Atlas Vector Search index."
        )
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in sample:
        emb = doc.get(path)
        if not isinstance(emb, list) or not emb:
            continue
        text = str(doc.get(text_field) or doc.get("text") or "")
        if not text:
            continue
        scored.append(
            (
                _cosine(query_vec, [float(x) for x in emb]),
                {"text": text, "score": 0.0, "source": doc.get("source")},
            )
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for score, item in scored[:top_k]:
        item["score"] = score
        out.append(item)
    if not out:
        raise RuntimeError(
            f"No documents with usable `{path}` embeddings in {db_name}.{coll_name}."
        )
    return out


def main() -> int:
    err = require_openai() or require_galileo() or require_env("MONGODB_URI")
    if err:
        return err

    try:
        import pymongo  # noqa: F401
        from openai import OpenAI
    except ImportError:
        print("ERROR: pip install pymongo openai")
        return 2

    keys = load_keys()
    project, stream = project_stream("mongodb-atlas-rag")
    os.environ.setdefault("GALILEO_PROJECT", project)
    os.environ.setdefault("GALILEO_LOG_STREAM", stream)

    query = os.environ.get(
        "RAG_QUERY",
        "What does our knowledge base say about vector search?",
    )
    try:
        hits = retrieve_atlas(query)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: Atlas retrieve failed: {type(exc).__name__}: {exc}")
        return 2

    context = "\n\n".join(f"[{i+1}] {h['text']}" for i, h in enumerate(hits))
    prompt = (
        "Answer using ONLY the context. If insufficient, say you don't know.\n\n"
        f"Context:\n{context}\n\nQuestion: {query}"
    )

    client = OpenAI()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a careful RAG assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    answer = (completion.choices[0].message.content or "").strip()

    mode = "manual"
    if keys.get("galileo") or os.environ.get("GALILEO_API_KEY"):
        try:
            from galileo import GalileoLogger

            logger = GalileoLogger(project=project, log_stream=stream)
            logger.start_trace(
                input=query,
                name="mongodb-atlas-rag",
                tags=["integration", "mongodb", "atlas", "rag"],
                metadata={"framework": "pymongo+openai", "hits": len(hits)},
            )
            logger.add_retriever_span(
                input=query,
                output=[h["text"] for h in hits],
                name="atlas.vector_search",
                metadata={"otel.span_name": "mongodb.retrieve", "top_k": len(hits)},
            )
            logger.add_llm_span(
                input=prompt,
                output=answer,
                model=model,
                name="rag.generate",
                metadata={"otel.span_name": "rag.generate", "openinference.span.kind": "LLM"},
            )
            logger.conclude(output=answer)
            logger.flush()
            mode = "galileo_logger"
            print(f"galileo: {mode} → {project}/{stream}")
        except ImportError:
            print("ERROR: pip install galileo")
            return 2

    print(f"retrieved {len(hits)} chunk(s)")
    print("── answer ──")
    print(answer[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
