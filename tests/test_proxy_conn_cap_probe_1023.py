#!/usr/bin/env python3
"""Offline test for tools/experiments/proxy_conn_cap_probe.py (2026-10-23): --help, the masked-IP helper and the
one() request helper against a closed local port (no vendor, no credentials, no network beyond loopback)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import proxy_conn_cap_probe as M  # noqa: E402

FAILS: list = []
CHECKS: list = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    CHECKS.append(name)
    if not cond:
        FAILS.append(name)


rc = subprocess.run([sys.executable, str(ROOT / "tools" / "experiments" / "proxy_conn_cap_probe.py"), "--help"],
                    capture_output=True, text=True, timeout=120)
check("help_runs", rc.returncode == 0 and "--via" in rc.stdout and "forwarder" in rc.stdout, rc.stdout[-300:] + rc.stderr[-300:])
check("mask_two_octets_and_tag", M.mask("73.246.10.20").startswith("73.246.x.x#") and len(M.mask("73.246.10.20")) == len("73.246.x.x#abcd")
      and M.mask("not-an-ip") == "?" and M.mask("") == "?", M.mask("73.246.10.20"))
# a closed loopback port as the proxy: the helper must return an ERR_ string with the reason, never raise
res, ms = M.one("http://127.0.0.1:9", timeout=3.0)
check("one_closed_port_errs_cleanly", res.startswith("ERR_") and "(" in res and ms >= 0, (res, ms))

print(f"\n=== {len(CHECKS) - len(FAILS)}/{len(CHECKS)} passed ===")
if FAILS:
    print("  FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
