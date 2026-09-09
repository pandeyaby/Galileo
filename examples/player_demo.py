#!/usr/bin/env python3
"""
One-command player demo for Trinity Stack / Galileo.

  make demo
  python examples/player_demo.py
  python app.py --demo

Flow (fail-loud, no mocks):
  1. Offline preflight (keys + corpus + Agent Control checklist)
  2. Short healthy baseline (2 queries)
  3. XL-2 poison → same queries → restore corpus
  4. Print Console links, fleet-vs-Galileo summary, cost note

Never prints API key values. Never commits secrets — use .env locally.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Short subset — keep spend low for players.
DEMO_QUERIES = [
    "How do I fix a CUDA out-of-memory error during training?",
    "How does vLLM get such high inference throughput?",
]

CONSOLE_BASE = "https://app.galileo.ai"


def _banner(msg: str, char: str = "=") -> None:
    print(f"\n{char * 65}\n  {msg}\n{char * 65}", flush=True)


def _fleet_status() -> str:
    hb = ROOT / "fleet" / "heartbeat.json"
    if not hb.exists():
        return "ALARM:MISSING (no heartbeat yet)"
    data = json.loads(hb.read_text(encoding="utf-8"))
    ts_raw = data.get("ts", "")
    try:
        ts = datetime.datetime.fromisoformat(ts_raw.rstrip("Z").replace("+00:00", ""))
        age = (datetime.datetime.utcnow() - ts).total_seconds()
    except Exception:
        age = -1
    latency = data.get("latency_ms", "?")
    status = data.get("status", "?")
    if age < 0:
        return f"{status} (latency={latency}ms)"
    return f"OK (last heartbeat {age:.0f}s ago, status={status}, latency={latency}ms)"


def _run_preflight() -> int:
    """Cheap fail-loud path via app.py --preflight (no heavy SDK import first)."""
    _banner("Player demo — preflight (offline, no API spend)")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run(
        [sys.executable, "-u", str(ROOT / "app.py"), "--preflight"],
        cwd=str(ROOT),
        env=env,
    )
    return int(proc.returncode)


def _console_links(project: str, stream: str) -> None:
    print(f"\nConsole: {CONSOLE_BASE}")
    print(f"  Project: {project}")
    print(f"  Stream:  {stream}")
    print(f"  Deep link pattern: {CONSOLE_BASE} → {project} → {stream}")
    print("  Filter tags: demo-baseline | demo-poisoned | demo-recovered")


def run_demo() -> int:
    code = _run_preflight()
    if code != 0:
        print("\nDemo aborted — fix preflight failures first.")
        print("  cp .env.example .env   # fill keys locally; never commit secrets")
        print("  Stuck on Galileo? https://pandeyaby.github.io/Galileo/troubleshooter/")
        return code

    # Heavy imports only after keys/corpus look present.
    from app import (  # noqa: WPS433 — intentional late import
        PROJECT,
        LOG_STREAM,
        MODEL,
        EMBED_MODEL,
        KB_FILE,
        KNOWLEDGE_BASE_POISONED,
        build_graph,
        run_query,
        save_kb,
        restore_full_corpus,
        load_full_corpus,
    )

    _banner("Player demo — short baseline (healthy corpus)")
    print(f"Model: {MODEL}  |  Embeddings: {EMBED_MODEL}")
    print(f"Queries: {len(DEMO_QUERIES)} (short path — not the full 10-query baseline)")
    graph = build_graph()
    for q in DEMO_QUERIES:
        run_query(graph, q, verbose=True, tag="demo-baseline")
    fleet_ok = _fleet_status()
    print(f"\nFleet status after baseline: {fleet_ok}")
    print("Expected Galileo (baseline): context_adherence / completeness / cites_kb_source ~ high")

    try:
        _banner("Player demo — XL-2 inject (poisoned retriever)", "─")
        # Preserve current on-disk KB so restore brings lab-scale corpus back.
        if KB_FILE.exists():
            save_kb(load_full_corpus(), KB_FILE.with_suffix(".json.bak"))
        save_kb(KNOWLEDGE_BASE_POISONED, KB_FILE)
        print("☠️  Corpus swapped to off-domain index (meal kits / yoga / plumbing).")

        graph_bad = build_graph(kb=KNOWLEDGE_BASE_POISONED)
        for q in DEMO_QUERIES:
            run_query(graph_bad, q, verbose=True, tag="demo-poisoned")

        fleet_blind = _fleet_status()
        print(f"\nFleet status while poisoned: {fleet_blind}")
        print("Fleet looks fine (process alive, latency normal) — TRUST layer catches the lie.")
    finally:
        _banner("Player demo — restore corpus", "─")
        path = restore_full_corpus()
        print(f"✅ Corpus restored → {path}")

    graph_ok = build_graph()
    run_query(graph_ok, DEMO_QUERIES[0], verbose=True, tag="demo-recovered")

    _banner("Player demo — fleet vs Galileo")
    print(
        textwrap.dedent(
            """
            Fleet (RUN):     green throughout — heartbeat/latency do not see bad retrieval
            Galileo (TRUST): baseline healthy → poisoned crater → recover after restore
                             Watch context_adherence / completeness / cites_kb_source
                             tags: demo-baseline vs demo-poisoned vs demo-recovered

            Thesis in one line: fleet can look fine while quality is dead.
            """
        ).strip()
    )
    _console_links(PROJECT, LOG_STREAM)

    n_queries = len(DEMO_QUERIES) * 2 + 1  # baseline + poisoned + one recover
    print(
        textwrap.dedent(
            f"""
            Cost note: ~{n_queries} chat calls on {MODEL} + embeddings ({EMBED_MODEL}).
            Embeddings are cached under .vector_cache/ after the first index build.
            Expect low single-digit cents at lab scale (gpt-4o-mini); not a full 6-drill run.

            Next: full baseline `python app.py --batch` or drills under drills/.
            Stuck: https://pandeyaby.github.io/Galileo/troubleshooter/
            Keys: stay in .env / env — never commit secrets.
            """
        ).strip()
    )
    print("\n✅ Player demo complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run_demo())
    except KeyboardInterrupt:
        print("\nInterrupted — attempting corpus restore…")
        try:
            from app import restore_full_corpus

            restore_full_corpus()
            print("✅ Corpus restored.")
        except Exception as exc:
            print(f"Restore failed ({type(exc).__name__}) — run: python app.py --restore-corpus")
        raise SystemExit(130)
