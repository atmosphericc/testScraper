#!/usr/bin/env python3
"""Offline test for tools/experiments/shape_replay_probe.py (2026-10-23): --help, header ordering (Chrome wire order,
Shape tokens right after accept, defaults filled), the impersonation picker and the verdict rule. No browser, no
network, no proxy, no cookie values."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import shape_replay_probe as M  # noqa: E402

FAILS: list = []
CHECKS: list = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    CHECKS.append(name)
    if not cond:
        FAILS.append(name)


rc = subprocess.run([sys.executable, str(ROOT / "tools" / "experiments" / "shape_replay_probe.py"), "--help"],
                    capture_output=True, text=True, timeout=120)
check("help_runs", rc.returncode == 0 and "--harvest" in rc.stdout and "--cookie-donor" in rc.stdout, rc.stdout[-300:] + rc.stderr[-300:])

captured = {"Accept": "application/json", "Content-Type": "application/json", "Cookie": "a=1; b=2", "Origin": "https://www.target.com",
            "Referer": "https://www.target.com/p/-/A-81926151", "User-Agent": "Mozilla/5.0 Chrome/136.0.0.0",
            "X-GyJwza5Z-a": "AAA", "X-GyJwza5Z-b": "BBB", "X-GyJwza5Z-c": "CCC", "X-GyJwza5Z-d": "DDD", "X-GyJwza5Z-f": "FFF", "X-GyJwza5Z-z": "q",
            "sec-ch-ua": '"Chromium";v="136"', "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"', "x-application-name": "web"}
o = M.order_headers(captured)
keys = list(o.keys())
check("order_starts_like_chrome", keys[:4] == ["sec-ch-ua", "x-application-name", "sec-ch-ua-mobile", "user-agent"], keys[:6])
check("shape_tokens_follow_accept", keys[keys.index("accept") + 1].startswith("x-gyjwza5z-") and sum(k.startswith("x-gyjwza5z-") for k in keys) == 6, keys)
check("defaults_filled", o.get("sec-fetch-mode") == "cors" and o.get("sec-fetch-site") == "same-site" and "accept-language" in o and "priority" in o, o)
check("cookie_near_end", keys.index("cookie") > keys.index("referer") and o["cookie"] == "a=1; b=2", keys)
check("no_pseudo_headers", not any(k.startswith(":") for k in M.order_headers({":authority": "x", "Accept": "a"})))

imp = M.impersonate_for("Mozilla/5.0 Chrome/136.0.0.0 Safari/537.36")
check("impersonate_matches_major", imp.startswith("chrome") and "136" in imp or imp == "chrome", imp)
check("impersonate_unknown_ua_falls_back", M.impersonate_for("") in ("chrome",) or M.impersonate_for("").startswith("chrome"), M.impersonate_for(""))
check("mask_two_octets", M.mask("73.246.1.2").startswith("73.246.x.x#") and M.mask("x") == "?")

print(f"\n=== {len(CHECKS) - len(FAILS)}/{len(CHECKS)} passed ===")
if FAILS:
    print("  FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
