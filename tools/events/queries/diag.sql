-- diag: run diagnostics per run (parser v4) -- stalls, health snapshots, monitor 'other' minutes, /16 steering
--
-- Usage:  python tools/events/q.py diag [--run 20261007]
-- stall rows: a core lock held >= 45 s or the stock sweep stuck >= 90 s; the thread stacks follow that line in
-- the run log (grep -n "STALL_WATCHDOG" logs/runs/run_<id>.log). health: last row = end-of-run resources.
SELECT run_id AS run, kind, COUNT(*) AS n, MIN(line) AS first_line, MAX(line) AS last_line,
       (SELECT g2.detail FROM gate_events g2 WHERE g2.run_id = g.run_id AND g2.kind = g.kind
         ORDER BY g2.line DESC LIMIT 1) AS last_detail
FROM gate_events g
WHERE kind IN ('stall', 'stall_watchdog_armed', 'stall_watchdog_error', 'health', 'monitor_other', 'subnet_steer')
GROUP BY run_id, kind
ORDER BY run_id, kind;
