"""
Multi-session queue race coordinator (Phase 2.5 — #2 from
docs/POKEMON_DROP_READINESS.md).

Today's drop-day flow with WALMART_USE_RESILIENT=1:
  Resilient pool monitors N SKUs across N=16 sessions → SKU goes QUEUED →
  ItemStatus(availability_status='QUEUED:<url>') → ONE signal fires →
  single purchase session (manager's WalmartSessionManager, separate
  Chrome from the pool) navigates to PDP, enters queue, waits, buys.

Net: one session in the queue regardless of pool size.

Multi-session race flow (target):
  SKU goes QUEUED → race dispatched across `pool.all_ready_sessions()` →
  every eligible session marks in_queue=True (keepalive skips per
  walmart/session_manager flag), navigates its own Chrome to the PDP,
  attaches a QueueHandler to its tab, waits for admission. First session
  to receive `state=valid` from the ticket API wins; the rest are
  cancelled. The winner hands off the admitted tab to the purchase
  executor.

Open issues that block dropping the FALLBACK_TO_MANAGER path:
  - WalmartPurchaseExecutor currently takes a WalmartSessionManager (single
    purchase session) — not a SessionEntry from the pool. Need either a
    SessionEntry-driven purchase variant or unify the two abstractions.
  - QueueHandler needs per-session instance lifecycle (currently designed
    around session_manager._page). The CDP listener attaches to a
    specific tab; constructing N handlers on N pool tabs is straightforward
    but untested.
  - "First to admit wins" requires cancellation semantics + a shared
    asyncio.Event the dispatched tasks watch.
  - Walmart's one-checkout-per-account-per-SKU cap: 16 sessions can ALL
    enter the queue but only one can place the order. The race still
    helps — admission is the bottleneck, not checkout — but it caps the
    win count at 1 per drop (until multi-account support exists).
  - _px3 survival under 16 concurrent queue waits (PerimeterX scoring)
    is unmeasured; readiness doc flags this as a risk.

For now `dispatch_queue_race` logs the race intent and falls through to
`manager._on_in_stock_signal` — the single-session path stays the source
of truth so this scaffold is observable in logs without changing behavior.
The signature is the contract future work will fulfil.
"""

from __future__ import annotations

import asyncio
import os
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.stack.multi_session_pool import SessionEntry
    from src.stack.resilient_checker import ResilientChecker
    from src.stack.retailer_adapter import ItemStatus
    from walmart.purchase_manager import WalmartPurchaseManager

logger = logging.getLogger(__name__)


QUEUED_PREFIX = "QUEUED:"

# Cap on the whole race. If no session reaches state=VALID within this
# window, the drop is effectively gone and we fall through to the single-
# session path so the manager at least gets a chance. 30 min matches the
# upper bound for typical Pokemon-Wednesday queue waits (per readiness doc).
DEFAULT_RACE_TIMEOUT_S = 1800.0


class PoolSessionShim:
    """Adapts a SessionEntry from `src/stack/multi_session_pool` to the
    `WalmartSessionManager` surface that `WalmartPurchaseExecutor` expects.

    The executor reaches into session via these methods:
      - `session.needs_rewarm()` — _px3 staleness check
      - `await session.warm_session(items)` — _px3 refresh by browsing
      - `await session.rewarm_tab1()` — homepage warm
      - `await session._handle_blocked_page_on(page)` — PX Press-and-Hold solver
      - `session._page` — the executor's tab handle

    Pool sessions don't own _px3 management (the harvester does it across
    all sessions every ~30s) and don't have the manager's specialized
    blocked-page recovery. The shim:
      - reports needs_rewarm()=False (harvester handles freshness)
      - makes warm_session / rewarm_tab1 no-ops
      - falls back to a plain `page.reload()` for _handle_blocked_page_on
      - mirrors `entry.tab` as `_page` so executor reads work unchanged

    Callers can still reach the raw SessionEntry via `shim.session_entry`
    when they need to flip `in_queue`, read `id`/`proxy_ip`, etc.
    """

    def __init__(self, entry: "SessionEntry"):
        self._entry = entry
        # Mirror entry.tab as _page — same primitive (a zendriver Tab),
        # just the attribute name the executor reads.
        self._page = entry.tab

    @property
    def session_entry(self) -> "SessionEntry":
        return self._entry

    def needs_rewarm(self) -> bool:
        return False

    async def warm_session(self, items=None) -> None:
        return None

    async def rewarm_tab1(self) -> None:
        return None

    async def _handle_blocked_page_on(self, page) -> bool:
        """Last-ditch recovery for PerimeterX /blocked redirects.
        Pool sessions lack the manager's press-and-hold solver, so reload
        the page and let the harvester's next cookie refresh do the rest.
        """
        try:
            await page.reload()
            return True
        except Exception as e:
            logger.warning("[QUEUE_RACE] PoolSessionShim reload failed: %s", e)
            return False


def is_queued_status(status: "ItemStatus") -> bool:
    """True if the ItemStatus represents a Walmart queue interstitial.

    The walmart adapter emits availability_status='QUEUED:<source>' when
    the monitoring fetch hits a /qp redirect or detects a ticket-API
    signature in the response body. Any other status (IN_STOCK,
    OUT_OF_STOCK, PARSE_FAIL, etc.) is not a queue event.
    """
    av = status.availability_status or ""
    return av.startswith(QUEUED_PREFIX)


def _fallback_to_manager(
    manager: "WalmartPurchaseManager",
    status: "ItemStatus",
) -> None:
    """Single-session path — same shape as the non-queued bridge call.
    Used when no eligible pool sessions exist, the manager loop is
    unavailable, or the race times out without admission.
    """
    manager._on_in_stock_signal(
        item_id=status.item_id,
        offer_id=None,
        name=status.title or status.item_id,
        price=status.price,
        order_limit=None,
    )


def _pdp_url_for(item_id: str) -> str:
    """Canonical Walmart product-detail-page URL. The walmart adapter sets
    availability_status='QUEUED:<source>' where <source> is just a label
    ('redirect_url' / 'body_signature') — not the redirect URL itself —
    so we reconstruct the PDP from item_id and let Walmart re-engage the
    queue interstitial on navigation.
    """
    return f"https://www.walmart.com/ip/{item_id}"


async def _race_one_session(
    entry: "SessionEntry",
    item_url: str,
    admitted_event: asyncio.Event,
    winner_holder: dict,
) -> None:
    """Drive ONE pool session through the queue. On `state=VALID` claim
    the win (set the shared event + holder) if no other session beat us.
    Returns None on any non-VALID terminal or cancellation.

    All exceptions are swallowed (logged) — one session crashing must not
    take down the whole race.
    """
    from walmart.queue_handler import QueueHandler, QueueState

    def _log(msg):
        logger.info("[QUEUE_RACE %s] %s", entry.id, msg)

    try:
        await entry.tab.get(item_url)
        handler = QueueHandler(entry.tab, _log, session=entry)
        ticket = await handler.detect()
        if ticket is None:
            _log("no queue interstitial detected after PDP nav")
            return None

        final = await handler.detect_and_wait()
        if final is not None and final.state == QueueState.VALID:
            # First-to-claim wins. asyncio.Event.set() is idempotent but
            # we still gate the winner_holder write so the "winner" is
            # deterministic even if two sessions admit in the same tick.
            if not admitted_event.is_set():
                winner_holder["entry"] = entry
                admitted_event.set()
                _log(f"ADMITTED (winner): {final}")
            else:
                _log(f"admitted but another session won first: {final}")
            return None
        _log(f"non-VALID terminal: {final}")
        return None
    except asyncio.CancelledError:
        _log("cancelled (another session won, or race timed out)")
        raise
    except Exception as e:
        logger.warning("[QUEUE_RACE %s] race_one error: %s", entry.id, e)
        return None


async def _run_race(
    checker: "ResilientChecker",
    manager: "WalmartPurchaseManager",
    status: "ItemStatus",
) -> None:
    """Async race coordinator — runs on the manager's event loop."""
    pool = checker.session_pool
    eligible = pool.all_ready_sessions() if pool is not None else []
    if not eligible:
        logger.warning(
            "[QUEUE_RACE] %s queued but no eligible pool sessions — "
            "falling through to single-session purchase",
            status.item_id,
        )
        _fallback_to_manager(manager, status)
        return

    item_id = status.item_id
    item_url = _pdp_url_for(item_id)
    timeout_s = float(os.environ.get(
        "WALMART_QUEUE_RACE_TIMEOUT_S", str(DEFAULT_RACE_TIMEOUT_S)))

    # Mark every racer in_queue=True BEFORE the pool's keepalive loop's
    # next pass — otherwise a heartbeat could navigate the tab off /qp
    # and forfeit the ticket before our race-one task even runs.
    for s in eligible:
        s.in_queue = True

    admitted_event = asyncio.Event()
    winner_holder: dict = {"entry": None}

    logger.warning(
        "[QUEUE_RACE] %s entered queue — racing %d session(s) "
        "(timeout=%.0fs)", item_id, len(eligible), timeout_s,
    )

    tasks = [
        asyncio.create_task(
            _race_one_session(s, item_url, admitted_event, winner_holder),
            name=f"qrace_{s.id}",
        )
        for s in eligible
    ]

    winner: Optional["SessionEntry"] = None
    try:
        await asyncio.wait_for(admitted_event.wait(), timeout=timeout_s)
        winner = winner_holder["entry"]
    except asyncio.TimeoutError:
        logger.warning(
            "[QUEUE_RACE] %s — no admission within %.0fs; cancelling "
            "racers and falling through to manager", item_id, timeout_s,
        )

    # Cancel all racer tasks. The winning task already finished setting
    # admitted_event before we got here, but Python is happy to cancel a
    # completed task (it's a no-op). Cancelling losers releases them
    # from their detect_and_wait so the pool can recover.
    for t in tasks:
        if not t.done():
            t.cancel()

    # Release in_queue on losers immediately so the keepalive can resume
    # rotating them. Winner stays in_queue=True until purchase finishes —
    # the executor's _navigate moves it off /qp but in_queue protects
    # the tab during the brief race-end → purchase-start window.
    for s in eligible:
        if s is not winner:
            s.in_queue = False

    if winner is None:
        _fallback_to_manager(manager, status)
        return

    # Drive purchase on the winning session's tab via PoolSessionShim.
    # PoolSessionShim adapts SessionEntry to the WalmartSessionManager
    # surface that WalmartPurchaseExecutor expects.
    from walmart.purchase_executor import WalmartPurchaseExecutor

    def _purchase_log(msg):
        logger.info("[QUEUE_RACE PURCHASE %s] %s", winner.id, msg)

    shim = PoolSessionShim(winner)
    executor = WalmartPurchaseExecutor(
        page=winner.tab,
        session=shim,
        status_callback=_purchase_log,
    )

    try:
        logger.warning(
            "[QUEUE_RACE] %s — winner=%s — dispatching purchase",
            item_id, winner.id,
        )
        result = await executor.purchase(item_id=item_id, item_url=item_url)
        logger.warning(
            "[QUEUE_RACE %s] purchase result: success=%s order=%s error=%s",
            winner.id, result.success, result.order_id, result.error,
        )
    except Exception as e:
        logger.exception("[QUEUE_RACE %s] purchase raised: %s", winner.id, e)
    finally:
        # Winner returns to pool's idle rotation. Caller hasn't cleaned
        # up cart/checkout state; that's the executor's concern.
        winner.in_queue = False


def dispatch_queue_race(
    checker: "ResilientChecker",
    manager: "WalmartPurchaseManager",
    status: "ItemStatus",
) -> None:
    """Sync entry from the resilient checker's on_in_stock bridge.

    Schedules the async race on the manager's event loop (where the
    pool sessions' tabs live and the executor must run). Falls back to
    the single-session manager path when the loop isn't available.
    """
    loop = getattr(manager, "_loop", None)
    if loop is None or not loop.is_running():
        logger.warning(
            "[QUEUE_RACE] manager loop unavailable — falling through "
            "to single-session purchase",
        )
        _fallback_to_manager(manager, status)
        return

    # Fire-and-forget: the bridge thread (resilient checker callback
    # dispatcher) must not block waiting on the race. The race owns its
    # own lifecycle and logs progress.
    asyncio.run_coroutine_threadsafe(_run_race(checker, manager, status), loop)
