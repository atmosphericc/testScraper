-- first_shot_delay: per window x ident, ms from the window OPEN to that ident's FIRST add-to-cart shot (2026-11-04)
--
-- Usage:  python tools/events/q.py first_shot_delay [--run 20261029]     (build the store first: python tools/events/build.py)
-- Pre-registers the readout for the flag-gated TARGET_SENTINEL_DEFER (bulletproof_purchase_manager.py; defers the session
-- sentinel's /account navigation while a window is live / recently flipped / in quiet hours). Motivation C-1104-09: on
-- run_20261029_231605 the sentinel delayed one account's first shot after the open in 3 of 5 windows (2,182 / 7,996 /
-- 621 / 1,927 ms) while the unaffected accounts fired at 28-47 ms.
--
-- PRE-REGISTERED RULE (first night with >= 3 flip windows after TARGET_SENTINEL_DEFER is armed)
--   PASS    in >= 90% of the (window, ident) cells where the ident fired, first_delay_ms <= 150 (Result 2 pass_pct),
--           AND no increase of ERR_UNAUTHORIZED 401s / guest rotations versus run_20261029_231605 (0 in the drop hours;
--           Result 3 err_unauth is the shot-side count; guest rotations are NOT in the store, use the grep below).
--   FAIL    any window with a first_delay_ms > 1000 attributable to a [SENTINEL] navigation (a sentinel line within 10 s
--           before the shot: Result 4 lists the late first shots with their log line numbers; see the GREP), OR an
--           account went guest / expired unnoticed inside the quiet window (grep below; an absent line is NOT evidence,
--           read the readiness check).
--   NO-DATA anything else (fewer than 3 flip windows, flag not armed that night, or delays 150-1000 ms unattributed).
--
-- COLUMNS
--   delay         shots.window_age_ms = the shot's ts_ms minus windows.first_read_ms. ts_ms is atc_t0 (epoch ms, the moment
--                 the POST was dispatched; the only per-shot ms stamp, the same series windows.sql lag_ms uses). ts_src is
--                 checked: n_logger = shots whose ts_src is NOT atc_t0 (a coarser logger stamp: distrust the cell if > 0).
--                 It is the dispatch stamp, not the wire time (atc_net.send_ms): a sentinel-held page shows as a late atc_t0.
--   window        flip windows (windows.src='flip', shots.window_id) where the run has any; otherwise the read-based
--                 episodes (windows.src='reads', shots.ep_window_id / ep_age_ms). src says which.
--   armed idents  distinct idents with a shot anywhere in the run (n_run_idents); the store has no roster table, so an
--                 ident that never fired all night is invisible (NOT ESTABLISHED); one that skipped a window shows as
--                 n_idents_fired < n_run_idents.
--
-- SENTINEL LINES ARE NOT IN THE STORE (build.py has no [SENTINEL] case; the lines are bare print()s with NO timestamp, so
-- a parser could only give a line number). The readout needs Bash grep (logs/runs is gitignored; not the Grep tool):
--   grep -n "\[SENTINEL\]" logs/runs/<run>.log | grep -v "logged_in=True"     # defers, skips, failures
--   grep -c "\[SENTINEL\] defer" logs/runs/<run>.log                           # deferred ticks
--   grep -c "\[SENTINEL\].*logged_in=" logs/runs/<run>.log                     # checks that ran (verdict printed)
--   grep -n "logged_in=False\|guest" logs/runs/<run>.log                       # went guest / expired
-- Attribution: Result 4 gives shots.line for each late first shot. The sentinel verdict line prints AFTER its navigation, so
-- look for a "[SENTINEL] ...logged_in=" line a few lines before first_line, then confirm the time from the nearest
-- timestamped logger line and the shot's atc_t0 epoch (<= 10 s before the shot) before calling it attributable.
--
-- Statements 0-1 create TEMP views and print nothing.
CREATE TEMP VIEW fsd_shots AS
SELECT s.run_id,
       CASE WHEN EXISTS (SELECT 1 FROM windows w WHERE w.run_id = s.run_id AND COALESCE(w.src, 'flip') = 'flip')
            THEN 'flip' ELSE 'reads' END AS src_used,
       CASE WHEN EXISTS (SELECT 1 FROM windows w WHERE w.run_id = s.run_id AND COALESCE(w.src, 'flip') = 'flip')
            THEN s.window_id ELSE s.ep_window_id END AS win,
       CASE WHEN EXISTS (SELECT 1 FROM windows w WHERE w.run_id = s.run_id AND COALESCE(w.src, 'flip') = 'flip')
            THEN s.window_age_ms ELSE s.ep_age_ms END AS age_ms,
       s.ident, s.gate, s.status, s.err_key, s.line, s.ts_ms, s.ts_src, s.tcin
FROM shots s
WHERE s.ident IS NOT NULL;
CREATE TEMP VIEW fsd_cell AS
SELECT run_id, src_used, win, ident,
       MIN(age_ms) AS first_delay_ms,
       (SELECT x.gate FROM fsd_shots x WHERE x.run_id = f.run_id AND x.win = f.win AND x.ident = f.ident
         ORDER BY x.age_ms, x.line LIMIT 1) AS first_gate,
       (SELECT x.line FROM fsd_shots x WHERE x.run_id = f.run_id AND x.win = f.win AND x.ident = f.ident
         ORDER BY x.age_ms, x.line LIMIT 1) AS first_line,
       MIN(tcin) AS tcin,
       COUNT(*) AS shots,
       SUM(ts_src <> 'atc_t0') AS n_logger
FROM fsd_shots f
WHERE win IS NOT NULL AND age_ms IS NOT NULL
GROUP BY run_id, src_used, win, ident;
-- Result 1: one row per run x window x ident (delay of that ident's first shot after the window opened)
SELECT c.run_id AS run, c.src_used AS src, c.win, c.tcin, c.ident,
       c.first_delay_ms, c.first_gate, c.first_line, c.shots, c.n_logger,
       c.first_delay_ms <= 150 AS within150,
       (SELECT COUNT(*) FROM fsd_cell k WHERE k.run_id = c.run_id AND k.win = c.win) AS n_idents_fired,
       (SELECT COUNT(DISTINCT ident) FROM fsd_shots k WHERE k.run_id = c.run_id) AS n_run_idents,
       (SELECT SUM(k.first_delay_ms <= 150) FROM fsd_cell k WHERE k.run_id = c.run_id AND k.win = c.win) AS n_idents_within_150ms,
       (SELECT MAX(k.first_delay_ms) FROM fsd_cell k WHERE k.run_id = c.run_id AND k.win = c.win) AS max_delay_ms
FROM fsd_cell c
ORDER BY c.run_id, c.win, c.ident;
-- Result 2: per run, windows with every fired ident within 150 ms vs not, and the cell-level pass_pct the rule uses
SELECT run_id AS run, MIN(src_used) AS src, COUNT(*) AS windows,
       SUM(all_fired_ok) AS win_fired_ok, COUNT(*) - SUM(all_fired_ok) AS win_not_ok,
       SUM(all_armed_ok) AS win_armed_ok, COUNT(*) - SUM(all_armed_ok) AS win_armed_not_ok,
       SUM(cells) AS cells, SUM(cells_ok) AS cells_ok,
       ROUND(100.0 * SUM(cells_ok) / NULLIF(SUM(cells), 0), 1) AS pass_pct,
       MAX(max_delay) AS max_delay_ms,
       CASE WHEN COUNT(*) < 3 THEN 'NO-DATA(<3 windows)'
            WHEN MAX(max_delay) > 1000 THEN 'FAIL?(>1000ms: attribute via Result 4 + grep)'
            WHEN 100.0 * SUM(cells_ok) / SUM(cells) >= 90 THEN 'PASS(delay half; check Result 3 + grep)'
            ELSE 'NO-DATA(<90%, none >1000)' END AS delay_verdict
FROM (SELECT c.run_id, MIN(c.src_used) AS src_used, c.win,
             COUNT(*) AS cells, SUM(c.first_delay_ms <= 150) AS cells_ok,
             MAX(c.first_delay_ms) AS max_delay,
             MIN(c.first_delay_ms <= 150) AS all_fired_ok,
             (COUNT(*) = (SELECT COUNT(DISTINCT ident) FROM fsd_shots k WHERE k.run_id = c.run_id)
              AND MIN(c.first_delay_ms <= 150)) AS all_armed_ok
      FROM fsd_cell c GROUP BY c.run_id, c.win)
GROUP BY run_id
ORDER BY run_id;
-- Result 3: per run, the shot-side ERR_UNAUTHORIZED count (member token missing/deleted; the KEYLESS 401 is the Shape
-- verdict and is not counted). Baseline run_20261029_231605 = 0. Guest rotations are not in the store: grep.
SELECT run_id AS run,
       SUM(err_key = 'ERR_UNAUTHORIZED') AS err_unauth,
       SUM(status = 401) AS all_401,
       COUNT(*) AS shots
FROM shots
GROUP BY run_id
ORDER BY run_id;
-- Result 4: late first shots (> 1000 ms) = the FAIL candidates. Compare first_line with `grep -n "\[SENTINEL\]"` line numbers.
SELECT run_id AS run, win, ident, tcin, first_delay_ms, first_gate, first_line
FROM fsd_cell
WHERE first_delay_ms > 1000
ORDER BY run_id, win, ident;
