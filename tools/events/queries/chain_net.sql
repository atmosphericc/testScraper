-- chain_net: the wire under pre_checkout / place-order / CVV PUT ([CHAIN_NET], CHK-1) -- WRITTEN AHEAD OF ITS TABLE
--
-- *** THE chain_net TABLE DOES NOT EXIST YET. build.py (the tools lane owns it) has no [CHAIN_NET] family, so this
-- *** file errors with "no such table: chain_net" against today's event store; q.py exits 1 and prints nothing. It is
-- *** written against the table a follow-up parser change must create (columns below). Until then read the raw lines:
-- ***     grep -h "\[CHAIN_NET\] ident=" logs/runs/run_<id>.log        (print-only: NO timestamp prefix, so never
-- ***     filter on the YYYY-MM-DD stamp -- agent-context 2C-bis; use send_ms, Chrome's wall-clock ms, as the clock)
-- *** and the pre-registered rules below by hand. This query is the spec of what the parser must expose.
--
-- Usage (once the parser lands):  python tools/events/q.py chain_net [--run 20261016]
-- Source: [CHAIN_NET] (purchase_executor.py chain_net_line; TARGET_CHAIN_NET_META=1, which needs TARGET_ATC_NET_META=1):
--   ONE print-only line per MAIN-tab pre_checkout / place-order / CVV PUT request, POST|PUT and its OPTIONS preflight:
--   [CHAIN_NET] ident=<a> u=pre|po|cvv m=POST|PUT|OPTIONS st=<http> pause_ms= send_ms= ttfb_ms= conn= reused=
--               proto= connect_ms= n=<ordinal> rid=<requestId>      ('-' = not reported -> NULL; ident=? = format error)
--   plus an install line "[CHAIN_NET] chain wire stamps on (...) ident=<a>" and, at most once per process, a
--   "[CHAIN_NET] ident=<a> cap=3000 reached - ..." line (neither is a request row; count them under checks).
-- Table the parser must build, one row per request line (line = physical line number):
--   chain_net(run_id, line, ident, u, method, status, pause_ms, send_ms, ttfb_ms, conn, reused, proto, connect_ms, n, rid)
--   send_ms = Chrome's wall-clock ms when the request LEFT Chrome (its own clock, like atc_net.send_ms).
--   pause_ms = send_ms - requestWillBeSent.wall_time: the Fetch pause + queueing, and on a NEW connection (reused=0)
--     also dns / connect / tls -- so the rules below use queue_ms = pause_ms - COALESCE(connect_ms, 0).
--   ttfb_ms = response headers minus send_end. n = the request's ordinal within its (u, method) since the last
--     successful ATC of that account: the first 20 are ALL logged, then every 10th, so n is the sampling ruler --
--     a gap in n is thinning, not a lost event. Cap per process = 3000 logged requests.
-- Validation of the clock before trusting pause_ms (CHK-1 evaluator): the same send_ms derivation on 690 joined ATC POSTs
--   of run_20261009_000934 gives atc_t0 -> wire median 86 ms (p10 72, p90 118), all 690 on a reused connection
--   ([MEASURED] atc_net, join_dt_ms) -- the order of magnitude pause_ms must reproduce on a reused pre/po request.
--
-- PRE-REGISTERED RULES (2026-10-15), read per run with >= 1 chain, result 3:
--   CHK-3 (cut the CDP pause / preflights) is worth building ONLY IF the median queue_ms of the pre + po requests
--     (POST and their OPTIONS preflights) is >= 100 ms on >= 10 requests; "under load" is the analyst's call (read the
--     n and send_ms columns of result 1 against the flip time) -- the query does not encode it.
--   CHK-2 (pre_checkout server time) is worth running ONLY IF the median ttfb_ms of u=pre m=POST is >= 600 ms on >= 10.
--   Fewer than 10 requests = INSUFFICIENT, no reading. No [CHAIN_NET] line at a chain that fired a place-order (see
--   checks / grep) = the INSTRUMENT is broken, not the leg.
-- Result 5 is the second half of first_po_age.sql's rule: the wins' WIRE-send age (po POST send_ms minus the window
--   open), which replaces the logger-stamp bounds; it reports whether EVERY win is <= 3 s / <= 4.4 s.

-- 1) coverage per run x ident x request class: lines, statuses, how many were thinned out (n gaps) and the n range
SELECT run_id AS run, ident, u, method, COUNT(*) AS lines,
       SUM(status BETWEEN 200 AND 299) AS s2xx, SUM(status = 429) AS s429, SUM(status IS NULL) AS no_status,
       SUM(send_ms IS NULL) AS no_send_ms, SUM(reused = 0) AS new_conn, MAX(n) AS max_n
FROM chain_net
GROUP BY run_id, ident, u, method
ORDER BY run_id, ident, u, method;

-- 2) latency per request class: queue_ms (pause minus connect) and ttfb_ms, p50 / p90, reused connections apart
WITH x AS (
  SELECT u, method, COALESCE(reused, -1) AS reused, pause_ms - COALESCE(connect_ms, 0) AS queue_ms, ttfb_ms
  FROM chain_net
),
q AS (
  SELECT u, method, reused, queue_ms,
         ROW_NUMBER() OVER (PARTITION BY u, method, reused ORDER BY queue_ms) AS rq,
         COUNT(queue_ms) OVER (PARTITION BY u, method, reused) AS nq,
         ttfb_ms,
         ROW_NUMBER() OVER (PARTITION BY u, method, reused ORDER BY ttfb_ms) AS rt,
         COUNT(ttfb_ms) OVER (PARTITION BY u, method, reused) AS nt
  FROM x
)
SELECT u, method, CASE reused WHEN 1 THEN 'reused' WHEN 0 THEN 'new' ELSE '?' END AS conn,
       COUNT(*) AS n,
       MAX(CASE WHEN rq = (nq + 1) / 2 THEN queue_ms END) AS queue_p50,
       MAX(CASE WHEN rq = (nq * 9 + 9) / 10 THEN queue_ms END) AS queue_p90,
       MAX(CASE WHEN rt = (nt + 1) / 2 THEN ttfb_ms END) AS ttfb_p50,
       MAX(CASE WHEN rt = (nt * 9 + 9) / 10 THEN ttfb_ms END) AS ttfb_p90
FROM q
GROUP BY u, method, reused
ORDER BY u, method, reused DESC;

-- 3) the pre-registered CHK-3 / CHK-2 reading, per run
WITH x AS (
  SELECT run_id, u, method, pause_ms - COALESCE(connect_ms, 0) AS queue_ms, ttfb_ms FROM chain_net
),
a AS (
  SELECT run_id, queue_ms,
         ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY queue_ms) AS r, COUNT(*) OVER (PARTITION BY run_id) AS c
  FROM x WHERE u IN ('pre', 'po') AND queue_ms IS NOT NULL
),
b AS (
  SELECT run_id, ttfb_ms,
         ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY ttfb_ms) AS r, COUNT(*) OVER (PARTITION BY run_id) AS c
  FROM x WHERE u = 'pre' AND method = 'POST' AND ttfb_ms IS NOT NULL
),
m AS (
  SELECT run_id, MAX(c) AS n_queue,
         MAX(CASE WHEN r = (c + 1) / 2 THEN queue_ms END) AS queue_p50 FROM a GROUP BY run_id
),
t AS (
  SELECT run_id, MAX(c) AS n_pre_ttfb,
         MAX(CASE WHEN r = (c + 1) / 2 THEN ttfb_ms END) AS pre_ttfb_p50 FROM b GROUP BY run_id
)
SELECT u.run_id AS run, COALESCE(m.n_queue, 0) AS n_pre_po_requests, m.queue_p50,
       CASE WHEN COALESCE(m.n_queue, 0) < 10 THEN 'INSUFFICIENT' WHEN m.queue_p50 >= 100 THEN 'BUILD_CHK3'
            ELSE 'NOT_WORTH_CHK3' END AS chk3,
       COALESCE(t.n_pre_ttfb, 0) AS n_pre_posts, t.pre_ttfb_p50,
       CASE WHEN COALESCE(t.n_pre_ttfb, 0) < 10 THEN 'INSUFFICIENT' WHEN t.pre_ttfb_p50 >= 600 THEN 'RUN_CHK2'
            ELSE 'NOT_WORTH_CHK2' END AS chk2
FROM (SELECT DISTINCT run_id FROM chain_net) u
LEFT JOIN m ON m.run_id = u.run_id
LEFT JOIN t ON t.run_id = u.run_id
ORDER BY u.run_id;

-- 4) the flip-to-place-order leg split, one row per place-order POST that has a successful ATC of the same account
--    before it: atc_resp_ms ~ the ATC POST's send_ms + ttfb_ms (response headers; the body adds a little, so the
--    gaps are upper bounds on the leg after the ATC response). gap_atc_to_pre = pre POST send - ATC response;
--    pre_ttfb = pre_checkout server time; gap_pre_to_po = po send - pre response (the CDP pauses, the CVV PUT and
--    any preflight sit here); po_ttfb = the place-order's own round trip. A NULL means the matching row is missing
--    (never a 0): count them, do not drop them.
WITH po AS (
  SELECT run_id, ident, line, n, send_ms, ttfb_ms, status FROM chain_net
  WHERE u = 'po' AND method = 'POST' AND send_ms IS NOT NULL
),
j AS (
  SELECT po.*,
         (SELECT a.send_ms + COALESCE(a.ttfb_ms, 0) FROM atc_net a
           WHERE a.run_id = po.run_id AND a.ident = po.ident AND a.method = 'POST' AND a.status IN (200, 201)
             AND a.send_ms IS NOT NULL AND a.send_ms + COALESCE(a.ttfb_ms, 0) <= po.send_ms
           ORDER BY a.send_ms DESC LIMIT 1) AS atc_resp_ms,
         (SELECT p.send_ms FROM chain_net p
           WHERE p.run_id = po.run_id AND p.ident = po.ident AND p.u = 'pre' AND p.method = 'POST'
             AND p.send_ms IS NOT NULL AND p.send_ms <= po.send_ms
           ORDER BY p.send_ms DESC LIMIT 1) AS pre_send_ms
  FROM po
)
SELECT j.run_id AS run, j.ident, j.line, j.n AS po_n, j.status AS po_status,
       j.send_ms - j.atc_resp_ms AS gap_atc_to_po_ms,
       j.pre_send_ms - j.atc_resp_ms AS gap_atc_to_pre_ms,
       (SELECT p.ttfb_ms FROM chain_net p WHERE p.run_id = j.run_id AND p.ident = j.ident AND p.u = 'pre'
          AND p.method = 'POST' AND p.send_ms = j.pre_send_ms LIMIT 1) AS pre_ttfb_ms,
       j.send_ms - j.pre_send_ms - COALESCE((SELECT p.ttfb_ms FROM chain_net p WHERE p.run_id = j.run_id
          AND p.ident = j.ident AND p.u = 'pre' AND p.method = 'POST' AND p.send_ms = j.pre_send_ms LIMIT 1), 0)
         AS gap_pre_resp_to_po_ms,
       j.ttfb_ms AS po_ttfb_ms
FROM j
WHERE j.n <= 20
ORDER BY j.run_id, j.send_ms
LIMIT 200;

-- 5) the wins' WIRE-send age: each first place-order (place_orders: po_idx 1, a cart) matched to the po POST row of the
--    same run + ident whose send_ms lies inside the PO's logger bracket [ts_lo_ms, ts_hi_ms] (+-500 ms), nearest to its
--    middle. wire_age = send_ms - the window open (ts_ms - window_age_ms). A first PO with no match keeps a NULL wire age
--    and is COUNTED (no_wire), never aged by its logger bound. Rule (per run with >= 5 first POs): report the wins'
--    max wire_age and whether EVERY win is <= 3 s / <= 4.4 s; a win with no wire stamp = INCONCLUSIVE.
WITH m AS (
  SELECT p.run_id AS run_id, p.line AS pline, c.send_ms AS send_ms,
         ROW_NUMBER() OVER (PARTITION BY p.run_id, p.line
                            ORDER BY ABS(c.send_ms - (p.ts_lo_ms + p.ts_hi_ms) / 2)) AS rk
  FROM place_orders p
  JOIN chain_net c ON c.run_id = p.run_id AND c.ident = p.ident AND c.u = 'po' AND c.method = 'POST'
                  AND c.send_ms IS NOT NULL AND c.send_ms BETWEEN p.ts_lo_ms - 500 AND p.ts_hi_ms + 500
  WHERE COALESCE(p.po_idx, 1) = 1 AND p.cart_line IS NOT NULL
),
f AS (
  SELECT p.run_id, p.ident, p.status, p.ts_src, p.window_age_ms,
         (p.ts_ms - p.window_age_ms) AS w0, m.send_ms AS wire_send_ms
  FROM place_orders p
  LEFT JOIN m ON m.run_id = p.run_id AND m.pline = p.line AND m.rk = 1
  WHERE COALESCE(p.po_idx, 1) = 1 AND p.cart_line IS NOT NULL
),
g AS (
  SELECT *, wire_send_ms - w0 AS wire_age FROM f
),
r AS (
  SELECT run_id, COUNT(*) AS first_pos, SUM(wire_send_ms IS NULL) AS no_wire,
         SUM(status = 200) AS wins, SUM(status = 200 AND wire_send_ms IS NULL) AS wins_no_wire,
         MAX(CASE WHEN status = 200 THEN wire_age END) AS max_win_wire_age_ms,
         SUM(status = 200 AND wire_age > 3000) AS wins_over_3s,
         SUM(status = 200 AND wire_age > 4400) AS wins_over_4_4s
  FROM g GROUP BY run_id
)
SELECT run_id AS run, first_pos, no_wire, wins, wins_no_wire, max_win_wire_age_ms, wins_over_3s, wins_over_4_4s,
       CASE WHEN first_pos < 5 THEN 'INSUFFICIENT'
            WHEN wins = 0 THEN 'NO_WIN'
            WHEN wins_no_wire > 0 THEN 'INCONCLUSIVE'
            WHEN wins_over_3s = 0 THEN 'EVERY_WIN_LE_3S'
            WHEN wins_over_4_4s = 0 THEN 'EVERY_WIN_LE_4_4S'
            ELSE 'SOME_WIN_OVER_4_4S' END AS reading
FROM r
WHERE no_wire < first_pos
ORDER BY run_id;
