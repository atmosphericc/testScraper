#!/usr/bin/env python3
"""Validate the 2026-07-08 mid-run self-heal helpers (NO proxy, home IP).

Exercises the exact machinery the sentinel's home-IP credential relogin uses:
  1. initialize() a worker SessionManager.
  2. _relaunch_browser() — tear-down + relaunch cycle (the proxy-toggle uses this).
  3. _run_proven_login() — full sign-out + proven credential login.
  4. ensure_fresh_access_token() — confirm a MEMBER token mints after.

Runs on the HOME IP (proxy_url=None) so there's no forwarder dependency; the
proxy-reattach step in _credential_relogin is the SAME _relaunch_browser call
with proxy_url restored. Ends the account freshly logged in.

Usage: python test_selfheal_mechanism.py   (defaults to alt-1)
"""
import sys, json, asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src.session.session_manager import SessionManager

ACCT = sys.argv[1] if len(sys.argv) > 1 else "alt-1"
cfg = json.load(open(ROOT / "config" / "target_accounts.json", encoding="utf-8"))
acc = next(a for a in cfg["accounts"] if a["account_id"] == ACCT)


async def main() -> int:
    sm = SessionManager(session_path=acc["session_path"],
                        user_data_dir="./" + acc["profile_dir"],
                        proxy_url=None, account_id=ACCT, apply_fingerprint=False)
    print(f"[test] {ACCT}: initialize on HOME IP...")
    if not await sm.initialize():
        print("[FATAL] initialize failed"); return 2
    try:
        # 1. relaunch cycle (the proxy toggle is two of these)
        print("[test] _relaunch_browser() ...")
        r1 = await sm._relaunch_browser()
        print(f"      relaunch -> {r1}")
        if not r1:
            print("[FAIL] relaunch cycle broke"); return 1

        # 2. proven login on current (home-IP) browser
        u, p = acc["username"], acc["password"]
        print("[test] _run_proven_login() (full signout + login) ...")
        logged = await sm._run_proven_login(u, p)
        print(f"      login -> {logged}")

        # 3. member token after login
        st = await sm.get_access_token_status()
        print(f"      token: member={st['member']} ttl={st['ttl_s']/3600:+.2f}h eid={st['eid']}")

        ok = logged and st['member']
        print("\n" + "=" * 56)
        print(f"{'✅ SELF-HEAL MACHINERY WORKS' if ok else '❌ FAILED'} "
              f"(relaunch + proven login + member token)")
        print("=" * 56)
        return 0 if ok else 1
    finally:
        try:
            sm.close_browser_sync()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
