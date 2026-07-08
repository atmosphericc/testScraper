#!/usr/bin/env python3
"""CRUX experiment (2026-07-07): does a page load actually MINT a fresh member
accessToken from a valid login-session, or just re-export the stale one?

Runs on the HOME IP (proxy_url=None) so there is NO Bright-Data proxy-auth
confound — this is the same environment relogin_one.py succeeds in. If the
accessToken TTL jumps from ~0 to ~hours after refresh_access_token(), the
keep-fresh fix is proven end-to-end. Reads target.json (primary) by default;
pass a session file + account id to test another.

No purchase. Usage: python test_token_mint_homeip.py [target-3.json alt-1]
"""
import sys, asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session.session_manager import SessionManager

SESSION = sys.argv[1] if len(sys.argv) > 1 else "target.json"
ACCT = sys.argv[2] if len(sys.argv) > 2 else "primary"
PROFILE = {"target.json": "./nodriver-profile",
           "target-2.json": "./nodriver-profile-2",
           "target-3.json": "./nodriver-profile-3"}.get(SESSION, "./nodriver-profile")


async def main() -> int:
    sm = SessionManager(session_path=SESSION, user_data_dir=PROFILE,
                        proxy_url=None, account_id=ACCT, apply_fingerprint=False)
    print(f"[init] launching {ACCT} on HOME IP (profile {PROFILE})...")
    if not await sm.initialize():
        print("[FATAL] initialize() failed")
        return 2
    try:
        tab = await sm.get_page()
        before = await sm.get_access_token_status(tab)
        print(f"\n  BEFORE : present={before['present']} member={before['member']} "
              f"ttl={before['ttl_s']/3600:+.2f}h eid={before['eid']}")

        print("  → calling refresh_access_token(allow_nav=True) ...")
        t0 = asyncio.get_event_loop().time()
        minted = await sm.refresh_access_token(tab, allow_nav=True)
        dt = asyncio.get_event_loop().time() - t0

        after = await sm.get_access_token_status(tab)
        print(f"  AFTER  : present={after['present']} member={after['member']} "
              f"ttl={after['ttl_s']/3600:+.2f}h eid={after['eid']}  (mint took {dt:.1f}s)")

        fresh = bool(after['member']) and after['ttl_s'] > 3000  # >~50min = a real fresh mint
        print("\n" + "=" * 60)
        if minted and fresh:
            print(f"✅ PROVEN: page load MINTED a fresh member token "
                  f"(ttl {before['ttl_s']/3600:+.2f}h → {after['ttl_s']/3600:+.2f}h)")
            print("   keep-fresh will keep this account hot through a drop.")
            rc = 0
        elif after['member'] and after['ttl_s'] > 600:
            print(f"✅ OK: member token present with {after['ttl_s']/60:.0f}m TTL after refresh")
            rc = 0
        else:
            print(f"❌ NOT MINTED: token still member={after['member']} ttl={after['ttl_s']/60:.0f}m")
            print("   → page-load minting assumption is WRONG; keep-fresh needs a different rung.")
            rc = 1
        print("=" * 60)
        return rc
    finally:
        try:
            sm.close_browser_sync()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
