-- arms: a treated ident vs the rest -- later-shot (and first-shot) wall-1 passes, per run and pooled
--
-- Usage:  python tools/events/q.py arms -p treated=alt-1 [--run 0925,0930]
--         (or set EVENTS_TREATED=alt-1 in the environment; unset -> every ident lands in 'rest')
-- w1_pass = any gate except wall1_limited/other/unknown (a 401 counts: it is past the limiter).
-- The pct denominators exclude other/unknown. Shots with no ident or no race (resp_only) are left out.
-- Selection effect: a later shot exists only because the earlier one failed -- compare arms at the
-- same shot position, never later-vs-first.

-- 1) per run
WITH t AS (
  SELECT run_id, is_first, gate,
         CASE WHEN ident = :treated THEN 'treated:' || ident ELSE 'rest' END AS arm
  FROM shots WHERE ident IS NOT NULL AND is_first IS NOT NULL
)
SELECT run_id AS run, arm,
       SUM(is_first = 0) AS later_n,
       SUM(is_first = 0 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory')) AS later_w1_pass,
       ROUND(100.0 * SUM(is_first = 0 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory'))
             / NULLIF(SUM(is_first = 0 AND gate NOT IN ('other', 'unknown')), 0), 2) AS later_pct,
       SUM(is_first = 1) AS first_n,
       SUM(is_first = 1 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory')) AS first_w1_pass,
       ROUND(100.0 * SUM(is_first = 1 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory'))
             / NULLIF(SUM(is_first = 1 AND gate NOT IN ('other', 'unknown')), 0), 2) AS first_pct
FROM t
GROUP BY run_id, arm
ORDER BY run_id, arm;

-- 2) pooled over the selected runs
WITH t AS (
  SELECT run_id, is_first, gate,
         CASE WHEN ident = :treated THEN 'treated:' || ident ELSE 'rest' END AS arm
  FROM shots WHERE ident IS NOT NULL AND is_first IS NOT NULL
)
SELECT 'ALL ' || COUNT(DISTINCT run_id) || ' runs' AS run, arm,
       SUM(is_first = 0) AS later_n,
       SUM(is_first = 0 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory')) AS later_w1_pass,
       ROUND(100.0 * SUM(is_first = 0 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory'))
             / NULLIF(SUM(is_first = 0 AND gate NOT IN ('other', 'unknown')), 0), 2) AS later_pct,
       SUM(is_first = 1) AS first_n,
       SUM(is_first = 1 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory')) AS first_w1_pass,
       ROUND(100.0 * SUM(is_first = 1 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory'))
             / NULLIF(SUM(is_first = 1 AND gate NOT IN ('other', 'unknown')), 0), 2) AS first_pct
FROM t
GROUP BY arm
ORDER BY arm;
