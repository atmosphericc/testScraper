-- shadow206: does a RedSky 206 body agree with an adjacent 200 read? (per run x clock hour)
--
-- Needs RESILIENT_206_INGEST=shadow (armed 2026-10-05, bat:129): one [STOCK][206-SHADOW]
-- line a minute. paired = TCIN reads from a qualifying 206 whose TCIN had a 200-sourced
-- read <= 2 s old; agree / disagree compare in_stock. in_206 = TCINs a 206 read as in stock.
-- Pre-registered 2026-10-05 (post-run wf_9651193b-efc, rule R-206SHADOW, CURRENT_STATE):
--   ELIGIBLE to build FS-206-INGEST = paired >= 1,000 with disagree = 0 over the run(s);
--   INCONCLUSIVE = paired < 1,000, or no in-stock read on either side (agreement on
--     out-of-stock reads alone does not validate positives -- say so);
--   FAILED (never ingest) = any disagree > 0 -> read first_disagree.
SELECT d.run_id AS run,
       strftime('%H', d.ts_ms / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS hh,
       COUNT(*) AS lines,
       SUM(d.bodies) AS bodies,
       SUM(d.qualifying) AS qualifying,
       SUM(d.tcin_reads) AS tcin_reads,
       SUM(d.paired) AS paired,
       SUM(d.agree) AS agree,
       SUM(d.disagree) AS disagree,
       group_concat(DISTINCT NULLIF(d.in_stock_206, '')) AS in_206,
       (SELECT x.first_disagree FROM shadow206 x WHERE x.run_id = d.run_id AND x.disagree > 0
         ORDER BY x.line LIMIT 1) AS first_disagree
FROM shadow206 d
JOIN runs r ON r.run_id = d.run_id
GROUP BY d.run_id, hh
ORDER BY d.run_id, hh;
