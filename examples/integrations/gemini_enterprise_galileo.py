"""Gemini (Google AI / Enterprise path) → GalileoLogger (real SDK, no mock).

For Gemini Enterprise / Vertex: set GOOGLE_APPLICATION_CREDENTIALS to a service
account JSON and optionally VERTEX_PROJECT / VERTEX_LOCATION. Console-side
Gemini Enterprise credential wiring is documented at:
  https://docs.galileo.ai/sdk-api/third-party-integrations/model-integrations/gemini-enterprise/gemini-enterprise-credentials

This starter uses the google-genai client against Gemini API when GOOGLE_API_KEY
is set, or Vertex when credentials + project are present.

Usage:
  pip install google-genai galileo
  export GALILEO_API_KEY=... GOOGLE_API_KEY=...
  # or Vertex:
  # export GOOGLE_APPLICATION_CREDENTIALS=/path/key.json VERTEX_PROJECT=... VERTEX_LOCATION=us-central1
  python examples/integrations/gemini_enterprise_galileo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo


def main() -> int:
    err = require_galileo()
    if err:
        return err

    has_api_key = bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    has_vertex = bool(
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.environ.get("VERTEX_PROJECT")
    )
    if not has_api_key and not has_vertex:
        print(
            "ERROR: set GOOGLE_API_KEY (Gemini API) or "
            "GOOGLE_APPLICATION_CREDENTIALS + VERTEX_PROJECT (Vertex/Enterprise). No mock."
        )
        return 2

    try:
        from google import genai
    except ImportError:
        print("ERROR: install google-genai: pip install google-genai")
        return 2

    project, stream = project_stream("gemini-enterprise-integration")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    query = "In one sentence: what is FSDP?"

    if has_vertex:
        client = genai.Client(
            vertexai=True,
            project=os.environ["VERTEX_PROJECT"],
            location=os.environ.get("VERTEX_LOCATION", "us-central1"),
        )
        path = "vertex"
    else:
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        path = "gemini_api"

    from galileo import GalileoLogger

    logger = GalileoLogger(project=project, log_stream=stream)
    logger.start_trace(
        input=query,
        name="gemini-enterprise-galileo",
        tags=["integration", "gemini", path, "dizzygraph-starter"],
        metadata={"framework": "google-genai", "path": path, "model": model},
    )

    try:
        resp = client.models.generate_content(model=model, contents=query)
    except Exception as exc:
        print(f"ERROR: Gemini generate failed: {type(exc).__name__}: {exc}")
        return 2

    output = (getattr(resp, "text", None) or str(resp)).strip()
    logger.add_llm_span(
        input=query,
        output=output,
        model=model,
        name="gemini.generate_content",
        metadata={
            "otel.span_name": "gemini.generate_content",
            "openinference.span.kind": "LLM",
            "path": path,
        },
    )
    logger.conclude(output=output)
    logger.flush()
    print(f"galileo: {project}/{stream} path={path}")
    print("── answer ──")
    print(output[:800])
    return 0 if output else 1


if __name__ == "__main__":
    raise SystemExit(main())
