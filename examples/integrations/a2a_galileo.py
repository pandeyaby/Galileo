"""A2A (Agent2Agent) → Galileo via galileo-a2a instrumentor (real SDK, no mock).

Minimal skeleton: instruments A2A + runs a tiny in-process agent card handshake.
Full multi-agent fan-out example lives in Galileo docs; this starter proves imports
and OTel wiring, then fails clearly if packages/keys are missing.

Usage:
  pip install galileo-a2a 'galileo[otel]' a2a-sdk opentelemetry-sdk
  export GALILEO_API_KEY=... GALILEO_PROJECT=... GALILEO_LOG_STREAM=a2a-integration
  # Optional live LLM path also needs OPENAI_API_KEY
  python examples/integrations/a2a_galileo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo, setup_galileo_otel


def main() -> int:
    err = require_galileo()
    if err:
        return err

    try:
        from galileo_a2a import A2AInstrumentor
    except ImportError:
        print("ERROR: install galileo-a2a: pip install galileo-a2a")
        return 2

    try:
        from a2a.types import AgentCapabilities, AgentCard, AgentSkill
    except ImportError:
        print("ERROR: install a2a-sdk: pip install a2a-sdk")
        return 2

    project, stream = project_stream("a2a-integration")
    try:
        provider = setup_galileo_otel(project=project, log_stream=stream)
    except ImportError as exc:
        print(f"ERROR: {exc}")
        return 2

    A2AInstrumentor().instrument(tracer_provider=provider, agent_name="dizzygraph-a2a-starter")
    print(f"galileo-a2a instrumented → {project}/{stream}")

    # Prove a2a-sdk types resolve (no fake agent success)
    card = AgentCard(
        name="dizzygraph-researcher",
        description="Minimal A2A agent card for Galileo instrumentation smoke",
        url="http://127.0.0.1:0",
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="qa",
                name="Q&A",
                description="Answer short ML infra questions",
                tags=["ml"],
            )
        ],
    )
    print(f"agent_card: {card.name} v{card.version}")
    print(
        "OK: galileo-a2a + a2a-sdk wired. "
        "See Galileo A2A docs for a full client/server LangGraph example."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
