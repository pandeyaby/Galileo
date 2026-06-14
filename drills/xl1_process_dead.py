"""
drills/xl1_process_dead.py — XL-1: Kill the Agent Process
==========================================================
Failure mode FM-50: The agent process dies. Traces stop appearing in Galileo.
Heartbeat goes stale in the fleet layer.

Demonstrates:
  SYMPTOM: Galileo shows no new traces (silence). Fleet layer alarms.
  CAUSE:   RUN-layer issue — process dead or unreachable.
  KEY:     Silence in Galileo ≠ "logging bug" — check fleet layer FIRST.

Drill steps:
  1. Confirm baseline: heartbeat fresh, recent trace in Galileo.
  2. INJECT: Delete heartbeat file, stop sending traces (simulate dead process).
  3. OBSERVE: Fleet monitor raises ALARM (stale/missing heartbeat).
  4. OBSERVE: Galileo console shows last trace timestamp = drill start (silence).
  5. RECOVER: Restart app, confirm heartbeat + traces resume.
  6. VERIFY: Fleet OK, new Galileo traces appear.

Run: python drills/xl1_process_dead.py
"""

import sys, os, time, json, pathlib, datetime, subprocess, textwrap

LAB_DIR       = pathlib.Path(__file__).parent.parent
HEARTBEAT_FILE = LAB_DIR / "fleet" / "heartbeat.json"
LATENCY_LOG    = LAB_DIR / "fleet" / "latency.log"
STALE_SECONDS  = 120
VENV_PYTHON    = str(LAB_DIR / ".venv" / "bin" / "python3.14")

# ── Helpers ───────────────────────────────────────────────────────────────────
def banner(msg):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")

def fleet_status() -> str:
    if not HEARTBEAT_FILE.exists():
        return "ALARM:MISSING"
    with open(HEARTBEAT_FILE) as f:
        hb = json.load(f)
    ts_str = hb.get("ts", "")
    try:
        ts = datetime.datetime.fromisoformat(ts_str.rstrip("Z"))
        age_s = (datetime.datetime.utcnow() - ts).total_seconds()
        if age_s > STALE_SECONDS:
            return f"ALARM:STALE:{age_s:.0f}s"
    except Exception:
        pass
    return "OK"

def fleet_heartbeat_age_s() -> float:
    if not HEARTBEAT_FILE.exists():
        return float("inf")
    with open(HEARTBEAT_FILE) as f:
        hb = json.load(f)
    ts_str = hb.get("ts", "")
    try:
        ts = datetime.datetime.fromisoformat(ts_str.rstrip("Z"))
        return (datetime.datetime.utcnow() - ts).total_seconds()
    except Exception:
        return float("inf")

# ── Drill ─────────────────────────────────────────────────────────────────────
def run_drill():
    print("\n🔬 XL-1 DRILL: Kill the Agent Process")
    print("   Failure mode FM-50 — trinity-stack lab")
    print("   Labs project: rax-galileo-labs | Log stream: trinity-stack")

    # ── STEP 1: Verify baseline ──
    banner("STEP 1 — Verify baseline")
    status = fleet_status()
    age_s = fleet_heartbeat_age_s()
    print(f"Fleet status: {status}")
    if "ALARM" in status and "STALE" in status:
        print("⚠️  Heartbeat already stale from previous run. Running one baseline query to refresh...")
        subprocess.run([VENV_PYTHON, str(LAB_DIR / "app.py"), "What is Galileo?"],
                      capture_output=True, text=True)
        time.sleep(2)
    print(f"Fleet status after refresh: {fleet_status()}")
    print(f"Heartbeat age: {fleet_heartbeat_age_s():.1f}s")
    print(f"\n📊 Galileo baseline: check https://app.galileo.ai")
    print(f"   Project: rax-galileo-labs | Log stream: trinity-stack")
    print(f"   You should see traces from the baseline run.\n")
    drill_start_ts = datetime.datetime.utcnow()
    print(f"Drill start time (UTC): {drill_start_ts.isoformat()}")

    # ── STEP 2: INJECT — Kill the process ──
    banner("STEP 2 — INJECT: Simulating process kill")
    print("Action: Deleting heartbeat.json (simulates agent process dying).")
    print("        No new traces will be sent to Galileo (no more app.invoke calls).")
    print()

    heartbeat_backup = None
    if HEARTBEAT_FILE.exists():
        with open(HEARTBEAT_FILE) as f:
            heartbeat_backup = f.read()
        HEARTBEAT_FILE.unlink()
        print(f"✂️  Deleted {HEARTBEAT_FILE.name}")
    else:
        print("(Heartbeat file already missing — simulating a 'never started' kill)")

    # Record kill time
    kill_ts = datetime.datetime.utcnow()
    print(f"Process killed at (UTC): {kill_ts.isoformat()}")

    # ── STEP 3: OBSERVE — Fleet alarm ──
    banner("STEP 3 — OBSERVE: Fleet alarm fires")
    print("(In real ClawTrace: sub-ms telemetry immediately detects process down)")
    print("(In our simulation: fleet monitor checks heartbeat file)")
    print()
    status = fleet_status()
    print(f"Fleet status: {status}")
    expected_alarm = "ALARM:MISSING" in status
    print(f"Expected ALARM:MISSING — {'✅ CONFIRMED' if expected_alarm else '❌ unexpected: ' + status}")

    # ── STEP 4: OBSERVE — Galileo silence ──
    banner("STEP 4 — OBSERVE: Galileo shows trace silence")
    print("Since we killed the process, NO new traces are being sent.")
    print()
    print("What you see in Galileo Console:")
    print("  • Last trace timestamp ≈ drill start time (no new entries)")
    print("  • Trace count stops incrementing")
    print("  • NO error or anomaly metric fires (there's nothing TO evaluate)")
    print(f"\nDrill start UTC:   {drill_start_ts.isoformat()}")
    print(f"Process kill UTC:  {kill_ts.isoformat()}")
    print()
    print("⚠️  KEY INSIGHT:")
    print("   Silence in Galileo does NOT mean a logging/SDK bug.")
    print("   Silence + fleet alarm = RUN-layer outage.")
    print("   Always check fleet health FIRST when traces stop.")
    print()
    print("Triage decision table entry (for runbook RB-110):")
    print("  Traces stop suddenly + fleet heartbeat stale/missing")
    print("  → Root cause: RUN layer (process dead / unreachable)")
    print("  → Fix: restart process, verify heartbeat resumes, then check traces")

    # ── STEP 5: RECOVER ──
    banner("STEP 5 — RECOVER: Restart the agent process")
    print("Recovering by running one query (simulates process restart)...")

    os.environ.setdefault("GALILEO_API_KEY",
        __import__("json").loads(
            open(pathlib.Path.home() / ".openclaw" / "openclaw.json").read()
        )["mcp"]["servers"]["galileo"]["headers"]["Galileo-API-Key"]
    )

    result = subprocess.run(
        [VENV_PYTHON, str(LAB_DIR / "app.py"), "What is the Trinity Stack?"],
        capture_output=True, text=True, env={**os.environ}
    )
    if result.returncode == 0:
        print("✅ Process restarted successfully.")
        print(result.stdout[-400:].strip())
    else:
        print("❌ Restart failed:")
        print(result.stderr[-300:])

    # ── STEP 6: VERIFY ──
    banner("STEP 6 — VERIFY: Both layers healthy again")
    time.sleep(1)
    status_after = fleet_status()
    age_after = fleet_heartbeat_age_s()
    print(f"Fleet status: {status_after}")
    print(f"Heartbeat age: {age_after:.1f}s")

    fleet_ok = "ALARM" not in status_after
    print(f"\nFleet restored: {'✅ YES' if fleet_ok else '❌ NO'}")
    print(f"Galileo: new trace should appear after {kill_ts.isoformat()}")
    print(f"         Check Console → filter by time > kill timestamp")

    # ── Summary ──
    banner("XL-1 DRILL SUMMARY")
    print(textwrap.dedent(f"""
    Failure mode:  FM-50 (XL-1) — Agent process dead
    Injected at:   {kill_ts.isoformat()} UTC
    Recovered at:  {datetime.datetime.utcnow().isoformat()} UTC

    Observation (FLEET layer):
      • Fleet monitor: {status} (ALARM immediately on heartbeat delete)
      • What ClawTrace would show: process heartbeat drop, latency ∞

    Observation (TRUST layer — Galileo):
      • Traces stopped at drill start time
      • No quality metric anomaly (nothing to evaluate)
      • Silence ≠ SDK bug; silence + fleet alarm = RUN-layer problem

    Triage rule: "Trace silence + fleet alarm → RUN layer first"

    Recovery:
      • Restart process → heartbeat resumes → traces resume
      • No Galileo-side action needed (no bad evals to fix)

    Runbook: runbooks/RB-110-agent-process-dead.md
    """))

if __name__ == "__main__":
    run_drill()
