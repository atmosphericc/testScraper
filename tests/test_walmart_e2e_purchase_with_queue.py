"""
End-to-end Walmart purchase against the sim, with queue (Phase A-E).

Drives the actual production code path:
  WalmartPurchaseManager.start()
    → WalmartStockMonitor monitors via Tab 1 fetch
    → SKU returns IN_STOCK (or QUEUED → admitted)
    → _on_in_stock_signal fires
    → _run_purchase → WalmartPurchaseExecutor.purchase()
      → navigate(item_url)
      → if queued: QueueHandler.detect_and_wait
      → ATC
      → /cart navigation
      → /checkout navigation
      → reserve_cheapest_slot via hybrid (env=WALMART_CHECKOUT_API=1)
      → submit_cvv_via_pie (env=WALMART_PIE_CVV=1)
      → CreateContract → pcid returned

The full production stack runs against the mitmproxy sim. Asserts:
  - Bot completes the purchase without human intervention
  - pcid is received within bounded time
  - Reports latency: t(queue_detected) → t(pcid_received)

Two scenarios:
  1. Non-queue happy path (baseline — proves the integration works)
  2. Queue path (PDP queue → admission → checkout)

Run: python tests/test_walmart_e2e_purchase_with_queue.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import urllib3   # noqa: E402
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


@dataclass
class LatencyMeasurement:
    """Captured during a run by inspecting the bot's activity stream."""
    queue_detected_at: float = 0.0
    queue_admitted_at: float = 0.0
    atc_clicked_at: float = 0.0
    checkout_reached_at: float = 0.0
    pcid_received_at: float = 0.0
    pcid: str = ""

    @property
    def queue_to_pcid_ms(self) -> float:
        if self.queue_detected_at and self.pcid_received_at:
            return (self.pcid_received_at - self.queue_detected_at) * 1000
        return -1.0

    @property
    def detect_to_pcid_ms(self) -> float:
        """Fallback latency: stock-detected → pcid (for non-queue scenarios)."""
        if self.atc_clicked_at and self.pcid_received_at:
            return (self.pcid_received_at - self.atc_clicked_at) * 1000
        return -1.0


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Status-callback that captures latency milestones ────────────────────


class LatencyRecorder:
    """Subscribes to WalmartPurchaseManager status_callback to capture
    the timestamps of key milestones."""

    def __init__(self):
        self.latency = LatencyMeasurement()
        self.all_messages: list[tuple[float, str]] = []
        self.pcid_event = asyncio.Event()
        self._started = time.perf_counter()

    def t(self) -> float:
        return time.perf_counter()

    def __call__(self, msg: str):
        now = self.t()
        rel = now - self._started
        self.all_messages.append((rel, msg))

        # Milestone detection from message text (matches the production
        # messages produced by purchase_manager + purchase_executor)
        lower = msg.lower()
        if "in stock" in lower and self.latency.atc_clicked_at == 0:
            # First in-stock signal — used as "detection" time for non-queue scenarios
            pass
        if ("queue detected" in lower or
                "queued" in lower or
                "in line" in lower) and self.latency.queue_detected_at == 0:
            self.latency.queue_detected_at = now
        if "admitted" in lower and self.latency.queue_admitted_at == 0:
            self.latency.queue_admitted_at = now
        if ("add to cart" in lower or "atc" in lower) and self.latency.atc_clicked_at == 0:
            self.latency.atc_clicked_at = now
        if "checkout" in lower and self.latency.checkout_reached_at == 0:
            self.latency.checkout_reached_at = now
        # pcid arrives via "ORDER PLACED — pcid=..." or "Hybrid SUCCESS — pcid=..."
        if "pcid" in lower:
            import re
            m = re.search(r'pcid[=:\s]+([\w-]+)', msg, re.IGNORECASE)
            if m:
                self.latency.pcid = m.group(1)
                self.latency.pcid_received_at = now
                self.pcid_event.set()


# ── scenario runner ──────────────────────────────────────────────────────


async def _patch_config_for_test(test_sku: str, tmp_dir: Path) -> Path:
    """Write a one-item walmart_config.json into tmp_dir + point env at it.

    The bot reads from walmart_config.json by default — we override the
    real one for this test only.
    """
    config_path = tmp_dir / "walmart_config.json"
    config_path.write_text(json.dumps({
        "products": [{
            "item_id": test_sku,
            "name": "Sim Test Item",
            "url": f"https://www.walmart.com/ip/{test_sku}",
            "enabled": True,
            "max_qty": 1,
        }],
    }))
    return config_path


async def run_scenario(scenario_name: str, queue: bool) -> tuple[LatencyRecorder, bool]:
    """Run one scenario: spawn sim, configure it, drive WalmartPurchaseManager.

    Returns (recorder, success: bool).
    """
    from walmart.sim.server import SimServer

    test_sku = "99999000001"
    sim_port = _free_port()

    # Spawn sim
    sim = SimServer(port=sim_port)
    sim.start(timeout=10.0)

    # Hoist so the outer finally can always read them even if init raises.
    real_config_path = REPO_ROOT / "walmart" / "walmart_config.json"
    real_config_backup = real_config_path.read_bytes() if real_config_path.exists() else None

    try:
        ctl = sim.control()
        ctl.reset()
        if queue:
            ctl.set_item(test_sku, "queued")
            ctl.set_queue(
                queue_id="qa484c0ebd7014",
                item_id=test_sku,
                next_refresh_relative_time_ms=1000,
                state_transitions=[[3, "valid"]],
            )
        else:
            ctl.set_item(test_sku, "in_stock")

        # Mark hashes as known so hybrid checkout doesn't trigger APQ retries
        from walmart.checkout_api import (
            UPDATE_ITEMS_HASH, CREATE_CONTRACT_HASH,
            GET_SLOTS_HASH, RESERVE_SLOT_HASH,
        )
        ctl.set_hash_scenario("updateItems", known_hashes=[UPDATE_ITEMS_HASH])
        ctl.set_hash_scenario("CreateContract", known_hashes=[CREATE_CONTRACT_HASH])
        ctl.set_hash_scenario("getSlots", known_hashes=[GET_SLOTS_HASH])
        ctl.set_hash_scenario("reserveSlotMutation", known_hashes=[RESERVE_SLOT_HASH])

        # Override walmart_config.json with our one-item test config
        real_config_path.write_text(json.dumps({
            "products": [{
                "item_id": test_sku,
                "name": "Sim Test Item",
                "url": f"https://www.walmart.com/ip/{test_sku}",
                "enabled": True,
                "max_qty": 1,
            }],
        }))

        # Set env vars to point bot at sim + enable hybrid paths
        os.environ["WALMART_SIM_PROXY"] = sim.proxy_url
        os.environ["WALMART_CHECKOUT_API"] = "1"
        os.environ["WALMART_PIE_CVV"] = "1"
        os.environ["WALMART_CVV"] = "123"
        os.environ["CHECKOUT_MODE"] = "PRODUCTION"
        os.environ["FINAL_PURCHASE"] = "YES"
        # Force profile to a tempdir so we don't touch walmart-profile/
        os.environ["WALMART_TEST_PROFILE_DIR"] = str(tempfile.mkdtemp(prefix="e2e_walmart_"))
        # Don't make the bot sleep 90-180s between purchase retries in tests —
        # we want it to keep retrying ASAP until either pcid arrives or our
        # 180s wait_for fires.
        os.environ["WALMART_TEST_LOOP_COOLDOWN"] = "off"

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(message)s",
        )

        recorder = LatencyRecorder()

        # Import lazily so env-var overrides are seen at import time
        from walmart.purchase_manager import WalmartPurchaseManager

        manager = WalmartPurchaseManager(status_callback=recorder)
        manager_task = asyncio.create_task(manager.start())

        # Wait up to 180s for the bot to complete the purchase
        # (browser launch + warmup + stock check + ATC + checkout chain)
        try:
            await asyncio.wait_for(recorder.pcid_event.wait(), timeout=180.0)
            ok = True
        except asyncio.TimeoutError:
            ok = False
        finally:
            # Tear down the bot
            await manager.stop()
            try:
                await asyncio.wait_for(manager_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        return recorder, ok
    finally:
        sim.stop(timeout=3.0)
        # Restore real config — must run even if the bot launch raised, or
        # the test SKU gets left in walmart_config.json on the next branch.
        try:
            if real_config_backup is not None:
                real_config_path.write_bytes(real_config_backup)
        except Exception:
            pass
        # Clean up env
        for k in ("WALMART_SIM_PROXY", "WALMART_CHECKOUT_API", "WALMART_PIE_CVV",
                  "WALMART_CVV", "CHECKOUT_MODE", "FINAL_PURCHASE",
                  "WALMART_TEST_PROFILE_DIR", "WALMART_TEST_LOOP_COOLDOWN"):
            os.environ.pop(k, None)


def _dump_message_trace(recorder: LatencyRecorder, max_lines: int = 30) -> None:
    """Print the captured status-callback trace for diagnostic purposes."""
    print("\n--- bot status trace (last %d) ---" % max_lines)
    for rel, msg in recorder.all_messages[-max_lines:]:
        print(f"  [{rel:6.2f}s] {msg[:140]}")
    print("--- end ---\n")


# ── runner ───────────────────────────────────────────────────────────────


async def main():
    print("=" * 72)
    print("Walmart bot — end-to-end purchase against sim (with queue)")
    print("=" * 72)

    # Scenario 1: non-queue baseline — proves the wiring works
    print("\n=== Scenario 1: non-queue baseline ===")
    try:
        recorder, ok = await run_scenario("non_queue", queue=False)
        _check("non-queue: purchase completed",
               ok and recorder.latency.pcid,
               detail=f"pcid={recorder.latency.pcid}")
        if ok and recorder.latency.pcid:
            print(f"  ✓ pcid received: {recorder.latency.pcid}")
            if recorder.latency.detect_to_pcid_ms > 0:
                print(f"  Latency (ATC → pcid): {recorder.latency.detect_to_pcid_ms:.0f}ms")
        else:
            _dump_message_trace(recorder)
    except Exception as e:
        _check("non-queue: harness ran without exception", False,
               detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}")

    # Scenario 2: queue path — the real validation
    print("\n=== Scenario 2: queue path ===")
    try:
        recorder, ok = await run_scenario("queue", queue=True)
        _check("queue: purchase completed",
               ok and recorder.latency.pcid,
               detail=f"pcid={recorder.latency.pcid}")
        if ok and recorder.latency.pcid:
            print(f"  ✓ pcid received: {recorder.latency.pcid}")
            if recorder.latency.queue_to_pcid_ms > 0:
                print(f"  Latency (queue_detected → pcid): "
                      f"{recorder.latency.queue_to_pcid_ms:.0f}ms")
        else:
            _dump_message_trace(recorder)
    except Exception as e:
        _check("queue: harness ran without exception", False,
               detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}")

    # ── summary ──────────────────────────────────────────────────────────
    print()
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:5]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
