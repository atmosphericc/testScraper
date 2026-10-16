-- limkey: P1 readout of the off-window limiter-key probe S2-LIMKEY-PROBE (parser v7, INS-LIMKEY-1016)
--
-- Usage:  python tools/events/q.py limkey [--run 20261017]     (build the store first: python tools/events/build.py)
-- Source: INS-LIMKEY-1016 (2026-10-16). Reads limkey ([LIMKEY_PROBE] lines, print + logger twins already collapsed), warm_dummy
--   (the per-account heartbeat dummy POST status lines "[WARMUP#N/acct] dummy POST status S") and decoys (the warmup-tab
--   [ATC_RESP] rows WITH the error key; the probe's own response, tagged limkey_probe=1, is already left out of decoys).
--   Statements 0a-0c create TEMP views and print nothing. A run still held at parser v6 has no limkey table: build.py first.
--
-- QUESTION (P1, 10-16 frame audit): is the edge limiter (429 ERR_A2C_TCIN_RATE_LIMITED) on a hot out-of-stock TCIN a static
--   per-TCIN policy (a TESTBED: every key question becomes testable off-window) or switched on by live demand?
--
-- WHAT IS COUNTED (one probe = one event 'probe' row)
--   answered POST   status > 0: Target answered the probe. This is the denominator ("n POSTs").
--   preflight       status = 0: the page's fetch threw (the request never got an answer). Counted separately, NEVER in the rate.
--   unreadable      status < 0 (-1: the evaluate raised / timed out / returned an unparseable answer). Counted separately and
--                   never in the rate [deliberate: the spec says "status != 0"; a -1 is not a known answer, so it is shown
--                   in its own column and in the unclassified remainder instead of diluting the share].
--   ERR_A2C         status 429 AND err_key contains ERR_A2C_TCIN_RATE_LIMITED (the edge limiter; the same test as gate_of() in build.py)
--   other statuses  everything else answered, labelled by status (a 429 with another key is '429:<key>'; a 429 whose key
--                   the interceptor could not attach, '?' / '-', is '429:unkeyed' and is ALSO in the unclassified remainder)
--   hours           host-local wall clock (ts_ms + runs.utc_offset_min); a print-only row carries the last logger stamp before it
--
-- PRE-REGISTERED RULE P1 (written 2026-10-16, before any probe data exists). Hot TCINs = every TCIN with a probe row in the
--   selected runs. Decoy control = the SAME accounts' heartbeat dummy POST answers (warm_dummy, ident in the probe accounts) in the
--   SAME local hours that had >= 1 answered probe, plus the warmup-tab decoys (decoys) in those hours with their error key.
--     NO DATA        no 'probe' row at all
--     TESTBED EXISTS every hot TCIN has >= 30 answered POSTs AND an ERR_A2C share >= 50% (each TCIN, not pooled)
--                    AND the decoy control is non-empty (>= 1 warm_dummy answer) with 0 ERR_A2C decoys (decoys, 429 + key)
--                    and 0 status-429 warm_dummy answers
--     DEMAND-SWITCHED every hot TCIN has >= 30 answered POSTs AND 0 ERR_A2C on all hot TCINs
--     INCONCLUSIVE   anything else (a TCIN under 30 answered POSTs, a share between the lines, a limited decoy, no decoy control):
--                    the 'why' column says which; statements 1-3 print the counts per TCIN and per hour
--   An empty control cannot support TESTBED (it would pass "0 ERR_A2C on the decoy" vacuously); DEMAND-SWITCHED does not use
--   the control. Nothing here says anything about a landed line: a 2xx / STOP / cleanup event is in statement 5.
--
-- Statement 0a-0c: TEMP views (nothing printed)
DROP VIEW IF EXISTS temp.lk_probe;
CREATE TEMP VIEW lk_probe AS
SELECT l.run_id, l.line, l.ts_ms, l.ts_src, l.ident, l.tcin, l.n, l.status, l.err_key, l.hop, l.net_line, l.body_len, l.err,
       strftime('%Y-%m-%d %H:00', l.ts_ms / 1000 + COALESCE((SELECT r.utc_offset_min FROM runs r WHERE r.run_id = l.run_id), 0) * 60,
                'unixepoch') AS hr,
       CASE WHEN l.status > 0 THEN 'post' WHEN l.status = 0 THEN 'preflight' ELSE 'unreadable' END AS cls,
       CASE WHEN l.status = 429 AND upper(COALESCE(l.err_key, '')) LIKE '%ERR_A2C_TCIN_RATE_LIMITED%' THEN 'ERR_A2C'
            WHEN l.status = 429 AND COALESCE(l.err_key, '') IN ('', '-', '?') THEN '429:unkeyed'
            WHEN l.status = 429 THEN '429:' || l.err_key
            WHEN l.status > 0 THEN CAST(l.status AS TEXT)
            WHEN l.status = 0 THEN 'preflight(0)' ELSE 'unreadable(' || COALESCE(l.status, 'NULL') || ')' END AS ans
FROM limkey l WHERE l.event = 'probe';

DROP VIEW IF EXISTS temp.lk_wd;
CREATE TEMP VIEW lk_wd AS
SELECT w.run_id, w.ident, w.status,
       strftime('%Y-%m-%d %H:00', w.ts_ms / 1000 + COALESCE((SELECT r.utc_offset_min FROM runs r WHERE r.run_id = w.run_id), 0) * 60,
                'unixepoch') AS hr
FROM warm_dummy w;

DROP VIEW IF EXISTS temp.lk_decoy;
CREATE TEMP VIEW lk_decoy AS
SELECT d.run_id, d.status, d.key,
       strftime('%Y-%m-%d %H:00', d.ts_ms / 1000 + COALESCE((SELECT r.utc_offset_min FROM runs r WHERE r.run_id = d.run_id), 0) * 60,
                'unixepoch') AS hr
FROM decoys d;

-- Statement 1: per probe TCIN -- the counts the rule reads
SELECT tcin,
       SUM(cls = 'post')                                                         AS posts,
       SUM(cls = 'post' AND ans = 'ERR_A2C')                                     AS err_a2c,
       ROUND(100.0 * SUM(cls = 'post' AND ans = 'ERR_A2C') / NULLIF(SUM(cls = 'post'), 0), 1) AS a2c_pct,
       SUM(cls = 'post' AND ans <> 'ERR_A2C')                                    AS other_answers,
       SUM(cls = 'preflight')                                                    AS preflight_0,
       SUM(cls = 'unreadable')                                                   AS unreadable,
       COUNT(DISTINCT ident)                                                     AS accounts,
       COUNT(DISTINCT net_line)                                                  AS net_lines,
       MIN(hr) AS first_hr, MAX(hr) AS last_hr
FROM lk_probe GROUP BY tcin ORDER BY tcin;

-- Statement 2: per probe TCIN x answer (what the "other statuses" are)
SELECT tcin, ans, COUNT(*) AS n, MIN(line) AS first_line, MIN(run_id) AS first_run
FROM lk_probe GROUP BY tcin, ans ORDER BY tcin, n DESC, ans;

-- Statement 3: per hour x TCIN, with the decoy control of the same hour (probe accounts' heartbeat dummy POSTs + warmup decoys)
SELECT p.hr, p.tcin,
       SUM(p.cls = 'post')                                  AS posts,
       SUM(p.cls = 'post' AND p.ans = 'ERR_A2C')            AS err_a2c,
       SUM(p.cls <> 'post')                                 AS not_in_rate,
       (SELECT COUNT(*) FROM lk_wd w WHERE w.hr = p.hr AND w.ident IN (SELECT ident FROM lk_probe))                     AS dummy_posts,
       (SELECT COUNT(*) FROM lk_wd w WHERE w.hr = p.hr AND w.status = 429 AND w.ident IN (SELECT ident FROM lk_probe))  AS dummy_429,
       (SELECT COUNT(*) FROM lk_decoy d WHERE d.hr = p.hr AND d.status = 429
               AND upper(COALESCE(d.key, '')) LIKE '%ERR_A2C_TCIN_RATE_LIMITED%')                                       AS decoy_err_a2c
FROM lk_probe p GROUP BY p.hr, p.tcin ORDER BY p.hr, p.tcin;

-- Statement 4: THE VERDICT (rule P1 above)
WITH t AS (
  SELECT tcin, SUM(cls = 'post') AS posts, SUM(cls = 'post' AND ans = 'ERR_A2C') AS a2c
  FROM lk_probe GROUP BY tcin),
hrs AS (SELECT DISTINCT hr FROM lk_probe WHERE cls = 'post' AND hr IS NOT NULL),
ids AS (SELECT DISTINCT ident FROM lk_probe),
ctl AS (
  SELECT (SELECT COUNT(*) FROM lk_wd w WHERE w.hr IN (SELECT hr FROM hrs) AND w.ident IN (SELECT ident FROM ids))                      AS dummy_n,
         (SELECT COUNT(*) FROM lk_wd w WHERE w.hr IN (SELECT hr FROM hrs) AND w.ident IN (SELECT ident FROM ids) AND w.status = 429)   AS dummy_429,
         (SELECT COUNT(*) FROM lk_decoy d WHERE d.hr IN (SELECT hr FROM hrs))                                                          AS decoy_n,
         (SELECT COUNT(*) FROM lk_decoy d WHERE d.hr IN (SELECT hr FROM hrs) AND d.status = 429
                 AND upper(COALESCE(d.key, '')) LIKE '%ERR_A2C_TCIN_RATE_LIMITED%')                                                    AS decoy_a2c),
s AS (
  SELECT COUNT(*) AS n_tcin, COALESCE(MIN(posts), 0) AS min_posts, COALESCE(SUM(a2c), 0) AS total_a2c,
         COALESCE(MIN(CASE WHEN posts > 0 THEN 100.0 * a2c / posts END), 0) AS min_share,
         COALESCE(SUM(posts), 0) AS total_posts
  FROM t)
SELECT s.n_tcin AS hot_tcins, s.total_posts AS posts, s.min_posts AS min_posts_per_tcin, s.total_a2c AS err_a2c,
       ROUND(s.min_share, 1) AS min_a2c_pct, c.dummy_n AS dummy_posts, c.dummy_429, c.decoy_n AS decoys, c.decoy_a2c AS decoy_err_a2c,
       CASE
         WHEN s.n_tcin = 0 THEN 'NO DATA'
         WHEN s.min_posts >= 30 AND s.min_share >= 50 AND c.dummy_n >= 1 AND c.decoy_a2c = 0 AND c.dummy_429 = 0
           THEN 'P1 TESTBED EXISTS'
         WHEN s.min_posts >= 30 AND s.total_a2c = 0 THEN 'P1 DEMAND-SWITCHED'
         ELSE 'P1 INCONCLUSIVE' END AS verdict,
       CASE
         WHEN s.n_tcin = 0 THEN 'no [LIMKEY_PROBE] result line in the selected runs'
         WHEN s.min_posts < 30 THEN 'a hot TCIN has < 30 answered POSTs (min ' || s.min_posts || ')'
         WHEN s.min_share >= 50 AND c.dummy_n < 1 THEN 'ERR_A2C share >= 50% but the decoy control is empty (no warm_dummy answer in the probe hours)'
         WHEN s.min_share >= 50 AND (c.decoy_a2c > 0 OR c.dummy_429 > 0) THEN 'ERR_A2C share >= 50% but the decoy was limited too (not a per-TCIN policy)'
         WHEN s.total_a2c = 0 THEN '>= 30 POSTs per TCIN and 0 ERR_A2C on all hot TCINs'
         WHEN s.min_share >= 50 THEN '>= 30 POSTs per TCIN, ERR_A2C share >= 50% on every TCIN, decoy clean'
         ELSE 'ERR_A2C seen (' || s.total_a2c || ') but not >= 50% on every hot TCIN (lowest ' || ROUND(s.min_share, 1) || '%)' END AS why
FROM s, ctl c;

-- Statement 5: other [LIMKEY_PROBE] events (skip / stop / error / cleanup / ...) by kind and reason; a 'stop' is a LANDED hot-TCIN line
SELECT event, COALESCE(reason, '-') AS reason, COUNT(*) AS n, MIN(line) AS first_line, MIN(run_id) AS first_run,
       MAX(CASE WHEN status BETWEEN 200 AND 299 THEN status END) AS status_2xx
FROM limkey WHERE event <> 'probe' GROUP BY event, COALESCE(reason, '-') ORDER BY event, n DESC;

-- Statement 6: the unclassified remainder -- probe rows that are not an ERR_A2C and not one of the known non-landing answers
--   (keyless 401, 424, 400, 403, 404, a keyed non-A2C 429): unreadable, 2xx (a line may have landed), unkeyed 429, 5xx, 3xx, anything else.
SELECT ans, cls, COUNT(*) AS n, MIN(line) AS first_line, MIN(run_id) AS first_run, MIN(err) AS err_sample
FROM lk_probe
WHERE NOT (ans = 'ERR_A2C' OR status IN (401, 424, 400, 403, 404) OR (status = 429 AND ans <> '429:unkeyed') OR cls = 'preflight')
GROUP BY ans, cls ORDER BY n DESC;

-- Statement 7: parser accounting -- [LIMKEY_PROBE] / dummy POST lines the parser saw but could not read (0 rows = none)
SELECT run_id, marker, seen, parsed, n AS unread, first_line FROM unparsed WHERE (marker LIKE 'LIMKEY%' OR marker LIKE 'WDUMMY%') AND n > 0;
