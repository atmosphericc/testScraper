"""
Walmart virtual queue handler.

Walmart deploys a proprietary virtual queue for high-demand drops (e.g. Pokemon
Wednesday releases ~9PM EST). Key confirmed mechanics:

  - The queue is an OVERLAY on the product page — URL stays at /ip/... (no redirect)
  - A "Hold my spot and Keep shopping" button appears when demand is high
  - After clicking, a floating widget at bottom-left shows "You're in line"
  - Users can continue browsing while in queue
  - Walmart releases users in WAVES (not sequentially) — timers shown are placeholders
  - When it's your turn, the floating widget updates with an action prompt
  - Clicking it (or the normal ATC button becoming active) is the pass-through signal

This module:
  1. Detects if the queue overlay is present on the current product page
  2. Clicks the "Hold my spot" button to join the queue if found
  3. Polls every QUEUE_POLL_INTERVAL seconds for pass-through
  4. On pass-through: clicks any notification widget, then confirms ATC is active
  5. Times out after QUEUE_TIMEOUT seconds if never released
"""

import asyncio
import json
import logging
import random
import time
from typing import Optional, Callable

from .config import QUEUE_POLL_INTERVAL, QUEUE_TIMEOUT
from .logging_manager import get_walmart_logger, log_activity

logger = logging.getLogger(__name__)
walmart_logger = get_walmart_logger()

# Button text patterns to click in order to JOIN the queue
# Note: :has-text() is patchright-specific; we use XPath in _find_entry_button instead
QUEUE_ENTRY_SELECTORS = [
    'Hold my spot and Keep shopping',
    'Hold my spot',
    'Keep my spot',
    'Join the queue',
    'Get in line',
]

# CSS-only queue entry selectors (no text matching needed)
QUEUE_ENTRY_CSS_SELECTORS = [
    '[data-automation-id*="queue-entry"]',
    '[data-automation-id*="hold-spot"]',
]

# Text patterns that confirm the user IS in the queue (overlay is active)
QUEUE_ACTIVE_SIGNALS = [
    "you're in line",
    "you are in line",
    "your place in line",
    "virtual queue",
    "high demand",
    "hold my spot",
    "keep my spot",
    "you're next",
]

# Text patterns in the floating widget that signal it's the user's turn
QUEUE_PASSTHROUGH_SIGNALS = [
    "it's your turn",
    "your turn",
    "time to checkout",
    "checkout now",
    "you're up",
    "ready to checkout",
    "complete your purchase",
]

# Text patterns for passthrough click buttons (used with XPath)
PASSTHROUGH_CLICK_TEXTS = [
    "It's your turn",
    "Your turn",
    "Checkout now",
    "Time to checkout",
    "Complete your purchase",
]

# ATC button selectors — becoming active is the primary pass-through signal
ATC_SELECTORS = [
    'button[data-automation-id="atc"]',  # Modern Walmart selector (primary)
    'button[data-automation-id="add-to-cart-btn"]',  # Legacy fallback
    'button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]',
]

# ATC button text patterns (used with XPath)
ATC_XPATH_TEXTS = [
    "Add to cart",
    "Add to Cart",
]


class QueueHandler:
    """
    Detects and handles Walmart's virtual queue overlay.

    The queue appears as an overlay on the product page (URL never changes).
    The bot must click the "Hold my spot" button to join, then poll for
    pass-through (ATC button becoming active or a "your turn" widget appearing).

    Usage:
        handler = QueueHandler(page, status_callback)

        # Check for queue and join it
        in_queue = await handler.detect()
        if not in_queue:
            # Try to join if entry button is visible
            in_queue = await handler.join_queue()

        if in_queue:
            passed_through = await handler.wait_for_passthrough()
            if not passed_through:
                # timed out — caller should reset / try again
    """

    def __init__(
        self,
        page,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self._page = page
        self._status_cb = status_callback or (lambda msg: None)

    async def detect(self) -> bool:
        """
        Check if the current browser page has an active Walmart queue overlay.
        Returns True if queue signals are present (already in queue or entry button visible).

        NOTE: URL-based detection is intentionally omitted — Walmart's queue keeps
        the user on the /ip/ product URL and uses a page overlay, not a redirect.

        Uses a single JS evaluation to avoid multiple browser round-trips (was ~6s,
        now <200ms). Falls back to multi-round-trip _find_entry_button only if the
        fast JS check finds a text signal but no visible button.
        """
        try:
            js_signals = json.dumps(QUEUE_ACTIVE_SIGNALS)
            js_entry_texts = json.dumps(QUEUE_ENTRY_SELECTORS)
            js_entry_css = json.dumps(QUEUE_ENTRY_CSS_SELECTORS)
            result = await self._page.evaluate(f"""() => {{
                const body = document.body ? document.body.innerText.toLowerCase() : '';
                const signals = {js_signals};
                const matchedSignal = signals.find(s => body.includes(s));
                if (!matchedSignal) return {{ queue: false }};

                // Queue text found — check for entry buttons in same JS call
                const entryTexts = {js_entry_texts};
                const entryCss = {js_entry_css};

                // Check text-based buttons
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {{
                    const t = btn.textContent || '';
                    if (entryTexts.some(et => t.includes(et))) {{
                        if (btn.offsetWidth || btn.offsetHeight) {{
                            return {{ queue: true, via: 'text:' + matchedSignal, hasButton: true }};
                        }}
                    }}
                }}

                // Check CSS-only selectors
                for (const sel of entryCss) {{
                    const el = document.querySelector(sel);
                    if (el && (el.offsetWidth || el.offsetHeight)) {{
                        return {{ queue: true, via: 'css:' + matchedSignal, hasButton: true }};
                    }}
                }}

                // Text signal present but no visible button
                return {{ queue: true, via: 'text-only:' + matchedSignal, hasButton: false }};
            }}""")

            if result and result.get('queue'):
                logger.info("[QUEUE] Queue detected via: %s (button=%s)",
                            result.get('via'), result.get('hasButton'))
                return True

        except Exception as e:
            logger.warning("[QUEUE] Detection error: %s", e)

        return False

    async def join_queue(self) -> bool:
        """
        Attempt to click the "Hold my spot" button to join the queue.
        Returns True if the button was found and clicked AND queue entry confirmed.
        Returns False if no entry button found (no queue active).
        """
        entry_btn = await self._find_entry_button()
        if not entry_btn:
            return False

        try:
            self._status_cb("[QUEUE] Found 'Hold my spot' button — joining queue...")
            logger.info("[QUEUE] Clicking queue entry button")
            await self._cdp_click_element(entry_btn)
            await asyncio.sleep(random.uniform(1.5, 2.5))  # wait for queue overlay to update

            # Verify we're now in the queue
            in_queue = await self._confirm_in_queue()
            if in_queue:
                self._status_cb("[QUEUE] Queue joined — 'You're in line'")
                logger.info("[QUEUE] Successfully joined queue")
                return True
            else:
                logger.warning("[QUEUE] Clicked entry button but queue confirmation not seen")
                return False

        except Exception as e:
            logger.warning("[QUEUE] Error joining queue: %s", e)
            return False

    async def wait_for_passthrough(self) -> bool:
        """
        Poll until the queue releases the user or QUEUE_TIMEOUT is exceeded.
        Returns True if passed through, False on timeout.

        Pass-through is detected when:
          - A "your turn" floating widget appears (click it), then ATC is active, OR
          - ATC button becomes directly active/available (primary reliable signal)

        The URL will NOT change — we stay on the /ip/ product page throughout.
        """
        start = time.monotonic()
        self._status_cb("[QUEUE] In queue — waiting for pass-through...")
        logger.info("[QUEUE] Entered wait_for_passthrough")

        # Poll every 1s for fast passthrough detection; emit status log every 30s.
        # Prior 5s interval meant up to 5.5s of lost time after passthrough opened.
        _FAST_POLL = 1.0
        _STATUS_LOG_EVERY = 30.0
        last_status_log = time.monotonic()

        while True:
            elapsed = time.monotonic() - start

            if elapsed > QUEUE_TIMEOUT:
                self._status_cb(f"[QUEUE] Timed out after {int(elapsed)}s")
                logger.warning("[QUEUE] Queue timeout after %.0fs", elapsed)
                return False

            await asyncio.sleep(_FAST_POLL)

            try:
                # First: check for the "it's your turn" floating widget and click it
                widget_clicked = await self._check_passthrough_widget()
                if widget_clicked:
                    self._status_cb("[QUEUE] 'Your turn' widget found — clicked!")
                    logger.info("[QUEUE] Passthrough widget clicked after %.0fs", elapsed)
                    await asyncio.sleep(random.uniform(0.8, 1.5))  # let page respond

                # Primary signal: ATC button is now active (2s timeout — post-passthrough ATC appears in <500ms)
                atc_available = await self._wait_for_atc(timeout=2)
                if atc_available:
                    self._status_cb(f"[QUEUE] Passed through after {int(elapsed)}s!")
                    logger.info("[QUEUE] Pass-through confirmed — ATC active after %.0fs", elapsed)
                    return True

                # Emit status log at 30s intervals to avoid spam
                if time.monotonic() - last_status_log >= _STATUS_LOG_EVERY:
                    still_queued = await self.detect()
                    if still_queued:
                        mins_waited = int(elapsed / 60)
                        secs_waited = int(elapsed % 60)
                        self._status_cb(
                            f"[QUEUE] Still in queue ({mins_waited}m {secs_waited}s elapsed)"
                        )
                    else:
                        # Queue signals gone but ATC not yet active — may be transitioning
                        logger.info(
                            "[QUEUE] Queue signals gone but ATC not yet active — page may be transitioning or queue was cancelled"
                        )
                        if elapsed > 60:
                            logger.warning(
                                "[QUEUE] Queue signals disappeared after %ds with no ATC — queue may have been cancelled or item sold out",
                                int(elapsed),
                            )
                    last_status_log = time.monotonic()

            except Exception as e:
                logger.warning("[QUEUE] Poll error: %s", e)

        # Guard — loop only exits via return statements above
        return False

    async def enter_queue(self, item_url: str) -> bool:
        """
        Navigate to the product URL, then attempt to join the queue if present.
        Returns True if queued successfully.
        """
        try:
            self._status_cb(f"[QUEUE] Navigating to enter queue: {item_url}")
            await self._page.get(item_url)
            await asyncio.sleep(2)

            # Try to join if the entry button is visible
            in_queue = await self.join_queue()
            if not in_queue:
                # Entry button not found — check if already in queue
                in_queue = await self.detect()
            return in_queue

        except Exception as e:
            logger.warning("[QUEUE] Error entering queue: %s", e)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cdp_click_element(self, element) -> bool:
        """
        Click an element via CDP mouse trajectory instead of JS el.click().
        PerimeterX detects synthetic JS clicks by the absence of prior mousemove events.
        Falls back to element.click() if CDP dispatch fails.
        """
        try:
            from zendriver.cdp import input_ as cdp_input
            rect = await element.apply("""(e) => {
                e.scrollIntoView({ behavior: 'instant', block: 'center' });
                const r = e.getBoundingClientRect();
                return { x: r.left, y: r.top, w: r.width, h: r.height };
            }""")
            if not rect or rect.get('w', 0) <= 0 or rect.get('h', 0) <= 0:
                await element.click()
                return True
            x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
            y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseMoved", x=x, y=y, pointer_type="mouse"))
            await asyncio.sleep(random.uniform(0.02, 0.06))
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mousePressed", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=1,
                click_count=1, pointer_type="mouse"))
            await asyncio.sleep(random.uniform(0.04, 0.10))
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseReleased", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=0,
                click_count=1, pointer_type="mouse"))
            return True
        except Exception as e:
            logger.warning("[QUEUE] CDP click failed: %s — falling back to JS click", e)
            try:
                await element.click()
            except Exception:
                pass
            return True

    async def _cdp_click_at(self, x: float, y: float) -> bool:
        """Dispatch CDP mouse click at raw coordinates (for JS-evaluated positions)."""
        try:
            from zendriver.cdp import input_ as cdp_input
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseMoved", x=x, y=y, pointer_type="mouse"))
            await asyncio.sleep(random.uniform(0.02, 0.06))
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mousePressed", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=1,
                click_count=1, pointer_type="mouse"))
            await asyncio.sleep(random.uniform(0.04, 0.10))
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseReleased", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=0,
                click_count=1, pointer_type="mouse"))
            return True
        except Exception as e:
            logger.warning("[QUEUE] CDP click_at failed: %s", e)
            return False

    async def _find_entry_button(self):
        """Find the 'Hold my spot' / queue entry button if visible on the page."""
        deadline = time.monotonic() + 5.0

        # Try text-based XPath selectors first
        for text in QUEUE_ENTRY_SELECTORS:
            if time.monotonic() > deadline:
                break
            try:
                els = await self._page.xpath(f'//button[contains(., "{text}")]')
                if els:
                    btn = els[0]
                    is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                    if is_vis:
                        return btn
            except Exception:
                continue

        # Try CSS-only selectors (no text matching)
        for selector in QUEUE_ENTRY_CSS_SELECTORS:
            if time.monotonic() > deadline:
                break
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                    if is_vis:
                        return btn
            except Exception:
                continue

        return None

    async def _confirm_in_queue(self) -> bool:
        """Check page text for confirmation that we are now in the queue."""
        try:
            body_text = await self._page.evaluate("document.body.innerText")
            body_lower = body_text.lower() if body_text else ""
            for signal in QUEUE_ACTIVE_SIGNALS:
                if signal in body_lower:
                    return True
        except Exception:
            pass
        return False

    async def _check_passthrough_widget(self) -> bool:
        """
        Look for the floating 'it's your turn' notification widget.
        If found, click it. Returns True if clicked.

        Since exact DOM selectors for Walmart's floating queue widget are not
        publicly documented, we use text-based matching and a JS fallback to
        find fixed/absolute positioned elements near the viewport bottom.
        """
        # Try known text-based XPath selectors first
        for text in PASSTHROUGH_CLICK_TEXTS:
            try:
                els = await self._page.xpath(f'//button[contains(., "{text}")]')
                if els:
                    btn = els[0]
                    is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                    if is_vis:
                        await self._cdp_click_element(btn)
                        return True
            except Exception:
                continue

        # Try stable attribute selectors — class names are hashed on every Walmart deploy
        # Only passthrough/notification patterns here — do NOT include queue-entry buttons
        for css_pattern in ['[data-automation-id*="queue"]', '[data-testid*="queue"]']:
            try:
                els = await self._page.query_selector_all(css_pattern)
                for el in els:
                    try:
                        text = await el.apply("(e) => e.innerText")
                        if text and "turn" in text.lower():
                            is_vis = await el.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                            if is_vis:
                                await self._cdp_click_element(el)
                                return True
                    except Exception:
                        continue
            except Exception:
                continue

        # Fallback: scan page text for passthrough signals, then return coordinates
        # from JS so we can dispatch a real CDP click (not synthetic el.click())
        try:
            body_text = await self._page.evaluate("document.body.innerText")
            body_lower = body_text.lower() if body_text else ""
            has_passthrough_text = any(
                signal in body_lower for signal in QUEUE_PASSTHROUGH_SIGNALS
            )
            if has_passthrough_text:
                # Find button coordinates near the bottom of the viewport, return them
                # to Python so we can use CDP mouse dispatch instead of el.click()
                result = await self._page.evaluate("""() => {
                    const vh = window.innerHeight;
                    const allBtns = document.querySelectorAll('button, [role="button"], a[href]');
                    for (const el of allBtns) {
                        const rect = el.getBoundingClientRect();
                        if (rect.top > vh * 0.6 && rect.bottom <= vh + 10 &&
                            rect.width > 0 && rect.height > 0) {
                            const text = el.textContent.toLowerCase();
                            const keywords = ['turn', 'checkout', 'purchase', 'queue'];
                            if (keywords.some(k => text.includes(k))) {
                                return {
                                    found: true,
                                    x: rect.left + rect.width / 2,
                                    y: rect.top + rect.height / 2
                                };
                            }
                        }
                    }
                    return { found: false };
                }""")
                if result and result.get('found'):
                    await self._cdp_click_at(result['x'], result['y'])
                    return True
        except Exception as e:
            logger.debug("[QUEUE] JS widget scan error: %s", e)

        return False

    async def _wait_for_atc(self, timeout: float = 5) -> bool:
        """
        Wait briefly for ATC button to appear and be enabled.
        Returns True if found and enabled within timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Try CSS selectors first
            for selector in ATC_SELECTORS:
                try:
                    btn = await self._page.query_selector(selector)
                    if btn:
                        is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                        if is_vis:
                            disabled = await btn.apply("(e) => e.getAttribute('disabled')")
                            aria_disabled = await btn.apply("(e) => e.getAttribute('aria-disabled')")
                            if disabled is None and aria_disabled != "true":
                                return True
                except Exception:
                    pass

            # Try XPath text-based selectors
            for text in ATC_XPATH_TEXTS:
                try:
                    els = await self._page.xpath(f'//button[contains(., "{text}")]')
                    if els:
                        btn = els[0]
                        is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                        if is_vis:
                            disabled = await btn.apply("(e) => e.getAttribute('disabled')")
                            aria_disabled = await btn.apply("(e) => e.getAttribute('aria-disabled')")
                            if disabled is None and aria_disabled != "true":
                                return True
                except Exception:
                    pass

            await asyncio.sleep(0.5)
        return False
