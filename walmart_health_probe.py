"""
walmart_health_probe.py — one-shot liveness probe for the Walmart bot process,
used by run_walmart_bot_with_nightly_restart.bat.

WHY THIS IS LOG-BASED, NOT /health-BASED
-----------------------------------------
walmart_app.py does NOT exit when its monitor fails to start — it logs the
error and keeps serving the Flask dashboard on :5001 forever with no
purchasing (a "dashboard-only zombie"). The obvious probe — read /health's
`running` field — DOES NOT WORK for that case: the manager sets `_running=True`
at startup (purchase_manager.py:136) and never clears it when the SEPARATE
resilient-checker start fails afterward (walmart_app.py:182-184). So /health
reports `running: true` in exactly the dead state we need to catch.

The authoritative boot signal is in the app's own log file
(logs/walmart_app_<ts>.log). Each launch writes a fresh timestamped file and
prints exactly one of:
    GREEN:  "[APP] Resilient stock checker started"
    DEAD:   "[APP] Resilient checker start() failed"
            "[APP] Manager start() failed"
            "[MANAGER] NOT LOGGED IN"
This probe reads the NEWEST such log and decides from those lines, then uses
/health only as a reachability check to tell "still booting" from "crashed".

Exit codes (consumed by the .bat):
    0  → healthy: the current boot reached "Resilient stock checker started"
    10 → DEAD: the current boot logged a terminal failure (zombie / not-logged-in)
    20 → indeterminate: no verdict line yet AND app not reachable
         (still booting, or already crashed — caller re-probes / waits)

Only stdlib — runs on the bare venv Python.
"""

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

HEALTH_URL = "http://127.0.0.1:5001/health"
TIMEOUT_S = 4.0
LOG_GLOB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "walmart_app_*.log")

GREEN_LINE = "Resilient stock checker started"
DEAD_LINES = (
    "Resilient checker start() failed",
    "Manager start() failed",
    "NOT LOGGED IN",
)

# Only trust a log file from THIS run, not a stale one from a previous night.
# The wrapper waits ~60s post-launch before the first probe, so the live log is
# always the newest and fresh; anything older than this is ignored.
MAX_LOG_AGE_S = 6 * 60 * 60


def _newest_log() -> str | None:
    logs = glob.glob(LOG_GLOB)
    if not logs:
        return None
    newest = max(logs, key=os.path.getmtime)
    if time.time() - os.path.getmtime(newest) > MAX_LOG_AGE_S:
        return None
    return newest


def _reachable() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=TIMEOUT_S) as resp:
            json.loads(resp.read().decode("utf-8", "replace"))
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> int:
    log = _newest_log()
    verdict = None  # None = no boot-outcome line seen yet
    if log:
        try:
            with open(log, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            # Scan bottom-up: the LAST outcome line wins (a self-heal relaunch
            # could log a failure then a later success within one process).
            for line in reversed(text.splitlines()):
                if GREEN_LINE in line:
                    verdict = "green"
                    break
                if any(d in line for d in DEAD_LINES):
                    verdict = "dead"
                    break
        except OSError:
            pass

    if verdict == "green":
        # Boot reached the monitor. Confirm the process is still up — a green
        # log line from a process that has since crashed should read as down.
        if _reachable():
            print("OK: resilient checker started (log) + reachable")
            return 0
        print("DEAD: was green but process no longer reachable on :5001")
        return 10

    if verdict == "dead":
        print("DEAD: boot logged a terminal failure (zombie / not-logged-in)")
        return 10

    # No outcome line yet.
    if _reachable():
        print("INDETERMINATE: server up, monitor not confirmed started yet")
        return 20
    print("INDETERMINATE: no verdict line and not reachable (booting or crashed)")
    return 20


if __name__ == "__main__":
    sys.exit(main())
