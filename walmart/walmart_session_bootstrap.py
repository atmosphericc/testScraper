"""
Walmart session bootstrap — seed N session profiles from one master login.

Rewritten 2026-05-16 to use the "Saved Session" pattern documented by
Refract, Stellar, and Kodai (and validated by your own walmart_relogin.py
+ production bot pipeline):

  1. You manually log in ONCE on your home IP via walmart_relogin.py
     → produces an authenticated Chrome profile at walmart-profile-login/
  2. This bootstrap COPIES that authenticated profile into each session's
     profile dir at state/walmart_session_profiles/sN/
  3. (Optional) This bootstrap then LAUNCHES each session briefly through
     its assigned proxy and verifies the session is still recognized
     (auth cookie valid, no /blocked redirect, /account returns logged-in)
  4. The resilient stack pool starts these pre-seeded sessions at runtime

Why this beats per-session manual login:
  - 1 login instead of N (saves you 30+ min on N=16)
  - Login happens on your home IP (high trust) — same as walmart_relogin.py
  - Each session retains its own profile dir → diverges over time as it
    accumulates its own behavioral history via its assigned proxy
  - Matches what Refract calls "Saved Session" mode
  - Matches what Target's resilient stack does today (one login, N seeded
    session profiles)

Why the verify step matters:
  - Even with a valid auth cookie, a specific BD proxy IP might be
    PerimeterX-flagged. The verify step catches that BEFORE the soak
    run so you can swap proxies up front, not mid-soak.

Usage:
    # Step 0 — manually log in (only needed once, or when session expires):
    python walmart_relogin.py

    # Step 1 — seed all sessions from that login + verify through proxies:
    python -m walmart.walmart_session_bootstrap

    # Just seed the first N (dev gate on laptop):
    python -m walmart.walmart_session_bootstrap --first 2

    # Skip verification (faster but you won't know if a proxy is bad):
    python -m walmart.walmart_session_bootstrap --first 2 --skip-verify

    # Reseed an existing session (e.g. after auth cookie expired):
    python -m walmart.walmart_session_bootstrap --session s3 --force
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── paths ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
MASTER_PROFILE_DIR = ROOT / "walmart-profile-login"
MASTER_PROFILE_DEFAULT = MASTER_PROFILE_DIR / "Default"
SESSIONS_ROOT = ROOT / "state" / "walmart_session_profiles"
PROXY_CONFIG = ROOT / "config" / "proxyIps.json"


# Files inside Default/ we DO NOT want to copy — Chrome regenerates them
# and copying them either fails or causes "profile in use" errors when
# the new Chrome instance starts.
_SKIP_PATTERNS = {
    "Singleton",          # SingletonLock, SingletonCookie, SingletonSocket
    "LOCK",
    "lockfile",
    "Crashpad",           # Crash reporting state — irrelevant + huge
    "GPUCache",           # Per-Chrome-instance cache
    "Code Cache",         # Same
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "DawnGraphiteCache",
    "optimization_guide_model_store",  # ~100MB of ML models we don't need
    "Cache",              # Generic Cache dir — saves a lot of GB
    "blob_storage",
    "BudgetDatabase",     # Recreated
    "Service Worker",     # Recreated
}


def _should_skip(name: str) -> bool:
    """True if this filename/dirname should be skipped during profile copy."""
    if name.startswith("Singleton"):
        return True
    for pattern in _SKIP_PATTERNS:
        if pattern in name:
            return True
    return False


def _load_active_proxies() -> list[str]:
    """Active pool only — `proxies` key in config/proxyIps.json."""
    with open(PROXY_CONFIG) as f:
        data = json.load(f)
    proxies = data.get("proxies") or []
    if not proxies:
        raise RuntimeError(f"No active proxies in {PROXY_CONFIG}")
    return proxies


def _session_profile_dir(session_id: str) -> Path:
    """state/walmart_session_profiles/sN/ — matches what ResilientChecker
    constructs at runtime so launched pool finds these.
    """
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    d = SESSIONS_ROOT / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── seed step ────────────────────────────────────────────────────────────

def _seed_one_profile(session_id: str, force: bool = False) -> bool:
    """Copy walmart-profile-login/Default → state/walmart_session_profiles/sN/Default.

    Returns True on success. Skips Chrome-managed files (locks, caches)
    that would either fail to copy or cause "profile in use" errors.

    If the destination already has a Default/ AND --force isn't set,
    leaves it alone (assumes prior seed is still valid).
    """
    if not MASTER_PROFILE_DEFAULT.exists():
        logger.error(
            "Master profile not found at %s. Run `python walmart_relogin.py` first.",
            MASTER_PROFILE_DEFAULT,
        )
        return False

    dst = _session_profile_dir(session_id)
    dst_default = dst / "Default"

    if dst_default.exists() and not force:
        # Check if already-seeded marker exists
        marker = dst / "bootstrap_snapshot.json"
        if marker.exists():
            logger.info("[%s] already seeded (use --force to reseed)", session_id)
            return True
        # Default exists but no marker — was the seed interrupted?
        logger.info("[%s] partial seed detected, cleaning up", session_id)
        shutil.rmtree(dst_default)

    if dst_default.exists() and force:
        logger.info("[%s] --force: removing existing Default/", session_id)
        shutil.rmtree(dst_default)

    dst_default.mkdir(parents=True, exist_ok=True)

    # Walk the master Default/, copying each file that doesn't match a skip pattern.
    src_root = MASTER_PROFILE_DEFAULT
    n_files = 0
    n_skipped = 0
    n_bytes = 0
    for src_path in src_root.rglob("*"):
        rel = src_path.relative_to(src_root)
        if any(_should_skip(part) for part in rel.parts):
            n_skipped += 1
            continue
        dst_path = dst_default / rel
        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
            continue
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            n_files += 1
            n_bytes += src_path.stat().st_size
        except (OSError, shutil.SameFileError) as e:
            logger.debug("[%s] skip %s: %s", session_id, rel, e)
            n_skipped += 1

    # Also copy Local State (lives alongside Default/, not inside it) —
    # contains profile-wide preferences, encryption keys, etc.
    local_state_src = MASTER_PROFILE_DIR / "Local State"
    if local_state_src.exists():
        try:
            shutil.copy2(local_state_src, dst / "Local State")
        except OSError as e:
            logger.debug("[%s] Local State copy skipped: %s", session_id, e)

    logger.info(
        "[%s] seeded: %d files (%.1f MB), %d skipped (cache/lock)",
        session_id, n_files, n_bytes / 1024 / 1024, n_skipped,
    )
    return True


def _write_snapshot(session_id: str, proxy_url: str, pinned_ip: str,
                    local_port: int, verified: bool, verify_detail: str = ""):
    """Mark the session as bootstrapped (used by --force to know what's
    already done, and useful for debugging session origin).
    """
    snapshot_path = _session_profile_dir(session_id) / "bootstrap_snapshot.json"
    snapshot = {
        "session_id": session_id,
        "proxy_url": proxy_url[:80],
        "pinned_ip": pinned_ip,
        "local_port": local_port,
        "verified": verified,
        "verify_detail": verify_detail,
        "bootstrapped_at": time.time(),
        "bootstrap_method": "profile_copy_v2_2026_05_16",
    }
    snapshot_path.write_text(json.dumps(snapshot, indent=2))


# ── verify step ──────────────────────────────────────────────────────────

async def _verify_one_session(
    session_id: str,
    proxy_url: str,
    local_port: int,
    timeout_s: float = 30.0,
) -> tuple[bool, str]:
    """Launch Chrome through the assigned proxy with this session's seeded
    profile. Check that walmart.com/account shows logged-in HTML and no
    /blocked redirect.

    Returns (success, detail). Detail is a one-line reason on failure.
    """
    from src.stack.local_forwarder import ForwarderPool

    try:
        import zendriver as uc
    except ImportError:
        return False, "zendriver not installed"

    forwarder = ForwarderPool()
    upstream = forwarder.add_upstream(proxy_url, local_port)
    await forwarder.start_all()
    pinned_ip = upstream.pinned_ip

    profile_dir = _session_profile_dir(session_id)

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

        try:
            tab = await asyncio.wait_for(
                browser.get("https://www.walmart.com/account"),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return False, "navigation timeout"

        # Give the page a moment to settle and resolve any redirects
        await asyncio.sleep(3)

        # Check final URL — /blocked means PX flagged this proxy
        try:
            current_url = await tab.evaluate(
                "window.location.href", await_promise=False
            )
        except Exception as e:
            return False, f"could not read URL: {type(e).__name__}"

        if isinstance(current_url, str) and "/blocked" in current_url:
            return False, f"blocked redirect to {current_url[:80]}"

        # Check HTML for logged-in signals
        try:
            content = await asyncio.wait_for(
                tab.get_content(),
                timeout=15.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            return False, f"could not read content: {type(e).__name__}"

        # Logged-in indicators (match what walmart_relogin.py checks for)
        logged_in_signals = [
            '"isLoggedIn":true',
            'account/logout',
            '"type":"REGISTERED"',
        ]
        if not any(sig in content for sig in logged_in_signals):
            # Could be PX challenge page that didn't /blocked-redirect us
            if "robot or human" in content.lower():
                return False, "PerimeterX challenge page"
            if "px-captcha" in content.lower():
                return False, "PerimeterX captcha"
            if "sign in" in content.lower() and "create" in content.lower():
                return False, "session not recognized — login form shown"
            return False, "no logged-in signals in /account HTML"

        return True, f"verified via /account on IP {pinned_ip}"

    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:100]}"
    finally:
        try:
            await browser.stop()
        except Exception:
            pass
        try:
            await forwarder.stop_all()
        except Exception:
            pass


# ── main ─────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Walmart session bootstrap (profile-copy + verify)",
    )
    parser.add_argument("--session", help="Bootstrap one session by id (e.g. s3)")
    parser.add_argument("--first", type=int, default=0,
                        help="Bootstrap first N sessions only (default: all)")
    parser.add_argument("--base-port", type=int, default=25000,
                        help="Base port for verify-step forwarders (default 25000)")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip the per-session verify step (faster, riskier)")
    parser.add_argument("--force", action="store_true",
                        help="Reseed existing sessions even if already bootstrapped")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")

    if not MASTER_PROFILE_DEFAULT.exists():
        print("ERROR: No master Walmart login profile found at:")
        print(f"   {MASTER_PROFILE_DEFAULT}")
        print()
        print("Run this first to create one:")
        print("   python walmart_relogin.py")
        sys.exit(1)

    proxies = _load_active_proxies()
    n = len(proxies) if args.first <= 0 else min(args.first, len(proxies))
    sessions = [(f"s{i+1}", proxies[i], args.base_port + i) for i in range(n)]

    if args.session:
        sessions = [s for s in sessions if s[0] == args.session]
        if not sessions:
            print(f"ERROR: --session {args.session} not in s1..s{n}")
            sys.exit(1)

    print()
    print("=" * 65)
    print("  Walmart Session Bootstrap (profile-copy + verify)")
    print("=" * 65)
    print(f"  Master profile: {MASTER_PROFILE_DEFAULT}")
    print(f"  Sessions to bootstrap: {len(sessions)}")
    print(f"  Verify step: {'OFF' if args.skip_verify else 'ON'}")
    print(f"  Force reseed: {'ON' if args.force else 'OFF'}")
    print()
    for sid, prox, port in sessions:
        ip = prox.split('-ip-')[1].split(':')[0] if '-ip-' in prox else '?'
        print(f"    {sid}  IP={ip:<18}  port={port}")
    print()
    confirm = input("Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    print()
    seed_ok = 0
    seed_fail = 0
    verify_ok = 0
    verify_fail = 0

    for sid, proxy_url, port in sessions:
        print(f"\n[{sid}] seeding from master profile...")
        ok = _seed_one_profile(sid, force=args.force)
        if not ok:
            seed_fail += 1
            _write_snapshot(sid, proxy_url, "?", port, False, "seed_failed")
            continue
        seed_ok += 1

        if args.skip_verify:
            _write_snapshot(sid, proxy_url, "?", port, False, "verify_skipped")
            continue

        # Verify step
        ip_from_url = proxy_url.split('-ip-')[1].split(':')[0] if '-ip-' in proxy_url else '?'
        print(f"[{sid}] verifying session through proxy {ip_from_url}...")
        ok, detail = await _verify_one_session(sid, proxy_url, port)
        if ok:
            verify_ok += 1
            print(f"[{sid}] ✓ {detail}")
            _write_snapshot(sid, proxy_url, ip_from_url, port, True, detail)
        else:
            verify_fail += 1
            print(f"[{sid}] ✗ verify failed: {detail}")
            print(f"[{sid}]   → swap proxy {ip_from_url} or re-run walmart_relogin.py")
            _write_snapshot(sid, proxy_url, ip_from_url, port, False, detail)

    print()
    print("=" * 65)
    print(f"Seed:   {seed_ok}/{len(sessions)} succeeded ({seed_fail} failed)")
    if not args.skip_verify:
        print(f"Verify: {verify_ok}/{len(sessions)} succeeded ({verify_fail} failed)")
    print("=" * 65)

    if verify_fail > 0:
        print()
        print("Some sessions failed verification. Options:")
        print("  - Swap the failing proxy IPs in config/proxyIps.json")
        print("  - Re-run walmart_relogin.py if the master session expired")
        print("  - Run with --session sN to retry just the failed ones")


if __name__ == "__main__":
    asyncio.run(main())
