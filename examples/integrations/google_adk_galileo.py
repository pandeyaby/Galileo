"""Google ADK → Galileo via galileo-adk GalileoADKPlugin (real SDK, no mock).

Usage:
  pip install galileo-adk google-adk
  export GALILEO_API_KEY=... GOOGLE_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=google-adk-integration
  python examples/integrations/google_adk_galileo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo


async def _run() -> int:
    err = require_galileo()
    if err:
        return err
    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GOOGLE_API_KEY (or GEMINI_API_KEY) required (no mock).")
        return 2

    try:
        from galileo_adk import GalileoADKPlugin
    except ImportError:
        print("ERROR: install galileo-adk: pip install galileo-adk")
        return 2
    try:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.genai import types
    except ImportError:
        print("ERROR: install google-adk: pip install google-adk")
        return 2

    project, stream = project_stream("google-adk-integration")
    plugin = GalileoADKPlugin(project=project, log_stream=stream)
    agent = LlmAgent(
        name="ml_infra_assistant",
        model=os.environ.get("GOOGLE_ADK_MODEL", "gemini-2.0-flash"),
        instruction="You are an ML platform engineer. Answer in one short paragraph.",
    )
    # ADK Runner app_name/session_service requirements vary by version —
    # construct with plugins; fall back to keyword variants if needed.
    try:
        runner = Runner(agent=agent, plugins=[plugin], app_name="dizzygraph-adk")
    except TypeError:
        try:
            runner = Runner(agent=agent, plugins=[plugin])
        except TypeError as exc:
            print(f"ERROR: google-adk Runner API mismatch: {exc}")
            return 2

    query = "In one sentence: what is gradient checkpointing?"
    message = types.Content(parts=[types.Part(text=query)])
    final = ""
    async for event in runner.run_async(
        user_id="dizzygraph",
        session_id="adk-starter",
        new_message=message,
    ):
        if getattr(event, "is_final_response", lambda: False)():
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) or []
            if parts:
                final = getattr(parts[0], "text", "") or ""

    print(f"galileo-adk: {project}/{stream}")
    print("── answer ──")
    print((final or "(no final response)").strip()[:800])
    return 0 if final else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
