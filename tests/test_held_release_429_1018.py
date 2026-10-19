#!/usr/bin/env python3
"""Offline test: FX-1018-TEST-RELEASE. A held / dirty line under a throttled cart read (429),
pinned as TODAY's behaviour (readiness audit 2026-10-18, buypath.C2).

What is pinned (the real _execute_purchase_impl + the real _delete_cart_items / _cart_items_read /
_held_cart_release / _woncart_dirty_release / _held_cart_entry / won-cart ticket loop; only the
tab is a stub whose every cart read and delete answers 429):

  (a) an identity holds a marker for X, X is not live, and the dispatch is for Y while every cart
      read answers 429: no add-to-cart fires, the result is held_cart_release_failed, the marker is
      handed to the dirty flag (strike on), and the flag PERSISTS across dispatches (each one
      retries the delete, fires nothing) until its age passes TARGET_HELD_CART_TTL_S; the dispatch
      that sees the expiry still fires nothing and drops the flag; only the NEXT dispatch shoots.
      That last step (a shot after the flag's TTL give-up while the line may still be in the cart) is
      the behaviour FX-1018-RELEASE-SHOOT would change; this test flips only with that flag on.
  (b) RIDE-TO-OOS held re-entry of a VERIFIED marker with the cart unreadable: the verified flag
      is demoted (False) and ticket 1 is the strict pre_po gate, never an unread po_only; with a
      clean exact read (control) the marker stays verified and ticket 1 is po_only.

Offline: no browser, no network. Run: python tests/test_held_release_429_1018.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_won_cart_direct_smoke as S  # noqa: E402  (import only; its main() does not run)

check = S.check
X = S.TCIN             # the held line's TCIN
Y = S.OTHER            # the TCIN being dispatched
TTL = 3600             # TARGET_HELD_CART_TTL_S as the wrapper arms it

# The wrapper's armed won-cart bundle plus the strike (the v4 dirty hand-off needs it).
ARMED = dict(S.RTO_ARMED, TARGET_WONCART_DIRECT="1", TARGET_AMBIGUOUS_COMMIT_LATCH="1",
             TARGET_HELD_LINE_FLIP_STRIKE="1", TARGET_API_PLACE_ORDER="true",
             TARGET_HELD_CART_TTL_S=str(TTL), TARGET_FASTLANE_QTY_GUARD="1", TARGET_STOCK_PROBE="1")
BAD = {"ok": False, "status": 429, "items": [], "has_items": False}


class Tab429:
    """Every cart read and delete answers 429; records every JS the executor evaluates."""

    def __init__(self):
        self.url = "https://www.target.com/account"
        self.handlers, self.enabled_domains, self.gets = {}, [], []
        self.js = []

    async def get(self, url):
        self.gets.append(url)

    async def send(self, *a, **k):
        return None

    async def evaluate(self, js, await_promise=False, **kw):
        self.js.append(js)
        if "method: 'DELETE'" in js:
            return 429
        if "web_checkouts/v1/cart?cart_type=REGULAR" in js or "has_items" in js:
            return dict(BAD)
        return None

    def atc_posts(self):
        return [j for j in self.js if "cart_items?field_groups=CART,CART_ITEMS,SUMMARY" in j
                and "method: 'POST'" in j]


def _marker(c, tcin=X, **kw):
    h = {"tcin": tcin, "qty": 2, "cart_id": "CART-1234567890", "created": c.t - 100.0,
         "first_201_ts": c.t - 100.0, "tickets": 3, "sched_used": 2, "verified": True, "pi_id": "",
         "cvv_put": "none", "last_ticket_ts": c.t - 50.0, "fs_seen_ts": 0.0, "source": "first"}
    h.update(kw)
    return h


def _dispatch(ex, c, tcin):
    with S.in_tmp_cwd(), S.env(**ARMED), S.fake_time(c):
        try:
            return S.run(ex._execute_purchase_impl(tcin, quantity=2))
        except S.LegacyReached:
            return {"reason": "LEGACY_CHECKOUT_REACHED"}


def test_a_held_release_under_cart_read_429():
    c = S.Clock(time.time())
    ex, _ = S.impl_ex(c, S.FL_PRE429)
    del ex._delete_cart_items                       # the REAL bounded delete (reads first)
    tab = Tab429()

    async def _gp():
        return tab

    ex.session_manager.get_page = _gp
    ex._held_cart = _marker(c)
    ex._stock_live_fn = lambda t: {"live": str(t) == Y, "window_start": c.t - 3}   # X is NOT live
    marker0 = ex._held_cart
    # dispatch 1 on Y: the held X line must be released before any shot; every read 429.
    r1 = _dispatch(ex, c, Y)
    d = getattr(ex, "_woncart_dirty", None)
    check("a_d1_result_held_cart_release_failed", r1.get("reason") == "held_cart_release_failed", str(r1))
    check("a_d1_no_add_to_cart", not tab.atc_posts() and ex.fl_calls == [] and ex.loop_calls == [],
          f"posts={len(tab.atc_posts())} fl={ex.fl_calls} loop={ex.loop_calls}")
    check("a_d1_marker_handed_to_the_dirty_flag", ex._held_cart is None and isinstance(d, dict)
          and d.get("tcin") == X and d.get("why") == "held_release_failed", f"held={ex._held_cart} dirty={d}")
    check("a_d1_the_delete_really_hit_the_429_read", any("has_items" in j or "cart?cart_type=REGULAR" in j
                                                         for j in tab.js), str(len(tab.js)))
    check("a_d1_error_text_has_no_digits_to_misroute", not any(ch.isdigit() for ch in str(r1.get("error", ""))),
          str(r1))
    ts0 = d.get("ts") if isinstance(d, dict) else None

    # dispatches 2, 3 inside the TTL: same answer, the SAME flag object survives, nothing fires.
    for n, gap in ((2, 30.0), (3, 600.0)):
        c.t += gap
        r = _dispatch(ex, c, Y)
        d2 = getattr(ex, "_woncart_dirty", None)
        check(f"a_d{n}_still_held_cart_release_failed", r.get("reason") == "held_cart_release_failed", str(r))
        check(f"a_d{n}_flag_persists_unchanged", d2 is d and d2.get("ts") == ts0, f"{d2} vs {d}")
        check(f"a_d{n}_no_add_to_cart", not tab.atc_posts() and ex.fl_calls == [], str(ex.fl_calls))

    # the flag's age passes the TTL: that dispatch STILL fires nothing and drops the flag ...
    c.t = ts0 + TTL + 1.0
    r4 = _dispatch(ex, c, Y)
    check("a_ttl_dispatch_fires_nothing", r4.get("reason") == "held_cart_release_failed"
          and not tab.atc_posts() and ex.fl_calls == [], f"{r4} {ex.fl_calls}")
    check("a_ttl_dispatch_drops_the_flag", getattr(ex, "_woncart_dirty", "x") is None,
          str(getattr(ex, "_woncart_dirty", "x")))
    # ... and the NEXT dispatch is a normal shot (the add-to-cart fast lane fires) although the
    # line was never confirmed deleted. FX-1018-RELEASE-SHOOT would change exactly this.
    r5 = _dispatch(ex, c, Y)
    check("a_next_dispatch_shoots_after_the_give_up", ex.fl_calls == [2], f"{r5} fl={ex.fl_calls}")
    check("a_marker_object_never_resurrected", ex._held_cart is not marker0)


def _held_entry_run(read_results, verified=True):
    """RIDE-TO-OOS held re-entry with the REAL ticket loop; returns (modes, verified_at_entry, ex)."""
    c = S.Clock(time.time())
    ex, _ = S.impl_ex(c, S.FL_PRE429)
    del ex._won_cart_ticket_loop                    # the real loop
    ex._held_cart = _marker(c, tcin=X, verified=verified)       # idle 50 s (> 20 s)
    ex._stock_live_fn = lambda t: {"live": str(t) == X, "window_start": c.t - 3}
    tab = S.ReadTab(ex, [S.t_pre(429, S.FS_BODY)] * 400, c, read_results)

    async def _gp():
        return tab

    ex.session_manager.get_page = _gp
    seen = []
    real = ex._won_cart_ticket_loop

    async def _spy(tab_, tcin, qty, fl, start_time, entry="first"):
        h = ex._held_cart
        seen.append((entry, bool(h.get("verified")) if isinstance(h, dict) else None))
        return await real(tab_, tcin, qty, fl, start_time, entry=entry)

    ex._won_cart_ticket_loop = _spy
    with S.in_tmp_cwd(), S.env(**ARMED), S.fake_time(c):
        try:
            r = S.run(ex._execute_purchase_impl(X, quantity=2))
        except S.LegacyReached:
            r = {"reason": "LEGACY_CHECKOUT_REACHED"}
    return [m for _, m, _ in tab.tickets], seen, ex, r, tab


def test_b_rto_held_reentry_demotion():
    modes, seen, ex, r, tab = _held_entry_run([BAD])
    check("b_unreadable_cart_loop_entered_held_no_atc", seen and seen[0][0] == "held" and ex.fl_calls == []
          and "NOT presumed gone (ride-to-OOS)" in S.run.last_out, f"{seen} {ex.fl_calls}")
    check("b_unreadable_cart_verified_demoted_to_false", seen and seen[0][1] is False, str(seen))
    check("b_unreadable_cart_ticket_1_is_pre_po", bool(modes) and modes[0] == "pre_po", str(modes[:3]))
    check("b_unreadable_cart_never_an_unread_po_only", "po_only" not in modes, str(sorted(set(modes))))
    check("b_both_reads_were_made", len(tab.read_ts) >= 2, str(tab.read_ts))

    exact = {"ok": True, "status": 200, "has_items": True, "items": [{"id": "CI-1", "tcin": X, "qty": 2}]}
    modes, seen, ex, r, tab = _held_entry_run([exact])
    check("b_control_exact_read_keeps_verified", seen and seen[0] == ("held", True), str(seen))
    check("b_control_exact_read_ticket_1_is_po_only", bool(modes) and modes[0] == "po_only", str(modes[:3]))

    # an UNVERIFIED marker keeps the presume (only proof of the line exempts it): dropped, a normal
    # shot on the fast lane, and the loop that follows is a FIRST entry, not a held one
    modes, seen, ex, r, tab = _held_entry_run([BAD], verified=False)
    check("b_unverified_marker_still_presumed_gone", ex.fl_calls == [2]
          and "presuming the held" in S.run.last_out and not any(e == "held" for e, _ in seen),
          f"{seen} fl={ex.fl_calls}")


def main():
    for fn in (test_a_held_release_under_cart_read_429, test_b_rto_held_reentry_demotion):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            S.FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(S.PASSED)}/{len(S.PASSED) + len(S.FAILED)} passed ===")
    for f in S.FAILED:
        print("  FAIL:", f)
    return 0 if not S.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
