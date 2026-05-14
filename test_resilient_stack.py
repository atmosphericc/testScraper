"""
Stage B — Browser-native resilient stack scale test.

Launches the full resilient stack (preflight → MultiSessionPool with N
persistent Chromes → TabDispatcher with bulk fetches) against all enabled
TCINs. Runs for DURATION_S at TARGET_SWEEPS_PER_SEC, reports pool health
and per-TCIN status at the end.

Pass criteria (22 Chromes @ 3 sweeps/sec, 30 min):
  - ≥85% of dispatches return HTTP 200
  - Burned IPs ≤ 2/22
  - Memory <14 GB at 30 min
  - ≥1 on_in_stock callback if any TCIN flipped
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.monitoring.stock_check_resilient import ResilientStockChecker, TcinStatus

DURATION_S = int(os.environ.get("RESILIENT_TEST_DURATION_S", "1800"))
TARGET_SWEEPS_PER_SEC = float(os.environ.get("RESILIENT_TEST_RPS", "3.0"))
                                   # one bulk fetch every ~0.33s aggregate;
                                   # per-IP at 22 Chromes = 1 fetch / ~7.3 sec / IP
# Behavioral OFF by default — 0.10 triggered a 7×403 burst at t=228s on
# 2026-05-13 (PDP-nav frequency too high). Pure API at 3 RPS = 100% over 20min.
BEHAVIORAL_MIX_RATIO = float(os.environ.get("RESILIENT_TEST_BEHAVIORAL", "0.0"))


def on_in_stock(s: TcinStatus):
    print(f"\n[!] IN_STOCK DETECTED: tcin={s.tcin} avail={s.availability_status} "
          f"title={s.title or '?'}\n", flush=True)


async def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    proxy_urls = json.loads((ROOT / "config" / "proxyIps.json").read_text())["proxies"]
    n_cap = int(os.environ.get("RESILIENT_TEST_NUM_IPS", "0"))
    if n_cap > 0:
        proxy_urls = proxy_urls[:n_cap]
    config = json.loads((ROOT / "config" / "product_config.json").read_text(encoding="utf-8"))
    tcins = [p["tcin"] for p in config.get("products", []) if p.get("enabled", True)]
    if not tcins:
        print("[TEST] no enabled TCINs in config/product_config.json")
        return 1

    print(f"[TEST] proxies={len(proxy_urls)} tcins={len(tcins)} duration={DURATION_S}s "
          f"sweeps_per_sec={TARGET_SWEEPS_PER_SEC} behavioral={BEHAVIORAL_MIX_RATIO}")

    checker = ResilientStockChecker(
        proxy_urls=proxy_urls,
        tcins=tcins,
        on_in_stock=on_in_stock,
        target_sweeps_per_sec=TARGET_SWEEPS_PER_SEC,
        behavioral_mix_ratio=BEHAVIORAL_MIX_RATIO,
        log_per_request=False,    # 30 min at 3 sweeps/sec = too noisy at debug
    )

    await checker.start()
    try:
        await asyncio.sleep(DURATION_S)
    finally:
        print("\n[TEST] stopping ...")
        await checker.stop()

    st = checker.stats()
    print("\n" + "=" * 70)
    print(f"[SUMMARY] elapsed={st['elapsed_s']}s  "
          f"actual_sweeps_per_sec={st['actual_sweeps_per_sec']}")
    print(f"  dispatched={st['total_dispatched']}  200={st['total_200']} "
          f"403={st['total_403']} other={st['total_other']} "
          f"behavioral={st['total_behavioral']}")
    ps = st['proxy_state']
    ss = st['session_state']
    print(f"  pool: total={ps['total']} active={ps['active']} parked={ps['parked']} "
          f"burned={ps['burned']}")
    print(f"  sessions: ready={ss.get('ready', 0)} crashed={ss.get('crashed', 0)} "
          f"recycling={ss.get('recycling', 0)}")
    print(f"  cum: total_success={ps['total_success']} total_403={ps['total_403']}")

    print("\n[TCIN STATUS]")
    print(f"  {'tcin':<12} {'in_stock':<9} {'http':<5} {'avail':<20} {'title':<40}")
    for tcin, s in checker.latest().items():
        title = (s.title or "")[:38]
        print(f"  {tcin:<12} {str(s.in_stock):<9} {s.last_status_code:<5} "
              f"{s.availability_status:<20} {title:<40}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
