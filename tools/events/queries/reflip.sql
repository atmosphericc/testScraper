-- reflip: R-REFLIP -- do later shots within 2 s after a RE-FLIP get past the edge limiter more often than other later shots?
--
-- Usage:  python tools/events/q.py reflip [--run 20261016]          (build the store first: python tools/events/build.py)
-- Source: INS-LAG-1015 (2026-10-15). Reads flips, shots, windows and atc_net (any parser version from v3 on). Statement 1 creates
--   the TEMP view rf_shots and prints nothing. Decides whether a FLIP-WAKE (wake a fast racer's 3-4.5 s sleep on its own TCIN's
--   re-flip) has any basis; today it has none: run_20261009_000934 had 9 later shots within 2 s after a re-flip, 0 past the limiter.
--
-- DEFINITIONS
--   re-flip      a flips row with new_window = 0: the TCIN read out of stock and back in stock inside a window that was already
--                open (the [STOCK][FLIP] line, read_ms = the read's epoch ms). new_window = 1 opens a window and is not a re-flip.
--   later shot   a shot with is_first = 0 inside a flip window (shots.ep_src 'flip', timed by atc_t0): a re-shot, which exists only
--                because the account's previous shot failed -- selection effect of agent-context 2B: both groups below are
--                conditioned on prior failure, read the RATIO between them, never a rate against the first volley.
--   after_reflip a later shot of the re-flip's own TCIN whose time t is in [read_ms, read_ms + 2000) of at least one re-flip (EXISTS:
--                a shot after two re-flips 1 s apart counts once). t = the WIRE time where the shot's POST is joined (atc_net
--                send_ms, Chrome's clock when the request left), else atc_t0: an ordering claim against a flip read must not use
--                shots.ts_ms where the wire time exists (atc_t0 is 70-135 ms EARLIER than the wire time on 10-09). after_reflip_t0 is the same count on
--                the atc_t0 basis (the basis of the hand number 9); the two can differ by a shot within ~100 ms of either edge of the 2 s window.
--   other        every other later shot in a flip window that has a flip log. A shot in a window whose per-TCIN flip cap (50) was hit,
--                and later than 2 s after the last LOGGED flip, cannot be told apart from a shot after an unlogged re-flip: it is
--                counted as capped, never as 'other'.
--   gate buckets past = wall2_denied / admitted_fs / cart / inventory / cart_limit (past the edge limiter), limited = wall1_limited,
--                unclassified = other + unknown + NULL (counted, shown, left out of the rate: 2C). rate = past / (past + limited).
--
-- PRE-REGISTERED RULE R-REFLIP (written 2026-10-15, before the 10-16 drop; copied from INS-LAG-1015's measurement)
--   Over the runs selected (a drop night: pass --run):
--     n_reflip (later shots within 2 s after a re-flip) >= 60  AND  rate_reflip >= 2 x rate_other  AND  past_reflip >= 1
--        => R-REFLIP MET: design FLIP-WAKE (a DESIGN, not a build: window age is not controlled -- the age-matched rate below
--           is information only, and a shot-path change still needs offline tests and an Opus verifier)
--     otherwise => R-REFLIP NOT MET: drop FLIP-WAKE.   (past_reflip >= 1 is added here: with rate_other = 0 the ratio rule is vacuous
--           at 0 >= 0, and a night with no pass in the re-flip group has shown nothing.)
--   No flip log, or no later shot after any re-flip, is NO DATA, never NOT MET. 0 < n_reflip < 60 prints NOT MET (n < 60): the rule
--   says drop it, and the sample is too small to say the effect is absent -- the line says both.
--
-- INSTRUMENT (a reading is trusted only if these still reproduce on the same run; FAIL = read nothing, find the parser first)
--   run_20261009_000934 [MEASURED 2026-10-15, event store rebuilt at parser v6]: 172 re-flip rows; after_reflip_t0 = 9 later shots,
--     0 past the limiter (8 wall1_limited + 1 other = the 503/431-class answer), all in flip windows. On the wire basis the count is
--     10 (9 limited + 1 other): one more shot sits within ~100 ms of an edge. 17 FIRST shots also fall within 2 s after a re-flip
--     (all limited); they are not part of this rule. Over the five flip-logged run files (09-30 x2, 10-02 x2, 10-09) the pooled
--     count is the same 10: no other night has a later shot within 2 s after a re-flip.

-- 0) the shot view (no output): later shots in flip windows, with their wire time and the re-flip tests
CREATE TEMP VIEW rf_shots AS
WITH s0 AS (
  SELECT s.run_id, s.line, s.tcin, s.ident, s.gate, s.ep_window_id AS window_id, s.ep_age_ms, s.ts_ms AS t0,
         COALESCE((SELECT MIN(n.send_ms) FROM atc_net n
                    WHERE n.run_id = s.run_id AND n.method = 'POST' AND n.shot_line = s.line
                      AND n.shot_ts_ms = s.ts_ms AND n.send_ms IS NOT NULL), s.ts_ms) AS wire
  FROM shots s
  WHERE s.is_first = 0 AND s.ep_src = 'flip' AND s.ts_src = 'atc_t0' AND s.ts_ms IS NOT NULL AND s.tcin IS NOT NULL
)
SELECT s0.*,
       CASE WHEN EXISTS (SELECT 1 FROM flips f WHERE f.run_id = s0.run_id AND f.tcin = s0.tcin AND f.new_window = 0
                            AND s0.wire >= f.read_ms AND s0.wire < f.read_ms + 2000) THEN 1 ELSE 0 END AS after_reflip,
       CASE WHEN EXISTS (SELECT 1 FROM flips f WHERE f.run_id = s0.run_id AND f.tcin = s0.tcin AND f.new_window = 0
                            AND s0.t0 >= f.read_ms AND s0.t0 < f.read_ms + 2000) THEN 1 ELSE 0 END AS after_reflip_t0,
       CASE WHEN EXISTS (SELECT 1 FROM windows w WHERE w.run_id = s0.run_id AND w.window_id = s0.window_id
                            AND COALESCE(w.src, 'flip') = 'flip' AND w.trunc = 1)
                 AND s0.wire >= 2000 + COALESCE((SELECT MAX(f.read_ms) FROM flips f WHERE f.run_id = s0.run_id
                            AND f.tcin = s0.tcin AND f.window_id = s0.window_id), 0)
            THEN 1 ELSE 0 END AS capped,
       CASE WHEN s0.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 'past'
            WHEN s0.gate = 'wall1_limited' THEN 'limited' ELSE 'unclassified' END AS bucket
FROM s0;

-- 1) per run: later shots after a re-flip (wire basis, and the atc_t0 basis in after_reflip_t0) vs the other later shots, with the
--    gate buckets. first_shot_unknown = flip-window shots whose is_first is NULL (cannot be called first or later: counted, not
--    folded in). reflips = new_window = 0 flip rows of the run.
SELECT r.run_id AS run,
       (SELECT COUNT(*) FROM flips f WHERE f.run_id = r.run_id AND f.new_window = 0) AS reflips,
       SUM(x.after_reflip = 1) AS after_reflip,
       SUM(x.after_reflip_t0 = 1) AS after_reflip_t0,
       SUM(x.after_reflip = 1 AND x.bucket = 'past') AS re_past,
       SUM(x.after_reflip = 1 AND x.bucket = 'limited') AS re_limited,
       SUM(x.after_reflip = 1 AND x.bucket = 'unclassified') AS re_unclass,
       ROUND(100.0 * SUM(x.after_reflip = 1 AND x.bucket = 'past')
             / NULLIF(SUM(x.after_reflip = 1 AND x.bucket <> 'unclassified'), 0), 1) AS re_past_pct,
       SUM(x.after_reflip = 0 AND x.capped = 0) AS other,
       SUM(x.after_reflip = 0 AND x.capped = 0 AND x.bucket = 'past') AS ot_past,
       SUM(x.after_reflip = 0 AND x.capped = 0 AND x.bucket = 'limited') AS ot_limited,
       SUM(x.after_reflip = 0 AND x.capped = 0 AND x.bucket = 'unclassified') AS ot_unclass,
       ROUND(100.0 * SUM(x.after_reflip = 0 AND x.capped = 0 AND x.bucket = 'past')
             / NULLIF(SUM(x.after_reflip = 0 AND x.capped = 0 AND x.bucket <> 'unclassified'), 0), 1) AS ot_past_pct,
       SUM(x.after_reflip = 0 AND x.capped = 1) AS capped,
       (SELECT COUNT(*) FROM shots s WHERE s.run_id = r.run_id AND s.ep_src = 'flip' AND s.is_first IS NULL) AS first_shot_unknown
FROM runs r
LEFT JOIN rf_shots x ON x.run_id = r.run_id
WHERE EXISTS (SELECT 1 FROM flips f WHERE f.run_id = r.run_id)
GROUP BY r.run_id
ORDER BY r.run_id;

-- 2) the verdict (pre-registered rule in the header). scope says 'POOLED over k runs' when the selection holds more than one run with a
--    flip log -- then pass --run for the night. reflip_pct / other_pct = past / (past + limited) over the classified shots.
--    other_age_ge_2s_* = the same for other later shots at window age >= 2 s (INFORMATION ONLY: the window-age control of agent-context
--    2B, it never gates the verdict).
WITH g AS (
  SELECT (SELECT COUNT(DISTINCT run_id) FROM flips) AS n_runs,
         (SELECT COUNT(*) FROM flips WHERE new_window = 0) AS reflips,
         SUM(after_reflip = 1) AS n_reflip,
         SUM(after_reflip = 1 AND bucket = 'past') AS re_past,
         SUM(after_reflip = 1 AND bucket = 'limited') AS re_limited,
         SUM(after_reflip = 0 AND capped = 0) AS n_other,
         SUM(after_reflip = 0 AND capped = 0 AND bucket = 'past') AS ot_past,
         SUM(after_reflip = 0 AND capped = 0 AND bucket = 'limited') AS ot_limited,
         SUM(after_reflip = 0 AND capped = 0 AND ep_age_ms >= 2000 AND bucket = 'past') AS old_past,
         SUM(after_reflip = 0 AND capped = 0 AND ep_age_ms >= 2000 AND bucket = 'limited') AS old_limited
  FROM rf_shots
),
h AS (
  SELECT g.*,
         1.0 * re_past / NULLIF(re_past + re_limited, 0) AS re_rate,
         1.0 * ot_past / NULLIF(ot_past + ot_limited, 0) AS ot_rate,
         1.0 * old_past / NULLIF(old_past + old_limited, 0) AS old_rate
  FROM g
)
SELECT CASE WHEN n_runs > 1 THEN 'POOLED over ' || n_runs || ' runs: pass --run for one night' ELSE 'one run' END AS scope,
       reflips, n_reflip AS later_after_reflip, re_past, re_limited,
       ROUND(100.0 * re_rate, 1) AS reflip_pct,
       n_other AS later_other, ot_past, ot_limited,
       ROUND(100.0 * ot_rate, 1) AS other_pct,
       ROUND(100.0 * old_rate, 1) AS other_age_ge_2s_pct,
       CASE WHEN n_runs = 0 THEN 'NO DATA (no flip log in the selected runs)'
            WHEN COALESCE(n_reflip, 0) = 0 THEN 'NO DATA (no later shot within 2 s after a re-flip)'
            WHEN ot_rate IS NULL THEN 'NO DATA (no classified other later shot to compare with)'
            WHEN n_reflip < 60 THEN 'R-REFLIP NOT MET (n < 60): drop FLIP-WAKE per the rule; the sample is too small to say the effect is absent'
            WHEN re_past >= 1 AND (re_rate >= 2 * COALESCE(ot_rate, 0)) THEN 'R-REFLIP MET: design FLIP-WAKE (a design, not a build)'
            ELSE 'R-REFLIP NOT MET (rate): drop FLIP-WAKE' END AS r_reflip
FROM h;
