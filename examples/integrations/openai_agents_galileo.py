"""OpenAI Agents SDK → Galileo logging starter (real keys when present).

Uses the official ``openai-agents`` package when installed. Logs the agent run
to Galileo via GalileoLogger. No silent mock of the Agents SDK.

Usage:
  pip install openai-agents galileo openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  python examples/integrations/openai_agents_galileo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

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


async def _run() -> int:
    keys = _load_keys()
    if not keys.get("openai"):
        print("ERROR: OPENAI_API_KEY required (no mock).")
        return 2
    try:
        from agents import Agent, Runner
    except ImportError:
        print("ERROR: install openai-agents first: pip install openai-agents")
        return 2

    project = os.environ.get("GALILEO_PROJECT", "rax-galileo-labs")
    stream = os.environ.get("GALILEO_LOG_STREAM", "openai-agents-integration")
    query = "In one short paragraph: what is vLLM PagedAttention?"

    agent = Agent(
        name="MLInfraAgent",
        instructions="You are an ML platform engineer. Answer briefly and concretely.",
    )

    logger = None
    if keys.get("galileo"):
        from galileo import GalileoLogger

        logger = GalileoLogger(project=project, log_stream=stream)
        logger.start_trace(
            input=query,
            name="openai-agents-galileo",
            tags=["integration", "openai-agents", "dizzygraph-starter"],
            metadata={"framework": "openai-agents"},
        )

    result = await Runner.run(agent, query)
    output = str(getattr(result, "final_output", None) or result)

    if logger is not None:
        logger.add_llm_span(
            input=query,
            output=output,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            name="openai_agents.runner.run",
            metadata={"otel.span_name": "openai_agents.runner.run"},
        )
        logger.conclude(output=output)
        logger.flush()
        print(f"galileo: {project}/{stream}")
    else:
        print("galileo: skipped (GALILEO_API_KEY missing)")

    print("── answer ──")
    print(output[:800])
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
