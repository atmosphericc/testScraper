"""
End-to-end test of the resilient stack:
  ForwarderPool + ProxyState + ResilientStockChecker

Loads enabled IPs from config/proxyIps.json and enabled TCINs from
config/product_config.json, runs the checker for DURATION_S, then prints
a summary.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.monitoring.stock_check_resilient import ResilientStockChecker, TcinStatus

DURATION_S = 60
TARGET_RPS = 3.0


def on_in_stock(s: TcinStatus):
    print(f"\n[!] IN_STOCK DETECTED: tcin={s.tcin} avail={s.availability_status} "
          f"title={s.title or '?'}\n", flush=True)


async def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    proxy_urls = json.loads((ROOT / "config" / "proxyIps.json").read_text())["proxies"]
    config = json.loads((ROOT / "config" / "product_config.json").read_text())
    tcins = [p["tcin"] for p in config.get("products", []) if p.get("enabled", True)]

    # Trim TCINs to a manageable set + add the known in-stock 50270379
    tcins = list({*tcins[:18], "50270379"})

    print(f"[TEST] proxies={len(proxy_urls)} tcins={len(tcins)} duration={DURATION_S}s "
          f"target_rps={TARGET_RPS}")

    checker = ResilientStockChecker(
        proxy_urls=proxy_urls,
        tcins=tcins,
        on_in_stock=on_in_stock,
        target_rps=TARGET_RPS,
        log_per_request=False,
    )

    await checker.start()
    try:
        await asyncio.sleep(DURATION_S)
    finally:
        print("\n[TEST] stopping ...")
        await checker.stop()

    # Summary
    st = checker.stats()
    print("\n" + "=" * 70)
    print(f"[SUMMARY] elapsed={st['elapsed_s']}s  actual_rps={st['actual_rps']}")
    print(f"  dispatched={st['total_dispatched']}  200={st['total_200']} "
          f"403={st['total_403']} other={st['total_other']}")
    ps = st['proxy_state']
    print(f"  pool: total={ps['total']} active={ps['active']} parked={ps['parked']} "
          f"burned={ps['burned']}")
    print(f"  cum: total_success={ps['total_success']} total_403={ps['total_403']}")

    print("\n[TCIN STATUS]")
    print(f"  {'tcin':<12} {'in_stock':<9} {'http':<5} {'avail':<20} {'title':<40}")
    for tcin, s in checker.latest().items():
        title = (s.title or "")[:38]
        print(f"  {tcin:<12} {str(s.in_stock):<9} {s.last_status_code:<5} "
              f"{s.availability_status:<20} {title:<40}")


if __name__ == "__main__":
    asyncio.run(main())
