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
