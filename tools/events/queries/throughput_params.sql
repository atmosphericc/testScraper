-- throughput_params.sql: measured input parameters for the throughput model (WS-G1, 2026-11-04)
--
-- Usage:  python tools/events/q.py throughput_params        (read-only; build the store first: python tools/events/build.py)
-- Each statement is the SQL G1 ran against logs/events/events.sqlite, in report order (sections 1-9 of
-- logs/analysis_2026_11_04/WS_G1_params.md). Wilson 95% CIs, and a few derived sets (contended-window split, harvest depth and
-- refill, window spacing, first-shot cell tallies) were computed in python from these result sets; the Q1c, Q2, Q6a-b notes say where.
-- Hot = funnel.py HOT_PREFIXES / HOT_EXACT.
-- Age: shots.window_age_ms (flip windows; dispatch stamp atc_t0 minus windows.first_read_ms; clock CDT; NULL outside a flip window).
-- Age bins: 0-0.5 | 0.5-1.5 | 1.5-3 | 3-10 | 10-60 | 60-300 | >300 s.
-- Past wall 1 = gate IN (wall2_denied, admitted_fs, cart, inventory, cart_limit); classified = past + wall1_limited + wall1_keyless;
-- other / unknown gates are reported as unmatched, never folded in.
-- Contended window (V2, VERIFICATION.md): window pass rate < 25% (WS_D does not define it).
-- Section 5 (wall 3): also run unchanged  po_by_age  and  first_po_age.   Section 2: first_shot_delay.

-- Q1a flip-era, hot, per run x age bin (window_age_ms = shot atc_t0 ts_ms - windows.first_read_ms). classified n = limited + past.
SELECT run_id, CASE WHEN window_age_ms<500 THEN '1 0-0.5s' WHEN window_age_ms<1500 THEN '2 0.5-1.5s' WHEN window_age_ms<3000 THEN '3 1.5-3s' WHEN window_age_ms<10000 THEN '4 3-10s' WHEN window_age_ms<60000 THEN '5 10-60s' WHEN window_age_ms<300000 THEN '6 60-300s' ELSE '7 >300s' END AS bin, SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) AS n, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) AS pass,
       SUM(gate IN ('other','unknown') OR gate IS NULL) AS unmatched, COUNT(*) AS rows_all
FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) GROUP BY 1,2 ORDER BY 1,2;

-- Q1c contended proxy (computed in python from this per-window query; window pass rate over ALL ages of that window's hot classified shots; contended = <25%)
SELECT run_id, window_id, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) p, SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) n FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit') GROUP BY 1,2;
-- then per (regime, bin): SELECT CASE WHEN window_age_ms<500 THEN '1 0-0.5s' WHEN window_age_ms<1500 THEN '2 0.5-1.5s' WHEN window_age_ms<3000 THEN '3 1.5-3s' WHEN window_age_ms<10000 THEN '4 3-10s' WHEN window_age_ms<60000 THEN '5 10-60s' WHEN window_age_ms<300000 THEN '6 60-300s' ELSE '7 >300s' END bin, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')), SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit') AND (run_id,window_id) IN (<contended|open windows>) GROUP BY 1;

-- Q1d per ident, age<3000 ms, flip-era hot
SELECT COALESCE(ident,'?') ident, proxied, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) pass, SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) n, SUM(gate IN ('other','unknown') OR gate IS NULL) unmatched
FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND window_age_ms<3000 GROUP BY 1,2 ORDER BY 1,2;

-- Q1d2 per run x ident, age<3000 ms
SELECT run_id, COALESCE(ident,'?') ident, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) pass, SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) n FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND window_age_ms<3000 GROUP BY 1,2 ORDER BY 1,2;

-- Q1e SUPPLEMENT: all store since run_20260827, hot, age = ep_age_ms (read-based in-stock episode age, ~1 s read granularity, not flip-anchored)
SELECT CASE WHEN ep_age_ms<500 THEN '1 0-0.5s' WHEN ep_age_ms<1500 THEN '2 0.5-1.5s' WHEN ep_age_ms<3000 THEN '3 1.5-3s' WHEN ep_age_ms<10000 THEN '4 3-10s' WHEN ep_age_ms<60000 THEN '5 10-60s' WHEN ep_age_ms<300000 THEN '6 60-300s' ELSE '7 >300s' END bin, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) pass, SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) n, SUM(gate IN ('other','unknown') OR gate IS NULL) unmatched FROM shots WHERE run_id>='run_20260827' AND ep_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) GROUP BY 1 ORDER BY 1;

-- Q2: python tools/events/q.py first_shot_delay  (queries/first_shot_delay.sql Result 1 = one row per run x window x ident; G1 tallied TEMP view fsd_cell WHERE src_used='flip' in python)

-- Q3: flip-era hot past-wall-1 shots by cred_source x age; 401 = gate wall2_denied; admitted = admitted_fs+cart+inventory+cart_limit
SELECT COALESCE(cred_source,'blank') cred, CASE WHEN window_age_ms<3000 THEN '0-3s' ELSE '>=3s' END ageb,
       COUNT(*) past_w1, SUM(gate='wall2_denied') n401, SUM(gate<>'wall2_denied') admitted FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit') GROUP BY 1,2 ORDER BY 1,2;

-- Q3b: wall-1 pass rate by cred_source, flip-era hot, all ages
SELECT COALESCE(cred_source,'blank') cred, COUNT(*) shots, SUM(gate IN ('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')) classified, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) past_w1 FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) GROUP BY 1;

-- Q3c: cred_source.sql population (home IP, since run_20260911, ALL tcins, all ages)
SELECT COALESCE(cred_source,'blank') cred, COUNT(*) shots, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) past_w1, SUM(gate IN ('admitted_fs','cart','inventory','cart_limit')) past_w2, SUM(gate='cart') carts
FROM shots WHERE proxied=0 AND run_id>='run_20260911' GROUP BY 1;

-- Q4: flip-era hot admitted shots by age bin; 201=cart, FS429=admitted_fs, 424=inventory, cart_limit=400 MAX_PURCHASE
SELECT CASE WHEN window_age_ms<500 THEN '1 0-0.5s' WHEN window_age_ms<1500 THEN '2 0.5-1.5s' WHEN window_age_ms<3000 THEN '3 1.5-3s' WHEN window_age_ms<10000 THEN '4 3-10s' WHEN window_age_ms<60000 THEN '5 10-60s' WHEN window_age_ms<300000 THEN '6 60-300s' ELSE '7 >300s' END bin, SUM(gate='cart') c201, SUM(gate='admitted_fs') fs, SUM(gate='inventory') inv, SUM(gate='cart_limit') cl, COUNT(*) n
FROM shots WHERE run_id>='run_20260930' AND window_age_ms IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND gate IN ('admitted_fs','cart','inventory','cart_limit') GROUP BY 1 ORDER BY 1;

-- Q4b per run admitted hot
SELECT run_id, SUM(gate='cart') c201, SUM(gate='admitted_fs') fs, SUM(gate='inventory') inv, SUM(gate='cart_limit') cl, COUNT(*) n FROM shots WHERE run_id>='run_20260930' AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) AND gate IN ('admitted_fs','cart','inventory','cart_limit') GROUP BY 1;

-- Q5a/Q5b: run unchanged: python tools/events/q.py po_by_age ; python tools/events/q.py first_po_age;

-- Q5c: WS_H reproduction: linked (cart_line NOT NULL) HOT place-orders by window_age_ms bin; FS = status 429 FAST_SELLING; RF/424 = other non-200
SELECT CASE WHEN window_age_ms IS NULL THEN '9 unknown' WHEN window_age_ms<=5000 THEN '1 <=5s' WHEN window_age_ms<=30000 THEN '2 5-30s'
            WHEN window_age_ms<=120000 THEN '3 30-120s' WHEN window_age_ms<=600000 THEN '4 2-10min' ELSE '5 >10min' END bin,
       COUNT(*) n, SUM(status=200) ok, SUM(err_key LIKE 'FAST_SELLING%') fs, SUM(status<>200 AND err_key NOT LIKE 'FAST_SELLING%') rf, SUM(status IS NULL) no_resp
FROM place_orders WHERE cart_line IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) GROUP BY 1 ORDER BY 1;

-- Q5d: err_key breakdown of the non-200 hot linked POs
SELECT status, err_key, COUNT(*) FROM place_orders WHERE cart_line IS NOT NULL AND (tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279','1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385')) GROUP BY 1,2;

-- Q6a depth at t=0: for each flip window (windows.src='flip') x acct, the last harvest row with bank_n NOT NULL and ts_ms <= windows.first_read_ms (same run)
SELECT w.run_id, w.window_id, a.acct, (SELECT h.bank_n FROM harvest h WHERE h.run_id=w.run_id AND h.acct=a.acct AND h.bank_n IS NOT NULL AND h.ts_ms<=w.first_read_ms ORDER BY h.ts_ms DESC LIMIT 1) AS bank_n,
       (SELECT w.first_read_ms-h.ts_ms FROM harvest h WHERE h.run_id=w.run_id AND h.acct=a.acct AND h.bank_n IS NOT NULL AND h.ts_ms<=w.first_read_ms ORDER BY h.ts_ms DESC LIMIT 1) AS gap_ms
FROM windows w, (SELECT 'primary' acct UNION SELECT 'business' UNION SELECT 'alt-1') a WHERE w.src='flip' AND w.run_id>='run_20260930';

-- Q6b refill: captured events with banked=1 per acct inside live-window intervals (union of flip windows [first_read_ms, COALESCE(end_ms,last_read_ms)] per run), divided by live minutes. Union and rate computed in python.
SELECT run_id, acct, ts_ms FROM harvest WHERE event='captured' AND banked=1 AND run_id>='run_20260930';

-- Q6c stale-discard events (all store) and counters
SELECT substr(run_id,5,8) d, COUNT(*) FROM harvest WHERE event='stale' GROUP BY 1;
SELECT run_id, MAX(c_expired) exp, MAX(c_stale) stale, MAX(c_harvested) harv, MAX(c_replayed) repl FROM harvest WHERE run_id>='run_20260930' GROUP BY 1;

-- Q6d banked-set pops in the first 3 s of each flip window: shots with cred_source=banked_replay and window_age_ms<3000 (one replayed set per shot), by window
SELECT run_id, window_id, SUM(cred_source='banked_replay') AS replay_shots_lt3s, SUM(cred_source='page_signed') AS page_signed_lt3s, COUNT(*) AS shots_lt3s FROM shots WHERE run_id>='run_20260930' AND window_age_ms<3000 GROUP BY 1,2;

-- Q7a runs since 09-30: windows, shots, past-wall-1, duration
SELECT r.run_id, r.first_ts, r.last_ts,
  (SELECT COUNT(*) FROM windows w WHERE w.run_id=r.run_id AND w.src='flip') flip_windows,
  (SELECT COUNT(*) FROM shots s WHERE s.run_id=r.run_id) shots,
  (SELECT COUNT(*) FROM shots s WHERE s.run_id=r.run_id AND s.gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) past_w1
FROM runs r WHERE r.run_id>='run_20260930' ORDER BY 1;

-- Q7b windows (flip) since 09-30 with reflips
SELECT w.run_id,w.window_id,w.tcin,w.first_read_ms,w.last_read_ms,w.end_ms,w.reads,w.flips,w.shots,
       (SELECT SUM(f.new_window=0) FROM flips f WHERE f.run_id=w.run_id AND f.window_id=w.window_id) reflips
FROM windows w WHERE w.src='flip' AND w.run_id>='run_20260930' ORDER BY 1,4;

-- Q8a same account (ident) + TCIN with >=2 shots gate=cart (201) inside one flip window; and across the whole run
SELECT run_id, ident, tcin, window_id, COUNT(*) n201, MIN(window_age_ms) first_age_ms, MAX(window_age_ms) last_age_ms
FROM shots WHERE gate='cart' AND run_id>='run_20260930' GROUP BY 1,2,3,4 ORDER BY 1,4,2;

-- Q8b distinct cart_id per (run, ident, tcin) from tickets.cls/n and the first tickets with ms_since_201; also place_orders cart_line distinct per ident+tcin+window
SELECT run_id, ident, tcin, COUNT(DISTINCT cart_id) carts FROM tickets WHERE run_id>='run_20260930' AND cart_id IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3;

-- Q8c MAX_PURCHASE_LIMIT_EXCEEDED shots (gate=cart_limit) all store, hot flag
SELECT run_id, ident, tcin, window_id, window_age_ms, status, err_key FROM shots WHERE gate='cart_limit' OR err_key LIKE 'MAX_PURCHASE%' ORDER BY 1,ts_ms;

-- Q8d loop_ends reasons (all store)
SELECT reason, COUNT(*) n FROM loop_ends GROUP BY 1 ORDER BY 2 DESC;

-- Q8e line-held / MAX_PURCHASE self-heal evidence present in tickets/status? count tickets with status 400
SELECT status, COUNT(*) FROM tickets GROUP BY 1 ORDER BY 2 DESC;

-- Q9: are the 09-15 and 08-27 runs in the event store; per ident pass/shots
SELECT s.run_id, COALESCE(s.ident,'?') ident, s.proxied, COUNT(*) shots, SUM(gate IN ('wall2_denied','admitted_fs','cart','inventory','cart_limit')) past_w1
FROM shots s WHERE s.run_id LIKE 'run_20260915%' OR s.run_id LIKE 'run_20260827%' GROUP BY 1,2,3 ORDER BY 1,2;
