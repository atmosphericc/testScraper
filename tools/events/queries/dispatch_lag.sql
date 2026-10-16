-- dispatch_lag: flip -> first shot, by ANY race, split into release / tick wait / thread start (parser v6, INS-LAG-1015)
--
-- Usage:  python tools/events/q.py dispatch_lag [--run 20261016]     (build the store first: python tools/events/build.py)
-- Source: INS-LAG-1015 (2026-10-15). Replaces the ad hoc decomp.py / lag_by_busy.py scrapers of the 10-15 session
--   (logs/analysis_2026_10_15/enhance/dispatch_lag/). Reads windows, races, shots, atc_net and lock_events
--   ([PURCHASE_LOCK] start / complete, the only millisecond-stamped dispatch markers: [MULTI_SKU_MISS], [WAVE_YIELD]
--   and [LEVEL_REARM] are bare print()s whose nearest logger line was off by up to 6.7 s). Statement 0 creates the
--   TEMP view dl_windows and prints nothing. Run build.py first: a run still held at parser v5 has no lock_events, so
--   its windows show busy_class 'unknown' (never 'idle') and the verdict says NO DATA.
--
-- WHAT A ROW MEANS (one flip window = windows.src 'flip', [STOCK][FLIP] new_window=1 up to that TCIN's next one)
--   busy_threads  purchase threads holding the lock when the window's first read arrived = lock_events starts minus
--                 completes with ts_ms before first_read_ms (one thread = one account's purchase)
--   busy_class    idle        no thread live at the first read
--                 busy_other  >= 1 thread live, and the first shot came from a race started AT/AFTER the first read (a new
--                             dispatch had to happen: the R-LAG population, less the redispatched ones, see intervening_starts)
--                 busy_same   >= 1 thread live, and the first shot came from a race of this TCIN that was already running
--                             (first race start before first_read: a window-3 racer fired into window 4): no dispatch involved
--                 no_shot     the window has no timed shot (atc_t0)      unknown   the run has no lock_events at all
--   first_shot_any_ms   earliest shot on this TCIN inside the window by ANY race (shots.ep_window_id, ts_src atc_t0) minus
--                 first_read_ms. lag_flip_race_ms is races.lag_ms (the flip-OPENED race only, the series windows.sql prints):
--                 for busy_same windows the two differ (window 4: 7,242 vs 757 ms on run_20261009_000934)
--   first_wire_any_ms   the same minimum in WIRE order: atc_net send_ms (Chrome's wall clock when the POST left) where the
--                 shot's POST is joined, else atc_t0. Ordering claims (the dispatch precedes the first request on the wire:
--                 ord_ok) use this one, as the 10-15 evaluator required: shots.ts_ms is atc_t0, not wire order. The
--                 magnitudes in the R-LAG rule use first_shot_any_ms (the series of the 10-09 baseline)
--   release_ms  first_read to the last [PURCHASE_LOCK] complete (the racers leaving) in [first_read - 200 ms, dispatch)
--   tick_ms     that release (or first_read, when later) to the FIRST start of the dispatch cluster = the wait for the next
--               level-re-arm tick plus the dispatch itself. The dispatch cluster = the last starts at/before the first
--               shot, grouped within 100 ms of the last one (three accounts start within ~3 ms)
--   start_ms    dispatch cluster's first start to the first shot (thread start + the ATC chain up to atc_t0)
--               release_ms + tick_ms + start_ms = first_shot_any_ms exactly. All three are NULL for busy_same / no dispatch
--   intervening_starts  lock starts after the flip but BEFORE the final dispatch cluster: the fleet was sent somewhere else
--               first (window 9 of run_20261009_000934: 3). Such a window's wait is not the plain release-then-tick case
--               [INFERRED: the 10-15 replay_w9.py stub replay puts window 9 on the flicker / defer logic, not on the
--               tick], so the split in statement 4 and the R-LAG verdict in statement 5 leave it out
--   late        first_shot_any_ms > 500 (the hand classification's line)
--
-- PRE-REGISTERED RULE R-LAG (written 2026-10-15, before the 10-16 drop; copied from MISS-PHASE-TICK-1015's measurement;
--   amended in fix round 2, same day, before any 10-16 data was read: redispatched / FAIR-FRESH windows are excluded;
--   amended again in fix round 3, still before any 10-16 data was read: a busy_other window with no dispatch found is excluded)
--   Population: busy_other windows (flip windows opened while another race was live, first shot from a NEW dispatch) that were
--   NOT redispatched (intervening_starts = 0 -- no lock start between the flip and the window's own dispatch), over the runs
--   selected (a drop night: pass --run). A redispatched window is the w9 / FAIR-FRESH class: the fleet was sent to another
--   TCIN first, so its wait is the flicker / defer logic, not the level-re-arm tick that C2 changes, and MISS-PHASE-TICK-1015's
--   evaluator requires it out of the build trigger. A busy_other window whose dispatch cannot be found (intervening_starts NULL:
--   no lock start in [first_read - 200 ms, first shot]) is out of the population too, on both bases, exactly as it is out of
--   statement 4, and is COUNTED in no_dispatch_not_counted, never silently dropped (a pre-data amendment: 0 such windows in
--   the 37 of the 10-09 instrument, so its values do not move). Statistic: first_shot_any_ms. Median = mean of the two middle values (n even),
--   p90 = nearest rank (rank ceil(0.9 n)).
--     n_busy < 3                                    => NO DATA (the rule needs the first dense night: >= 3 busy windows)
--     median > 1,500 ms  OR  >= 20% of them > 2,000 => R-LAG BUILD C2 (MISS-PHASE-TICK-1015: pull the next level-re-arm tick to
--                                                      flip + 0.6 s and poll the yield every 0.1 s)
--     median <= 1,000 ms AND p90 <= 2,000           => R-LAG SKIP C2
--     anything else                                 => R-LAG INCONCLUSIVE (between the lines: keep observing, build nothing)
--   busy_same windows (no dispatch happened), redispatched busy_other windows and busy_other windows with no dispatch found are
--   counted and shown (busy_same_not_counted, redispatched_not_counted, no_dispatch_not_counted in statement 5) but are NOT in
--   the population; the 10-09 baseline below is the 4 other-TCIN windows
--   left (windows 10-13). Fast cadence is not expected to change the busy lag [INFERRED: research workflow d3153804, the
--   lag is tick-bound], so a fast night is a valid sample. The rule says nothing about WHY a window was late: statement 1 shows release / tick / start so a build is
--   aimed at the 74% (tick), not at the 3% (start).
--
-- INSTRUMENT (a reading is trusted only if these still reproduce on the same runs; FAIL = read nothing, find the parser first)
--   run_20261009_000934 [MEASURED 2026-10-15, event store rebuilt at parser v6]: 15 flip windows = 9 idle + 6 busy.
--     busy = window 4 (busy_same: lag_flip_race 7,242 ms, first_shot_any 757 ms) + windows 9-13 (busy_other: first_shot_any
--     6,736 / 2,337 / 945 / 2,727 / 804 ms); all 6 late (> 500 ms), the 9 idle windows 0 late (33-51 ms). lock_events = 370
--     starts + 370 completes. Windows 10-13 (no intervening starts) split release / tick / start = 1,584 / 5,031 / 198 ms of
--     6,813 = 23.2 / 73.8 / 2.9 % (the hand table's 23 / 74 / 3 %; its w13 row read start 35 ms against the third start of the
--     cluster, 5 ms off its own 804 ms lag). Window 9 = 6,736 ms with intervening_starts 3 (redispatched = 1).
--     R-LAG on this run alone (redispatched window 9 and busy_same window 4 out): n_busy 4, median 1,641 ms, p90 2,727 ms,
--     2 of 4 > 2,000 => BUILD C2 (the median alone is over 1,500; with window 9 in it read n_busy 5, median 2,337, 3 of 5, same verdict).
--   the five flip-logged run files (run_20260930_014647, _233818, run_20261002_011217, _021547, run_20261009_000934), 37 flip
--     windows [MEASURED, a worktree store rebuilt from the main logs at parser v6]: 30 idle, 0 of them late (31-110 ms, median 40), 6 busy, 1
--     no_shot (run_20261002_011217: no race ever fired, so no shot; counted as no_shot, never as idle). 0 unknown, ord_bad 0.
--   Statements 1-4 are descriptive: nothing here is an established unit lever [NOT ESTABLISHED]; first-shot pass falls with
--   window age (1/15 at 2-10 s, n small [REPORTED: research workflow d3153804]), the rule only asks whether the busy lag is worth a build.

-- 0) the per-window view (no output)
CREATE TEMP VIEW dl_windows AS
WITH fw AS (
  SELECT w.run_id, w.window_id, w.tcin, w.first_read_ms AS fr, r.utc_offset_min AS off_min,
         (SELECT x.lag_ms FROM races x WHERE x.run_id = w.run_id AND x.window_id = w.window_id
            AND x.flip_opened = 1) AS lag_flip,
         (SELECT x.race_seq FROM races x WHERE x.run_id = w.run_id AND x.window_id = w.window_id
            AND x.flip_opened = 1) AS flip_race
  FROM windows w
  JOIN runs r ON r.run_id = w.run_id
  WHERE COALESCE(w.src, 'flip') = 'flip'
),
sh AS (
  SELECT s.run_id, s.ep_window_id AS window_id, s.line, s.race_seq, s.ident, s.ts_ms AS t0,
         COALESCE((SELECT MIN(n.send_ms) FROM atc_net n
                    WHERE n.run_id = s.run_id AND n.method = 'POST' AND n.shot_line = s.line
                      AND n.shot_ts_ms = s.ts_ms AND n.send_ms IS NOT NULL), s.ts_ms) AS wire
  FROM shots s
  WHERE s.ep_src = 'flip' AND s.ep_window_id IS NOT NULL AND s.ts_src = 'atc_t0' AND s.ts_ms IS NOT NULL
),
rk AS (
  SELECT sh.*,
         ROW_NUMBER() OVER (PARTITION BY run_id, window_id ORDER BY t0, line) AS rn_t0,
         ROW_NUMBER() OVER (PARTITION BY run_id, window_id ORDER BY wire, t0, line) AS rn_wire
  FROM sh
),
fs AS (
  SELECT run_id, window_id,
         MAX(CASE WHEN rn_t0 = 1 THEN t0 END) AS first_t0,
         MAX(CASE WHEN rn_t0 = 1 THEN ident END) AS first_ident,
         MAX(CASE WHEN rn_t0 = 1 THEN race_seq END) AS first_race,
         MAX(CASE WHEN rn_wire = 1 THEN wire END) AS first_wire,
         COUNT(*) AS n_timed
  FROM rk
  GROUP BY run_id, window_id
),
a AS (
  SELECT fw.*, fs.first_t0, fs.first_ident, fs.first_race, fs.first_wire, fs.n_timed,
         (SELECT x.start_ms FROM races x WHERE x.run_id = fw.run_id AND x.race_seq = fs.first_race) AS first_race_start,
         (SELECT COUNT(*) FROM lock_events l WHERE l.run_id = fw.run_id) AS lock_n,
         (SELECT COALESCE(SUM(CASE l.kind WHEN 'start' THEN 1 ELSE -1 END), 0) FROM lock_events l
           WHERE l.run_id = fw.run_id AND l.ts_ms < fw.fr) AS depth,
         (SELECT MAX(l.ts_ms) FROM lock_events l
           WHERE l.run_id = fw.run_id AND l.kind = 'start' AND l.ts_ms >= fw.fr - 200
             AND l.ts_ms <= fs.first_t0) AS disp_last
  FROM fw
  LEFT JOIN fs ON fs.run_id = fw.run_id AND fs.window_id = fw.window_id
),
b AS (
  SELECT a.*,
         CASE WHEN a.lock_n = 0 THEN 'unknown'
              WHEN a.first_t0 IS NULL THEN 'no_shot'
              WHEN a.depth <= 0 THEN 'idle'
              WHEN a.first_race_start < a.fr THEN 'busy_same'
              ELSE 'busy_other' END AS busy_class
  FROM a
),
c AS (
  SELECT b.*,
         CASE WHEN b.busy_class IN ('idle', 'busy_other') THEN
           (SELECT MIN(l.ts_ms) FROM lock_events l
             WHERE l.run_id = b.run_id AND l.kind = 'start' AND l.ts_ms >= b.fr - 200
               AND l.ts_ms >= b.disp_last - 100 AND l.ts_ms <= b.disp_last) END AS disp_first
  FROM b
),
d AS (
  SELECT c.*,
         (SELECT MAX(l.ts_ms) FROM lock_events l
           WHERE l.run_id = c.run_id AND l.kind = 'complete' AND l.ts_ms >= c.fr - 200
             AND l.ts_ms < c.disp_first) AS rel_last,
         (SELECT COUNT(*) FROM lock_events l
           WHERE l.run_id = c.run_id AND l.kind = 'complete' AND l.ts_ms >= c.fr - 200
             AND l.ts_ms < c.disp_first) AS rel_n,
         (SELECT COUNT(*) FROM lock_events l
           WHERE l.run_id = c.run_id AND l.kind = 'start' AND l.ts_ms >= c.fr - 200
             AND l.ts_ms < c.disp_first) AS intervening
  FROM c
)
SELECT d.run_id AS run,
       d.window_id AS win,
       d.tcin,
       strftime('%H:%M:%f', d.fr / 1000.0 + d.off_min * 60, 'unixepoch') AS first_read,
       CASE WHEN d.lock_n = 0 THEN NULL ELSE MAX(d.depth, 0) END AS busy_threads,
       d.busy_class,
       d.rel_n,
       CASE WHEN d.disp_first IS NULL THEN NULL ELSE MAX(COALESCE(d.rel_last, d.fr), d.fr) - d.fr END AS release_ms,
       CASE WHEN d.disp_first IS NULL THEN NULL ELSE d.disp_first - MAX(COALESCE(d.rel_last, d.fr), d.fr) END AS tick_ms,
       CASE WHEN d.disp_first IS NULL THEN NULL ELSE d.first_t0 - d.disp_first END AS start_ms,
       CASE WHEN d.disp_first IS NULL THEN NULL ELSE d.intervening END AS intervening_starts,
       d.lag_flip AS lag_flip_race_ms,
       d.first_t0 - d.fr AS first_shot_any_ms,
       d.first_ident,
       CASE WHEN d.first_race IS NULL OR d.flip_race IS NULL THEN NULL WHEN d.first_race = d.flip_race THEN 1 ELSE 0 END
         AS first_is_flip_race,
       d.first_wire - d.fr AS first_wire_any_ms,
       d.first_wire - d.first_t0 AS wire_minus_t0_ms,
       CASE WHEN d.disp_first IS NULL OR d.first_wire IS NULL THEN NULL
            WHEN d.disp_first <= d.first_wire THEN 1 ELSE 0 END AS ord_ok,
       d.n_timed AS timed_shots,
       CASE WHEN d.first_t0 IS NULL THEN NULL WHEN d.first_t0 - d.fr > 500 THEN 1 ELSE 0 END AS late
FROM d;

-- 1) every flip window: who was busy, how long the first shot took by ANY race, and where the time went
SELECT run, win, tcin, first_read, busy_threads AS busy, busy_class, release_ms, tick_ms, start_ms, intervening_starts AS interv,
       lag_flip_race_ms AS lag_flip_race, first_shot_any_ms, first_ident, first_is_flip_race AS first_is_flip, first_wire_any_ms,
       ord_ok, late
FROM dl_windows
ORDER BY run, win;

-- 2) instrument: per run, the lock balance and what could not be classified (unknown / no_shot are COUNTED, never folded
--    into idle). imbalance = starts - completes (a run that ended mid-purchase leaves > 0). ord_bad = windows whose dispatch
--    start is AFTER the first request left on the wire (a clock or parser fault: anything but 0 means read nothing).
SELECT w.run AS run,
       (SELECT COUNT(*) FROM lock_events l WHERE l.run_id = w.run AND l.kind = 'start') AS lock_starts,
       (SELECT COUNT(*) FROM lock_events l WHERE l.run_id = w.run AND l.kind = 'complete') AS lock_completes,
       (SELECT COUNT(*) FROM lock_events l WHERE l.run_id = w.run AND l.kind = 'start')
         - (SELECT COUNT(*) FROM lock_events l WHERE l.run_id = w.run AND l.kind = 'complete') AS imbalance,
       COUNT(*) AS windows,
       SUM(w.busy_class = 'idle') AS idle,
       SUM(w.busy_class = 'busy_other') AS busy_other,
       SUM(w.busy_class = 'busy_same') AS busy_same,
       SUM(w.busy_class = 'no_shot') AS no_shot,
       SUM(w.busy_class = 'unknown') AS unknown,
       COALESCE(SUM(w.ord_ok = 0), 0) AS ord_bad,
       COALESCE(SUM(w.wire_minus_t0_ms < -10), 0) AS wire_before_t0
FROM dl_windows w
GROUP BY w.run
ORDER BY w.run;

-- 3) by busy_class: windows, late (> 500 ms), and the first_shot_any_ms distribution (median = mean of the middle two,
--    p90 = nearest rank). The hand classification: busy (busy_other + busy_same) 6 of 6 late and idle 0 of 30 late, over the
--    four flip-logged runs. late_flip_race counts the same windows by the flip-opened race's lag (races.lag_ms).
WITH t AS (
  SELECT busy_class AS cls, first_shot_any_ms AS v, lag_flip_race_ms AS lf,
         ROW_NUMBER() OVER (PARTITION BY busy_class ORDER BY first_shot_any_ms) AS rn,
         COUNT(first_shot_any_ms) OVER (PARTITION BY busy_class) AS n
  FROM dl_windows
  WHERE first_shot_any_ms IS NOT NULL
)
SELECT cls AS busy_class,
       COUNT(*) AS windows,
       SUM(v > 500) AS late,
       SUM(lf > 500) AS late_flip_race,
       MIN(v) AS min_ms,
       AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN v END) AS median_ms,
       MAX(CASE WHEN rn = (9 * n + 9) / 10 THEN v END) AS p90_ms,
       MAX(v) AS max_ms
FROM t
GROUP BY cls
ORDER BY cls;

-- 4) where the time goes: busy_other windows with NO intervening dispatch (the clean release / tick / start cases),
--    summed. Run_20261009_000934 windows 10-13 give 1,584 / 5,031 / 198 ms = 23.2 / 73.8 / 2.9 %. redispatched = busy_other
--    windows left out because the fleet was sent elsewhere first (window 9).
SELECT COUNT(*) AS windows,
       SUM(release_ms) AS release_ms, SUM(tick_ms) AS tick_ms, SUM(start_ms) AS start_ms,
       SUM(first_shot_any_ms) AS total_ms,
       ROUND(100.0 * SUM(release_ms) / NULLIF(SUM(first_shot_any_ms), 0), 1) AS release_pct,
       ROUND(100.0 * SUM(tick_ms) / NULLIF(SUM(first_shot_any_ms), 0), 1) AS tick_pct,
       ROUND(100.0 * SUM(start_ms) / NULLIF(SUM(first_shot_any_ms), 0), 1) AS start_pct,
       (SELECT COUNT(*) FROM dl_windows WHERE busy_class = 'busy_other' AND intervening_starts > 0) AS redispatched
FROM dl_windows
WHERE busy_class = 'busy_other' AND release_ms IS NOT NULL AND intervening_starts = 0;

-- 5) R-LAG verdict (pre-registered rule in the header). The rules are per DROP NIGHT: scope says 'POOLED over k runs' when
--    the selection holds more than one run with flip windows -- then pass --run for the night. The verdict is read on
--    first_shot_any_ms (atc_t0, the series of the 10-09 baseline). r_lag_wire is the same rule on first_wire_any_ms (the request
--    leaving the wire, 70-135 ms later than atc_t0 on 10-09): if it differs from r_lag the night sits within one wire gap of a
--    rule line, so say so and read nothing off the verdict. Both read the same population: busy_other and NOT redispatched
--    (the w9 / FAIR-FRESH class, intervening_starts > 0, is counted in redispatched_not_counted and left out; a busy_other window
--    with no dispatch found, intervening_starts NULL, is counted in no_dispatch_not_counted and left out the same way).
WITH p AS (
  SELECT first_shot_any_ms AS v, first_wire_any_ms AS vw,
         ROW_NUMBER() OVER (ORDER BY first_shot_any_ms) AS rn,
         ROW_NUMBER() OVER (ORDER BY first_wire_any_ms) AS rw,
         COUNT(*) OVER () AS n
  FROM dl_windows
  WHERE busy_class = 'busy_other' AND first_shot_any_ms IS NOT NULL AND intervening_starts = 0
),
s AS (
  SELECT COUNT(*) AS n_busy,
         AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN v END) AS median_ms,
         MAX(CASE WHEN rn = (9 * n + 9) / 10 THEN v END) AS p90_ms,
         SUM(v > 2000) AS gt_2s,
         AVG(CASE WHEN rw IN ((n + 1) / 2, (n + 2) / 2) THEN vw END) AS median_wire_ms,
         MAX(CASE WHEN rw = (9 * n + 9) / 10 THEN vw END) AS p90_wire_ms,
         SUM(vw > 2000) AS gt_2s_wire
  FROM p
),
t AS (
  SELECT s.*,
         (SELECT COUNT(*) FROM dl_windows) AS n_win,
         (SELECT COUNT(*) FROM dl_windows WHERE busy_class = 'unknown') AS n_unknown,
         (SELECT COUNT(*) FROM dl_windows WHERE busy_class = 'busy_same') AS n_same,
         (SELECT COUNT(*) FROM dl_windows WHERE busy_class = 'busy_other' AND first_shot_any_ms IS NOT NULL
             AND COALESCE(intervening_starts, 0) > 0) AS n_redisp,
         (SELECT COUNT(*) FROM dl_windows WHERE busy_class = 'busy_other' AND first_shot_any_ms IS NOT NULL
             AND intervening_starts IS NULL) AS n_nodisp
  FROM s
)
SELECT CASE WHEN (SELECT COUNT(DISTINCT run) FROM dl_windows) > 1
            THEN 'POOLED over ' || (SELECT COUNT(DISTINCT run) FROM dl_windows) || ' runs: pass --run for one night'
            ELSE 'one run' END AS scope,
       n_win AS windows, n_unknown AS unknown, n_same AS busy_same_not_counted, n_redisp AS redispatched_not_counted,
       n_nodisp AS no_dispatch_not_counted,
       n_busy, median_ms, p90_ms, gt_2s,
       ROUND(100.0 * gt_2s / NULLIF(n_busy, 0), 1) AS pct_gt_2s,
       CASE WHEN n_win = 0 THEN 'NO DATA (no flip windows)'
            WHEN n_win = n_unknown THEN 'NO DATA (no lock_events: rebuild the store with parser v6)'
            WHEN n_busy < 3 THEN 'NO DATA (< 3 busy windows)'
            WHEN median_ms > 1500 OR 5 * gt_2s >= n_busy THEN 'R-LAG BUILD C2 (MISS-PHASE-TICK-1015)'
            WHEN median_ms <= 1000 AND p90_ms <= 2000 THEN 'R-LAG SKIP C2'
            ELSE 'R-LAG INCONCLUSIVE (between the build and skip lines)' END AS r_lag,
       CASE WHEN n_win = 0 OR n_win = n_unknown OR n_busy < 3 THEN NULL
            WHEN median_wire_ms > 1500 OR 5 * gt_2s_wire >= n_busy THEN 'R-LAG BUILD C2 (MISS-PHASE-TICK-1015)'
            WHEN median_wire_ms <= 1000 AND p90_wire_ms <= 2000 THEN 'R-LAG SKIP C2'
            ELSE 'R-LAG INCONCLUSIVE (between the build and skip lines)' END AS r_lag_wire
FROM t;
