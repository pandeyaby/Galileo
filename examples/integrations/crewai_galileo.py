"""CrewAI → Galileo via official CrewAIEventListener (real SDK, no mock).

Usage:
  pip install crewai galileo openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  python examples/integrations/crewai_galileo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import load_keys, project_stream, require_openai


def main() -> int:
    err = require_openai()
    if err:
        return err
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        print("ERROR: install crewai first: pip install crewai")
        return 2

    keys = load_keys()
    project, stream = project_stream("crewai-integration")
    query = "In one short paragraph: what is gradient checkpointing?"

    # Prefer official Galileo CrewAI event listener when GALILEO_API_KEY is set
    listener = None
    if keys.get("galileo") or os.environ.get("GALILEO_API_KEY"):
        try:
            from galileo import GalileoLogger
            from galileo.handlers.crewai.handler import CrewAIEventListener

            logger = GalileoLogger(project=project, log_stream=stream)
            listener = CrewAIEventListener(
                galileo_logger=logger,
                start_new_trace=True,
                flush_on_crew_completed=True,
            )
            print(f"galileo listener: {project}/{stream}")
        except ImportError:
            print("WARN: galileo.handlers.crewai unavailable — falling back to manual logger")
            listener = None

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

    # Keep listener referenced so it is not GC'd before kickoff
    _ = listener
    result = crew.kickoff()
    output = str(result)

    if listener is None and (keys.get("galileo") or os.environ.get("GALILEO_API_KEY")):
        from galileo import GalileoLogger

        logger = GalileoLogger(project=project, log_stream=stream)
        logger.start_trace(
            input=query,
            name="crewai-galileo",
            tags=["integration", "crewai", "dizzygraph-starter"],
            metadata={"framework": "crewai", "path": "manual_logger_fallback"},
        )
        logger.add_llm_span(
            input=query,
            output=output,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            name="crewai.crew.kickoff",
            metadata={"otel.span_name": "crewai.crew.kickoff"},
        )
        logger.conclude(output=output)
        logger.flush()
        print(f"galileo manual: {project}/{stream}")
    elif not (keys.get("galileo") or os.environ.get("GALILEO_API_KEY")):
        print("galileo: skipped (GALILEO_API_KEY missing)")

    print("── answer ──")
    print(output[:800])
    return 0


if __name__ == "__main__":
    # Allow `python examples/integrations/crewai_galileo.py` from repo root
    raise SystemExit(main())
