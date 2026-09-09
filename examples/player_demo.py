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
  5. On failure/success: deep-link into the public troubleshooter

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
TROUBLESHOOTER = "https://pandeyaby.github.io/Galileo/troubleshooter/"
ISSUE_TEMPLATE = (
    "https://github.com/pandeyaby/Galileo/issues/new"
    "?template=player-friction.yml"
)

# Symptom → existing public troubleshooter anchors (do not rebuild that site).
TS_LINKS = {
    "missing_keys": (
        f"{TROUBLESHOOTER}#no-galileo-api-key-found-before-any-traces-are-logged"
    ),
    "key_name_mismatch": (
        f"{TROUBLESHOOTER}#i-set-the-galileo-api-key-but-the-sdk-still-says-it-is-missing"
    ),
    "ssl_url": (
        f"{TROUBLESHOOTER}#self-hosted-console-api-url-derivation-cert-failures"
    ),
    "zero_controls": (
        f"{TROUBLESHOOTER}"
        "#agent-gives-confident-fluent-wrong-answers-evals-look-ok-except-one-metric"
    ),
    "protect_block": (
        f"{TROUBLESHOOTER}"
        "#agent-gives-confident-fluent-wrong-answers-evals-look-ok-except-one-metric"
    ),
    "generic": TROUBLESHOOTER,
}


def _banner(msg: str, char: str = "=") -> None:
    print(f"\n{char * 65}\n  {msg}\n{char * 65}", flush=True)


def _print_friction_footer(*, mode: str, detail: str = "") -> None:
    """Point players at the right troubleshooter section, then the issue template."""
    url = TS_LINKS.get(mode, TS_LINKS["generic"])
    print("\n── Player feedback loop ──")
    if detail:
        print(f"  Symptom hint: {detail}")
    print(f"  Troubleshoot: {url}")
    if mode == "generic":
        print("  (No exact runbook match — use Ctrl+F / report symptom in the issue form.)")
    print(f"  Still stuck? File friction (no secrets): {ISSUE_TEMPLATE}")


def _classify_preflight_failure(out: str) -> tuple[str, str]:
    low = out.lower()
    if "galileo_api_key missing" in low or "openai_api_key missing" in low or "present:  no" in low:
        if "galileo_api_key" in low and "alias" in low:
            return "key_name_mismatch", "missing / misnamed API keys"
        return "missing_keys", "missing API keys (OPENAI_API_KEY / GALILEO_API_KEY)"
    if "controls attached" in low and ("== 0" in out or "controls attached: 0" in low):
        return "zero_controls", "Agent Control controls attached == 0"
    if "ssl" in low or "agent-control.galileo.ai" in low:
        return "ssl_url", "AGENT_CONTROL_URL / SSL host mismatch"
    if "protect" in low and ("block" in low or "deny" in low or "triggered" in low):
        return "protect_block", "Protect / Agent Control gate"
    return "generic", "preflight / demo failure"


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


def _run_preflight() -> tuple[int, str]:
    """Cheap fail-loud path via app.py --preflight (no heavy SDK import first)."""
    _banner("Player demo — preflight (offline, no API spend)")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run(
        [sys.executable, "-u", str(ROOT / "app.py"), "--preflight"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if combined:
        print(combined, end="" if combined.endswith("\n") else "\n", flush=True)
    return int(proc.returncode), combined


def _console_links(project: str, stream: str) -> None:
    print(f"\nConsole: {CONSOLE_BASE}")
    print(f"  Project: {project}")
    print(f"  Stream:  {stream}")
    print(f"  Deep link pattern: {CONSOLE_BASE} → {project} → {stream}")
    print("  Filter tags: demo-baseline | demo-poisoned | demo-recovered")


def run_demo() -> int:
    code, preflight_out = _run_preflight()
    if code != 0:
        print("\nDemo aborted — fix preflight failures first.")
        print("  cp .env.example .env   # fill keys locally; never commit secrets")
        print("  Canonical names: OPENAI_API_KEY + GALILEO_API_KEY (exact case for CI/Cloud Agents)")
        mode, detail = _classify_preflight_failure(preflight_out)
        _print_friction_footer(mode=mode, detail=detail)
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

    try:
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
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        print(f"\nDemo failed mid-run: {msg}", file=sys.stderr)
        low = msg.lower()
        if "ssl" in low or "certificate" in low or "agent-control.galileo.ai" in low:
            mode = "ssl_url"
        elif "control" in low and ("0" in low or "attach" in low):
            mode = "zero_controls"
        elif "api_key" in low or "api key" in low:
            mode = "missing_keys"
        elif "block" in low or "violation" in low or "protect" in low:
            mode = "protect_block"
        else:
            mode = "generic"
        _print_friction_footer(mode=mode, detail=msg)
        return 1

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
            Keys: stay in .env / env — never commit secrets.
            """
        ).strip()
    )
    print("\n✅ Player demo complete.")
    _print_friction_footer(
        mode="generic",
        detail="Success path — bookmark troubleshooter for the next friction",
    )
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
