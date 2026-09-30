-- per_tcin: per run x TCIN -- races, windows, shots, wall-1 passes on first and later shots, carts, orders
--
-- w1 = passed the hot-item limiter (any gate except wall1_limited/other/unknown; a 401 counts as a pass).
-- first/later shown as passes/classified (other and unknown excluded). tcin is the race's TCIN; a shot
-- outside any race takes the last [FAST_LANE] Firing TCIN (shots.tcin_src says which).
SELECT s.run_id AS run,
       COALESCE(s.tcin, '?') AS tcin,
       (SELECT COUNT(*) FROM races r WHERE r.run_id = s.run_id AND r.tcin = s.tcin) AS races,
       (SELECT COUNT(*) FROM windows w WHERE w.run_id = s.run_id AND w.tcin = s.tcin) AS windows,
       COUNT(*) AS shots,
       SUM(s.is_first = 1 AND s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory')) || '/' ||
         SUM(s.is_first = 1 AND s.gate NOT IN ('other', 'unknown')) AS first_w1,
       SUM(s.is_first = 0 AND s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory')) || '/' ||
         SUM(s.is_first = 0 AND s.gate NOT IN ('other', 'unknown')) AS later_w1,
       SUM(s.gate = 'wall2_denied') AS n401,
       SUM(s.gate = 'admitted_fs') AS fs,
       SUM(s.gate = 'inventory') AS inv,
       SUM(s.gate = 'cart') AS carts,
       SUM(s.gate IN ('other', 'unknown')) AS other_unknown,
       (SELECT COUNT(*) FROM orders o WHERE o.run_id = s.run_id AND o.tcin = s.tcin) AS orders
FROM shots s
GROUP BY s.run_id, s.tcin
ORDER BY s.run_id, shots DESC;
