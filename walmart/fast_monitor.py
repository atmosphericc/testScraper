"""
walmart/fast_monitor.py — Phase 3 of docs/WALMART_FAST_MONITOR.md.

Runnable fast stock monitor built on the curl_cffi http_stock_checker. It
distributes per-SKU stock checks across the ISP proxy pool at a controlled
aggregate rate, ANONYMOUSLY (no account, no _px3 — validated 2026-08-19:
160 anon requests through one IP, zero blocks). It fires on_signal(item_id,
result) when a SKU transitions to in_stock or QUEUED.

This is the DECOUPLED detection layer — it never touches the buyer's account,
so we can push it hard (even burst to 3/s/SKU at the drop) without cooking the
logged-in buyer, which runs separately on the home IP.

Rate model: `target_rps` = total checks/sec across the whole pool. Each check
is assigned a SKU (round-robin) and a proxy (round-robin), so:
    per-IP rate    = target_rps / n_proxies
    per-SKU refresh = target_rps / n_skus
Run gentle for a sustained soak; crank target_rps up for a drop-window burst.

CLI soak test:
    python -m walmart.fast_monitor --duration 1800 --rps 6
    python -m walmart.fast_monitor --duration 120 --rps 6 --item 20497167347
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from .http_stock_checker import check_item, HttpCheckResult

logger = logging.getLogger("walmart.fast_monitor")

_ROOT = Path(__file__).resolve().parent.parent


def _load_items() -> list[str]:
    cfg = _ROOT / "walmart" / "walmart_config.json"
    data = json.loads(cfg.read_text())
    items = [str(p["item_id"]) for p in data.get("products", [])
             if p.get("enabled") and p.get("item_id")]
    return items or ["20497167347"]


def _load_proxies() -> list[str]:
    cfg = _ROOT / "config" / "proxyIps.json"
    data = json.loads(cfg.read_text())
    return data.get("proxies") or []


def _ip_of(proxy: str) -> str:
    m = re.search(r"-ip-([\d.]+)", proxy)
    return m.group(1) if m else proxy[:24]


class FastMonitor:
    """Anonymous, rate-controlled, pool-distributed stock monitor."""

    def __init__(
        self,
        items: list[str],
        proxies: list[str],
        on_signal: Optional[Callable[[str, HttpCheckResult], None]] = None,
        target_rps: float = 6.0,
        workers: int = 16,
        timeout: float = 12.0,
        stats_interval: float = 15.0,
    ):
        if not items:
            raise ValueError("no items to monitor")
        if not proxies:
            raise ValueError("no proxies loaded")
        self.items = items
        self.proxies = proxies
        self.on_signal = on_signal or self._default_signal
        self.target_rps = max(0.1, target_rps)
        self.workers = workers
        self.timeout = timeout
        self.stats_interval = stats_interval

        self._lock = threading.Lock()
        self._stats = dict(checks=0, clean=0, blocked=0, queued=0,
                           instock=0, errors=0, oos=0)
        # per-SKU last-known in-stock/queued state, to fire the signal only on
        # a transition (not every check).
        self._sku_state: dict[str, str] = {i: "unknown" for i in items}
        # per-IP block tally — surfaces which IPs (if any) start flagging.
        self._ip_blocks: dict[str, int] = {}
        self._window = dict(checks=0, blocked=0)  # since last stats print
        self._stop = threading.Event()

    # ── result handling (runs in worker threads) ─────────────────────────
    def _handle(self, item_id: str, fut):
        try:
            r: HttpCheckResult = fut.result()
        except Exception as e:  # pragma: no cover - defensive
            with self._lock:
                self._stats["checks"] += 1
                self._stats["errors"] += 1
                self._window["checks"] += 1
            return

        new_state = ("blocked" if r.blocked else "queued" if r.queued
                     else "instock" if r.in_stock else "oos" if r.ok
                     else "error")
        signal = None
        with self._lock:
            self._stats["checks"] += 1
            self._window["checks"] += 1
            if r.blocked:
                self._stats["blocked"] += 1
                self._window["blocked"] += 1
                ip = _ip_of(getattr(r, "_proxy", "") or "")
                self._ip_blocks[ip] = self._ip_blocks.get(ip, 0) + 1
            elif r.queued:
                self._stats["queued"] += 1
            elif r.in_stock:
                self._stats["instock"] += 1
            elif r.ok:
                self._stats["oos"] += 1
            else:
                self._stats["errors"] += 1

            # transition detection → signal on the interesting edges only
            prev = self._sku_state.get(item_id)
            if new_state in ("instock", "queued") and prev != new_state:
                signal = r
            self._sku_state[item_id] = new_state

        if signal is not None:
            try:
                self.on_signal(item_id, signal)
            except Exception:
                logger.exception("[FAST_MON] on_signal raised")

    def _default_signal(self, item_id: str, r: HttpCheckResult):
        state = "QUEUED" if r.queued else "IN STOCK"
        logger.warning("[FAST_MON] *** %s: %s *** %s", state, item_id, r.summary())

    # ── stats ────────────────────────────────────────────────────────────
    def _print_stats(self, elapsed: float):
        with self._lock:
            s = dict(self._stats)
            w = dict(self._window)
            self._window = dict(checks=0, blocked=0)
            hot_ips = sorted(self._ip_blocks.items(), key=lambda kv: -kv[1])[:3]
        rate = s["checks"] / elapsed if elapsed else 0
        wrate = "?" if w["checks"] == 0 else f"{100*w['blocked']/w['checks']:.0f}%"
        hot = (" hot_ips=" + ",".join(f"{ip}:{n}" for ip, n in hot_ips)) if hot_ips else ""
        logger.info(
            "[FAST_MON] %.0fs | %.1f chk/s avg | checks=%d clean=%d blocked=%d(win %s) "
            "queued=%d instock=%d err=%d%s",
            elapsed, rate, s["checks"], s["clean"] + s["oos"], s["blocked"], wrate,
            s["queued"], s["instock"], s["errors"], hot,
        )

    # ── main loop ────────────────────────────────────────────────────────
    def run(self, duration_s: float):
        interval = 1.0 / self.target_rps
        item_cycle = itertools.cycle(self.items)
        proxy_cycle = itertools.cycle(self.proxies)
        per_ip = self.target_rps / len(self.proxies)
        per_sku = self.target_rps / len(self.items)
        logger.info(
            "[FAST_MON] start: %d SKUs, %d proxies, target=%.1f chk/s "
            "(%.2f/s/IP, %.2f/s/SKU), %ds soak",
            len(self.items), len(self.proxies), self.target_rps,
            per_ip, per_sku, int(duration_s),
        )

        t0 = time.monotonic()
        deadline = t0 + duration_s
        next_dispatch = t0
        next_stats = t0 + self.stats_interval

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            while not self._stop.is_set() and time.monotonic() < deadline:
                now = time.monotonic()
                # dispatch any checks that are due (catch up if behind, but
                # cap catch-up so a stall can't unleash a burst)
                dispatched = 0
                while now >= next_dispatch and dispatched < self.workers:
                    iid = next(item_cycle)
                    prox = next(proxy_cycle)

                    def _submit(iid=iid, prox=prox):
                        r = check_item(iid, proxy=prox, timeout=self.timeout)
                        # tag the proxy for per-IP block accounting
                        try:
                            r._proxy = prox
                        except Exception:
                            pass
                        return r

                    fut = ex.submit(_submit)
                    fut.add_done_callback(lambda f, iid=iid: self._handle(iid, f))
                    next_dispatch += interval
                    dispatched += 1

                if now >= next_stats:
                    self._print_stats(now - t0)
                    next_stats += self.stats_interval

                time.sleep(min(interval, 0.05))

        self._print_stats(time.monotonic() - t0)
        with self._lock:
            s = dict(self._stats)
        block_pct = (100 * s["blocked"] / s["checks"]) if s["checks"] else 0
        logger.warning(
            "[FAST_MON] SOAK DONE: %d checks, %.1f%% blocked — %s",
            s["checks"], block_pct,
            "CLEAN ✓ (transport holds)" if block_pct < 2 else
            "⚠ blocking rose — sustained rate too high for anon",
        )
        return s

    def stop(self):
        self._stop.set()


def _main():
    ap = argparse.ArgumentParser(description="Walmart anonymous fast-monitor soak")
    ap.add_argument("--duration", type=float, default=120, help="seconds to run")
    ap.add_argument("--rps", type=float, default=6.0, help="aggregate checks/sec")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--item", action="append", help="override item id(s)")
    ap.add_argument("--max-proxies", type=int, default=0, help="cap proxy count (0=all)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    items = args.item or _load_items()
    proxies = _load_proxies()
    if args.max_proxies > 0:
        proxies = proxies[:args.max_proxies]

    mon = FastMonitor(items, proxies, target_rps=args.rps, workers=args.workers)
    try:
        mon.run(args.duration)
    except KeyboardInterrupt:
        mon.stop()
        print("\ninterrupted")


if __name__ == "__main__":
    _main()
