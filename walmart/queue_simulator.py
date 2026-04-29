"""
Mock Walmart queue system for testing without waiting for live drop events.

This simulator injects fake queue overlays + passthrough signals into a zendriver
tab so you can test queue_handler.py logic without relying on actual Walmart
queue availability (which is late-night only, unpredictable, and hard to debug).

Usage:
    from walmart.queue_simulator import QueueSimulator

    sim = QueueSimulator(page)  # page is a zendriver tab

    # Option 1: Run a full simulated queue (join → wait → passthrough)
    await sim.run_full_queue(duration_seconds=5)  # User waits 5s in queue

    # Option 2: Manually inject queue states
    await sim.inject_queue_joined()
    await asyncio.sleep(2)
    await sim.inject_queue_passthrough()

    # Now your queue_handler.detect() will see the queue,
    # and wait_for_passthrough() will exit after the inject
"""

import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


class QueueSimulator:
    """
    Injects DOM elements + text content to simulate a Walmart queue overlay.
    Does NOT make real HTTP requests — just modifies the DOM.
    """

    QUEUE_OVERLAY_HTML = """
    <div id="queue-overlay-sim" style="position: fixed; top: 0; left: 0; z-index: 999999;
                                        width: 100%; height: 100%; background: rgba(0,0,0,0.5);
                                        display: flex; align-items: center; justify-content: center;">
        <div style="background: white; padding: 20px; border-radius: 8px; text-align: center;">
            <h2>You're in line</h2>
            <p id="queue-status-sim">Position: Waiting...</p>
            <button id="queue-hold-btn-sim" style="padding: 10px 20px; background: #0071ce; color: white; border: none; border-radius: 4px; cursor: pointer;">
                Hold my spot and Keep shopping
            </button>
        </div>
    </div>
    """

    PASSTHROUGH_INJECTION = """
    (function() {
        const overlay = document.getElementById('queue-overlay-sim');
        if (overlay) {
            const status = document.getElementById('queue-status-sim');
            if (status) {
                status.textContent = "It's your turn! Click below to checkout";
                // Trigger passthrough signal via DOM visibility
                const btn = document.getElementById('queue-hold-btn-sim');
                if (btn) btn.textContent = "It's your turn";
            }
        }
    })();
    """

    def __init__(self, page, status_callback=None):
        """
        Args:
            page: A zendriver tab object
            status_callback: Optional callback for status messages
        """
        self._page = page
        self._status_cb = status_callback or (lambda msg: None)
        self._queue_active = False

    async def inject_queue_joined(self):
        """
        Inject the queue overlay into the page to simulate the user joining the queue.
        This modifies the DOM but does NOT redirect the URL (real Walmart queue stays on /ip/).
        """
        self._status_cb("[QUEUE_SIM] Injecting queue joined state...")
        try:
            await self._page.apply(f"""
            (function() {{
                // Remove any existing simulator overlay
                const existing = document.getElementById('queue-overlay-sim');
                if (existing) existing.remove();

                // Inject new overlay
                const html = `{self.QUEUE_OVERLAY_HTML}`;
                document.body.insertAdjacentHTML('beforeend', html);

                // Inject button click handler
                const btn = document.getElementById('queue-hold-btn-sim');
                if (btn) {{
                    btn.onclick = function(e) {{
                        e.preventDefault();
                        console.log('[QUEUE_SIM] User clicked hold-my-spot button');
                    }};
                }}
            }})();
            """)
            self._queue_active = True
            self._status_cb("[QUEUE_SIM] Queue overlay injected")
            logger.info("[QUEUE_SIM] Queue joined state active")
        except Exception as e:
            logger.warning("[QUEUE_SIM] Failed to inject queue: %s", e)

    async def inject_queue_passthrough(self):
        """
        Update the queue overlay to signal passthrough (user's turn to checkout).
        This is what queue_handler.wait_for_passthrough() looks for.
        """
        self._status_cb("[QUEUE_SIM] Injecting queue passthrough signal...")
        try:
            await self._page.apply(self.PASSTHROUGH_INJECTION)
            self._status_cb("[QUEUE_SIM] Passthrough signal injected")
            logger.info("[QUEUE_SIM] Passthrough signal active")
        except Exception as e:
            logger.warning("[QUEUE_SIM] Failed to inject passthrough: %s", e)

    async def remove_queue(self):
        """Remove the queue overlay (simulates exiting the queue)."""
        try:
            await self._page.apply("""
            (function() {
                const overlay = document.getElementById('queue-overlay-sim');
                if (overlay) overlay.remove();
            })();
            """)
            self._queue_active = False
            self._status_cb("[QUEUE_SIM] Queue overlay removed")
            logger.info("[QUEUE_SIM] Queue removed")
        except Exception as e:
            logger.warning("[QUEUE_SIM] Failed to remove queue: %s", e)

    async def run_full_queue(self, duration_seconds: float = 5.0):
        """
        Run a complete simulated queue experience:
        1. Inject "joined" state
        2. Wait for duration_seconds (simulating queue time)
        3. Inject passthrough signal

        Args:
            duration_seconds: How long to simulate waiting in queue
        """
        self._status_cb(f"[QUEUE_SIM] Starting full queue simulation ({duration_seconds}s)...")

        await self.inject_queue_joined()
        await asyncio.sleep(duration_seconds)
        await self.inject_queue_passthrough()

        self._status_cb("[QUEUE_SIM] Full queue simulation complete")
        logger.info("[QUEUE_SIM] Full queue cycle complete after %.1fs", duration_seconds)

    async def test_queue_handler_integration(self, queue_handler_instance):
        """
        Integration test: Run a simulated queue with your actual QueueHandler
        to verify detect() and wait_for_passthrough() work correctly.

        Args:
            queue_handler_instance: A QueueHandler object bound to the same page

        Returns:
            (detected: bool, passthrough: bool) — did detect and wait work?
        """
        logger.info("[QUEUE_SIM] Starting integration test with QueueHandler...")

        # Phase 1: Inject queue, run detect()
        await self.inject_queue_joined()
        await asyncio.sleep(0.5)

        detected = await queue_handler_instance.detect()
        self._status_cb(f"[QUEUE_SIM] detect() returned: {detected}")

        if not detected:
            logger.warning("[QUEUE_SIM] QueueHandler.detect() failed to find queue overlay")
            return (False, False)

        # Phase 2: Spawn wait_for_passthrough() in background, then trigger passthrough
        wait_task = asyncio.create_task(queue_handler_instance.wait_for_passthrough())

        # Simulate 2s of queue time before passthrough
        await asyncio.sleep(2.0)
        await self.inject_queue_passthrough()

        # Wait for the passthrough detection (with timeout)
        try:
            passthrough = await asyncio.wait_for(wait_task, timeout=10.0)
            self._status_cb(f"[QUEUE_SIM] wait_for_passthrough() returned: {passthrough}")
            logger.info("[QUEUE_SIM] Integration test PASSED: detect=%s, passthrough=%s",
                       detected, passthrough)
            return (detected, passthrough)
        except asyncio.TimeoutError:
            logger.error("[QUEUE_SIM] wait_for_passthrough() timed out")
            self._status_cb("[QUEUE_SIM] Integration test FAILED: wait_for_passthrough timeout")
            return (detected, False)
        finally:
            await self.remove_queue()
