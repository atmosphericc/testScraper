-- first_shot_era: the home line's FIRST shot per run x account x TCIN at the edge limiter, per night, in race units,
-- with a regime-break flag. Written 2026-10-16 after the frame audit (C-1016-06): the post-run read each night alone
-- and never saw the home line's first-shot pass rate fall from 21/59 (09-11..09-30a) to 2/69 (10-02, 10-09, 10-16).
--
-- Usage:  python tools/events/q.py first_shot_era            (all runs; --run narrows, but the break flag needs history)
--
-- DEFINITIONS
--   first shot   the earliest row by ts_ms per (run_id, ident, tcin) with proxied = 0 (home line). NOT shots.is_first, which
--                is first per ident per RACE (a re-race of the same TCIN starts a new one); this is the account's first
--                contact with that TCIN that night, before any volume of ours on it.
--   past         gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit') -- the walls.sql definition.
--   limited      gate = 'wall1_limited'.  unclassified = anything else (other / unknown / NULL): counted, shown, left out
--                of the rate (agent-context 2C).
--   volley       the first shots of one (run_id, tcin) group. Home accounts pass or fail TOGETHER (C-1015-01), so the
--                volley, not the shot, is the unit for any test across nights: volley_pass = at least one first shot past.
--   10-01        run_20260930_233818 (the limiter-loosened night, 13/15) is shown but kept out of the baselines.
--
-- RULE R-ERA (written 2026-10-16 AFTER seeing the 10-02 / 10-09 / 10-16 numbers -- so it is prospective from the next
--   drop on; the 10-16 reading below is post hoc). Unit = volley, never shot (home accounts pass or fail together).
--   Newest 3 nights (each >= 3 classified volleys; 10-01 excluded) pooled vs every earlier night since 2026-09-11 pooled:
--   one-sided Fisher p (newest lower) < 0.01  => REGIME BREAK: say so in the post-run's first paragraph and append
--   docs/TARGET_CHANGES.md; never read such a night as "inside the range" or "night x TCIN" on its own.
--   p < 0.05 => WATCH.  Otherwise no break.  Shot-level p-values across nights are not evidence (wrong unit).

DROP VIEW IF EXISTS temp.fse_first;
CREATE TEMP VIEW fse_first AS
SELECT s.run_id, s.ident, s.tcin, s.ts_ms, s.gate
FROM shots s
WHERE s.proxied = 0
  AND s.ts_ms = (SELECT MIN(t.ts_ms) FROM shots t
                 WHERE t.run_id = s.run_id AND t.ident = s.ident AND t.tcin = s.tcin AND t.proxied = 0)
GROUP BY s.run_id, s.ident, s.tcin;

DROP VIEW IF EXISTS temp.fse_volley;
CREATE TEMP VIEW fse_volley AS
SELECT run_id, tcin,
       COUNT(*) AS n,
       SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) AS past,
       SUM(gate = 'wall1_limited') AS limited,
       SUM(gate IS NULL OR gate NOT IN ('wall1_limited','wall2_denied','admitted_fs','cart','inventory','cart_limit')) AS unclass
FROM fse_first GROUP BY run_id, tcin;

-- 1  per night: first shots and volleys
SELECT run_id,
       SUM(n) AS first_shots, SUM(past) AS past, SUM(limited) AS limited, SUM(unclass) AS unclass,
       printf('%.1f', 100.0 * SUM(past) / NULLIF(SUM(past) + SUM(limited), 0)) AS shot_pct,
       COUNT(*) AS volleys,
       SUM(past > 0) AS volleys_past,
       SUM(past + limited > 0) AS volleys_classified,
       printf('%.1f', 100.0 * SUM(past > 0) / NULLIF(SUM(past + limited > 0), 0)) AS volley_pct,
       CASE WHEN run_id = 'run_20260930_233818' THEN 'excluded (10-01)' ELSE '' END AS note
FROM fse_volley
WHERE run_id >= 'run_20260911'
GROUP BY run_id
HAVING SUM(n) > 0
ORDER BY run_id;

-- 2  R-ERA: newest 3 nights vs all earlier nights, pooled volleys, one-sided Fisher (hypergeometric lower tail)
WITH RECURSIVE nights AS (
  SELECT run_id, SUM(past > 0) AS vp, SUM(past + limited > 0) AS vc
  FROM fse_volley
  WHERE run_id >= 'run_20260911' AND run_id <> 'run_20260930_233818'
  GROUP BY run_id
  HAVING SUM(past + limited > 0) >= 3
), ranked AS (
  SELECT run_id, vp, vc, ROW_NUMBER() OVER (ORDER BY run_id DESC) AS rn FROM nights
), g AS (
  SELECT (SELECT COUNT(*) FROM ranked) AS nn,
         (SELECT COALESCE(SUM(vp), 0) FROM ranked WHERE rn <= 3) AS a,  (SELECT COALESCE(SUM(vc), 0) FROM ranked WHERE rn <= 3) AS n1,
         (SELECT COALESCE(SUM(vp), 0) FROM ranked WHERE rn > 3)  AS b,  (SELECT COALESCE(SUM(vc), 0) FROM ranked WHERE rn > 3)  AS n2
), lf(i, v) AS (
  SELECT 0, 0.0
  UNION ALL SELECT i + 1, v + ln(i + 1) FROM lf WHERE i < (SELECT n1 + n2 FROM g)
), terms AS (
  -- P(X = x) for x = 0..a, X ~ Hypergeometric(N = n1 + n2, K = a + b, draws = n1)
  SELECT x.i AS x,
         exp( (SELECT v FROM lf WHERE i = g.n1) - (SELECT v FROM lf WHERE i = x.i) - (SELECT v FROM lf WHERE i = g.n1 - x.i)
            + (SELECT v FROM lf WHERE i = g.n2) - (SELECT v FROM lf WHERE i = g.a + g.b - x.i)
            - (SELECT v FROM lf WHERE i = g.n2 - (g.a + g.b - x.i))
            - ( (SELECT v FROM lf WHERE i = g.n1 + g.n2) - (SELECT v FROM lf WHERE i = g.a + g.b)
              - (SELECT v FROM lf WHERE i = g.n1 + g.n2 - g.a - g.b) ) ) AS p
  FROM lf AS x, g
  WHERE x.i <= g.a AND g.a + g.b - x.i <= g.n2 AND g.a + g.b - x.i >= 0
)
SELECT g.nn AS nights,
       (SELECT group_concat(run_id || ' ' || vp || '/' || vc, ', ') FROM ranked WHERE rn <= 3) AS newest3,
       g.a || '/' || g.n1 AS newest3_volleys,
       g.b || '/' || g.n2 AS earlier_volleys,
       printf('%.4f', (SELECT SUM(p) FROM terms)) AS fisher_p_lower,
       CASE
         WHEN g.nn < 4 THEN 'NO DATA (< 4 nights with >= 3 classified volleys)'
         WHEN (SELECT SUM(p) FROM terms) < 0.01 THEN 'REGIME BREAK by the pooled R-ERA (p < 0.01) - pooled only: C-1021-03 (10-21) found the within-TCIN stratified test borderline (p=0.032, leave-one-night-out 0.009-0.22) and downgraded the 10-16 reading to WATCH; cite both'
         WHEN (SELECT SUM(p) FROM terms) < 0.05 THEN 'WATCH (p < 0.05)'
         ELSE 'no break by R-ERA'
       END AS r_era
FROM g;
