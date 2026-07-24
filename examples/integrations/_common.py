"""Shared helpers for Galileo integration starters."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_keys() -> dict[str, bool]:
    if os.environ.get("DIZZY_SKIP_DOTENV", "").strip().lower() in {"1", "true", "yes"}:
        return {
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "galileo": bool(os.environ.get("GALILEO_API_KEY")),
        }
    try:
        from trinity_dizzy import load_runtime_keys

        return load_runtime_keys()
    except Exception:
        return {
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "galileo": bool(os.environ.get("GALILEO_API_KEY")),
        }


def require_openai() -> int | None:
    load_keys()
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY required (no mock).")
        return 2
    return None


def require_galileo() -> int | None:
    load_keys()
    if not os.environ.get("GALILEO_API_KEY"):
        print("ERROR: GALILEO_API_KEY required for this starter (no silent skip).")
        return 2
    return None


def project_stream(default_stream: str) -> tuple[str, str]:
    return (
        os.environ.get("GALILEO_PROJECT", "rax-galileo-labs"),
        os.environ.get("GALILEO_LOG_STREAM", default_stream),
    )


def setup_galileo_otel(*, project: str | None = None, log_stream: str | None = None) -> Any:
    """
    Configure a global TracerProvider with GalileoSpanProcessor.

    Requires: pip install 'galileo[otel]' opentelemetry-sdk
    """
    try:
        from galileo.otel import GalileoSpanProcessor, add_galileo_span_processor
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError as exc:
        raise ImportError(
            "Install OTel extras: pip install 'galileo[otel]' opentelemetry-sdk opentelemetry-api"
        ) from exc

    if project:
        os.environ.setdefault("GALILEO_PROJECT", project)
    if log_stream:
        os.environ.setdefault("GALILEO_LOG_STREAM", log_stream)

    provider = TracerProvider()
    add_galileo_span_processor(provider, GalileoSpanProcessor())
    trace.set_tracer_provider(provider)
    return provider
