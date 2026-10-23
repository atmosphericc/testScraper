-- admits: per window, how many shots got PAST the edge limiter and WHEN (2026-10-23, the first-purchase plan).
--
-- The binding wall is Target's add-to-cart edge limiter: almost every shot is 429 before the cart service.
-- A "pass" = any gate that reached the cart service (wall2_denied / admitted_fs / cart / inventory / cart_limit).
-- This query answers the lever question for every drop: did admits scale with how many shots we fired in the
-- window, and did they cluster at window OPEN (being first/fast is the lever) or spread across it (raw volume is)?
--
-- shots          total shots in the window
-- past           shots past wall 1
-- rate_pct       past / shots
-- first_age_ms   window_age_ms of the FIRST admit in the window (NULL = none); small = admits open early
-- open10/open60  admits in the first 10 s / 10-60 s of the window; late = admits after 60 s
-- idents         distinct accounts that fired; lines = distinct home/proxied lines that fired
-- med_gap_ms     median ms between consecutive shots of one ident in the window (cadence; small = we fired fast)
SELECT s.run_id AS run,
       s.window_id AS win,
       MIN(s.tcin) AS tcin,
       COUNT(*) AS shots,
       SUM(s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) AS past,
       ROUND(100.0 * SUM(s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit'))
             / NULLIF(COUNT(*), 0), 2) AS rate_pct,
       MIN(CASE WHEN s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')
                THEN s.window_age_ms END) AS first_age_ms,
       SUM(s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')
           AND s.window_age_ms <= 10000) AS open10,
       SUM(s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')
           AND s.window_age_ms > 10000 AND s.window_age_ms <= 60000) AS open60,
       SUM(s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')
           AND s.window_age_ms > 60000) AS late,
       SUM(s.gate = 'cart') AS carts,
       COUNT(DISTINCT s.ident) AS idents,
       COUNT(DISTINCT s.proxied) AS lines
FROM shots s
WHERE s.window_id IS NOT NULL
GROUP BY s.run_id, s.window_id
HAVING COUNT(*) > 0
ORDER BY s.run_id, s.window_id;
