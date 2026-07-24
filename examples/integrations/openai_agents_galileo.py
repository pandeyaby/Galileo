"""OpenAI Agents SDK → Galileo via GalileoTracingProcessor (real SDK, no mock).

Usage:
  pip install openai-agents galileo openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  python examples/integrations/openai_agents_galileo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import load_keys, project_stream, require_openai


async def _run() -> int:
    err = require_openai()
    if err:
        return err
    try:
        from agents import Agent, Runner
        from agents.tracing import set_trace_processors
    except ImportError:
        print("ERROR: install openai-agents first: pip install openai-agents")
        return 2

    keys = load_keys()
    project, stream = project_stream("openai-agents-integration")
    query = "In one short paragraph: what is vLLM PagedAttention?"

    if keys.get("galileo") or os.environ.get("GALILEO_API_KEY"):
        try:
            from galileo import GalileoLogger
            from galileo.handlers.openai_agents import GalileoTracingProcessor

            logger = GalileoLogger(project=project, log_stream=stream)
            set_trace_processors([GalileoTracingProcessor(galileo_logger=logger)])
            print(f"galileo processor: {project}/{stream}")
        except ImportError:
            print("ERROR: galileo.handlers.openai_agents unavailable — upgrade galileo")
            return 2
    else:
        print("galileo: skipped (GALILEO_API_KEY missing)")

    agent = Agent(
        name="MLInfraAgent",
        instructions="You are an ML platform engineer. Answer briefly and concretely.",
    )
    result = await Runner.run(agent, query)
    output = str(getattr(result, "final_output", None) or result)

    print("── answer ──")
    print(output[:800])
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
