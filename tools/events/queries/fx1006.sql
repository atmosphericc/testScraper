-- fx1006: the 10-06 multi-SKU dispatch fixes, per run x kind (parser v4)
--
-- Usage:  python tools/events/q.py fx1006 [--run 20261007]
-- Readout (CURRENT_STATE MASS-RESTOCK AUDIT): R-FAIR (fair_share + multi_sku_dispatch at one flip read),
-- R-MISS (every miss_rearm TCIN raced later or out of stock first), R-YIELD (every wave_yield followed by a
-- [RACE] on the TCIN it names), R-KEEP (keep_live_failed rows; 0 'VERIFICATION FAILED' lines).
-- Per-line detail: SELECT line, kind, ident_or_acct, detail FROM gate_events WHERE run_id LIKE '%<run>%'
--                  AND kind IN ('fair_share','multi_sku_miss','miss_rearm','wave_yield','keep_live_failed',
--                  'multi_sku_dispatch','fx1006_bought','fx1006_flags') ORDER BY line;
SELECT run_id AS run, kind, COUNT(*) AS n, MIN(line) AS first_line, MAX(line) AS last_line,
       SUM(detail LIKE '%NOT hinted%') AS not_hinted
FROM gate_events
WHERE kind IN ('fx1006_flags', 'multi_sku_dispatch', 'fair_share', 'multi_sku_miss', 'miss_rearm',
               'wave_yield', 'keep_live_failed', 'fx1006_bought')
GROUP BY run_id, kind
ORDER BY run_id, kind;
