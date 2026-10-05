-- po_by_age: place-order (main-tab [CHECKOUT_POST]) HTTP 200s by window age, per run and pooled
--
-- Usage:  python tools/events/q.py po_by_age [--run 0604_,0611_,0713_,0723_,0731_,0804_,0930_]
-- Rows are place_orders (parser v2: one per main-tab [CHECKOUT_POST], paired FIFO with its
--   [CHECKOUT_RESPONSE] HTTP line). window_age_ms = POST time - the TCIN's in-stock episode open:
--   the [STOCK][FLIP] window that covers the POST (window_src='flip'), else the first in-stock read
--   after the TCIN's last out-of-stock read (window_src='reads': [STOCK] IN STOCK / [STOCK WATCH] /
--   cache-bust VERIFY, in log order). POST time (ts_src) = the response's Date header clamped into
--   [ts_lo_ms, ts_hi_ms] when one was logged (rejections only); else ts_lo_ms = the last logger stamp
--   before the POST (or the chain's 201, whichever is later) -- a LOWER bound, so a 200's age reads young.
-- Bins on whole seconds (window_age_ms / 1000, truncated): <=5 | 6-30 | 31-120 | >120. 'neg' and
--   'unknown' (no TCIN or no episode) are shown, never folded into a bin.
-- Result 2 re-bins every POST at its bracket bounds (ts_lo_ms / ts_hi_ms = the logger stamps around the
--   POST line): a cell that changes between est, lo and hi is clock-limited, not measured.
-- Reference (built 2026-10-01; the 6 pre-September order-bearing runs + run_20260930_014647 +
--   run_20260930_233818; 331 POSTs, 21 orders): <=5 s 18/34, 6-30 s 2/52, 31-120 s 1/96, >120 s 0/149.
--
-- PRE-REGISTERED READING (2026-10-01), applied per run by result 3 to the next night with a restock:
--   "a place-order converts only early in the window" is WEAKENED when any >120 s POST returns 200;
--   otherwise INCONCLUSIVE when the run has < 10 POSTs <=5 s or no 200 at all; otherwise SUPPORTED when
--   the <=5 s 200-rate is above the pooled >5 s 200-rate, and WEAKENED when it is not.
--   Window age is confounded with checkout path (in_chain POSTs are the early ones) and with cart age:
--   read the path columns of result 1 and tools/events/queries/late_carts.sql before naming the clock.

-- 1) per run x age bin
WITH b AS (
  SELECT run_id, status, path, window_age_ms AS age FROM place_orders
),
c AS (
  SELECT *, CASE WHEN age IS NULL THEN '9 unknown' WHEN age < 0 THEN '0 neg'
                 WHEN age / 1000 <= 5 THEN '1 <=5s' WHEN age / 1000 <= 30 THEN '2 6-30s'
                 WHEN age / 1000 <= 120 THEN '3 31-120s' ELSE '4 >120s' END AS bin
  FROM b
)
SELECT run_id AS run, bin,
       SUM(status = 200) || '/' || COUNT(*) AS ok_of_posts,
       ROUND(100.0 * SUM(status = 200) / COUNT(*), 1) AS pct200,
       SUM(path = 'in_chain') AS in_chain,
       SUM(path = 'ticket') AS ticket,
       SUM(path = 'legacy') AS legacy,
       SUM(path = 'unknown') AS path_unknown,
       SUM(status IS NULL) AS no_resp
FROM c
GROUP BY run_id, bin
ORDER BY run_id, bin;

-- 2) pooled over the selected runs, at the estimate and at both bracket bounds
WITH b AS (
  SELECT run_id, status, window_age_ms AS age, ts_lo_ms, ts_hi_ms,
         ts_ms - window_age_ms AS w0
  FROM place_orders
),
k AS (
  SELECT 'est' AS clock, run_id, status, age FROM b
  UNION ALL
  SELECT 'lo', run_id, status, CASE WHEN w0 IS NOT NULL AND ts_lo_ms IS NOT NULL THEN ts_lo_ms - w0 END FROM b
  UNION ALL
  SELECT 'hi', run_id, status, CASE WHEN w0 IS NOT NULL AND ts_hi_ms IS NOT NULL THEN ts_hi_ms - w0 END FROM b
),
c AS (
  SELECT *, CASE WHEN age IS NULL THEN '9 unknown' WHEN age < 0 THEN '0 neg'
                 WHEN age / 1000 <= 5 THEN '1 <=5s' WHEN age / 1000 <= 30 THEN '2 6-30s'
                 WHEN age / 1000 <= 120 THEN '3 31-120s' ELSE '4 >120s' END AS bin
  FROM k
)
SELECT 'ALL ' || (SELECT COUNT(DISTINCT run_id) FROM place_orders) || ' runs' AS run, bin,
       SUM(clock = 'est' AND status = 200) || '/' || SUM(clock = 'est') AS est,
       ROUND(100.0 * SUM(clock = 'est' AND status = 200) / NULLIF(SUM(clock = 'est'), 0), 1) AS est_pct200,
       SUM(clock = 'lo' AND status = 200) || '/' || SUM(clock = 'lo') AS at_lo_bound,
       SUM(clock = 'hi' AND status = 200) || '/' || SUM(clock = 'hi') AS at_hi_bound
FROM c
GROUP BY bin
ORDER BY bin;

-- 3) the pre-registered reading, per run
WITH c AS (
  SELECT run_id, status,
         CASE WHEN window_age_ms IS NULL OR window_age_ms < 0 THEN NULL
              WHEN window_age_ms / 1000 <= 5 THEN 'early'
              WHEN window_age_ms / 1000 > 120 THEN 'late120' ELSE 'mid' END AS k
  FROM place_orders
),
r AS (
  SELECT run_id,
         SUM(k = 'early') AS e_n, SUM(k = 'early' AND status = 200) AS e_ok,
         SUM(k IN ('mid', 'late120')) AS l_n, SUM(k IN ('mid', 'late120') AND status = 200) AS l_ok,
         SUM(k = 'late120' AND status = 200) AS ok120,
         SUM(k IS NULL) AS unbinned
  FROM c GROUP BY run_id
)
SELECT u.run_id AS run,
       COALESCE(r.e_ok, 0) || '/' || COALESCE(r.e_n, 0) AS early_le5s,
       COALESCE(r.l_ok, 0) || '/' || COALESCE(r.l_n, 0) AS later_gt5s,
       COALESCE(r.ok120, 0) AS ok_after_120s,
       COALESCE(r.unbinned, 0) AS unbinned,
       CASE WHEN r.ok120 > 0 THEN 'WEAKENED'
            WHEN r.run_id IS NULL OR r.e_n < 10 OR r.e_ok + r.l_ok = 0 THEN 'INCONCLUSIVE'
            WHEN r.l_n = 0 OR 1.0 * r.e_ok / r.e_n > 1.0 * r.l_ok / r.l_n THEN 'SUPPORTED'
            ELSE 'WEAKENED' END AS reading
FROM runs u
LEFT JOIN r ON r.run_id = u.run_id
ORDER BY u.run_id;
