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

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.stack.multi_session_pool import SessionEntry
    from src.stack.resilient_checker import ResilientChecker
    from src.stack.retailer_adapter import ItemStatus
    from walmart.purchase_manager import WalmartPurchaseManager

logger = logging.getLogger(__name__)


QUEUED_PREFIX = "QUEUED:"


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


def dispatch_queue_race(
    checker: "ResilientChecker",
    manager: "WalmartPurchaseManager",
    status: "ItemStatus",
) -> None:
    """Entry point for the multi-session queue race.

    Called from the resilient stack's on_in_stock bridge whenever
    `is_queued_status(status)` is True. The intent is to dispatch a
    queue-then-purchase task on every session that
    `pool.all_ready_sessions()` returns; the first session to reach
    `state=valid` wins and the rest are cancelled.

    Current behavior: log the race intent (session count, item) and
    forward the signal to the existing single-session purchase pipeline
    via `manager._on_in_stock_signal`. Reasons documented in the module
    docstring.

    Idempotent and safe to call multiple times — the underlying
    `manager._on_in_stock_signal` already guards against re-entering an
    in-progress purchase for the same item.
    """
    pool = checker.session_pool
    eligible = pool.all_ready_sessions() if pool is not None else []
    n_eligible = len(eligible)

    logger.warning(
        "[QUEUE_RACE] %s entered queue (%s) — %d session(s) eligible to race; "
        "falling through to single-session purchase (race not yet implemented)",
        status.item_id, status.availability_status, n_eligible,
    )

    # Single-session fallback — same as the non-queued path today.
    manager._on_in_stock_signal(
        item_id=status.item_id,
        offer_id=None,
        name=status.title or status.item_id,
        price=status.price,
        order_limit=None,
    )
