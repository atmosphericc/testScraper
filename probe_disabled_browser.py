"""
Re-test config/proxyIps.json's `disabled_proxies` using the EXACT production
path: persistent zendriver Chrome → local CONNECT forwarder → BD ISP IP →
RedSky bulk fetch via `tab.evaluate(fetch(...))`.

Many of these IPs were marked disabled by audit scripts that hit RedSky with
raw Python `requests` — Shape flags Python TLS regardless of IP health, so
those audits underestimate the recoverable count. This probe uses real Chrome
JA3/JA4 + live session cookies, which is the actual production fingerprint.

Any IP that returns HTTP 200 from the bulk RedSky call gets promoted back
into `proxies`. Everything else stays in `disabled_proxies`.

Env knobs (all optional):
    PROBE_BATCH_SIZE      max IPs to test (default: all disabled)
    PROBE_STAGGER_S       Chrome launch stagger window (default 90s — fast smoke)
                          Override the prod CHROME_STAGGER_TOTAL_S default (600s).
    PROBE_BASE_PORT       local forwarder base port (default 24000 — prod uses 22000)
    PROBE_DRY_RUN=1       don't rewrite proxyIps.json, just print verdict
    PROBE_STORE_ID        RedSky store_id for the bulk call (default 865 — prod)

Usage:
    python probe_disabled_browser.py                 # full run, rewrites file
    PROBE_BATCH_SIZE=3 PROBE_DRY_RUN=1 python probe_disabled_browser.py   # smoke
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session.multi_session_pool import MultiSessionPool
from src.monitoring.tab_dispatcher import TabDispatcher

PROXY_FILE = ROOT / "config" / "proxyIps.json"
PRODUCT_FILE = ROOT / "config" / "product_config.json"
PROFILE_ROOT = ROOT / "state" / "disabled_probe_profiles"
JAR_PATH = ROOT / "state" / "disabled_probe_jar.json"


def _enabled_tcins(limit: int = 5) -> list[str]:
    cfg = json.loads(PRODUCT_FILE.read_text(encoding="utf-8"))
    out = []
    for p in cfg.get("products", []):
        if p.get("enabled", True):
            tcin = p.get("tcin")
            if tcin:
                out.append(str(tcin))
        if len(out) >= limit:
            break
    if not out:
        raise RuntimeError("no enabled products in config/product_config.json")
    return out


def _atomic_write_proxyfile(payload: dict) -> None:
    tmp = PROXY_FILE.with_suffix(f".tmp.{os.getpid()}")
    data = json.dumps(payload, indent=2) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, PROXY_FILE)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("PROBE")

    data = json.loads(PROXY_FILE.read_text(encoding="utf-8"))
    enabled = list(data.get("proxies", []))
    disabled = list(data.get("disabled_proxies", []))
    if not disabled:
        log.info("no disabled_proxies to test")
        return 0

    batch_size = int(os.environ.get("PROBE_BATCH_SIZE", str(len(disabled))))
    targets = disabled[:batch_size]
    stagger_s = float(os.environ.get("PROBE_STAGGER_S", "90"))
    base_port = int(os.environ.get("PROBE_BASE_PORT", "24000"))
    store_id = os.environ.get("PROBE_STORE_ID", "865")
    dry_run = os.environ.get("PROBE_DRY_RUN", "").lower() in ("1", "true", "yes")

    tcins = _enabled_tcins(limit=5)
    log.info(
        f"testing {len(targets)} disabled IPs (of {len(disabled)} total) "
        f"with TCINs={tcins} store={store_id} stagger={stagger_s:.0f}s "
        f"dry_run={dry_run}"
    )

    # Force short stagger via env override that MultiSessionPool reads.
    os.environ["CHROME_STAGGER_TOTAL_S"] = str(stagger_s)

    # Fresh profile root so we don't pollute prod state/session_profiles/.
    if PROFILE_ROOT.exists():
        try:
            shutil.rmtree(PROFILE_ROOT)
        except Exception as e:
            log.warning(f"could not clear {PROFILE_ROOT}: {e}")
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)

    pool = MultiSessionPool(
        proxy_urls=targets,
        cookies_jar_path=JAR_PATH,
        profile_root=PROFILE_ROOT,
        forwarder_base_port=base_port,
    )

    try:
        await pool.start()
    except Exception as e:
        log.error(f"pool.start failed: {e}")
        return 2

    ready, total = pool.session_count()
    log.info(f"after launch: {ready}/{total} sessions reached homepage + harvested cookies")

    dispatcher = TabDispatcher(
        session_pool=pool,
        tcins=tcins,
        store_id=store_id,
    )

    # Fire one bulk RedSky sweep per ready session. Pin to a specific session
    # by temporarily marking the others as not-ready so dispatch_one_sweep
    # doesn't randomly skip us.
    per_session_result: dict[str, dict] = {}
    for s in pool.sessions:
        if s.state != "ready" or s.tab is None or not s.cookies or not s.visitor_id:
            per_session_result[s.proxy_ip] = {
                "status": 0,
                "ms": 0,
                "error": f"launch_state={s.state} cookies={len(s.cookies)} "
                         f"visitor_id={'yes' if s.visitor_id else 'no'}",
            }
            continue
        # Temporarily hide all other ready sessions so the dispatcher MUST pick s
        original_states = {}
        for other in pool.sessions:
            if other is not s and other.state == "ready":
                original_states[other.id] = other.state
                other.state = "_probe_hidden"
        try:
            t0 = time.time()
            result = await dispatcher.dispatch_one_sweep()
            ms = int((time.time() - t0) * 1000)
            if result is None:
                per_session_result[s.proxy_ip] = {
                    "status": 0, "ms": ms, "error": "no_session_picked"
                }
            else:
                per_session_result[s.proxy_ip] = {
                    "status": result.http_status,
                    "ms": result.latency_ms,
                    "error": result.error,
                }
        finally:
            for sid, state in original_states.items():
                for other in pool.sessions:
                    if other.id == sid:
                        other.state = state
                        break
        # gentle pacing between sessions so a coordinated burst doesn't itself
        # invite Shape attention while we're probing
        await asyncio.sleep(2.0)

    # ─── classify + report ───
    recovered: list[str] = []
    still_blocked: list[str] = []

    print()
    print("=" * 78)
    print(f"{'ip':<18} {'http':<6} {'ms':<7} {'verdict':<14} note")
    print("-" * 78)
    for url in targets:
        # parse pinned ip from url
        import re
        m = re.search(r"-ip-([\d\.]+):", url)
        ip = m.group(1) if m else "?"
        r = per_session_result.get(ip, {"status": 0, "ms": 0, "error": "no_result"})
        status = r["status"]
        ms = r["ms"]
        note = (r.get("error") or "")[:34]
        if status == 200:
            recovered.append(url)
            verdict = "RECOVERED"
        else:
            still_blocked.append(url)
            verdict = "BLOCKED" if status in (0, 403) else f"HTTP_{status}"
        print(f"{ip:<18} {status:<6} {ms:<7} {verdict:<14} {note}")

    print("=" * 78)
    print(f"recovered: {len(recovered)} / {len(targets)} tested "
          f"(still blocked: {len(still_blocked)})")

    if recovered and not dry_run:
        new_enabled = enabled + recovered
        new_disabled = still_blocked + disabled[len(targets):]   # untested stays disabled
        note = (
            f"{len(new_disabled)} IPs below returned non-200 from Target's RedSky bulk "
            f"endpoint via real-Chrome JA3 on most recent browser probe "
            f"(probe_disabled_browser.py). Loaders only read 'proxies'; "
            f"this key is ignored."
        )
        payload = {
            "proxies": new_enabled,
            "_disabled_note": note,
            "disabled_proxies": new_disabled,
        }
        _atomic_write_proxyfile(payload)
        print(f"[WROTE] {PROXY_FILE.name}: "
              f"{len(new_enabled)} enabled, {len(new_disabled)} disabled")
    elif recovered and dry_run:
        print(f"[DRY_RUN] would promote {len(recovered)} IPs back into proxies")
    else:
        print("[NO CHANGES] no recoverable IPs in this probe")

    await pool.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
