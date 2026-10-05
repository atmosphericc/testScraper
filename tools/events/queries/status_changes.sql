-- status_changes: every [STOCK STATUS] change and SELLABLE-PARSED-OOS line, per run x TCIN
--
-- Needs RESILIENT_STATUS_LOG=1 (armed 2026-10-05, bat:128). One row per TCIN:
--   changes        [STOCK STATUS] lines (first sighting included; key = avail|rtc|svcN|loyalty)
--   first_seen     local time of the first status line (a TCIN published mid-run appears here)
--   sellable_reads changes whose new status is IN_STOCK or PRE_ORDER_SELLABLE
--   in_stock_reads changes the parser read as in stock
--   parsed_oos     SELLABLE-PARSED-OOS lines (Target said sellable, our parser said no) + reasons
--   last_status    the newest key
-- Pre-registered 2026-10-05 (post-run wf_9651193b-efc, rule R-STATUS, CURRENT_STATE):
--   COVERAGE PASS = every enabled TCIN visible to RedSky has >= 1 row (a first sighting);
--   any parsed_oos > 0 = a parser classification gap -> investigate before the next drop;
--   an operator "X restocked" report is CONFIRMED by sellable_reads > 0 for X and REFUTED
--   (for the bot's run window) by sellable_reads = 0 with first_seen before the claimed time.
SELECT s.run_id AS run,
       s.tcin,
       COUNT(*) AS changes,
       strftime('%H:%M:%S', MIN(s.ts_ms) / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS first_seen,
       SUM(s.avail IN ('IN_STOCK', 'PRE_ORDER_SELLABLE')) AS sellable_reads,
       SUM(s.in_stock) AS in_stock_reads,
       (SELECT COUNT(*) FROM sellable_oos o WHERE o.run_id = s.run_id AND o.tcin = s.tcin) AS parsed_oos,
       (SELECT group_concat(DISTINCT o.reason) FROM sellable_oos o
         WHERE o.run_id = s.run_id AND o.tcin = s.tcin) AS oos_reasons,
       (SELECT x.new_key FROM stock_status x WHERE x.run_id = s.run_id AND x.tcin = s.tcin
         ORDER BY x.line DESC LIMIT 1) AS last_status
FROM stock_status s
JOIN runs r ON r.run_id = s.run_id
GROUP BY s.run_id, s.tcin
ORDER BY s.run_id, MIN(s.ts_ms);
