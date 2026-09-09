"""Player demo entry — fail loud without keys; no live API spend in CI."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_player_demo_fails_loud_without_keys():
    with tempfile.TemporaryDirectory() as home:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "GALILEO_API_KEY", "Galileo_API_Key")
        }
        env["HOME"] = home
        proc = _run([sys.executable, str(ROOT / "examples" / "player_demo.py")], env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "PREFLIGHT FAILED" in out
    assert "Demo aborted" in out
    assert "never commit secrets" in out.lower()
    assert "troubleshooter" in out.lower()
    # deep-link into missing-keys runbook (or generic troubleshooter)
    assert "no-galileo-api-key-found" in out or "pandeyaby.github.io/Galileo/troubleshooter" in out
    assert "player-friction" in out or "issues/new" in out
    # never echo secret values
    assert "sk-proj-" not in out


def test_app_py_demo_flag_fails_loud_without_keys():
    with tempfile.TemporaryDirectory() as home:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "GALILEO_API_KEY", "Galileo_API_Key")
        }
        env["HOME"] = home
        proc = _run([sys.executable, str(ROOT / "app.py"), "--demo"], env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "PREFLIGHT FAILED" in out


def test_makefile_demo_target_exists():
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "demo:" in text
    assert "examples/player_demo.py" in text


def test_readme_for_players_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## For players" in text
    assert "make demo" in text
    assert "troubleshooter" in text.lower()
    assert "Never commit secrets" in text or "never commit secrets" in text
    assert "https://api.galileo.ai/agent-control" in text
    assert "player-friction" in text or "Filing friction" in text
    assert "OPENAI_API_KEY" in text and "GALILEO_API_KEY" in text


def test_player_friction_issue_template_exists():
    path = ROOT / ".github" / "ISSUE_TEMPLATE" / "player-friction.yml"
    assert path.is_file(), "missing player-friction issue template"
    text = path.read_text(encoding="utf-8")
    assert "symptom" in text.lower() or "Symptom" in text
    assert "protect_path" in text
    assert "troubleshooter" in text.lower()
    assert "OPENAI_API_KEY" in text
    assert "controls" in text.lower()


def test_ci_workflow_offline_safe():
    path = ROOT / ".github" / "workflows" / "ci.yml"
    assert path.is_file(), "missing CI workflow"
    text = path.read_text(encoding="utf-8")
    assert "app.py --preflight" in text
    assert "pytest" in text
    assert "workflow_dispatch" in text
    assert "live-smoke" in text
    # default path must not require real secrets spend
    assert "GALILEO_PREFLIGHT_LIVE" in text
