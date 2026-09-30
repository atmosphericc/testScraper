-- monitor_hours: monitor sweep loss per run x CLOCK HOUR, for like-for-like comparisons (the
-- same hours on a restock night vs a quiet night). A whole-run average hid 09-30's restock-hour
-- degradation: 0.54% whole run vs 1.20% in 02:00-04:59 against 0.040% the same hours on 09-29
-- (C-0930-11). Loss = (403 + 429 + other) / (200 + 403 + 429 + other) from [STOCK STATS]
-- cumulative-counter deltas; a counter that goes DOWN is a monitor restart and counts from 0.
-- Hours are the log's local clock (runs.utc_offset_min says which offset that was).
WITH st AS (
  SELECT run_id, line, substr(ts, 12, 2) AS hh, sweeps, s200, s403, COALESCE(s429, 0) AS s429, other,
         LAG(sweeps) OVER w AS p_sw, LAG(s200) OVER w AS p200, LAG(s403) OVER w AS p403,
         LAG(COALESCE(s429, 0)) OVER w AS p429, LAG(other) OVER w AS poth
  FROM monitor_stats
  WINDOW w AS (PARTITION BY run_id ORDER BY line)
),
d AS (
  SELECT run_id, hh,
         CASE WHEN p_sw IS NULL OR sweeps < p_sw THEN s200 ELSE s200 - p200 END AS ok,
         CASE WHEN p_sw IS NULL OR sweeps < p_sw THEN s403 + s429 + other
              ELSE (s403 - p403) + (s429 - p429) + (other - poth) END AS bad
  FROM st
)
SELECT run_id AS run, hh AS hour, SUM(ok) + SUM(bad) AS sweeps, SUM(bad) AS lost,
       ROUND(100.0 * SUM(bad) / NULLIF(SUM(ok) + SUM(bad), 0), 3) AS loss_pct
FROM d
GROUP BY run_id, hh
ORDER BY run_id, hh;
