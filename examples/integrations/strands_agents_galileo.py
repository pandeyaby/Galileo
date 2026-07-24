"""Strands Agents → Galileo via OpenTelemetry (real SDK, no mock).

Usage:
  pip install strands-agents 'galileo[otel]' opentelemetry-sdk
  export GALILEO_API_KEY=... 
  # Model keys depend on provider — OpenAI example:
  export OPENAI_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=strands-agents
  python examples/integrations/strands_agents_galileo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo, require_openai, setup_galileo_otel


def main() -> int:
    err = require_openai() or require_galileo()
    if err:
        return err

    try:
        from strands import Agent
    except ImportError:
        print("ERROR: install strands-agents: pip install strands-agents")
        return 2

    project, stream = project_stream("strands-agents")
    try:
        provider = setup_galileo_otel(project=project, log_stream=stream)
    except ImportError as exc:
        print(f"ERROR: {exc}")
        return 2

    # Strands has native OTel hooks in recent versions; prefer telemetry enable if present
    try:
        from strands.telemetry import StrandsTelemetry

        StrandsTelemetry().setup_otlp_exporter()
        print("strands telemetry: setup_otlp_exporter()")
    except Exception:
        # Still emit via global TracerProvider (GalileoSpanProcessor)
        print("strands telemetry: using global TracerProvider only")

    _ = provider
    agent = Agent(
        system_prompt="You are an ML platform engineer. Answer briefly and concretely.",
    )
    query = "In one sentence: what is ZeRO optimizer sharding?"
    result = agent(query)
    output = str(getattr(result, "message", None) or result)
    print(f"galileo: {project}/{stream}")
    print("── answer ──")
    print(output[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
