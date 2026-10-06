-- atc_net: the wire under each add-to-cart ([ATC_NET]) -- coverage per run, then the first volley of flip races
--
-- Usage:  python tools/events/q.py atc_net [--run 20261006]
-- Source: [ATC_NET] (parser v3, 2026-10-05; TARGET_ATC_NET_META=1, purchase_executor.py atc_net_line): one
--   print-only line per MAIN-tab cart_items response (the POST, its OPTIONS preflight, any other method).
--   send_ms = Chrome's wall-clock ms when the request left -- its own clock, not the print time.
--   Each POST row is joined one-to-one to a shot of the same ident by join_dt_ms = send_ms - atc_t0, inside
--   [-10, atc_rt + 10] ms (a request leaves after its JS start and before its response headers; build.py
--   module doc, ATC_NET). An unjoined row keeps NULL shot columns and is COUNTED (post_unjoined here,
--   ATC_NET@post_unjoined in unparsed) -- never folded into a shot.
-- Armed? TARGET_ATC_NET_META defaults to '0'. An empty result means "not armed" until the bat says
--   otherwise; installed = the per-ident install lines (checks.atc_net_installed).
-- No pass/fail rule is pre-registered here: pre-register one before reading a drop through 2-4.
--
-- 1) coverage per run (runs with an [ATC_NET] line or an install line). shots = every shots row (all are
--    main-tab add-to-cart requests); t0_shots = those with a JS atc_t0 (the only joinable ones);
--    t0_no_post = t0_shots - post_joined (instrument off for that ident, a dropped Network event, or a
--    window miss). q_ts / q_ts_rt / q_multi = the joined POSTs by join_q (ts: dt <= 300 ms; ts_rt: a
--    longer pause, still before the response; multi: picked among >1 candidate -- read join_dt_ms).
--    fo_races_2shots = flip-opened races with >= 2 shots; fo_volleys = those whose first volley has
--    >= 2 joined POSTs, i.e. the population of 2-4.
-- 2-4) the FIRST VOLLEY of each flip-opened race (races.flip_opened = 1; NULL in a run without the flip
--    log, so such runs print nothing): one row per account's first shot (is_first = 1) whose POST is
--    joined, volleys with >= 2 such rows only. A re-shot exists only because the same account's previous
--    shot failed (agent-context 2B corollary), so re-shots never sit in a rank.
--    w1_pass = past the edge limiter (wall2_denied / admitted_fs / cart / inventory / cart_limit, as in
--    walls); limited = wall1_limited; other_unk = other + unknown, shown, never folded in;
--    pass_pct = w1_pass / (w1_pass + limited). Three accounts give at most 3 rows per volley: read n.
--    2) wire_rank = arrival order by send_ms within the volley (RANK: a same-ms tie shares a rank);
--       behind_ms = send_ms - the volley's first send_ms.
--    3) reused = Chrome sent the POST on an already-open connection (1) or a new one (0); '?' = not reported.
--    4) edge_ip = the remote address the POST went to (remoteIPAddress); rank1 = volleys it arrived first in.

-- 1) coverage per run
WITH n AS (
  SELECT run_id,
         COUNT(*) AS net_lines,
         SUM(method = 'POST') AS post_lines,
         SUM(method = 'OPTIONS') AS options_lines,
         SUM(method IS NULL OR method NOT IN ('POST', 'OPTIONS')) AS other_lines,
         SUM(method = 'POST' AND shot_line IS NOT NULL) AS post_joined,
         SUM(method = 'POST' AND join_q = 'ts') AS q_ts,
         SUM(method = 'POST' AND join_q = 'ts_rt') AS q_ts_rt,
         SUM(method = 'POST' AND join_q = 'ts_multi') AS q_multi,
         SUM(method = 'POST' AND shot_line IS NULL) AS post_unjoined,
         SUM(method = 'OPTIONS' AND shot_line IS NOT NULL) AS options_joined
  FROM atc_net GROUP BY run_id
),
r AS (
  SELECT run_id FROM atc_net
  UNION
  SELECT run_id FROM checks WHERE name IN ('atc_net_installed', 'atc_net_install_failed', 'atc_net_enable_failed')
),
s AS (
  SELECT run_id, COUNT(*) AS shots, SUM(ts_src = 'atc_t0') AS t0_shots FROM shots GROUP BY run_id
),
fo AS (
  SELECT run_id, COUNT(*) AS fo_races_2shots FROM races WHERE flip_opened = 1 AND n_shots >= 2 GROUP BY run_id
),
fv AS (
  SELECT run_id, COUNT(*) AS fo_volleys FROM (
    SELECT run_id, race_seq FROM atc_net
    WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
    GROUP BY run_id, race_seq HAVING COUNT(*) >= 2)
  GROUP BY run_id
)
SELECT r.run_id AS run,
       COALESCE(n.net_lines, 0) AS net_lines,
       COALESCE(n.post_lines, 0) AS post_lines,
       COALESCE(n.options_lines, 0) AS options_lines,
       COALESCE(n.other_lines, 0) AS other_lines,
       COALESCE(s.shots, 0) AS shots,
       COALESCE(s.t0_shots, 0) AS t0_shots,
       COALESCE(n.post_joined, 0) AS post_joined,
       ROUND(100.0 * n.post_joined / NULLIF(n.post_lines, 0), 1) AS joined_pct,
       COALESCE(n.post_unjoined, 0) AS post_unjoined,
       COALESCE(s.t0_shots, 0) - COALESCE(n.post_joined, 0) AS t0_no_post,
       COALESCE(n.q_ts, 0) AS q_ts,
       COALESCE(n.q_ts_rt, 0) AS q_ts_rt,
       COALESCE(n.q_multi, 0) AS q_multi,
       COALESCE(n.options_joined, 0) AS options_joined,
       COALESCE(fo.fo_races_2shots, 0) AS fo_races_2shots,
       COALESCE(fv.fo_volleys, 0) AS fo_volleys,
       (SELECT c.value FROM checks c WHERE c.run_id = r.run_id AND c.name = 'atc_net_installed') AS installed
FROM r
LEFT JOIN n ON n.run_id = r.run_id
LEFT JOIN s ON s.run_id = r.run_id
LEFT JOIN fo ON fo.run_id = r.run_id
LEFT JOIN fv ON fv.run_id = r.run_id
ORDER BY 1;

-- 2) first volley: wire-arrival order vs the edge limiter
WITH fv AS (
  SELECT run_id, race_seq, ident, send_ms, reused, ip, shot_gate AS gate
  FROM atc_net
  WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
),
v AS (
  SELECT run_id, race_seq, MIN(send_ms) AS t_first FROM fv GROUP BY run_id, race_seq HAVING COUNT(*) >= 2
),
k AS (
  SELECT fv.*, RANK() OVER (PARTITION BY fv.run_id, fv.race_seq ORDER BY fv.send_ms) AS wire_rank,
         fv.send_ms - v.t_first AS behind_ms
  FROM fv JOIN v ON v.run_id = fv.run_id AND v.race_seq = fv.race_seq
)
SELECT run_id AS run,
       wire_rank,
       COUNT(*) AS n,
       SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS w1_pass,
       SUM(gate = 'wall1_limited') AS limited,
       SUM(gate IS NULL OR gate IN ('other', 'unknown')) AS other_unk,
       ROUND(100.0 * SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit'))
             / NULLIF(SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit',
                                   'wall1_limited')), 0), 1) AS pass_pct,
       ROUND(AVG(behind_ms), 0) AS avg_behind_ms,
       MAX(behind_ms) AS max_behind_ms
FROM k
GROUP BY run_id, wire_rank
ORDER BY run_id, wire_rank;

-- 3) first volley: connection reuse vs the edge limiter
WITH fv AS (
  SELECT run_id, race_seq, ident, send_ms, reused, ip, shot_gate AS gate
  FROM atc_net
  WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
),
v AS (
  SELECT run_id, race_seq FROM fv GROUP BY run_id, race_seq HAVING COUNT(*) >= 2
),
k AS (
  SELECT fv.*, RANK() OVER (PARTITION BY fv.run_id, fv.race_seq ORDER BY fv.send_ms) AS wire_rank
  FROM fv JOIN v ON v.run_id = fv.run_id AND v.race_seq = fv.race_seq
)
SELECT run_id AS run,
       COALESCE(CAST(reused AS TEXT), '?') AS reused,
       COUNT(*) AS n,
       SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS w1_pass,
       SUM(gate = 'wall1_limited') AS limited,
       SUM(gate IS NULL OR gate IN ('other', 'unknown')) AS other_unk,
       ROUND(100.0 * SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit'))
             / NULLIF(SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit',
                                   'wall1_limited')), 0), 1) AS pass_pct,
       SUM(wire_rank = 1) AS rank1
FROM k
GROUP BY run_id, 2
ORDER BY run_id, 2;

-- 4) first volley: edge IP vs the edge limiter
WITH fv AS (
  SELECT run_id, race_seq, ident, send_ms, reused, ip, shot_gate AS gate
  FROM atc_net
  WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
),
v AS (
  SELECT run_id, race_seq FROM fv GROUP BY run_id, race_seq HAVING COUNT(*) >= 2
),
k AS (
  SELECT fv.*, RANK() OVER (PARTITION BY fv.run_id, fv.race_seq ORDER BY fv.send_ms) AS wire_rank
  FROM fv JOIN v ON v.run_id = fv.run_id AND v.race_seq = fv.race_seq
)
SELECT run_id AS run,
       COALESCE(ip, '?') AS edge_ip,
       COUNT(*) AS n,
       COUNT(DISTINCT ident) AS idents,
       SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS w1_pass,
       SUM(gate = 'wall1_limited') AS limited,
       SUM(gate IS NULL OR gate IN ('other', 'unknown')) AS other_unk,
       ROUND(100.0 * SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit'))
             / NULLIF(SUM(gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit',
                                   'wall1_limited')), 0), 1) AS pass_pct,
       SUM(wire_rank = 1) AS rank1
FROM k
GROUP BY run_id, 2
ORDER BY run_id, n DESC, 2;
