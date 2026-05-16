"""
Walmart resilient stock checker — entry point.

Wires the framework (src/stack/) + WalmartAdapter (walmart/walmart_adapter.py)
into a runnable engine. Mirrors Target's
`src/monitoring/stock_check_resilient.py` startup pattern but the body is
adapter-driven, not Target-specific.

Usage from code:
    from walmart.walmart_stock_resilient import build_walmart_checker
    checker = build_walmart_checker(
        items=["320424995"],
        on_in_stock=my_callback,
        target_rps=6.0,
    )
    asyncio.create_task(checker.start())

Usage from CLI (for smoke testing):
    WALMART_RESILIENT_NUM_CHROMES=2 WALMART_RESILIENT_RPS=1 \
        python -m walmart.walmart_stock_resilient

Env vars:
  WALMART_RESILIENT_NUM_CHROMES (default: len(active proxies))
    Cap on how many proxies to pool. Useful for dev — set to 2 on the
    laptop so 16 Chromes don't OOM it.
  WALMART_RESILIENT_RPS (default: 6.0)
    Aggregate sweeps per second across all sessions. Validated against
    adapter.per_ip_rps_ceiling (0.5) at startup.
  WALMART_RESILIENT_FIRST_PORT (default: 25000)
    Base port for the local CONNECT forwarders. Each session takes
    base+i. Avoid colliding with Target's 24000+ range when both run
    (note: per CLAUDE.md they don't run concurrently right now).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Callable, Optional

from src.stack.resilient_checker import ResilientChecker
from src.stack.retailer_adapter import ItemStatus
from walmart.walmart_adapter import WalmartAdapter

logger = logging.getLogger(__name__)


DEFAULT_RPS = 6.0


def _load_active_proxies() -> list[str]:
    """Active pool only — the `proxies` key in config/proxyIps.json.
    Reserves stay reserved until manually promoted.
    """
    config_path = Path(__file__).resolve().parent.parent / "config" / "proxyIps.json"
    with open(config_path) as f:
        data = json.load(f)
    proxies = data.get("proxies") or []
    if not proxies:
        raise RuntimeError(f"No active proxies in {config_path}")
    return proxies


def _load_enabled_items() -> list[str]:
    """Read enabled item_ids from walmart/walmart_config.json. Falls back
    to the throwaway product if no enabled items configured.
    """
    cfg_paths = [
        Path(__file__).resolve().parent / "walmart_config.json",
        Path(__file__).resolve().parent.parent / "walmart_config.json",
    ]
    for p in cfg_paths:
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                items = [
                    str(prod["item_id"])
                    for prod in data.get("products", [])
                    if prod.get("enabled", True) and prod.get("item_id")
                ]
                if items:
                    return items
            except Exception as e:
                logger.warning("failed reading %s: %s", p, e)
    logger.warning("No enabled items in walmart_config.json; using throwaway")
    return ["320424995"]


def build_walmart_checker(
    items: list[str],
    on_in_stock: Callable[[ItemStatus], None],
    target_rps: float = DEFAULT_RPS,
    num_chromes: Optional[int] = None,
    first_local_port: int = 25000,
    state_dir: Optional[Path] = None,
    preflight: bool = False,
    log_per_request: bool = True,
) -> ResilientChecker:
    """Construct a ResilientChecker wired with the Walmart adapter.

    Caller is responsible for `await checker.start()` and `await checker.stop()`.
    """
    adapter = WalmartAdapter()
    all_proxies = _load_active_proxies()
    proxies = all_proxies if num_chromes is None else all_proxies[:num_chromes]
    if state_dir is None:
        state_dir = Path(__file__).resolve().parent.parent / "state"

    return ResilientChecker(
        adapter=adapter,
        proxy_urls=proxies,
        items=items,
        on_in_stock=on_in_stock,
        target_aggregate_rps=target_rps,
        state_dir=state_dir,
        first_local_port=first_local_port,
        log_per_request=log_per_request,
        preflight=preflight,
    )


# ── CLI smoke entry point ────────────────────────────────────────────────


def _logged_on_in_stock(status: ItemStatus):
    """Default callback for CLI smoke runs — logs the in-stock transition.
    Production wiring (walmart_app.py / purchase_manager.py) should pass a
    real callback that enqueues a purchase.
    """
    price = f"${status.price:.2f}" if status.price is not None else "?"
    logger.warning(
        "[CLI] *** IN STOCK: %s (%s) @ %s — %s",
        status.title or "?", status.item_id, price, status.availability_status,
    )


async def _run_cli():
    logging.basicConfig(
        level=os.environ.get("WALMART_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(message)s",
    )

    num = os.environ.get("WALMART_RESILIENT_NUM_CHROMES")
    rps = float(os.environ.get("WALMART_RESILIENT_RPS", DEFAULT_RPS))
    first_port = int(os.environ.get("WALMART_RESILIENT_FIRST_PORT", 25000))

    items = _load_enabled_items()
    logger.info("Walmart resilient checker starting: items=%s rps=%.1f num=%s",
                items, rps, num or "all")

    checker = build_walmart_checker(
        items=items,
        on_in_stock=_logged_on_in_stock,
        target_rps=rps,
        num_chromes=int(num) if num else None,
        first_local_port=first_port,
    )

    # Graceful shutdown on Ctrl+C
    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Shutdown requested.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _request_stop)  # fallback for Windows / non-asyncio

    try:
        await checker.start()
        await stop_event.wait()
    finally:
        await checker.stop()


if __name__ == "__main__":
    asyncio.run(_run_cli())
