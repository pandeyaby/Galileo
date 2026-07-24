"""Microsoft Agent Framework → Galileo via built-in OTel + GalileoSpanProcessor.

Usage:
  pip install agent-framework 'galileo[otel]' opentelemetry-sdk openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=ms-agent-framework
  python examples/integrations/ms_agent_framework_galileo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo, require_openai, setup_galileo_otel


async def _run() -> int:
    err = require_openai() or require_galileo()
    if err:
        return err

    try:
        from agent_framework import openai as af_openai
        from agent_framework.observability import enable_instrumentation
    except ImportError:
        print("ERROR: install agent-framework: pip install agent-framework")
        return 2

    project, stream = project_stream("ms-agent-framework")
    try:
        from opentelemetry import trace

        provider = setup_galileo_otel(project=project, log_stream=stream)
        _ = provider
        _ = trace.get_tracer_provider()
    except ImportError as exc:
        print(f"ERROR: {exc}")
        return 2

    enable_instrumentation(enable_sensitive_data=True)
    print(f"galileo otel + agent-framework → {project}/{stream}")

    client = af_openai.OpenAIChatClient(model_id=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    agent = client.as_agent(
        name="MLInfraAgent",
        instructions="You are an ML platform engineer. Answer in one short paragraph.",
    )
    result = await agent.run("In one sentence: what is vLLM PagedAttention?")
    output = str(result)
    print("── answer ──")
    print(output[:800])
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
