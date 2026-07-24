#!/usr/bin/env python3
"""Unit test: CDP interceptor dedup-hit leak guard (2026-07-23 wedge fix).

The 07-21→22 overnight run wedged every Chrome on a rigid ~70-75 min clock from
its own launch (22 sentinel destroys). The interceptor's dedup early-return is a
paused-request leak: CDP re-pauses every redirect hop under the SAME request id,
and the warmup tabs' interception-job ids collide in the shared
_cdp_continued_ids set — any event whose (id, stage) key was already seen was
dropped WITHOUT Fetch.continueRequest, leaving that request paused forever.

Load-bearing properties:
  1. A dedup-hit event still gets continue_request (the request is released).
  2. Duplicate PROCESSING stays suppressed — no double Shape cache/ring pushes.
  3. continue_request failing on a true duplicate is swallowed (no handler error).
  4. Kill-switch TARGET_CDP_DEDUP_CONTINUE=0 restores the pre-07-23 drop.

Drives the REAL _setup_cdp_fetch_interceptor handler on a bare instance with a
fake tab. No browser, no network.
Run: python tests/test_cdp_dedup_leak_guard.py  (or: pytest tests/test_cdp_dedup_leak_guard.py)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["TARGET_API_CAPTURE_CHECKOUT_STEPS"] = "false"  # no file writes in tests
os.environ["TARGET_API_CAPTURE_PLACE_ORDER"] = "false"

from zendriver import cdp  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402


class _FakeTab:
    """Records every CDP command the handler sends. Driving the zendriver
    command generator (next → send) mirrors what the real connection does."""

    def __init__(self, fail_continue_ids=None):
        self.sent = []                      # list of (method, params) tuples
        self.enabled_domains = []
        self.handlers = {}
        self.fail_continue_ids = set(fail_continue_ids or [])

    async def send(self, gen):
        cmd = next(gen)
        method = cmd.get("method", "?")
        params = cmd.get("params", {})
        self.sent.append((method, params))
        if (method == "Fetch.continueRequest"
                and params.get("requestId") in self.fail_continue_ids):
            raise RuntimeError("Invalid InterceptionId (already continued)")
        try:
            gen.send({})
        except StopIteration:
            pass
        return None

    def add_handler(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def remove_handler(self, handler, event_type):
        self.handlers.get(event_type, []).remove(handler)

    def continues(self):
        return [p.get("requestId") for m, p in self.sent
                if m == "Fetch.continueRequest"]


def _make_executor():
    ex = object.__new__(PurchaseExecutor)  # bypass heavy __init__
    ex._cdp_continued_ids = set()
    ex._cdp_dedup_hits = 0
    ex._cached_cart_headers = {}
    ex._cached_cart_headers_ts = 0.0
    ex._shape_capture_ring = []
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    return ex


def _paused_event(req_id: str, url: str, method: str = "POST"):
    return SimpleNamespace(
        request_id=cdp.fetch.RequestId(req_id),
        response_status_code=None,
        response_error_reason=None,
        response_headers=None,
        request=SimpleNamespace(
            url=url,
            method=method,
            headers={"x-abc123": "shape-token", "x-def456": "shape-token",
                     "content-type": "application/json"},
        ),
    )


async def _install(ex, tab):
    await ex._setup_cdp_fetch_interceptor(tab, persistent=True)
    handlers = tab.handlers.get(cdp.fetch.RequestPaused, [])
    assert handlers, "interceptor handler was not registered"
    return handlers[-1]


CART_URL = "https://carts.target.com/web_checkouts/v1/cart_items?key=x"

_results = []


def _check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail and not cond else ""))
    _results.append(cond)


async def test_dedup_hit_still_continues():
    """Property 1+2: second event with the same (id, stage) is continued but
    not re-processed (single ring entry)."""
    ex = _make_executor()
    tab = _FakeTab()
    handler = await _install(ex, tab)

    await handler(_paused_event("interception-job-1.0", CART_URL))
    await handler(_paused_event("interception-job-1.0", CART_URL))  # collision/redirect hop

    conts = tab.continues()
    _check("dedup_hit_continued", conts.count("interception-job-1.0") == 2,
           f"expected 2 continues, got {conts}")
    _check("no_double_processing", len(ex._shape_capture_ring) == 1,
           f"ring has {len(ex._shape_capture_ring)} entries, expected 1")
    _check("dedup_hit_counted", ex._cdp_dedup_hits == 1,
           f"counter={ex._cdp_dedup_hits}")


async def test_true_duplicate_error_swallowed():
    """Property 3: Chrome rejecting the redundant continue must not raise."""
    ex = _make_executor()
    tab = _FakeTab(fail_continue_ids={"interception-job-2.0"})
    handler = await _install(ex, tab)

    # First event: the continue fails (simulates already-continued) — the
    # handler's own tail-continue try/except swallows it.
    await handler(_paused_event("interception-job-2.0", CART_URL))
    # Dedup hit: continue fails again — the leak-guard try/except swallows it.
    try:
        await handler(_paused_event("interception-job-2.0", CART_URL))
        ok = True
    except Exception as e:
        ok = False
        print(f"  handler raised: {e}")
    _check("duplicate_continue_error_swallowed", ok)


async def test_kill_switch_restores_drop():
    """Property 4: TARGET_CDP_DEDUP_CONTINUE=0 → dedup hit is dropped without
    a continue (exact pre-07-23 behavior)."""
    os.environ["TARGET_CDP_DEDUP_CONTINUE"] = "0"
    try:
        ex = _make_executor()
        tab = _FakeTab()
        handler = await _install(ex, tab)
        await handler(_paused_event("interception-job-3.0", CART_URL))
        await handler(_paused_event("interception-job-3.0", CART_URL))
        conts = tab.continues()
        _check("kill_switch_drops_without_continue",
               conts.count("interception-job-3.0") == 1,
               f"expected 1 continue, got {conts}")
    finally:
        os.environ.pop("TARGET_CDP_DEDUP_CONTINUE", None)


async def test_distinct_stages_not_deduped():
    """Regression guard: REQUEST vs RESPONSE stages of the same id keep their
    separate dedup keys — the RESPONSE event is processed, not counted as a hit."""
    ex = _make_executor()
    tab = _FakeTab()
    handler = await _install(ex, tab)
    await handler(_paused_event("interception-job-4.0", CART_URL))
    resp = _paused_event("interception-job-4.0",
                         "https://carts.target.com/web_checkouts/v1/checkout?key=x")
    resp.response_status_code = 201
    resp.response_headers = []
    await handler(resp)
    _check("stages_have_separate_keys", ex._cdp_dedup_hits == 0,
           f"counter={ex._cdp_dedup_hits}, expected 0")
    _check("both_stages_continued", len(tab.continues()) == 2,
           f"continues={tab.continues()}")


async def _main():
    await test_dedup_hit_still_continues()
    await test_true_duplicate_error_swallowed()
    await test_kill_switch_restores_drop()
    await test_distinct_stages_not_deduped()
    passed = sum(_results)
    print(f"\n=== {passed}/{len(_results)} passed ===")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
