-- first_po_age: the FIRST place-order of each cart, by window age at BOTH bracket bounds, split by ts_src (CHK-1)
--
-- Usage:  python tools/events/q.py first_po_age [--run 20261016]
-- Runs on the CURRENT event store (place_orders only). The wire-send half of the pre-registered rule (a win's
--   [CHAIN_NET] po send_ms minus the window open) needs the [CHAIN_NET] rows and lives in chain_net.sql, result 5.
-- Why this query exists (2026-10-15, CHK-1 proposal + evaluator; [MEASURED] 78 first POs over 17 nights):
--   every WINNING first place-order carries ts_src='logger_prior' (the last logger stamp before the POST, a LOWER
--   bound; mean bracket ~2.4 s, n=18) and every NON-winning one carries ts_src='resp_date' (the response Date
--   header, +-1 s; n=60). A win therefore reads YOUNG in any single-clock age table (po_by_age.sql). The upper
--   bound of the 18 wins' age is 2.6-4.4 s; 0 of 54 first POs with an accurate age >= 3 s won. This query prints
--   both bounds, never one clock alone, and keeps ts_src as a column so the confound stays visible.
-- First PO = coalesce(po_idx, 1) = 1 AND cart_line IS NOT NULL (the same population as the 78 above); later POs
--   (1/270) are a different population (ride-to-OOS tickets) and are not in these tables.
-- Clocks: w0 = ts_ms - window_age_ms (the window open). age_lo = ts_lo_ms - w0, age_est = window_age_ms,
--   age_hi = ts_hi_ms - w0 (NULL when a bound or the window is unknown: shown as '9 unknown', never binned).
-- Bins in whole ms: <=1 s | 1-2 s | 2-3 s | 3-4.4 s | >4.4 s ('0 neg' and '9 unknown' are shown, never folded in).
--
-- PRE-REGISTERED RULE (2026-10-15), applied by result 3 to the next drop with >= 5 first POs in a run:
--   report the wins' upper-bound age (age_hi) and say, in words, whether EVERY win is <= 3 s and whether EVERY win
--   is <= 4.4 s. 'EVERY' is only claimed over wins whose age_hi is known; a win with an unknown age is counted in
--   unknown_age_wins and blocks the claim (reading = INCONCLUSIVE). Fewer than 5 first POs = INSUFFICIENT (no
--   reading). Then read chain_net.sql result 5 for the same wins' WIRE-send age ([CHAIN_NET] po send_ms), which
--   replaces the logger bounds once the instrument has logged a chain.

-- 1) ts_src x outcome: the bias exhibit (bracket width shows how loose each clock is)
SELECT ts_src,
       CASE WHEN status = 200 THEN 'win' WHEN status IS NULL THEN 'no_resp' ELSE 'rejected' END AS outcome,
       COUNT(*) AS n,
       ROUND(AVG(ts_hi_ms - ts_lo_ms)) AS mean_bracket_ms,
       MIN(ts_hi_ms - (ts_ms - window_age_ms)) AS min_age_hi_ms,
       MAX(ts_hi_ms - (ts_ms - window_age_ms)) AS max_age_hi_ms,
       SUM(window_age_ms IS NULL OR ts_lo_ms IS NULL OR ts_hi_ms IS NULL) AS age_unknown
FROM place_orders
WHERE COALESCE(po_idx, 1) = 1 AND cart_line IS NOT NULL
GROUP BY ts_src, outcome
ORDER BY ts_src, outcome;

-- 2) wins / first POs by age bin, at the lower bound, the estimate and the upper bound; ts_src split in columns
WITH f AS (
  SELECT run_id, status, ts_src, window_age_ms AS age_est,
         ts_lo_ms - (ts_ms - window_age_ms) AS age_lo,
         ts_hi_ms - (ts_ms - window_age_ms) AS age_hi
  FROM place_orders
  WHERE COALESCE(po_idx, 1) = 1 AND cart_line IS NOT NULL
),
k AS (
  SELECT 'lo' AS clock, status, ts_src, age_lo AS age FROM f
  UNION ALL SELECT 'est', status, ts_src, age_est FROM f
  UNION ALL SELECT 'hi', status, ts_src, age_hi FROM f
),
c AS (
  SELECT *, CASE WHEN age IS NULL THEN '9 unknown' WHEN age < 0 THEN '0 neg'
                 WHEN age <= 1000 THEN '1 <=1s' WHEN age <= 2000 THEN '2 1-2s'
                 WHEN age <= 3000 THEN '3 2-3s' WHEN age <= 4400 THEN '4 3-4.4s'
                 ELSE '5 >4.4s' END AS bin
  FROM k
)
SELECT clock, bin,
       SUM(status = 200) || '/' || COUNT(*) AS wins_of_first_pos,
       ROUND(100.0 * SUM(status = 200) / COUNT(*), 1) AS pct_win,
       SUM(ts_src = 'logger_prior' AND status = 200) || '/' || SUM(ts_src = 'logger_prior') AS logger_prior,
       SUM(ts_src = 'resp_date' AND status = 200) || '/' || SUM(ts_src = 'resp_date') AS resp_date,
       SUM(ts_src IS NULL OR ts_src NOT IN ('logger_prior', 'resp_date')) AS other_src
FROM c
GROUP BY clock, bin
ORDER BY CASE clock WHEN 'lo' THEN 1 WHEN 'est' THEN 2 ELSE 3 END, bin;

-- 3) the pre-registered reading, per run that has at least one first PO (< 5 of them = INSUFFICIENT)
WITH f AS (
  SELECT run_id, status,
         ts_hi_ms - (ts_ms - window_age_ms) AS age_hi
  FROM place_orders
  WHERE COALESCE(po_idx, 1) = 1 AND cart_line IS NOT NULL
),
r AS (
  SELECT run_id, COUNT(*) AS first_pos, SUM(status = 200) AS wins,
         SUM(status = 200 AND age_hi IS NULL) AS unknown_age_wins,
         MAX(CASE WHEN status = 200 THEN age_hi END) AS max_win_age_hi_ms,
         SUM(status = 200 AND age_hi IS NOT NULL AND age_hi > 3000) AS wins_over_3s,
         SUM(status = 200 AND age_hi IS NOT NULL AND age_hi > 4400) AS wins_over_4_4s
  FROM f GROUP BY run_id
)
SELECT r.run_id AS run, r.first_pos, r.wins, r.max_win_age_hi_ms, r.unknown_age_wins,
       r.wins_over_3s, r.wins_over_4_4s,
       CASE WHEN r.first_pos < 5 THEN 'INSUFFICIENT'
            WHEN r.wins = 0 THEN 'NO_WIN'
            WHEN r.unknown_age_wins > 0 THEN 'INCONCLUSIVE'
            WHEN r.wins_over_3s = 0 THEN 'EVERY_WIN_LE_3S'
            WHEN r.wins_over_4_4s = 0 THEN 'EVERY_WIN_LE_4_4S'
            ELSE 'SOME_WIN_OVER_4_4S' END AS reading
FROM r
ORDER BY r.run_id;
