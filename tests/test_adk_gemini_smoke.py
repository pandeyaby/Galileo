"""Offline honesty checks for Google ADK / Gemini smoke starters (no API spend)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "examples" / "integrations"


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INTEGRATIONS / script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _base_env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "VERTEX_PROJECT",
            "GALILEO_API_KEY",
            "Galileo_API_Key",
        )
    }
    env["DIZZY_SKIP_DOTENV"] = "1"
    env.update(extra)
    return env


def test_google_adk_skips_without_google_keys():
    env = _base_env(GALILEO_API_KEY="galileo-test-placeholder-not-real")
    proc = _run("google_adk_galileo.py", env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "SMOKE SKIP" in out
    assert "GOOGLE_API_KEY" in out
    assert "SMOKE FAIL" not in out


def test_gemini_skips_without_google_keys():
    env = _base_env(GALILEO_API_KEY="galileo-test-placeholder-not-real")
    proc = _run("gemini_enterprise_galileo.py", env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "SMOKE SKIP" in out
    assert "SMOKE FAIL" not in out


def test_smoke_results_marks_adk_gemini_skip():
    text = (INTEGRATIONS / "SMOKE-RESULTS.md").read_text(encoding="utf-8")
    assert "| Google ADK |" in text
    assert "**SKIP**" in text
    assert "not FAIL" in text
    assert "Do not invent keys" in text or "do not invent keys" in text.lower()
