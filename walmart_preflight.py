#!/usr/bin/env python3
"""
walmart_preflight.py — "Am I ready for the drop?" in plain language.

You are new to this and can't easily judge whether the bot is drop-ready.
This script checks every prerequisite and prints GO / WARN / STOP for each,
then a one-line verdict. Read-only — it changes nothing, so run it as often
as you like (T-90, T-60, T-15).

    python walmart_preflight.py

It does NOT test the live checkout (that needs a real drop). It confirms the
things that must be true BEFORE you launch: credentials, a fresh login, seeded
sessions, proxies, and which SKUs are armed. A STOP means "fix this or the bot
cannot work"; a WARN means "this will probably hurt but isn't fatal".

Exit code: 0 if no STOPs, 1 if any STOP (so a launcher can gate on it).
"""

import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── tiny status vocabulary ───────────────────────────────────────────────
GO, WARN, STOP = "GO  ", "WARN", "STOP"
_ICON = {GO: "✅", WARN: "🟡", STOP: "🔴"}
_results = []


def check(status: str, title: str, detail: str = "", fix: str = ""):
    _results.append((status, title, detail, fix))


def _load_env_file() -> dict:
    """Read .env (the app loads it too). Returns {KEY: value}."""
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # real environment overrides the file
    for k in ("WALMART_EMAIL", "WALMART_PASSWORD", "WALMART_CVV"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


# ── the checks ───────────────────────────────────────────────────────────

def check_credentials():
    env = _load_env_file()
    missing = [k for k in ("WALMART_EMAIL", "WALMART_PASSWORD")
               if not env.get(k)]
    if missing:
        check(STOP, "Account credentials",
              f"{', '.join(missing)} not set in .env",
              "Put a real Walmart account's email + password in .env. The bot "
              "cannot log in or buy without them.")
    else:
        check(GO, "Account credentials",
              f"email={env['WALMART_EMAIL'][:3]}***  password set")

    if not env.get("WALMART_CVV"):
        check(WARN, "Card CVV",
              "WALMART_CVV not set",
              "Set WALMART_CVV in .env. Without it the bot skips CVV entry and "
              "checkout fails at payment. (Card itself lives in your Walmart "
              "account/profile; CVV is entered per-order.)")
    else:
        check(GO, "Card CVV", "WALMART_CVV set")


def check_master_login():
    cookies = ROOT / "walmart-profile-login" / "Default" / "Cookies"
    if not cookies.exists():
        check(STOP, "Master Walmart login",
              "no walmart-profile-login/Default/Cookies — never logged in",
              "Run: python walmart_relogin.py  (log in by hand once).")
        return
    age_days = (time.time() - cookies.stat().st_mtime) / 86400
    if age_days > 5:
        check(STOP, "Master Walmart login",
              f"login profile is {age_days:.0f} days old (stale)",
              "Re-run: python walmart_relogin.py  — sessions older than ~5 days "
              "usually fail auth on drop night. The playbook says log in T-90.")
    else:
        check(GO, "Master Walmart login", f"fresh ({age_days:.1f} days old)")


def check_seeded_sessions():
    root = ROOT / "state" / "walmart_session_profiles"
    seeded = sorted(d.name for d in root.glob("s*")) if root.exists() else []
    if not seeded:
        check(STOP, "Seeded session profiles",
              "none in state/walmart_session_profiles/",
              "Run: python -m walmart.walmart_session_bootstrap  (after login).")
    else:
        check(GO, "Seeded session profiles",
              f"{len(seeded)} seeded: {', '.join(seeded)}")


def check_proxies():
    p = ROOT / "config" / "proxyIps.json"
    if not p.exists():
        check(STOP, "Proxy pool", "config/proxyIps.json missing",
              "Restore the proxy config.")
        return
    try:
        d = json.loads(p.read_text())
    except ValueError:
        check(STOP, "Proxy pool", "proxyIps.json is not valid JSON", "Fix the file.")
        return
    active = d.get("proxies") or []
    reserve = d.get("reserve_proxies") or []
    n = len(active)
    if n == 0:
        check(STOP, "Proxy pool", "0 active proxies",
              "Populate the 'proxies' list in config/proxyIps.json.")
    elif n < 3:
        check(WARN, "Proxy pool",
              f"only {n} active proxies (+{len(reserve)} reserve)",
              "Few proxies = fewer sessions racing the queue. Works, but weaker.")
    else:
        check(GO, "Proxy pool", f"{n} active (+{len(reserve)} reserve)")
    # Zone-label heads-up: the pool is labelled for Target.
    sample = active[0] if active else ""
    if "target" in sample.lower():
        check(WARN, "Proxy provenance",
              "proxies are labelled for Target (zone '...target...')",
              "These are Target's IPs reused for Walmart. They may be fine, but "
              "if you see lots of '456 Access Denied' on drop night, the proxy "
              "class is likely burned for Walmart — swap in fresh residential/ISP.")


def check_skus():
    cfg = ROOT / "walmart" / "walmart_config.json"
    if not cfg.exists():
        check(STOP, "Armed SKUs", "walmart/walmart_config.json missing", "Restore it.")
        return
    try:
        products = json.loads(cfg.read_text()).get("products", [])
    except ValueError:
        check(STOP, "Armed SKUs", "walmart_config.json is not valid JSON", "Fix it.")
        return
    enabled = [p for p in products if p.get("enabled")]
    real = [p for p in enabled
            if "throwaway" not in (p.get("name", "").lower())
            and p.get("item_id") != "320424995"]
    if not enabled:
        check(STOP, "Armed SKUs", "no products enabled",
              "Set enabled:true on the drop's item_id in walmart_config.json.")
    elif not real:
        names = ", ".join(p.get("item_id", "?") for p in enabled)
        check(WARN, "Armed SKUs",
              f"only a throwaway/test item is enabled ({names})",
              "Fine for a pure capture/monitoring dry-run. But to capture the "
              "REAL queue + checkout you must arm the actual drop SKU: get this "
              "Wednesday's Pokémon item_id (from the product URL) and set "
              "enabled:true on it in walmart/walmart_config.json.")
    else:
        ids = ", ".join(p.get("item_id", "?") for p in real)
        check(GO, "Armed SKUs", f"{len(real)} real SKU(s) armed: {ids}")


def check_capture_ready():
    # These aren't set yet at preflight time (the launcher sets them), so this
    # is informational — just confirms the capture code is importable.
    try:
        import walmart.queue_events  # noqa: F401
        chk = (ROOT / "walmart" / "checkout_capture.py").exists()
        check(GO, "Capture tooling",
              "queue_events + " + ("checkout_capture present" if chk else "checkout_capture MISSING"))
    except Exception as e:
        check(WARN, "Capture tooling", f"queue_events import issue: {e}")


def check_known_gates():
    # Surfaced as WARNs because they're real-world blockers a new operator
    # won't know about — not things this script can detect from disk.
    check(WARN, "SMS verification (external)",
          "cannot be checked from here",
          "Walmart increasingly REQUIRES an SMS-verified account on drops. If "
          "your account has no phone verified, the bot will hit 'SMS Required' "
          "and stop. Verify the account's phone before Wednesday.")
    check(WARN, "Walmart+ early access (external)",
          "cannot be checked from here",
          "Pokémon drops often give Walmart+ members early/priority access; "
          "non-members can be locked out or see 400s. A Walmart+ membership on "
          "the account materially improves your odds.")


def main() -> int:
    print("\n" + "=" * 72)
    print("  WALMART DROP PRE-FLIGHT  —  am I ready?")
    print("=" * 72)

    check_credentials()
    check_master_login()
    check_seeded_sessions()
    check_proxies()
    check_skus()
    check_capture_ready()
    check_known_gates()

    # Print grouped, STOPs first so the eye lands on blockers.
    order = {STOP: 0, WARN: 1, GO: 2}
    for status, title, detail, fix in sorted(_results, key=lambda r: order[r[0]]):
        print(f"\n{_ICON[status]} {status}  {title}")
        if detail:
            print(f"        {detail}")
        if fix and status != GO:
            for i, line in enumerate(_wrap(fix, 62)):
                print(f"        {'→ ' if i == 0 else '  '}{line}")

    stops = [r for r in _results if r[0] == STOP]
    warns = [r for r in _results if r[0] == WARN]
    print("\n" + "=" * 72)
    if stops:
        print(f"  VERDICT: 🔴 NOT READY — {len(stops)} blocker(s) must be fixed "
              f"before launch.")
        print("  Fix the STOP items above, then re-run this script.")
    elif warns:
        print(f"  VERDICT: 🟡 LAUNCHABLE with {len(warns)} caveat(s).")
        print("  No hard blockers. Read the WARNs — some (SMS, Walmart+, proxy")
        print("  provenance) strongly affect whether you actually get anything.")
    else:
        print("  VERDICT: ✅ READY — all prerequisites met.")
    print("=" * 72 + "\n")
    return 1 if stops else 0


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    sys.exit(main())
