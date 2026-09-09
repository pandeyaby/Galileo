"""Offline preflight checks (no network / no API spend)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_preflight(env: dict[str, str], *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "app.py"), *extra_args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_env_example_lists_required_vars():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in text
    assert "GALILEO_API_KEY=" in text
    assert "rax-galileo-labs" in text
    assert "trinity-stack" in text
    assert "Agent Control" in text
    assert "trinity-protect" in text  # deprecated mention
    # Pin hosted Agent Control path (legacy agent-control.galileo.ai SSL-mismatches)
    assert "AGENT_CONTROL_URL=https://api.galileo.ai/agent-control" in text
    assert "agent-control.galileo.ai" not in text or "SSL" in text
    # Cloud Agents / CI must use exact canonical names
    assert "Cloud Agents" in text or "CI" in text
    assert "exact" in text.lower() or "GALILEO_API_KEY" in text
    # placeholders only — no obvious live secret shapes
    assert "sk-proj-" not in text
    assert "sk-your-openai-key" in text
    # useful optional starters retained from env preflight PR
    assert "GOOGLE_API_KEY=" in text or "# GOOGLE_API_KEY=" in text
    assert "BEDROCK_MODEL_ID=" in text or "# BEDROCK_MODEL_ID=" in text
    assert "SMOKE SKIP" in text or "SKIP" in text


def test_preflight_prints_api_path_agent_control_url():
    with tempfile.TemporaryDirectory() as home:
        env = {
            **{k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GALILEO_API_KEY", "Galileo_API_Key", "AGENT_CONTROL_URL")},
            "OPENAI_API_KEY": "sk-test-placeholder-not-real",
            "GALILEO_API_KEY": "galileo-test-placeholder-not-real",
            "HOME": home,
        }
        proc = _run_preflight(env, "--preflight")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "https://api.galileo.ai/agent-control" in out
    assert "https://agent-control.galileo.ai" not in out


def test_preflight_fails_loud_without_keys():
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GALILEO_API_KEY", "Galileo_API_Key")}
        env["HOME"] = home
        proc = _run_preflight(env, "--preflight")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert "PREFLIGHT FAILED" in out
    assert "OPENAI_API_KEY present:  NO" in out
    assert "GALILEO_API_KEY present: NO" in out
    assert "Agent Control" in out
    assert "trinity-protect" in out  # deprecated mention
    # never echo placeholder-looking secret values from env (none set)
    assert "sk-" not in out


def test_preflight_ok_with_keys_and_corpus():
    with tempfile.TemporaryDirectory() as home:
        env = {
            **os.environ,
            "OPENAI_API_KEY": "sk-test-placeholder-not-real",
            "GALILEO_API_KEY": "galileo-test-placeholder-not-real",
            "HOME": home,
        }
        env.pop("Galileo_API_Key", None)
        proc = _run_preflight(env, "--preflight")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "PREFLIGHT OK" in out
    assert "OPENAI_API_KEY present:  YES" in out
    assert "GALILEO_API_KEY present: YES" in out
    assert "restore path: OK" in out
    # boolean presence only — must not echo the env values
    assert "sk-test-placeholder-not-real" not in out
    assert "galileo-test-placeholder-not-real" not in out
    assert "does not create Controls" in out or "Controls tab" in out


def test_preflight_live_skips_network_without_greenlit_flag():
    with tempfile.TemporaryDirectory() as home:
        env = {
            **os.environ,
            "OPENAI_API_KEY": "sk-test-placeholder-not-real",
            "GALILEO_API_KEY": "galileo-test-placeholder-not-real",
            "HOME": home,
        }
        env.pop("GALILEO_PREFLIGHT_LIVE", None)
        env.pop("Galileo_API_Key", None)
        proc = _run_preflight(env, "--preflight-live")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Live check: SKIPPED" in out
    assert "no Control" in out.lower() or "no invoke_protect" in out.lower() or "no spend" in out.lower()
