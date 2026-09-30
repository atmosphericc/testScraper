-- regime: per run -- monitor loss, decoy mix, main-tab 401 rate, flip-race first-shot and every-other-shot wall-1 pass
--
-- mon_sweeps, mon_loss_pct  [STOCK STATS] deltas: (403+429+other)/(200+403+429+other). The counters are
--                           cumulative; a counter that goes DOWN is a monitor restart and its new value
--                           counts from 0 (mon_resets = how many). 429= only exists from 09-2x on.
-- decoys, d424_pct, d401_pct warmup-tab add-to-cart responses (heartbeat decoys), each counted once.
-- shots, resp, r401_pct     main-tab shots / with a response / 401 share of the responses.
-- f1  flip-race FIRST shots: admitted/(admitted + wall1_limited), admitted = admitted_fs+cart+inventory.
--     401s (f1_401) and other/unknown (f1_excl) are excluded from BOTH sides.
-- eo  every other shot (flip_opened_race and is_first both known), same rule. f1/eo are NULL when the run
--     has no [STOCK][FLIP] lines (unknown, not 0).
-- first / later  the same rate split by shot_idx only (defined whenever the run has races).
-- unk shots with gate 'unknown' (no response, or a 429 without a tgt-cart-error-key: every 429 before 08-27).
WITH st AS (
  SELECT run_id, line, sweeps, s200, s403, COALESCE(s429, 0) AS s429, other,
         LAG(sweeps) OVER w AS p_sw, LAG(s200) OVER w AS p200, LAG(s403) OVER w AS p403,
         LAG(COALESCE(s429, 0)) OVER w AS p429, LAG(other) OVER w AS poth
  FROM monitor_stats
  WINDOW w AS (PARTITION BY run_id ORDER BY line)
),
mon AS (
  SELECT run_id,
         SUM(CASE WHEN p_sw IS NULL OR sweeps < p_sw THEN sweeps ELSE sweeps - p_sw END) AS sw,
         SUM(CASE WHEN p_sw IS NULL OR sweeps < p_sw THEN s200 ELSE s200 - p200 END) AS ok,
         SUM(CASE WHEN p_sw IS NULL OR sweeps < p_sw THEN s403 + s429 + other
                  ELSE (s403 - p403) + (s429 - p429) + (other - poth) END) AS bad,
         SUM(CASE WHEN p_sw IS NOT NULL AND sweeps < p_sw THEN 1 ELSE 0 END) AS resets
  FROM st GROUP BY run_id
),
dec AS (
  SELECT run_id, COUNT(*) AS n, SUM(status = 424) AS n424, SUM(status = 401) AS n401
  FROM decoys GROUP BY run_id
),
sh AS (
  SELECT run_id,
    COUNT(*) AS n,
    SUM(status IS NOT NULL) AS resp,
    SUM(status = 401) AS n401,
    SUM(gate = 'unknown') AS unk,
    SUM(flip_opened_race = 1 AND is_first = 1 AND gate IN ('admitted_fs', 'cart', 'inventory')) AS f1_adm,
    SUM(flip_opened_race = 1 AND is_first = 1 AND gate = 'wall1_limited') AS f1_w1,
    SUM(flip_opened_race = 1 AND is_first = 1 AND gate = 'wall2_denied') AS f1_401,
    SUM(flip_opened_race = 1 AND is_first = 1 AND gate IN ('other', 'unknown')) AS f1_excl,
    SUM(flip_opened_race IS NOT NULL AND is_first IS NOT NULL AND NOT (flip_opened_race = 1 AND is_first = 1) AND gate IN ('admitted_fs', 'cart', 'inventory')) AS eo_adm,
    SUM(flip_opened_race IS NOT NULL AND is_first IS NOT NULL AND NOT (flip_opened_race = 1 AND is_first = 1) AND gate = 'wall1_limited') AS eo_w1,
    SUM(flip_opened_race IS NOT NULL AND is_first IS NOT NULL AND NOT (flip_opened_race = 1 AND is_first = 1) AND gate = 'wall2_denied') AS eo_401,
    SUM(is_first = 1 AND gate IN ('admitted_fs', 'cart', 'inventory')) AS fi_adm,
    SUM(is_first = 1 AND gate = 'wall1_limited') AS fi_w1,
    SUM(is_first = 0 AND gate IN ('admitted_fs', 'cart', 'inventory')) AS la_adm,
    SUM(is_first = 0 AND gate = 'wall1_limited') AS la_w1
  FROM shots GROUP BY run_id
)
SELECT r.run_id AS run,
       m.sw AS mon_sweeps,
       ROUND(100.0 * m.bad / NULLIF(m.ok + m.bad, 0), 3) AS mon_loss_pct,
       m.resets AS mon_resets,
       d.n AS decoys,
       ROUND(100.0 * d.n424 / NULLIF(d.n, 0), 1) AS d424_pct,
       ROUND(100.0 * d.n401 / NULLIF(d.n, 0), 1) AS d401_pct,
       s.n AS shots,
       s.resp,
       ROUND(100.0 * s.n401 / NULLIF(s.resp, 0), 2) AS r401_pct,
       CASE WHEN r.flip_log = 1 THEN s.f1_adm || '/' || (s.f1_adm + s.f1_w1) END AS f1,
       CASE WHEN r.flip_log = 1 THEN ROUND(100.0 * s.f1_adm / NULLIF(s.f1_adm + s.f1_w1, 0), 1) END AS f1_pct,
       CASE WHEN r.flip_log = 1 THEN s.f1_401 END AS f1_401,
       CASE WHEN r.flip_log = 1 THEN s.f1_excl END AS f1_excl,
       CASE WHEN r.flip_log = 1 THEN s.eo_adm || '/' || (s.eo_adm + s.eo_w1) END AS eo,
       CASE WHEN r.flip_log = 1 THEN ROUND(100.0 * s.eo_adm / NULLIF(s.eo_adm + s.eo_w1, 0), 2) END AS eo_pct,
       CASE WHEN r.flip_log = 1 THEN s.eo_401 END AS eo_401,
       CASE WHEN s.n IS NOT NULL THEN s.fi_adm || '/' || (s.fi_adm + s.fi_w1) END AS first,
       CASE WHEN s.n IS NOT NULL THEN s.la_adm || '/' || (s.la_adm + s.la_w1) END AS later,
       s.unk
FROM runs r
LEFT JOIN mon m ON m.run_id = r.run_id
LEFT JOIN dec d ON d.run_id = r.run_id
LEFT JOIN sh s ON s.run_id = r.run_id
WHERE m.sw IS NOT NULL OR s.n IS NOT NULL OR d.n IS NOT NULL
ORDER BY r.run_id;
