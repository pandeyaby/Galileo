"""Offline tests for Agent Control protect path (no live API spend)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_ac_state(monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "_ac_initialized", False)
    monkeypatch.setattr(app_mod, "_ac_init_error", None)
    yield
    monkeypatch.setattr(app_mod, "_ac_initialized", False)
    monkeypatch.setattr(app_mod, "_ac_init_error", None)


def test_preflight_fails_without_keys(monkeypatch, capsys, tmp_path):
    import app as app_mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GALILEO_API_KEY", raising=False)
    monkeypatch.delenv("Galileo_API_Key", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Avoid picking up a workspace .env if present
    monkeypatch.setattr(app_mod, "_load_dotenv", lambda path=None: None)

    code = app_mod.run_preflight(live=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "PREFLIGHT FAILED" in out
    assert "OPENAI_API_KEY present:  NO" in out
    assert "GALILEO_API_KEY present: NO" in out
    assert "Agent Control" in out
    assert "trinity-protect" in out  # deprecated mention
    # Never echo secret-looking values
    assert "sk-" not in out


def test_preflight_ok_with_placeholder_keys(monkeypatch, capsys, tmp_path):
    import app as app_mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GALILEO_API_KEY", "g-test")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app_mod, "_load_dotenv", lambda path=None: None)

    code = app_mod.run_preflight(live=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "PREFLIGHT OK" in out
    assert "g-test" not in out
    assert "sk-test" not in out


def test_preflight_live_skipped_without_env(monkeypatch, capsys, tmp_path):
    import app as app_mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GALILEO_API_KEY", "g-test")
    monkeypatch.delenv("GALILEO_PREFLIGHT_LIVE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app_mod, "_load_dotenv", lambda path=None: None)

    code = app_mod.run_preflight(live=True)
    out = capsys.readouterr().out
    assert code == 0
    assert "Live check: SKIPPED" in out


def test_normalize_galileo_api_key_alias(monkeypatch):
    import app as app_mod

    monkeypatch.delenv("GALILEO_API_KEY", raising=False)
    monkeypatch.setenv("Galileo_API_Key", "from-alias")
    app_mod._normalize_galileo_api_key()
    assert os.environ.get("GALILEO_API_KEY") == "from-alias"


def test_agent_control_url_derivation(monkeypatch):
    import app as app_mod

    monkeypatch.delenv("AGENT_CONTROL_URL", raising=False)
    monkeypatch.setenv("GALILEO_API_URL", "https://api.galileo.ai")
    assert app_mod._agent_control_url() == "https://api.galileo.ai/agent-control"

    monkeypatch.setenv("GALILEO_API_URL", "https://api.acme.galileo.ai")
    assert app_mod._agent_control_url() == "https://api.acme.galileo.ai/agent-control"

    monkeypatch.setenv("GALILEO_API_URL", "https://api.galileo.ai/agent-control")
    assert app_mod._agent_control_url() == "https://api.galileo.ai/agent-control"

    monkeypatch.setenv("AGENT_CONTROL_URL", "https://custom.example/ac/")
    assert app_mod._agent_control_url() == "https://custom.example/ac"


def test_agent_control_url_default_constant():
    import app as app_mod

    assert app_mod.DEFAULT_AGENT_CONTROL_URL == "https://api.galileo.ai/agent-control"
    assert "agent-control.galileo.ai" not in app_mod.DEFAULT_AGENT_CONTROL_URL


def test_protect_node_agent_control_deny(monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "_AGENT_CONTROL_SDK_AVAILABLE", True)
    monkeypatch.setattr(app_mod, "_ac_initialized", True)
    monkeypatch.setattr(app_mod, "_ac_init_error", None)
    monkeypatch.setenv("GALILEO_API_KEY", "g-test")

    def _deny(**kwargs):
        raise app_mod.ControlViolationError(
            control_id=1, control_name="grounding", message="ungrounded"
        )

    monkeypatch.setattr(app_mod, "evaluate_post_control", _deny)

    out = app_mod.protect_node(
        {
            "query": "q",
            "draft_answer": "fabricated answer",
            "retrieved_docs": ["irrelevant"],
            "context_score": 0.1,
        }
    )
    assert out["protect_status"] == "triggered"
    assert out["protect_path"] == "agent_control"
    assert "BLOCKED" in out["final_answer"]


def test_protect_node_agent_control_allow(monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "_AGENT_CONTROL_SDK_AVAILABLE", True)
    monkeypatch.setattr(app_mod, "_ac_initialized", True)
    monkeypatch.setattr(app_mod, "_ac_init_error", None)
    monkeypatch.setenv("GALILEO_API_KEY", "g-test")
    monkeypatch.setattr(
        app_mod, "evaluate_post_control", lambda **kwargs: kwargs["draft"]
    )

    draft = "grounded answer from KB"
    out = app_mod.protect_node(
        {
            "query": "q",
            "draft_answer": draft,
            "retrieved_docs": ["doc"],
            "context_score": 0.9,
        }
    )
    assert out["protect_status"] == "not_triggered"
    assert out["protect_path"] == "agent_control"
    assert out["final_answer"] == draft


def test_protect_node_llm_fallback_when_control_unavailable(monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "_AGENT_CONTROL_SDK_AVAILABLE", True)
    monkeypatch.setattr(app_mod, "_ac_initialized", True)
    monkeypatch.setattr(app_mod, "_ac_init_error", None)
    monkeypatch.setenv("GALILEO_API_KEY", "g-test")

    def _boom(**kwargs):
        raise RuntimeError("Control API down")

    monkeypatch.setattr(app_mod, "evaluate_post_control", _boom)
    monkeypatch.setattr(app_mod, "_judge_context_adherence", lambda q, c, a: 0.1)

    out = app_mod.protect_node(
        {
            "query": "q",
            "draft_answer": "bad",
            "retrieved_docs": [],
            "context_score": None,
        }
    )
    assert out["protect_path"] == "llm_judge_fallback"
    assert out["protect_status"] == "triggered"
    assert out["context_score"] == 0.1


def test_evaluate_post_control_maps_deny_match(monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "_AGENT_CONTROL_SDK_AVAILABLE", True)
    monkeypatch.setattr(app_mod, "_ac_initialized", True)
    monkeypatch.setattr(app_mod, "_ac_init_error", None)

    match = SimpleNamespace(
        action="deny",
        control_id=42,
        control_name="post-grounding",
        result=SimpleNamespace(message="score below floor"),
    )
    fake = SimpleNamespace(is_safe=False, matches=[match], errors=[])

    async def _fake_eval(*args, **kwargs):
        return fake

    mock_ac = MagicMock()
    mock_ac.evaluate_controls = _fake_eval
    monkeypatch.setattr(app_mod, "agent_control", mock_ac)

    with pytest.raises(app_mod.ControlViolationError) as ei:
        app_mod.evaluate_post_control(query="q", draft="d", context="c")
    assert ei.value.control_name == "post-grounding"


def test_run_query_fails_loud_without_keys(monkeypatch):
    import app as app_mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GALILEO_API_KEY", raising=False)
    monkeypatch.setattr(app_mod, "_normalize_galileo_api_key", lambda: None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        app_mod.run_query(MagicMock(), "hello", verbose=False)
