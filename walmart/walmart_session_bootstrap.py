"""
Walmart session bootstrap — one-time Walmart login per session profile.

The resilient stack pools N=16 Chrome sessions, each with its own profile
dir at state/walmart_session_profiles/sN/. Each profile needs an
authenticated Walmart session before the pool can be used productively —
GraphQL stock checks are gentler on logged-in sessions, and checkout
requires login.

Usage:
    # Bootstrap all sessions (one at a time, manual login per session):
    python -m walmart.walmart_session_bootstrap

    # Bootstrap one specific session (resume after interruption):
    python -m walmart.walmart_session_bootstrap --session s3

    # Bootstrap dev pool (just the first N sessions for laptop testing):
    python -m walmart.walmart_session_bootstrap --first 2

Each session launches Chrome through its own proxy + profile dir, waits for
you to manually log in, snapshots cookies, exits. After all sessions are
bootstrapped, `python -m walmart.walmart_stock_resilient` can pool them.

Why per-session login (not profile-clone): each session needs a distinct
fingerprint, distinct Akamai `bm_sz`/`_abck` history, and distinct
PerimeterX `_pxvid`. Cloning one profile across N sessions would create
16 identical fingerprints that PerimeterX clusters into a single bot
account in minutes. Doing N=16 manual logins is tedious but produces
N=16 distinct trust profiles.

(In the future, the bootstrap can be partially automated by feeding
WALMART_EMAIL / WALMART_PASSWORD env vars and using zendriver to drive
the login form — but for now we keep it manual to avoid login-flow
detection that would burn the proxy.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _load_active_proxies() -> list[str]:
    """Load the `proxies` key (active pool) from config/proxyIps.json.
    Reserve / warn / disabled pools are NOT loaded — they get manually
    moved to `proxies` when an active IP burns.
    """
    config_path = Path(__file__).resolve().parent.parent / "config" / "proxyIps.json"
    with open(config_path) as f:
        data = json.load(f)
    proxies = data.get("proxies") or []
    if not proxies:
        raise RuntimeError(f"No active proxies in {config_path} (proxies key)")
    return proxies


def _profile_dir_for(session_id: str) -> Path:
    """state/walmart_session_profiles/sN/ — matches what ResilientChecker
    constructs at runtime so the launched pool finds the bootstrapped
    profiles by id.
    """
    root = Path(__file__).resolve().parent.parent / "state" / "walmart_session_profiles"
    root.mkdir(parents=True, exist_ok=True)
    d = root / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _bootstrap_one(
    session_id: str,
    proxy_url: str,
    local_port: int,
    skip_warmup: bool = False,
) -> bool:
    """Launch Chrome with this session's profile dir + proxy, wait for
    manual login, snapshot cookies, exit. Returns True on success.
    """
    from src.stack.local_forwarder import ForwarderPool

    try:
        import zendriver as uc
    except ImportError:
        print("ERROR: zendriver not installed. Run: pip install zendriver")
        return False

    profile_dir = _profile_dir_for(session_id)
    print()
    print("=" * 60)
    print(f"  Bootstrap {session_id}")
    print(f"  Profile: {profile_dir}")
    print(f"  Proxy:   {proxy_url[:60]}...")
    print("=" * 60)

    # Stand up a local forwarder for this single proxy so Chrome can use
    # 127.0.0.1:<port> as its proxy server (no BD auth headers needed in
    # the Chrome flag).
    forwarder_pool = ForwarderPool()
    upstream = forwarder_pool.add_upstream(proxy_url, local_port)
    await forwarder_pool.start_all()

    try:
        config = uc.Config(
            user_data_dir=str(profile_dir),
            headless=False,
            browser_args=[
                "--window-size=1280,800",
                f"--proxy-server=127.0.0.1:{local_port}",
            ],
            browser_connection_timeout=1.0,
            browser_connection_max_tries=30,
        )
        browser = await uc.start(config)

        # Warmup: build legitimacy before walmart.com so this session's
        # first Walmart hit has a referer chain
        if not skip_warmup:
            print("  Warmup: visiting external sites for trust profile...")
            import random
            warmup_sites = [
                "https://www.google.com",
                "https://www.youtube.com",
                "https://www.amazon.com",
                "https://www.reddit.com",
            ]
            random.shuffle(warmup_sites)
            for url in warmup_sites[:3]:
                try:
                    await browser.get(url)
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                except Exception as e:
                    logger.debug("warmup %s skipped: %s", url, e)

        print("  Navigating to walmart.com — please log in manually.")
        print("  When you see your account name in the top-right, press Enter here.")
        tab = await browser.get("https://www.walmart.com/account/login")

        # Manual login gate
        input("  >>> Press Enter after you have logged in successfully... ")

        # Capture cookies for diagnostic snapshot. The actual session state
        # lives in the profile dir which Chrome already persisted; this
        # snapshot is purely a sanity check.
        try:
            cookies = await browser.cookies.get_all()
            login_cookies = [
                c for c in cookies
                if c.name in {"_pxhd", "_pxvid", "_abck", "bm_sz", "auth-id"}
            ]
            print(f"  Captured {len(login_cookies)} session cookies.")
        except Exception as e:
            print(f"  Cookie snapshot warning: {e}")

        # Snapshot diagnostic info to the profile dir for future debugging
        snapshot_path = profile_dir / "bootstrap_snapshot.json"
        snapshot = {
            "session_id": session_id,
            "proxy_url": proxy_url[:80],
            "pinned_ip": upstream.pinned_ip,
            "local_port": local_port,
            "bootstrapped_at": time.time(),
        }
        snapshot_path.write_text(json.dumps(snapshot, indent=2))
        print(f"  Wrote {snapshot_path}")

        # Clean shutdown
        await browser.stop()
        print(f"  {session_id} bootstrap complete.")
        return True

    except Exception as e:
        logger.exception("bootstrap failed for %s: %s", session_id, e)
        return False
    finally:
        try:
            await forwarder_pool.stop_all()
        except Exception:
            pass


async def main():
    parser = argparse.ArgumentParser(description="Walmart per-session bootstrap")
    parser.add_argument("--session", help="Bootstrap one session by id (e.g. s3)")
    parser.add_argument("--first", type=int, default=0,
                        help="Bootstrap first N sessions only (default: all)")
    parser.add_argument("--base-port", type=int, default=25000,
                        help="Base port for local forwarders (default 25000)")
    parser.add_argument("--skip-warmup", action="store_true",
                        help="Skip external warmup sites (faster but less stealthy)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")

    proxies = _load_active_proxies()
    n = len(proxies) if args.first <= 0 else min(args.first, len(proxies))

    sessions = [(f"s{i+1}", proxies[i], args.base_port + i) for i in range(n)]
    if args.session:
        sessions = [s for s in sessions if s[0] == args.session]
        if not sessions:
            print(f"ERROR: --session {args.session} not in s1..s{n}")
            return

    print(f"Will bootstrap {len(sessions)} session(s).")
    for sid, _, _ in sessions:
        print(f"  - {sid}")
    confirm = input("Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    success = 0
    for sid, proxy, port in sessions:
        ok = await _bootstrap_one(sid, proxy, port, skip_warmup=args.skip_warmup)
        if ok:
            success += 1
        else:
            print(f"  {sid} FAILED — continuing to next.")

    print()
    print(f"Done. {success}/{len(sessions)} sessions bootstrapped.")


if __name__ == "__main__":
    asyncio.run(main())
