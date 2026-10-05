-- regime_tcin: the regime check per TCIN, so a composition artifact cannot pass as a regime
-- verdict (C-0930-08 was weakened for exactly that: 09-30's pooled 4/33 flip-race first-shot
-- rate mixed 1010892076 - 0/145 home first shots ever - with a new TCIN that went 3/3).
--
-- Result 1, per run x TCIN (main-tab shots; home and proxied kept apart):
--   first_pass   past the limiter / classified FIRST shots  (past = any gate but wall1_limited;
--                a keyless 401 counts as past, C-0930-03; other/unknown excluded from both sides)
--   first_adm    admitted (201 / FAST_SELLING / 424 / cart_limit 400) / (admitted + wall1_limited)  - the f1 rule
--                (cart_limit: parser v2; before v2 that 400 was 'other' and excluded from both sides)
--   later_pass   the same as first_pass for every later shot
--   share        this TCIN's share of the run's classified first shots (the composition)
-- Result 2, per run: the pooled first-shot rates and the same with the TCIN that has the most
--   first shots LEFT OUT. A regime flag that does not survive leave-one-out is composition.
WITH s AS (
  SELECT run_id, tcin, proxied, is_first, gate,
         CASE WHEN gate IN ('other', 'unknown') THEN NULL
              WHEN gate = 'wall1_limited' THEN 0 ELSE 1 END AS past,
         CASE WHEN gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 1
              WHEN gate = 'wall1_limited' THEN 0 ELSE NULL END AS adm
  FROM shots
),
per AS (
  SELECT run_id, tcin, COALESCE(proxied, -1) AS proxied,
         SUM(is_first = 1 AND past IS NOT NULL) AS f_n,
         SUM(is_first = 1 AND past = 1) AS f_past,
         SUM(is_first = 1 AND adm IS NOT NULL) AS fa_n,
         SUM(is_first = 1 AND adm = 1) AS fa_adm,
         SUM(is_first = 0 AND past IS NOT NULL) AS l_n,
         SUM(is_first = 0 AND past = 1) AS l_past
  FROM s GROUP BY run_id, tcin, COALESCE(proxied, -1)
),
tot AS (SELECT run_id, SUM(f_n) AS f_tot FROM per GROUP BY run_id)
SELECT p.run_id AS run, p.tcin,
       CASE p.proxied WHEN 0 THEN 'home' WHEN 1 THEN 'proxied' ELSE '?' END AS line,
       p.f_past || '/' || p.f_n AS first_pass,
       ROUND(100.0 * p.f_past / NULLIF(p.f_n, 0), 1) AS first_pct,
       p.fa_adm || '/' || p.fa_n AS first_adm,
       p.l_past || '/' || p.l_n AS later_pass,
       ROUND(100.0 * p.f_n / NULLIF(t.f_tot, 0), 1) AS share_pct
FROM per p JOIN tot t ON t.run_id = p.run_id
WHERE p.f_n + p.l_n > 0
ORDER BY p.run_id, p.f_n DESC;

WITH s AS (
  SELECT run_id, tcin, is_first,
         CASE WHEN gate IN ('other', 'unknown') THEN NULL
              WHEN gate = 'wall1_limited' THEN 0 ELSE 1 END AS past,
         CASE WHEN gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 1
              WHEN gate = 'wall1_limited' THEN 0 ELSE NULL END AS adm
  FROM shots WHERE is_first = 1
),
per AS (
  SELECT run_id, tcin,
         SUM(past IS NOT NULL) AS n, SUM(past = 1) AS k,
         SUM(adm IS NOT NULL) AS an, SUM(adm = 1) AS ak
  FROM s GROUP BY run_id, tcin
),
top AS (
  SELECT run_id, tcin, n FROM (
    SELECT run_id, tcin, n, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY n DESC, tcin) AS rk FROM per
  ) WHERE rk = 1
)
SELECT p.run_id AS run,
       SUM(p.k) || '/' || SUM(p.n) AS first_pass_pooled,
       ROUND(100.0 * SUM(p.k) / NULLIF(SUM(p.n), 0), 1) AS pooled_pct,
       t.tcin AS dominant_tcin,
       ROUND(100.0 * t.n / NULLIF(SUM(p.n), 0), 1) AS dominant_share_pct,
       SUM(CASE WHEN p.tcin <> t.tcin THEN p.k ELSE 0 END) || '/' ||
         SUM(CASE WHEN p.tcin <> t.tcin THEN p.n ELSE 0 END) AS first_pass_without_dominant,
       ROUND(100.0 * SUM(CASE WHEN p.tcin <> t.tcin THEN p.k ELSE 0 END)
             / NULLIF(SUM(CASE WHEN p.tcin <> t.tcin THEN p.n ELSE 0 END), 0), 1) AS without_pct,
       SUM(CASE WHEN p.tcin <> t.tcin THEN p.ak ELSE 0 END) || '/' ||
         SUM(CASE WHEN p.tcin <> t.tcin THEN p.an ELSE 0 END) AS first_adm_without_dominant
FROM per p JOIN top t ON t.run_id = p.run_id
GROUP BY p.run_id
HAVING SUM(p.n) > 0
ORDER BY p.run_id;
