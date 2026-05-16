"""
Walmart resilient stack smoke test — mirrors test_resilient_stack.py but
exercises the Walmart adapter through the new framework.

Run modes:
  Fast dev smoke (laptop, N=2, 15 min, throwaway product):
    WALMART_TEST_DURATION_S=900 WALMART_TEST_NUM_IPS=2 WALMART_TEST_RPS=1 \
        python test_walmart_resilient.py

  Default (30 min, all active proxies, RPS=2):
    python test_walmart_resilient.py

  Prod soak (64GB box, N=16, 60 min, RPS=6):
    WALMART_TEST_DURATION_S=3600 WALMART_TEST_NUM_IPS=16 WALMART_TEST_RPS=6 \
        python test_walmart_resilient.py

The throwaway product 320424995 (per MEMORY.md) is the default test TCIN —
swap to a real Pokemon item only after the soak passes clean.

Acceptance criteria for "pass":
  - ≥98% of dispatches return http_status=200 and not blocked
  - Zero IPs burned (state/walmart_proxy_state.json shows no "burned" entries)
  - Sweep loop maintains within ±20% of target_rps over the full duration
  - No worker crashes that don't recover via watchdog
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Make src/ + walmart/ importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.stack.retailer_adapter import ItemStatus
from walmart.walmart_stock_resilient import build_walmart_checker

DEFAULT_DURATION_S = 1800       # 30 min default
DEFAULT_RPS = 2.0
DEFAULT_TEST_ITEM = "320424995"   # throwaway product from MEMORY.md


def _on_in_stock(status: ItemStatus):
    """Smoke-test callback — just log. Real purchase callback is in walmart_app.py."""
    price = f"${status.price:.2f}" if status.price is not None else "?"
    logging.warning(
        "[TEST] *** IN STOCK: %s (%s) @ %s",
        status.title or "?", status.item_id, price,
    )


async def main():
    logging.basicConfig(
        level=os.environ.get("WALMART_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(message)s",
    )

    duration_s = int(os.environ.get("WALMART_TEST_DURATION_S", DEFAULT_DURATION_S))
    rps = float(os.environ.get("WALMART_TEST_RPS", DEFAULT_RPS))
    num = os.environ.get("WALMART_TEST_NUM_IPS")
    item = os.environ.get("WALMART_TEST_ITEM", DEFAULT_TEST_ITEM)

    logging.info(
        "Walmart resilient smoke: duration=%ds rps=%.1f num=%s item=%s",
        duration_s, rps, num or "all", item,
    )

    checker = build_walmart_checker(
        items=[item],
        on_in_stock=_on_in_stock,
        target_rps=rps,
        num_chromes=int(num) if num else None,
        log_per_request=False,    # too chatty over 30+ min
    )

    started_at = time.time()
    try:
        await checker.start()
        # Wait the full duration; stats loop logs heartbeats every 30s
        deadline = started_at + duration_s
        while time.time() < deadline:
            await asyncio.sleep(min(30, deadline - time.time()))
    except KeyboardInterrupt:
        logging.info("Interrupted at %.1fs", time.time() - started_at)
    finally:
        # Final stats snapshot before shutdown
        try:
            s = checker.stats()
            logging.info("=" * 60)
            logging.info("FINAL STATS")
            for k, v in s.items():
                if isinstance(v, dict):
                    logging.info("  %s:", k)
                    for k2, v2 in v.items():
                        logging.info("    %s: %s", k2, v2)
                else:
                    logging.info("  %s: %s", k, v)
            logging.info("=" * 60)

            # Compute the headline 200-success ratio
            total = s.get("total_dispatched", 0)
            success = s.get("total_200", 0)
            blocked = s.get("total_blocked", 0)
            ratio = (success / total * 100) if total else 0.0
            logging.info("Success ratio: %.2f%% (%d/%d 200s, %d blocked)",
                         ratio, success, total, blocked)
        except Exception:
            logging.exception("Stats reporting failed")

        await checker.stop()
        logging.info("Test complete after %.1fs", time.time() - started_at)


if __name__ == "__main__":
    asyncio.run(main())
