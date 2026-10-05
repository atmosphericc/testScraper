-- windows: every stock window ([STOCK][FLIP] new_window=1 -> that TCIN's next one) with its races and shots
--
-- first_read  local time of the window's first in-stock read (the flip's read_ms)
-- span_s      last in-stock read - first read (IN STOCK reads are whole-second [API_CYCLE] times)
-- lag_ms      first race's first shot atc_t0 - first read
-- flip_first  the flip-opened race's first shots, ident:gate
-- w1_pass     wall-1 passes among ALL the window's shots (401 and cart_limit count as passes); later_w1 = among later shots
-- trunc       1 = the per-TCIN flip-log cap (50) was hit, so this window's end is unknown
-- Windows exist only for runs with RESILIENT_FLIP_LOG lines (first data 2026-09-30). Parser v2 also stores
-- read-based episodes (windows.src='reads', for place_orders / shots.ep_*); this query shows flip windows only.
SELECT w.run_id AS run,
       w.window_id AS win,
       w.tcin,
       strftime('%H:%M:%f', w.first_read_ms / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS first_read,
       ROUND((w.last_read_ms - w.first_read_ms) / 1000.0, 1) AS span_s,
       w.reads,
       w.flips,
       w.races,
       w.shots,
       (SELECT x.lag_ms FROM races x
         WHERE x.run_id = w.run_id AND x.window_id = w.window_id AND x.flip_opened = 1) AS lag_ms,
       (SELECT group_concat(g, ' ') FROM (
          SELECT COALESCE(s.ident, '?') || ':' || s.gate AS g
            FROM shots s JOIN races x ON x.run_id = s.run_id AND x.race_seq = s.race_seq
           WHERE s.run_id = w.run_id AND x.window_id = w.window_id AND x.flip_opened = 1
             AND s.is_first = 1
           ORDER BY s.ident)) AS flip_first,
       (SELECT SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) FROM shots s
         WHERE s.run_id = w.run_id AND s.window_id = w.window_id) AS w1_pass,
       (SELECT SUM(s.is_first = 0 AND s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) || '/' ||
               SUM(s.is_first = 0) FROM shots s
         WHERE s.run_id = w.run_id AND s.window_id = w.window_id) AS later_w1,
       (SELECT SUM(s.gate = 'cart') FROM shots s
         WHERE s.run_id = w.run_id AND s.window_id = w.window_id) AS carts,
       w.trunc
FROM windows w
JOIN runs r ON r.run_id = w.run_id
WHERE COALESCE(w.src, 'flip') = 'flip'
ORDER BY w.run_id, w.window_id;
