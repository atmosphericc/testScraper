-- rto: pre-registered readout R-RTO for FX-1014-RTO (ride a won cart to sold-out) and
-- FX-1014-FSTCIN (per-item FAST_SELLING back-off), armed 2026-10-14 (commits 89c2e0a1,
-- 1f3f2aff). Run AFTER the first drop with carts:  python tools/events/q.py rto --run <run_id>
--
-- Mechanism PASS (the change did what it says):
--   * no won-cart loop on a LIVE cart (live=1) ends as cart_ticket_cap / yield_fleet below
--     1200 tickets, and no call_cap with live=1 (the call cap is exempt while live);
--   * live carts end as budget_spent (then held re-entry), tail_spent / call_cap at OOS
--     (live=0, line held), or placed / terminal.
-- Value (descriptive, n tiny): count of 200s at ticket n > 40 or ms_since_201 > 120000 --
--   place-orders that could not have existed before (C-1001-07: 0/150 such 200s before).
-- KILL (set TARGET_WONCART_RIDE_TO_OOS=0 + LIVE_CAP_EXEMPT=0 + YIELD_FLEET=1 + TTL 900):
--   any [AMBIGUOUS_COMMIT] / a second order for one account x TCIN in the window (order
--   history), or a ride of >= 40 tickets that ends cart_evicted after a
--   "NOT presumed gone (ride-to-OOS)" line (an empty-cart ride; grep the log).
-- Not parsed by the store (grep the run log): "NOT presumed gone (ride-to-OOS)", "dummy_add",
--   "[THROTTLE] FAST_SELLING cooldown active" next to a fast-lane shot on another TCIN.

-- 1) every won-cart loop end: reason x live, with the cart's ticket count
SELECT run_id AS run, ident, tcin, reason, verdict, live, oos, held,
       tickets_call, tickets_cart, dl_left_s,
       CASE WHEN live = 1 AND reason IN ('cart_ticket_cap', 'yield_fleet', 'call_cap')
                 AND COALESCE(tickets_cart, 0) < 1200 THEN 'FAIL-mechanism' ELSE 'ok' END AS rule
FROM loop_ends
ORDER BY run_id, line;

-- 2) per cart: tickets, 2xx, how far past the old caps the ride went
SELECT run_id AS run, ident, tcin, cart_id,
       COUNT(*) AS tickets,
       SUM(status IN (200, 201)) AS ok_2xx,
       SUM(status IN (200, 201) AND (n > 40 OR ms_since_201 > 120000)) AS ok_past_old_caps,
       MAX(n) AS max_ticket_n,
       ROUND(MAX(ms_since_201) / 1000.0, 1) AS last_s_since_201,
       SUM(key LIKE '%FAST_SELLING%') AS fast_selling,
       SUM(key LIKE '%RESERVATION%') AS reservation_fail
FROM tickets
GROUP BY run_id, ident, tcin, cart_id
ORDER BY run_id, MIN(line);
