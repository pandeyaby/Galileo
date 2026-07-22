"""Tenant ↔ Galileo project / log_stream mapping.

Resolve ``tenant_id`` to Console project + stream for fleet flush and Trinity logs.
Config via env ``DIZZY_TENANT_GALILEO`` (JSON) or per-tenant env vars.
"""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_PROJECT = "rax-galileo-labs"
DEFAULT_STREAM = "trinity-dizzy"


def _parse_env_table() -> dict[str, dict[str, str]]:
    raw = os.environ.get("DIZZY_TENANT_GALILEO") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, str]] = {}
    if not isinstance(data, dict):
        return out
    for tenant, cfg in data.items():
        if isinstance(cfg, dict):
            out[str(tenant)] = {
                "project": str(cfg.get("project") or DEFAULT_PROJECT),
                "log_stream": str(cfg.get("log_stream") or cfg.get("stream") or DEFAULT_STREAM),
            }
    return out


def resolve_galileo_target(tenant_id: str | None = None) -> dict[str, str]:
    """
    Map tenant_id → {project, log_stream}.

    Precedence:
      1. DIZZY_TENANT_GALILEO JSON table
      2. DIZZY_GALILEO_PROJECT_<TENANT> / DIZZY_GALILEO_STREAM_<TENANT>
      3. GALILEO_PROJECT / GALILEO_LOG_STREAM (global)
      4. Built-in defaults
    """
    tid = (tenant_id or "default").strip() or "default"
    table = _parse_env_table()
    if tid in table:
        return dict(table[tid])

    env_key = tid.upper().replace("-", "_")
    proj = os.environ.get(f"DIZZY_GALILEO_PROJECT_{env_key}")
    stream = os.environ.get(f"DIZZY_GALILEO_STREAM_{env_key}")
    if proj or stream:
        return {
            "project": proj or os.environ.get("GALILEO_PROJECT") or DEFAULT_PROJECT,
            "log_stream": stream or os.environ.get("GALILEO_LOG_STREAM") or DEFAULT_STREAM,
        }

    return {
        "project": os.environ.get("GALILEO_PROJECT") or DEFAULT_PROJECT,
        "log_stream": os.environ.get("GALILEO_LOG_STREAM") or DEFAULT_STREAM,
        "tenant_id": tid,
    }


def list_tenant_mappings() -> dict[str, Any]:
    """Return effective mapping table (env table + defaults)."""
    table = _parse_env_table()
    if "default" not in table:
        table = {
            "default": {
                "project": os.environ.get("GALILEO_PROJECT") or DEFAULT_PROJECT,
                "log_stream": os.environ.get("GALILEO_LOG_STREAM") or DEFAULT_STREAM,
            },
            **table,
        }
    return {"tenants": table, "source": "DIZZY_TENANT_GALILEO|env|defaults"}
