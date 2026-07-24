"""Instruction Adherence cookbook demo → Galileo experiment (real SDK, no mock).

Runs a small prompt-quality experiment with Galileo's built-in
``instruction_adherence`` metric: a vague prompt (expect weaker adherence) vs an
explicit constrained prompt (expect stronger adherence).

Usage:
  pip install galileo openai
  export OPENAI_API_KEY=... GALILEO_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs
  # optional: GALILEO_LOG_STREAM is not used by run_experiment; project hosts the experiment
  python examples/integrations/instruction_adherence_galileo.py

Fail-loud: missing keys/packages exit 2. Network/API failures surface as errors
(no fake "all green" scores).
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo, require_openai


VAGUE_INSTRUCTIONS = (
    "Explain Newton's First Law succinctly."
)

STRICT_INSTRUCTIONS = """
1. Explain Newton's First Law in one sentence of no more than fifteen (15) words.
2. Do not add any additional sentences, examples, parentheses, bullet points,
   or further clarifications.
3. Your answer must be exactly one sentence and must not exceed 15 words.
""".strip()


def _openai_complete(instructions: str, user: str) -> str:
    from openai import OpenAI

    client = OpenAI()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


def main() -> int:
    err = require_openai() or require_galileo()
    if err:
        return err

    try:
        from galileo.experiments import run_experiment
        from galileo.schema.metrics import GalileoMetrics
        from openai import OpenAI  # noqa: F401
    except ImportError:
        print("ERROR: pip install galileo openai")
        return 2

    project, _stream = project_stream("instruction-adherence")
    os.environ.setdefault("GALILEO_PROJECT", project)
    experiment_name = os.environ.get(
        "GALILEO_EXPERIMENT_NAME", "instruction-adherence-cookbook"
    )
    user_q = "What is Newton's First Law of Motion?"

    dataset: list[dict[str, Any]] = [
        {
            "id": "vague",
            "instructions": VAGUE_INSTRUCTIONS,
            "input": user_q,
            "label": "vague_prompt",
        },
        {
            "id": "strict",
            "instructions": STRICT_INSTRUCTIONS,
            "input": user_q,
            "label": "strict_prompt",
        },
    ]

    def runner(row: dict[str, Any]) -> str:
        # Galileo runner functions receive dataset row dicts
        instructions = row.get("instructions") or VAGUE_INSTRUCTIONS
        user = row.get("input") or user_q
        return _openai_complete(str(instructions), str(user))

    print(f"galileo experiment → project={project} name={experiment_name}")
    print("metric: Instruction Adherence (GalileoMetrics.instruction_adherence)")
    print("rows: vague prompt vs strict constrained prompt")

    try:
        result = run_experiment(
            experiment_name,
            project=project,
            dataset=dataset,
            function=runner,
            metrics=[GalileoMetrics.instruction_adherence],
            experiment_tags={
                "cookbook": "instruction-adherence",
                "framework": "openai",
            },
        )
    except Exception as exc:
        print(f"ERROR: run_experiment failed: {type(exc).__name__}: {exc}")
        return 2

    print("── experiment submitted ──")
    print(repr(result)[:1200])
    print(
        "Open the Galileo Console → Experiments to inspect Instruction Adherence "
        "scores (vague vs strict). Scores are server-side — not local mocks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
