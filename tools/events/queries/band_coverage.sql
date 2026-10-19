-- band_coverage: was the bot MONITORING during the 02:00-05:30 local band, per calendar date, and did any stock window open in it?
--
-- Source: readiness audit 2026-10-18 (frame1.3, F4). Reproduces the weekday table in
--   logs/analysis_2026_10_18/cadence/bands.py (since 2026-09-01, covered bands only):
--     Fri 7 covered / 6 with a window, Wed 4 / 3, Thu 2 / 1, Sat-Tue 13 / 0.
-- Pre-registered readout (statement 2 IS the table; do not recompute it by hand):
--     covered_bands = dates whose band has > 0 min of monitor_stats coverage
--     with_window   = covered dates with >= 1 window whose first in-stock read falls in the band
-- Statement 1 creates the TEMP view band_days and prints nothing. Statement 2 prints the weekday table, statement 3 the
-- per-date rows (newest last).
--
-- DEFINITIONS
--   band         [date 02:00:00, date 05:30:00) on the log's local clock (monitor_stats.ts; windows.first_read_ms is shifted by
--                runs.utc_offset_min to the same clock).
--   covered_min  minutes of the band inside a monitor interval. A monitor_stats row ([STOCK STATS], 30 s cadence) covers
--                (ts - 30 s, ts]; rows are chained into one interval while the gap between consecutive rows is <= 120 s
--                (bands.py: start-of-row to previous end <= 90 s). A full band is 210 min.
--   win_in_band  windows (ALL src: 'flip' and read-based 'reads') whose first_read falls in the band; tcins = distinct TCINs.
--   prev_mon_end the last monitor interval end at or before the band start; gap_h = band start - prev_mon_end. A large gap_h with
--                covered_min 0 is a night the bot was not running (host off, stopped, not restarted). When the band IS covered by a
--                long-running interval that began before it, prev_mon_end/gap_h describe the stop BEFORE that interval (not a gap in the band).
--   prev_run / prev_run_last_ts  the run log whose [first_ts, last_ts + 60 s] contains prev_mon_end, and that log's last line.
--                The store keeps NO shutdown marker: whether that run ended 'Shutdown complete' (graceful), 'Force-exit on second
--                Ctrl+C' or truncated (power loss / kill) is only in the log tail -- read logs/runs/<prev_run>.log.
--   Parser-version caveat: windows exist only for runs with RESILIENT_FLIP_LOG lines (first data 2026-09-30) or read-based
--   episodes (parser v2+); a store with stale parser versions (4 vs 7) under-counts windows -- rebuild is the main session's choice.
--   The date range starts at 2026-09-01 (edit since_date below) and ends at the last monitor_stats date.
DROP VIEW IF EXISTS band_days;
CREATE TEMP VIEW band_days AS
WITH cfg AS (SELECT '2026-09-01' AS since_date),
rows_ AS (
  SELECT julianday(substr(ts, 1, 19)) AS t FROM monitor_stats
),
chain AS (
  SELECT t, CASE WHEN LAG(t) OVER (ORDER BY t) IS NULL OR t - LAG(t) OVER (ORDER BY t) > 120.0 / 86400.0 THEN 1 ELSE 0 END AS brk
  FROM rows_
),
isl AS (
  SELECT MIN(t) - 30.0 / 86400.0 AS s, MAX(t) AS e
  FROM (SELECT t, SUM(brk) OVER (ORDER BY t) AS g FROM chain)
  GROUP BY g
),
days(d) AS (
  SELECT (SELECT since_date FROM cfg)
  UNION ALL
  SELECT date(d, '+1 day') FROM days
  WHERE d < (SELECT date(MAX(substr(ts, 1, 10))) FROM monitor_stats)
),
bd AS (
  SELECT d, julianday(d || ' 02:00:00') AS a, julianday(d || ' 05:30:00') AS b FROM days
)
SELECT bd.d AS date,
       CASE strftime('%w', bd.d) WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue' WHEN '3' THEN 'Wed'
            WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' ELSE 'Sat' END AS wd,
       ROUND(COALESCE((SELECT SUM(MAX(0.0, MIN(isl.e, bd.b) - MAX(isl.s, bd.a))) FROM isl), 0.0) * 1440.0, 1) AS covered_min,
       (SELECT COUNT(*) FROM windows w JOIN runs r ON r.run_id = w.run_id
         WHERE julianday(datetime(w.first_read_ms / 1000 + r.utc_offset_min * 60, 'unixepoch')) >= bd.a
           AND julianday(datetime(w.first_read_ms / 1000 + r.utc_offset_min * 60, 'unixepoch')) < bd.b) AS win_in_band,
       (SELECT COUNT(DISTINCT w.tcin) FROM windows w JOIN runs r ON r.run_id = w.run_id
         WHERE julianday(datetime(w.first_read_ms / 1000 + r.utc_offset_min * 60, 'unixepoch')) >= bd.a
           AND julianday(datetime(w.first_read_ms / 1000 + r.utc_offset_min * 60, 'unixepoch')) < bd.b) AS tcins,
       (SELECT strftime('%Y-%m-%d %H:%M:%S', MAX(isl.e)) FROM isl WHERE isl.e <= bd.a) AS prev_mon_end,
       ROUND((bd.a - (SELECT MAX(isl.e) FROM isl WHERE isl.e <= bd.a)) * 24.0, 1) AS gap_h,
       (SELECT r.run_id FROM runs r
         WHERE julianday(substr(r.first_ts, 1, 19)) <= (SELECT MAX(isl.e) FROM isl WHERE isl.e <= bd.a)
           AND (SELECT MAX(isl.e) FROM isl WHERE isl.e <= bd.a) <= julianday(substr(r.last_ts, 1, 19)) + 60.0 / 86400.0
         ORDER BY r.first_ts DESC LIMIT 1) AS prev_run,
       (SELECT substr(r.last_ts, 1, 19) FROM runs r
         WHERE julianday(substr(r.first_ts, 1, 19)) <= (SELECT MAX(isl.e) FROM isl WHERE isl.e <= bd.a)
           AND (SELECT MAX(isl.e) FROM isl WHERE isl.e <= bd.a) <= julianday(substr(r.last_ts, 1, 19)) + 60.0 / 86400.0
         ORDER BY r.first_ts DESC LIMIT 1) AS prev_run_last_ts
FROM bd;

-- 2) the weekday table (covered = any monitor coverage in the band)
SELECT wd AS weekday,
       SUM(covered_min > 0) AS covered_bands,
       SUM(covered_min > 0 AND win_in_band > 0) AS with_window,
       SUM(covered_min > 0 AND win_in_band = 0) AS covered_empty,
       SUM(covered_min = 0) AS not_monitored,
       ROUND(SUM(CASE WHEN covered_min > 0 THEN covered_min END) / NULLIF(SUM(covered_min > 0), 0), 1) AS avg_covered_min
FROM band_days
GROUP BY wd
ORDER BY CASE wd WHEN 'Mon' THEN 1 WHEN 'Tue' THEN 2 WHEN 'Wed' THEN 3 WHEN 'Thu' THEN 4 WHEN 'Fri' THEN 5 WHEN 'Sat' THEN 6 ELSE 7 END;

-- 3) per date
SELECT * FROM band_days ORDER BY date;
