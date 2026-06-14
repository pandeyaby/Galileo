"""
fleet/monitor.py — Simulated RUN layer (ClawTrace equivalent)

Reads the heartbeat.json file written by app.py and reports fleet health.
In a real ClawTrace deployment this would be replaced by the ClawTrace SDK.
The separation demonstrates: ClawTrace sees process liveness/latency;
Galileo sees output quality. Neither replaces the other.

Usage:
  python fleet/monitor.py          # check current status
  python fleet/monitor.py --watch  # poll every 5s
"""

import json, time, sys, pathlib, datetime

HEARTBEAT_FILE = pathlib.Path(__file__).parent / "heartbeat.json"
LATENCY_LOG    = pathlib.Path(__file__).parent / "latency.log"
STALE_SECONDS  = 120  # > 2 min since last heartbeat = alarm

def read_heartbeat() -> dict:
    if not HEARTBEAT_FILE.exists():
        return {"status": "MISSING", "ts": None, "latency_ms": None}
    try:
        with open(HEARTBEAT_FILE) as f:
            return json.load(f)
    except Exception as e:
        return {"status": "PARSE_ERROR", "error": str(e)}

def check_status() -> tuple[str, dict]:
    hb = read_heartbeat()
    ts_str = hb.get("ts")

    if hb.get("status") == "MISSING":
        return "ALARM", {"reason": "No heartbeat file — agent process never ran or was killed.", "hb": hb}

    if ts_str:
        try:
            ts = datetime.datetime.fromisoformat(ts_str.rstrip("Z"))
            age_s = (datetime.datetime.utcnow() - ts).total_seconds()
            if age_s > STALE_SECONDS:
                return "ALARM", {"reason": f"Heartbeat STALE — {age_s:.0f}s ago (threshold: {STALE_SECONDS}s)", "hb": hb}
        except Exception:
            pass

    if hb.get("status") == "error":
        return "WARN", {"reason": "Last run reported an error.", "hb": hb}

    return "OK", {"reason": "Heartbeat fresh and healthy.", "hb": hb}

def print_status():
    level, detail = check_status()
    hb = detail["hb"]
    icons = {"OK": "✅", "WARN": "⚠️", "ALARM": "🚨"}
    icon = icons.get(level, "❓")
    print(f"\n{icon} Fleet status: {level}")
    print(f"   Reason:  {detail['reason']}")
    print(f"   Last HB: {hb.get('ts', 'N/A')}")
    print(f"   Latency: {hb.get('latency_ms', 'N/A')} ms")
    print(f"   Process: {hb.get('process', 'N/A')}")
    if level == "ALARM":
        print(f"\n   👉 Triage: Is this a RUN-layer outage (process dead) or a TRUST-layer issue (bad output)?")
        print(f"   👉 Check Galileo Console: if traces stopped → RUN-layer. If traces continue with bad metrics → TRUST-layer.")

if __name__ == "__main__":
    if "--watch" in sys.argv:
        print("Watching fleet (Ctrl-C to stop)...")
        try:
            while True:
                print_status()
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        print_status()
