-- cred_source: past-wall-1 shots by CREDENTIAL SOURCE x run, home IP only (proxied = 0).
--
-- Source: readiness audit 2026-10-18/19 (FS-1019-R-CRED). Question: do page-signed credentials (cred_source = 'page_signed',
--   the Shape headers minted by the live page) get past the edge limiter / the SSX hop any better than banked-replay ones
--   ('banked_replay', headers harvested earlier and replayed)? Read-only; no tuning knobs except since_run below.
--
-- PRE-REGISTERED RULE (written before the data exist; do not edit after a drop night):
--   After each drop night append the page-signed row's past_w1 and past_w2 (statement 2, column by column) to the baseline
--   n = 8 past wall 1 / 1 past wall 2 / 0 carts (page_signed, home IP, since run_20260911, store parser v4/v7 mix).
--   NO RATE is claimed (no "page-signed passes X %") until page-signed past_w1 >= 30 (column n_to_30 reaches 0). Below that,
--   report only the counts. banked_replay is the comparison arm; 'blank' (cred_source IS NULL) is not an arm: it is the
--   unattributed remainder (shots whose credential line the parser could not join) and is shown, never folded in.
--
-- Expected totals on the 2026-10-19 store (audit numbers; a parser rebuild can move them by a few rows -- explain a difference,
--   never tune the query to hit it):  banked_replay 1,891 shots / 14 carts;  page_signed 2,258 shots / 0 carts / 8 past wall 1;
--   blank 27 / 0.
--
-- DEFINITIONS
--   past_w1  shots whose gate is past the hot-item limiter = gate IN (wall2_denied, admitted_fs, cart, inventory, cart_limit)
--            (the same set as walls.sql w1_pass: a 401 IS past wall 1; an edge 429 'wall1_limited' / 'other' / 'unknown' is not).
--   past_w2  past_w1 AND not a 401 = gate IN (admitted_fs, cart, inventory, cart_limit) (got an answer from the cart service).
--   carts    gate = 'cart' (HTTP 201).
--   cred     COALESCE(cred_source, 'blank').
--   harvest_at_boot  (statement 3 only) the kind of the run's FIRST row in the `harvest` table (captured / empty / pre_shot / replay),
--            NULL when the run has none. The store carries `harvest` rows only from parser v5 (run_20261015_231603 on) and the
--            SELFTEST / MISS-PROBE px=yes lines are NOT ingested (decoys.selftest is the decoy mode on/off flag, not a harvest
--            status), so for every run before 2026-10-15 this column is NULL: it does NOT say the harvest was fine, only unknown.
--   since_run  string compare on run_id (run ids start run_YYYYMMDD_), edit it below. Default run_20260911 (first run with the
--            [EXPOSURE] proxied= marker the home-IP filter needs).
-- Statement 1 creates the TEMP view cred_cells and prints nothing; statement 2 prints the totals per credential source;
-- statement 3 the run x credential cells (oldest run first).
DROP VIEW IF EXISTS cred_cells;
CREATE TEMP VIEW cred_cells AS
WITH cfg AS (SELECT 'run_20260911' AS since_run)
SELECT s.run_id AS run,
       COALESCE(s.cred_source, 'blank') AS cred,
       COUNT(*) AS shots,
       SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS past_w1,
       SUM(s.gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit')) AS past_w2,
       SUM(s.gate = 'cart') AS carts
FROM shots s
WHERE s.proxied = 0
  AND s.run_id >= (SELECT since_run FROM cfg)
GROUP BY s.run_id, COALESCE(s.cred_source, 'blank');

-- 2) totals per credential source (the pre-registered table)
SELECT cred,
       SUM(shots) AS shots,
       SUM(past_w1) AS past_w1,
       SUM(past_w2) AS past_w2,
       SUM(carts) AS carts,
       COUNT(*) AS runs,
       MAX(0, 30 - SUM(past_w1)) AS n_to_30
FROM cred_cells
GROUP BY cred
ORDER BY CASE cred WHEN 'banked_replay' THEN 1 WHEN 'page_signed' THEN 2 ELSE 3 END;

-- 3) run x credential source
SELECT c.run, c.cred, c.shots, c.past_w1, c.past_w2, c.carts,
       (SELECT h.event FROM harvest h WHERE h.run_id = c.run ORDER BY h.ts_ms, h.line LIMIT 1) AS harvest_at_boot
FROM cred_cells c
ORDER BY c.run, CASE c.cred WHEN 'banked_replay' THEN 1 WHEN 'page_signed' THEN 2 ELSE 3 END;
