-- coverage: per run x TCIN -- did every stock window get raced, and how fast was the first shot?
--
-- windows    [STOCK][FLIP] windows (windows.src='flip'); needs RESILIENT_FLIP_LOG lines (2026-09-30 on)
-- unraced    windows with races=0 -- a flip we never fired at: the C-0924-01 contention skip (every
--            account racing another TCIN), a gate, a dead buyer Chrome, or a window that closed first
-- lag_min/max  flip-opened race's first shot atc_t0 - the window's first in-stock read (ms)
-- reads      in-stock reads summed over the windows
-- Pre-registered 2026-10-04 for the 10-05 03:00 ET 30th Celebration slot (rule R-COVER,
-- .claude/state/CURRENT_STATE.md): PASS = unraced 0 on every target-list TCIN that flipped.
SELECT w.run_id AS run,
       w.tcin,
       COUNT(*) AS windows,
       SUM(COALESCE(w.races, 0) > 0) AS raced,
       SUM(COALESCE(w.races, 0) = 0) AS unraced,
       SUM(w.reads) AS reads,
       MIN((SELECT x.lag_ms FROM races x
             WHERE x.run_id = w.run_id AND x.window_id = w.window_id AND x.flip_opened = 1)) AS lag_min,
       MAX((SELECT x.lag_ms FROM races x
             WHERE x.run_id = w.run_id AND x.window_id = w.window_id AND x.flip_opened = 1)) AS lag_max,
       SUM(COALESCE(w.shots, 0)) AS shots
FROM windows w
WHERE COALESCE(w.src, 'flip') = 'flip'
GROUP BY w.run_id, w.tcin
ORDER BY w.run_id, MIN(w.first_read_ms);
