"""
One-shot proxy health validator — drives the EXACT production browser-native
path the Target bot uses: MultiSessionPool (one persistent Chrome per BD ISP IP)
+ TabDispatcher firing bulk RedSky via tab.evaluate(fetch(...)). No curl_cffi,
no raw requests anywhere in the request path.

Differences from a real run, on purpose:
  - preflight=False  -> every configured IP is exercised through the in-tab
    fetch() path (the request path that actually matters tonight), instead of
    being pre-filtered by the curl_cffi startup probe.
  - throwaway state dir -> production state/proxy_state.json and the warmed
    state/session_profiles are left untouched, and no IP gets PARKED for 3h on
    a transient 403 right before a real run.
  - on_in_stock only prints -> no purchase manager wired, nothing is ever bought.
  - STOCK_DIAG_PROBES respected from env (defaults off here to keep the per-IP
    sweep signal clean).

Per-IP verdict is read back from the throwaway proxy_state.json after the run.

Usage:
    venv\\Scripts\\python.exe validate_proxies.py
Env:
    VALIDATE_DURATION_S (default 120)  sweep window after pool warmup
    VALIDATE_RPS        (default 3.0)  aggregate sweeps/sec (production rate)
    VALIDATE_POOL       (default proxies | reserve_proxies | warn_proxies | all)
"""

from __future__ import annotations

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

os.environ.setdefault("STOCK_DIAG_PROBES", "0")  # keep per-IP signal clean

from src.monitoring.stock_check_resilient import ResilientStockChecker  # noqa: E402
from src.session.multi_session_pool import _pinned_ip  # noqa: E402

DURATION_S = int(os.environ.get("VALIDATE_DURATION_S", "120"))
RPS = float(os.environ.get("VALIDATE_RPS", "3.0"))
POOL_KEY = os.environ.get("VALIDATE_POOL", "proxies")


def load_proxies() -> list[str]:
    cfg = json.loads((ROOT / "config" / "proxyIps.json").read_text(encoding="utf-8"))
    if POOL_KEY == "all":
        urls: list[str] = []
        for k in ("proxies", "reserve_proxies", "warn_proxies"):
            urls += cfg.get(k, [])
        return urls
    return cfg.get(POOL_KEY, [])


def _verdict(status: str, ok: int, bad: int, last: int, last_at: float) -> str:
    if ok > 0 and bad == 0 and status == "active":
        return "HEALTHY"
    if ok > 0 and bad > 0 and status == "active":
        return f"FLAKY ({bad}x403 but recovered)"
    if status == "parked":
        return "PARKED (>=2 consec 403 during test)"
    if status == "burned":
        return "BURNED (403/captcha)"
    if ok == 0 and bad > 0:
        return "BAD (all 403)"
    if ok == 0 and bad == 0 and last_at == 0:
        return "NOT EXERCISED (no sweep landed)"
    return f"UNCLEAR (last={last})"


async def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    proxy_urls = load_proxies()
    if not proxy_urls:
        print(f"[VALIDATE] no proxies under key '{POOL_KEY}'")
        return 1
    cfg = json.loads((ROOT / "config" / "product_config.json").read_text(encoding="utf-8"))
    tcins = [p["tcin"] for p in cfg.get("products", []) if p.get("enabled", True)]

    tmp_state = ROOT / "state" / "_proxy_validation_tmp"
    if tmp_state.exists():
        shutil.rmtree(tmp_state, ignore_errors=True)
    tmp_state.mkdir(parents=True, exist_ok=True)

    print(f"[VALIDATE] pool='{POOL_KEY}' proxies={len(proxy_urls)} "
          f"tcins={len(tcins)} sweep_window={DURATION_S}s rps={RPS} "
          f"diag_probes={os.environ.get('STOCK_DIAG_PROBES')}", flush=True)

    checker = ResilientStockChecker(
        proxy_urls=proxy_urls,
        tcins=tcins,
        on_in_stock=lambda s: print(f"[VALIDATE] (print-only) in_stock {s.tcin}"),
        target_sweeps_per_sec=RPS,
        state_dir=tmp_state,
        preflight=False,        # exercise every IP via the browser fetch path
        log_per_request=False,
    )

    t_start = time.time()
    await checker.start()
    warmup_s = time.time() - t_start
    print(f"[VALIDATE] pool warmed in {warmup_s:.0f}s; sweeping for {DURATION_S}s ...",
          flush=True)
    try:
        await asyncio.sleep(DURATION_S)
    finally:
        print("[VALIDATE] stopping pool ...", flush=True)
        await checker.stop()

    st = checker.stats()
    ps_path = tmp_state / "proxy_state.json"
    entries = json.loads(ps_path.read_text(encoding="utf-8")) if ps_path.exists() else {}

    rows = []
    for ip, e in entries.items():
        rows.append((
            ip, e.get("status", "?"), int(e.get("last_status", 0)),
            int(e.get("total_success", 0)), int(e.get("total_403", 0)),
            float(e.get("last_status_at", 0.0)),
        ))
    rows.sort(key=lambda r: (r[1] != "active", -(r[3])))

    healthy = flaky = bad = 0
    print("\n" + "=" * 78)
    print(f"  PER-IP RESULT  (pool='{POOL_KEY}', browser-native fetch path, "
          f"no curl_cffi)")
    print("=" * 78)
    print(f"  {'IP':<18}{'status':<9}{'200s':<7}{'403s':<7}{'last':<6}verdict")
    print("  " + "-" * 74)
    for ip, status, last, ok, b403, last_at in rows:
        v = _verdict(status, ok, b403, last, last_at)
        if v == "HEALTHY":
            healthy += 1
        elif v.startswith("FLAKY"):
            flaky += 1
        elif v.startswith(("BAD", "BURNED", "PARKED")):
            bad += 1
        print(f"  {ip:<18}{status:<9}{ok:<7}{b403:<7}{last:<6}{v}")

    seen = set(entries.keys())
    missing = [ip for ip in (_pinned_ip(u) for u in proxy_urls) if ip and ip not in seen]
    for ip in missing:
        print(f"  {ip:<18}{'-':<9}{'-':<7}{'-':<7}{'-':<6}NO SESSION (Chrome never reached ready)")

    ss = st.get("session_state", {})
    print("  " + "-" * 74)
    print(f"  TOTALS: healthy={healthy} flaky={flaky} bad/parked/burned={bad} "
          f"no_session={len(missing)}  of {len(proxy_urls)} configured")
    print(f"  RUN: sweeps={st['sweep_count']} 200={st['total_200']} "
          f"403={st['total_403']} other={st['total_other']} | "
          f"sessions ready={ss.get('ready', 0)} crashed={ss.get('crashed', 0)}")
    print("=" * 78, flush=True)

    shutil.rmtree(tmp_state, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
