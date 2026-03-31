"""
Walmart self-healing agent.

Wraps WalmartPurchaseManager with an automatic recovery loop:

  1. Purchase attempt fails
  2. Agent captures the live page HTML before closing the browser
  3. Browser + manager are stopped
  4. Rule-based diagnostician categorizes the failure from logs
  5. For WRONG_SELECTOR: HtmlSelectorPatcher parses the captured HTML,
     finds button candidates by label text, extracts stable attributes
     (data-automation-id, data-testid, id, name, aria-label), builds new
     selectors, and prepends them to the relevant selector lists in
     purchase_executor.py / queue_handler.py
  6. All other categories: appropriate non-code response (rotate proxy,
     bump timeouts, etc.)
  7. Browser + manager restart, session re-logs in
  8. Stock monitoring resumes — retries on next in-stock signal

Failure categories and responses
─────────────────────────────────
  WRONG_SELECTOR    → parse HTML → find button → patch selector list
  ANTIBOT_BLOCK     → no code change, rotate proxy on restart
  QUEUE_TIMEOUT     → restart monitoring (normal, no fix needed)
  OUT_OF_STOCK      → restart monitoring (lost the race)
  LOGIN_FAILURE     → stop (needs human intervention)
  NETWORK_TIMEOUT   → bump timeout= values in executor + session manager
  UNKNOWN           → log screenshot path, restart without patching

Safety constraints
──────────────────
  - Only modifies files inside walmart/ — never touches src/ or app.py
  - Validates Python syntax (ast.parse) before writing any patch
  - Exactly 1 HTML match required — 0 or 2+ candidates → skip patch
  - Max MAX_PATCHES_PER_SESSION patches per failure streak (resets on OOS/queue-timeout
    so each new drop event gets a fresh patch budget); restarts are unlimited
  - Minimum RESTART_DELAY seconds between restarts
  - All patches logged to walmart/logs/patches.log with unified diff
"""

import ast
import asyncio
import difflib
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

from .config import LOGS_DIR
from .purchase_manager import WalmartPurchaseManager, PurchaseState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

MAX_PATCHES_PER_SESSION = 5
RESTART_DELAY = 15           # seconds between restarts
LOG_TAIL_LINES = 150
PATCH_LOG_FILE = f"{LOGS_DIR}/patches.log"
HTML_DUMP_FILE = f"{LOGS_DIR}/last_failure_page.html"

# ---------------------------------------------------------------------------
# Failure categories
# ---------------------------------------------------------------------------

class FailureCategory:
    WRONG_SELECTOR  = "WRONG_SELECTOR"
    ANTIBOT_BLOCK   = "ANTIBOT_BLOCK"
    QUEUE_TIMEOUT   = "QUEUE_TIMEOUT"
    OUT_OF_STOCK    = "OUT_OF_STOCK"
    LOGIN_FAILURE   = "LOGIN_FAILURE"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    UNKNOWN         = "UNKNOWN"


# ---------------------------------------------------------------------------
# Rule-based log diagnostician
# ---------------------------------------------------------------------------

class LogDiagnostician:
    """Classifies failure category from recent log lines. No API calls."""

    _RULES = [
        (re.compile(r"403|429|captcha|perimeterx|px.*block|akamai.*block|access denied|robot|human.*verif", re.I), FailureCategory.ANTIBOT_BLOCK),
        (re.compile(r"login.*fail|still on login|could not find.*email|could not find.*password|login error", re.I), FailureCategory.LOGIN_FAILURE),
        (re.compile(r"queue.*timeout|timed out.*queue|queue.*timed out", re.I), FailureCategory.QUEUE_TIMEOUT),
        (re.compile(r"cart.*empty|empty.*cart|out.of.stock|item not found in cart|oos", re.I), FailureCategory.OUT_OF_STOCK),
        (re.compile(r"not found|no.*button|selector.*not|element.*not|locator.*not|no_atc|no_checkout|no_place_order", re.I), FailureCategory.WRONG_SELECTOR),
        (re.compile(r"timeout.*exceeded|timeouterror|connection.*timeout|read.*timed out|network.*error|err_network", re.I), FailureCategory.NETWORK_TIMEOUT),
    ]

    def diagnose(self, log_lines: list[str]) -> tuple[str, str]:
        """Returns (FailureCategory, human_readable_reason). Most recent line wins."""
        for line in reversed(log_lines):
            for pattern, category in self._RULES:
                if pattern.search(line):
                    return category, line.strip()
        return FailureCategory.UNKNOWN, "No matching pattern found in recent logs"


# ---------------------------------------------------------------------------
# HTML button extractor (stdlib html.parser — no dependencies)
# ---------------------------------------------------------------------------

class _ButtonExtractor(HTMLParser):
    """
    Parses HTML and collects all <button> and <a> elements with their
    attributes and inner text. Uses stdlib html.parser — no pip install needed.
    """

    def __init__(self):
        super().__init__()
        self.buttons: list[dict] = []
        self._current: Optional[dict] = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in ("button", "a", "input"):
            self._current = {
                "tag": tag,
                "attrs": dict(attrs),
                "text": "",
                "depth": self._depth,
            }
            self._depth += 1
        elif self._current is not None:
            self._depth += 1

    def handle_endtag(self, tag: str):
        if self._current is not None:
            self._depth -= 1
            if tag in ("button", "a", "input") and self._depth <= self._current["depth"]:
                self.buttons.append(self._current)
                self._current = None

    def handle_data(self, data: str):
        if self._current is not None:
            self._current["text"] += data


# ---------------------------------------------------------------------------
# HTML selector patcher
# ---------------------------------------------------------------------------

class HtmlSelectorPatcher:
    """
    Reads captured page HTML, finds button candidates for a given role
    (ATC / checkout / place-order / CVV), extracts the most stable attribute
    as a CSS selector, and prepends it to the relevant selector list in
    purchase_executor.py or queue_handler.py.

    Confidence rule: exactly 1 candidate must match — 0 or 2+ → skip patch.
    """

    WALMART_DIR = Path("walmart")

    # Maps selector list variable name → label texts to search for in HTML
    SELECTOR_TARGETS = {
        # variable name in purchase_executor.py  →  label texts
        "ATC_SELECTORS": [
            "add to cart", "add to Cart", "add item to cart",
        ],
        "CHECKOUT_SELECTORS": [
            "checkout", "proceed to checkout", "go to checkout",
        ],
        "PLACE_ORDER_SELECTORS": [
            "place order", "place my order", "submit order", "confirm order",
        ],
        "CVV_SELECTORS": [
            "cvv", "cvc", "security code", "card verification",
        ],
        # queue_handler.py
        "QUEUE_ENTRY_SELECTORS": [
            "hold my spot", "keep my spot", "join queue", "get in line",
        ],
    }

    # Which file each variable lives in
    _FILE_MAP = {
        "ATC_SELECTORS":          "purchase_executor.py",
        "CHECKOUT_SELECTORS":     "purchase_executor.py",
        "PLACE_ORDER_SELECTORS":  "purchase_executor.py",
        "CVV_SELECTORS":          "purchase_executor.py",
        "QUEUE_ENTRY_SELECTORS":  "queue_handler.py",
    }

    # Attribute priority — most stable first
    _ATTR_PRIORITY = [
        "data-automation-id",
        "data-testid",
        "data-tl-id",
        "id",
        "name",
        "aria-label",
        "type",
    ]

    def patch_from_html(self, html: str) -> bool:
        """
        Parse HTML, try to patch each selector group that might be broken.
        Returns True if at least one selector list was successfully patched.
        """
        if not html:
            logger.warning("[PATCHER] No HTML to parse")
            return False

        extractor = _ButtonExtractor()
        try:
            extractor.feed(html)
        except Exception as e:
            logger.warning("[PATCHER] HTML parse error — cannot extract selectors: %s", e)
            return False

        buttons = extractor.buttons
        logger.info("[PATCHER] Extracted %d button/link elements from page HTML", len(buttons))

        patched_any = False
        for var_name, label_texts in self.SELECTOR_TARGETS.items():
            result = self._find_candidate(buttons, label_texts)
            if result is None:
                continue  # no match or ambiguous — skip
            new_selector = result
            file_name = self._FILE_MAP[var_name]
            if self._prepend_selector(var_name, new_selector, file_name):
                patched_any = True
                logger.info("[PATCHER] Patched %s in %s with: %s", var_name, file_name, new_selector)

        return patched_any

    def _find_candidate(self, buttons: list[dict], label_texts: list[str]) -> Optional[str]:
        """
        Find buttons whose text matches one of label_texts (case-insensitive).
        Returns a CSS selector string if exactly 1 match found, else None.
        """
        matches = []
        for btn in buttons:
            btn_text = btn["text"].strip().lower()
            if any(label.lower() in btn_text or btn_text in label.lower()
                   for label in label_texts):
                matches.append(btn)

        if len(matches) == 0:
            return None  # not found on this page — don't patch
        if len(matches) > 1:
            # Try to narrow down: prefer buttons over links, and exact text match
            exact = [b for b in matches if b["text"].strip().lower() in
                     [l.lower() for l in label_texts]]
            if len(exact) == 1:
                matches = exact
            else:
                logger.debug("[PATCHER] Ambiguous match (%d candidates) — skipping", len(matches))
                return None

        btn = matches[0]
        logger.info("[PATCHER] Selected candidate: tag=%s text='%s' attrs=%s",
                    btn['tag'], btn['text'].strip()[:60], btn['attrs'])
        return self._build_selector(btn)

    def _build_selector(self, btn: dict) -> str:
        """
        Build the most specific CSS selector from a button's attributes.
        Priority: stable data attributes > id > aria-label > text fallback.
        """
        tag = btn["tag"]
        attrs = btn["attrs"]

        for attr in self._ATTR_PRIORITY:
            val = attrs.get(attr, "").strip()
            if val:
                # Escape quotes in value
                val_escaped = val.replace('"', '\\"')
                return f'{tag}[{attr}="{val_escaped}"]'

        # Fallback: text-based selector (least stable but better than nothing)
        text = btn["text"].strip()
        if text:
            text_escaped = text.replace('"', '\\"')
            return f'{tag}:has-text("{text_escaped}")'

        return ""

    def _prepend_selector(self, var_name: str, new_selector: str, file_name: str) -> bool:
        """
        Prepend new_selector to the Python list named var_name in file_name.
        Validates syntax before writing. Logs the diff.
        """
        if not new_selector:
            return False

        path = self.WALMART_DIR / file_name
        if not path.exists():
            logger.warning("[PATCHER] File not found: %s", path)
            return False

        original = path.read_text()

        # Duplicate selector guard
        if new_selector in original:
            logger.info("[PATCHER] Selector already present — skipping duplicate patch")
            return False

        # Match the list assignment, e.g.:
        #   ATC_SELECTORS = [
        #       'button[data-automation-id="add-to-cart-btn"]',
        # We insert the new selector as the first element.
        pattern = re.compile(
            r'(' + re.escape(var_name) + r'\s*=\s*\[)\s*\n(\s*)',
            re.MULTILINE,
        )
        match = pattern.search(original)
        if not match:
            logger.warning("[PATCHER] Could not locate %s list in %s", var_name, file_name)
            return False

        # Build the insertion: add new selector as first item with same indentation
        indent = match.group(2)
        insertion = f'{match.group(1)}\n{indent}"{new_selector}",  # auto-patched {datetime.now().strftime("%Y-%m-%d %H:%M")}\n{indent}'
        updated = original[:match.start()] + insertion + original[match.end():]

        # Validate syntax
        try:
            ast.parse(updated)
        except SyntaxError as e:
            logger.error("[PATCHER] Patched %s failed syntax check: %s", file_name, e)
            return False

        path.write_text(updated)
        self._log_diff(path, original, updated)
        return True

    def _log_diff(self, path: Path, before: str, after: str):
        try:
            Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            ))
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(PATCH_LOG_FILE, "a") as f:
                f.write(f"\n{'='*60}\n[{ts}] Selector patch: {path}\n{'='*60}\n")
                f.write(diff or "(no textual diff)\n")
        except Exception as e:
            logger.warning("[PATCHER] Failed to write patch log: %s", e)


# ---------------------------------------------------------------------------
# Code patcher (non-selector patches)
# ---------------------------------------------------------------------------

class CodePatcher:
    """Applies non-selector patches (timeout bumps). Validates syntax before writing."""

    WALMART_DIR = Path("walmart")

    def patch_network_timeout(self) -> bool:
        """Bump timeout=NNNNN values by 50%, ceiling 60 000 ms."""
        targets = [
            self.WALMART_DIR / "purchase_executor.py",
            self.WALMART_DIR / "session_manager.py",
        ]
        patched_any = False
        for path in targets:
            if not path.exists():
                continue
            original = path.read_text()

            def bump(m):
                val = int(m.group(1))
                if val >= 45_000:
                    return m.group(0)  # already bumped at least once — leave unchanged
                new_val = min(int(val * 1.5), 60_000)
                return m.group(0).replace(str(val), str(new_val))

            updated = re.sub(r'timeout=(\d{4,})', bump, original)
            if updated != original and self._validate_and_write(path, original, updated):
                patched_any = True
        return patched_any

    def _validate_and_write(self, path: Path, original: str, updated: str) -> bool:
        try:
            ast.parse(updated)
        except SyntaxError as e:
            logger.error("[HEALER] Patch for %s failed syntax check: %s", path, e)
            return False
        path.write_text(updated)
        self._log_diff(path, original, updated)
        logger.info("[HEALER] Patched %s", path)
        return True

    def _log_diff(self, path: Path, before: str, after: str):
        try:
            Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            ))
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(PATCH_LOG_FILE, "a") as f:
                f.write(f"\n{'='*60}\n[{ts}] Timeout patch: {path}\n{'='*60}\n")
                f.write(diff or "(no textual diff)\n")
        except Exception as e:
            logger.warning("[HEALER] Failed to write patch log: %s", e)


# ---------------------------------------------------------------------------
# Self-healing agent
# ---------------------------------------------------------------------------

class SelfHealingAgent:
    """
    Top-level runner that wraps WalmartPurchaseManager with automatic recovery.

    Usage (same API as WalmartPurchaseManager):
        agent = SelfHealingAgent(status_callback=cb)
        await agent.start(email="...", password="...")
        await agent.stop()
    """

    def __init__(self, status_callback: Optional[Callable[[str], None]] = None):
        self._status_cb = status_callback or (lambda msg: None)
        self._diagnostician = LogDiagnostician()
        self._html_patcher = HtmlSelectorPatcher()
        self._code_patcher = CodePatcher()
        self._manager: Optional[WalmartPurchaseManager] = None
        self._running = False
        self._patches_this_session = 0
        self._email = ""
        self._password = ""
        self._lock = threading.Lock()
        self._log_buffer: deque = deque(maxlen=500)
        self._log_lock = threading.Lock()
        self._captured_html: str = ""   # page HTML captured before browser close
        self._failure_event: Optional[asyncio.Event] = None

        Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, email: str, password: str):
        self._email = email
        self._password = password
        self._running = True
        self._status_cb("[HEALER] Self-healing agent started")
        await self._run_manager_with_recovery()

    async def stop(self):
        self._running = False
        await self._stop_manager()
        self._status_cb("[HEALER] Self-healing agent stopped")

    def get_status(self) -> dict:
        if self._manager:
            status = self._manager.get_status()
            status["healer_patches"] = self._patches_this_session
            return status
        return {"running": False, "healer_patches": self._patches_this_session}

    def get_activity_log(self) -> list[dict]:
        if self._manager:
            return self._manager.get_activity_log()
        return []

    # ------------------------------------------------------------------
    # Core recovery loop
    # ------------------------------------------------------------------

    async def _run_manager_with_recovery(self):
        while self._running:
            try:
                await self._start_manager()
                await self._wait_for_failure()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[HEALER] Unexpected error: %s", e)
                self._status_cb(f"[HEALER] Unexpected crash: {e}")

            if not self._running:
                break

            # Capture page HTML BEFORE closing the browser
            await self._capture_page_html()

            self._status_cb("[HEALER] Stopping browser for diagnosis...")
            await self._stop_manager()
            await asyncio.sleep(2)

            category, reason = self._diagnose()
            self._status_cb(f"[HEALER] Diagnosis: {category} — {reason[:120]}")
            logger.info("[HEALER] Failure: %s | %s", category, reason)

            should_restart = await self._handle_category(category, reason)
            if not should_restart:
                self._status_cb("[HEALER] Login failure — stopping. Check credentials.")
                break

            self._status_cb(f"[HEALER] Restarting in {RESTART_DELAY}s...")
            await asyncio.sleep(RESTART_DELAY)

        self._status_cb("[HEALER] Recovery loop exited")

    async def _start_manager(self):
        self._failure_event = asyncio.Event()
        self._captured_html = ""

        self._manager = WalmartPurchaseManager(status_callback=self._on_status_message)

        # Instrument _run_purchase to detect failures
        original_run_purchase = self._manager._run_purchase

        async def _instrumented(item_id, item_url, name):
            try:
                await original_run_purchase(item_id, item_url, name)
            except Exception as exc:
                logger.warning("[HEALER] _run_purchase raised exception for %s: %s", item_id, exc)
                if self._failure_event and not self._failure_event.is_set():
                    self._failure_event.set()
                raise
            # Check state immediately after _run_purchase returns — before the 5s
            # sleep in its finally block resets FAILED back to MONITORING.
            # _run_purchase sets state to FAILED (inside its except block, under lock)
            # before sleeping, so we can read it here while it's still FAILED.
            with self._manager._lock:
                state = self._manager._state.get(item_id, "")
            if state in (PurchaseState.FAILED, PurchaseState.MONITORING):
                # MONITORING means the finally block already ran and reset state —
                # check the consecutive_failures counter to detect a real failure.
                failed = (state == PurchaseState.FAILED or
                          self._manager._consecutive_failures > 0)
            else:
                failed = False
            if failed:
                logger.info("[HEALER] Purchase failed for %s — triggering recovery", item_id)
                if self._failure_event and not self._failure_event.is_set():
                    self._failure_event.set()

        self._manager._run_purchase = _instrumented
        await self._manager.start(email=self._email, password=self._password)

    async def _stop_manager(self):
        if self._manager:
            try:
                await self._manager.stop()
            except Exception as e:
                logger.warning("[HEALER] Error stopping manager: %s", e)
            finally:
                self._manager = None

    async def _wait_for_failure(self):
        while self._running:
            if self._failure_event and self._failure_event.is_set():
                return
            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # HTML capture
    # ------------------------------------------------------------------

    async def _capture_page_html(self):
        """
        Grab the current page HTML from the browser before we close it.
        Saves to HTML_DUMP_FILE and stores in self._captured_html.
        """
        if not self._manager:
            return
        try:
            session = self._manager._session
            page = session.get_page() if session else None
            if page:
                html = await page.content()
                self._captured_html = html
                Path(HTML_DUMP_FILE).write_text(html, encoding="utf-8")
                logger.info("[HEALER] Page HTML captured (%d bytes) → %s",
                            len(html), HTML_DUMP_FILE)
                self._status_cb(f"[HEALER] Page HTML captured ({len(html):,} bytes)")
        except Exception as e:
            logger.warning("[HEALER] Could not capture page HTML: %s", e)
            # Try reading a previously saved dump as fallback
            try:
                dump = Path(HTML_DUMP_FILE)
                if dump.exists():
                    self._captured_html = dump.read_text(encoding="utf-8", errors="replace")
                    logger.info("[HEALER] Using previous HTML dump as fallback")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Status interception
    # ------------------------------------------------------------------

    def _on_status_message(self, message: str):
        self._status_cb(message)
        with self._log_lock:
            self._log_buffer.append(message)
            # No manual trim needed — deque(maxlen=500) handles it automatically

    # ------------------------------------------------------------------
    # Diagnosis
    # ------------------------------------------------------------------

    def _diagnose(self) -> tuple[str, str]:
        lines = []
        with self._log_lock:
            lines.extend(list(self._log_buffer)[-LOG_TAIL_LINES:])

        logs_dir = Path(LOGS_DIR)
        if logs_dir.exists():
            for lf in sorted(logs_dir.glob("*.log"),
                             key=lambda p: p.stat().st_mtime, reverse=True)[:2]:
                try:
                    lines.extend(lf.read_text(errors="replace").splitlines()[-LOG_TAIL_LINES:])
                except Exception:
                    pass
            for ss in sorted(logs_dir.glob("*.png"),
                             key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                lines.append(f"[screenshot] {ss.name}")

        return self._diagnostician.diagnose(lines)

    # ------------------------------------------------------------------
    # Category handlers
    # ------------------------------------------------------------------

    async def _handle_category(self, category: str, reason: str) -> bool:
        """Returns True to restart, False to stop entirely."""

        if category == FailureCategory.LOGIN_FAILURE:
            return False

        elif category == FailureCategory.ANTIBOT_BLOCK:
            self._status_cb("[HEALER] Anti-bot block — rotating proxy on restart.")
            with self._log_lock:
                self._log_buffer.clear()

        elif category == FailureCategory.QUEUE_TIMEOUT:
            self._status_cb("[HEALER] Queue timeout — resuming monitor.")
            self._patches_this_session = 0  # fresh drop event = fresh patch budget

        elif category == FailureCategory.OUT_OF_STOCK:
            self._status_cb("[HEALER] Sold out before checkout — resuming monitor.")
            self._patches_this_session = 0  # fresh drop event = fresh patch budget

        elif category == FailureCategory.NETWORK_TIMEOUT:
            if self._patches_this_session < MAX_PATCHES_PER_SESSION:
                self._status_cb("[HEALER] Network timeout — bumping timeout values...")
                if self._code_patcher.patch_network_timeout():
                    self._patches_this_session += 1
                    self._status_cb(
                        f"[HEALER] Timeouts bumped (patch #{self._patches_this_session})"
                    )
                else:
                    self._status_cb("[HEALER] Timeout values already at ceiling — no change")
            else:
                self._status_cb("[HEALER] Max patches reached — skipping timeout bump")

        elif category == FailureCategory.WRONG_SELECTOR:
            await self._handle_wrong_selector()

        else:  # UNKNOWN
            screenshot = self._find_latest_failure_screenshot()
            self._status_cb(
                f"[HEALER] Unknown failure: '{reason[:100]}' — "
                f"see screenshot: {LOGS_DIR}/{screenshot}"
            )

        return True

    async def _handle_wrong_selector(self):
        """Parse captured HTML and attempt to auto-patch the broken selector."""
        if self._patches_this_session >= MAX_PATCHES_PER_SESSION:
            self._status_cb(
                f"[HEALER] Max patches ({MAX_PATCHES_PER_SESSION}) reached — "
                "skipping selector patch"
            )
            return

        if not self._captured_html:
            self._status_cb(
                "[HEALER] No page HTML available — cannot auto-patch selector. "
                f"Check screenshot: {LOGS_DIR}/{self._find_latest_failure_screenshot()}"
            )
            return

        self._status_cb("[HEALER] Parsing page HTML to find correct selectors...")
        patched = self._html_patcher.patch_from_html(self._captured_html)

        if patched:
            self._patches_this_session += 1
            self._status_cb(
                f"[HEALER] Selector(s) auto-patched from live HTML "
                f"(patch #{self._patches_this_session}). "
                f"Diff saved to {PATCH_LOG_FILE}"
            )
        else:
            screenshot = self._find_latest_failure_screenshot()
            self._status_cb(
                "[HEALER] Could not confidently identify new selector from HTML "
                "(0 or 2+ candidates). "
                f"HTML dump: {HTML_DUMP_FILE} | "
                f"Screenshot: {LOGS_DIR}/{screenshot}"
            )

    def _find_latest_failure_screenshot(self) -> str:
        logs_dir = Path(LOGS_DIR)
        if not logs_dir.exists():
            return "none"
        candidates = []
        for pattern in ["no_atc_*.png", "empty_cart_*.png", "error_*.png",
                        "no_checkout_*.png", "no_place_order_*.png"]:
            candidates.extend(logs_dir.glob(pattern))
        if not candidates:
            candidates = list(logs_dir.glob("*.png"))
        if not candidates:
            return "none"
        return max(candidates, key=lambda p: p.stat().st_mtime).name


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

async def _main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    email = os.environ.get("WALMART_EMAIL", "")
    password = os.environ.get("WALMART_PASSWORD", "")
    if not email or not password:
        print("Set WALMART_EMAIL and WALMART_PASSWORD environment variables to run.")
        return

    agent = SelfHealingAgent(status_callback=print)
    try:
        await agent.start(email=email, password=password)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(_main())
