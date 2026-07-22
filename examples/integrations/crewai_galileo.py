"""CrewAI → Galileo logging starter (real keys when present).

Thin, runnable example — not a fake integration. Uses GalileoLogger manually
around a CrewAI crew when ``crewai`` is installed; otherwise exits with a clear
install hint (no silent mock of CrewAI).

Usage:
  pip install crewai galileo openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  python examples/integrations/crewai_galileo.py
"""

from __future__ import annotations

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


def main() -> int:
    keys = _load_keys()
    if not keys.get("openai"):
        print("ERROR: OPENAI_API_KEY required (no mock).")
        return 2
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        print("ERROR: install crewai first: pip install crewai")
        return 2

    project = os.environ.get("GALILEO_PROJECT", "rax-galileo-labs")
    stream = os.environ.get("GALILEO_LOG_STREAM", "crewai-integration")
    query = "In one short paragraph: what is gradient checkpointing?"

    researcher = Agent(
        role="ML Infra Researcher",
        goal="Answer ML training/inference questions accurately and briefly",
        backstory="You are a platform engineer who cites concrete techniques.",
        verbose=False,
        allow_delegation=False,
    )
    task = Task(
        description=query,
        expected_output="A short grounded paragraph.",
        agent=researcher,
    )
    crew = Crew(agents=[researcher], tasks=[task], process=Process.sequential, verbose=False)

    logger = None
    if keys.get("galileo"):
        from galileo import GalileoLogger

        logger = GalileoLogger(project=project, log_stream=stream)
        logger.start_trace(
            input=query,
            name="crewai-galileo",
            tags=["integration", "crewai", "dizzygraph-starter"],
            metadata={"framework": "crewai"},
        )

    result = crew.kickoff()
    output = str(result)

    if logger is not None:
        logger.add_llm_span(
            input=query,
            output=output,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            name="crewai.crew.kickoff",
            metadata={"otel.span_name": "crewai.crew.kickoff"},
        )
        logger.conclude(output=output)
        logger.flush()
        print(f"galileo: {project}/{stream}")
    else:
        print("galileo: skipped (GALILEO_API_KEY missing)")

    print("── answer ──")
    print(output[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
