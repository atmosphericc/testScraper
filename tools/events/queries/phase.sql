-- phase: window-phase, bank and wire-order readouts for the 10-16 decisions (BANK-RESERVE, ATC-TICKETS, fast cadence)
--
-- Usage:  python tools/events/q.py phase [--run 20261016]      (build the store first: python tools/events/build.py)
-- Source: INS-STORE-HARVEST-1015 (2026-10-15). These are the ad hoc readouts of the 10-15 session (q13, q12, q22, q4
--   and the atc_net wire join), kept so they are SQL-answerable after the drop instead of re-derived per night.
--   Statements 2-9, 11 and 12 read shots / windows / atc_net only. Statements 1, 10 and 13 read the `harvest` table
--   (parser v5). Run build.py first: it creates the (empty) table in an older store even before it re-parses a log,
--   and a run still held at parser v4 has no harvest rows, so 13 then prints NO DATA rather than a negative. (With no
--   build.py run at all, the whole query stops with "no such table: harvest".)
-- Scope: statements 2-6 count HOME-line shots only (shots.proxied = 0, as the ad hoc queries did; NULL proxied is left
--   out, never folded in); runs from run_20260906 on (statement 2). Time: a shot's ts_ms is its atc_t0 (epoch ms; NOT
--   wire order, see statements 8-9), a harvest row's ts_ms is the line's own logger stamp.
--
-- WHAT EACH STATEMENT ANSWERS
--   1  harvest rows per run x account x event, and the in-store agreement checks (instrument)
--   2  (q13) cart-service admission past the limiter, by window phase (ep_age_ms) x cred_source
--   3  (q12) windows that opened while ANOTHER TCIN's window was open: first-shot lag
--   4  (q12) per run: lag of the first shot, overlapped windows vs plain ones
--   5  (q22) early (<= 3 s) admitted tickets per window, and carts
--   6  (q4)  fresh-window shots (ep_age <= 5 s) by cred_source x the account's shots in the prior 60 s
--   7  wire-order coverage: first volleys per run that have >= 2 joined POSTs
--   8  first volley of each flip-opened race in WIRE order (atc_net.send_ms) next to atc_t0 order
--   9  per run: how often the two orders differ, and who is first in a mixed (pass + limited) volley
--   10-13 R-BANK (CURRENT_STATE, pre-registered 2026-10-15): fresh-flip first shots by cred_source x "another TCIN in the
--      prior 110 s" x "harvest live"; re-shot page-signed share; past-limiter cred_source x outcome; the verdict
--
-- PRE-REGISTERED RULES (written 2026-10-15, before the 10-16 drop)
--   R-BANK (statement 13, copied from CURRENT_STATE): >= 3 fresh-flip first shots (flip_opened_race = 1, is_first = 1)
--     that went page-signed WHILE that account's harvest was live => build the flag-gated "fast re-shots never pop the
--     last K sets" reservation (a shot-path change: offline tests + an Opus verifier, not under a pre-drop clock).
--     "Harvest live" = a harvest row event = 'captured' with banked = 1 for that account in the 300 s before the shot
--     (a CAPTURED that added a set; the no-token variant is shown separately as n_..._any_capture, 4 of 47,734 CAPTURED
--     lines across the 213 logs). Fewer than 3 => rule NOT MET (the reservation is not justified by this drop). No
--     fresh-flip first shots, or no harvest rows in the store => NO DATA, never NOT MET.
--     C-1015-04 ("page-signed shots convert worse") moves only with >= 20 page-signed PAST-LIMITER shots (statement 13,
--     which counts every shot of the selected runs -- pass --run so it counts one night); below that it stays NOT
--     ESTABLISHED whatever the rates look like (home shots, runs 2026-09-06..10-14: 8 page-signed past-limiter shots,
--     1 early + 7 late in statement 2, n=8).
--   INSTRUMENT (statements 1-6, 8-9): a reading is trusted only if the references below still reproduce on the same runs.
--     FAIL = any difference => read nothing from this query for that night, find the parser regression first.
--       2  on the store as of 2026-10-15 (runs 2026-09-07..2026-10-14; later runs add rows) [MEASURED, main store copy]:
--            early<=3s  banked_replay past 40  d401 6  adm 33 carts 4 other 1   page_signed past 1  adm 1
--            late>20s   banked_replay past 98  d401 77 adm 16 carts 10 other 5  page_signed past 7  d401 7 adm 0
--            mid3-20s   banked_replay past 1   adm 1
--       3-4 run_20261009_000934 [MEASURED]: 16 windows with a shot: 6 OVERLAPPED (opened while another TCIN's window was
--            open), first-shot lags 6736 / 2337 / 945 / 2727 / 804 / 38 ms (wid 9-14), 5 of the 6 between 0.8 and 6.7 s;
--            10 PLAIN windows: 8 of 10 with a lag of 33-51 ms, the other two at 757 ms (wid 4) and 1,076 ms (wid 16).
--            (The ad hoc write-up said 9 of 10; the query gives 8 of 10.)
--       5  same store: 1 ticket x 14 windows (1 cart), 2 x 2 (1), 3 x 4 (0), 4 x 1 (2 carts, 1 window with a cart).
--       6  same store: 16 rows, among them 20260925 idle0 banked_replay n 23 past 7 d401 2 adm 5 and p1-5 18 / 0 / 0 / 0;
--            20261009 idle0 page_signed 9 / 0 / 0 / 0 and p1-5 page_signed 12 / 1 / 0 / 1.
--       1  run_20260925_004139 [MEASURED, logger form only, each line once]: captured 1,426 (alt-1 per minute 03:34-03:38 =
--            3,3,3,3,3 -- a start-of-line `cut -c1-16` grep reads 3,3,3,2,3 because one alt-1 logger line sits glued behind
--            a print on line 33940), pre_shot 1,212, replay 220, empty 992, stale 0; cred rows = shots_with_cred per account.
--            run_20260925_004139 is a HOME night (idents.proxied = 0 for all three accounts, [EXPOSURE] proxied=no 435 / 385
--            / 392 rows for alt-1 / business / primary, 1,212 of 1,212 shots proxied = 0), not a proxy night as C-1015-03 reads.
--       8-9 run_20261009_000934 window 13 (TCIN 1010892068) [MEASURED]: wire order alt-1 (send ...182 ms) < business (...195)
--            < primary (...199); atc_t0 order alt-1 LAST (106 vs 101 / 104); alt-1 was the FAST_SELLING pass. shots.ts_ms is
--            atc_t0 and does not give wire order.
--   Statements 3-9 and 10-12 are descriptive: no verdict is pre-registered for them. Nothing here is an established
--   unit lever [NOT ESTABLISHED]; statements 10-13 count home and proxied shots alike (pass --run for one night).

-- 1) harvest rows per run x account, and agreement with the shot join. cred_rows (replay + empty + stale) are the very
--    lines that feed shots.cred_source: cred_rows >= shots_with_cred, the gap = lines no shot claimed (checks.cred_unclaimed).
--    bank_null = captured / pre_shot rows whose bank counters could not be read (also counted in unparsed, marker CRED):
--    anything but 0 is a parser regression. captured_no_tokens = the "(NO Shape tokens - not banked)" variant.
SELECT h.run_id AS run, h.acct,
       SUM(h.event = 'captured') AS captured,
       SUM(h.event = 'captured' AND h.banked = 0) AS captured_no_tokens,
       SUM(h.event = 'pre_shot') AS pre_shot,
       SUM(h.event = 'replay') AS replay,
       SUM(h.event = 'empty') AS empty,
       SUM(h.event = 'stale') AS stale,
       SUM(h.event IN ('replay', 'empty', 'stale')) AS cred_rows,
       (SELECT COUNT(*) FROM shots s WHERE s.run_id = h.run_id AND s.ident = h.acct
                                       AND s.cred_source IS NOT NULL) AS shots_with_cred,
       SUM(h.event IN ('captured', 'pre_shot') AND h.bank_n IS NULL) AS bank_null,
       MAX(h.bank_max) AS bank_max
FROM harvest h
GROUP BY h.run_id, h.acct
ORDER BY h.run_id, h.acct;

-- 2) (q13) shots past the edge limiter (wall2_denied / admitted_fs / cart / inventory / cart_limit), by window phase
--    (ep_age_ms: the shot's time since its in-stock episode opened) x cred_source. adm = admitted_fs + cart (the cart
--    service answered), d401 = keyless 401 at the SSX hop, other = inventory + cart_limit.
SELECT CASE WHEN ep_age_ms <= 3000 THEN 'early<=3s' WHEN ep_age_ms <= 20000 THEN 'mid3-20s' ELSE 'late>20s' END AS phase,
       COALESCE(NULLIF(cred_source, ''), '(pre-09-11 blank)') AS cs,
       COUNT(*) AS past,
       SUM(gate = 'wall2_denied') AS d401,
       SUM(gate IN ('admitted_fs', 'cart')) AS adm,
       SUM(gate = 'cart') AS carts,
       SUM(gate IN ('inventory', 'cart_limit')) AS other
FROM shots
WHERE proxied = 0 AND ep_age_ms >= 0
  AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')
  AND run_id >= 'run_20260906'
GROUP BY 1, 2
ORDER BY 1, 2;

-- 3) (q12) windows that opened while another TCIN's window was open (open_others >= 1; which = those TCINs).
--    first_lag = ms from the window's first in-stock read to its first shot (ep_age_ms of the earliest shot). A window
--    with no windows row (fr NULL) cannot be classified and is left out here; statement 4 counts it.
WITH w AS (
  SELECT run_id, tcin, ep_window_id AS wid, MIN(ep_age_ms) AS first_lag, COUNT(*) AS n,
         COUNT(DISTINCT ident) AS accts,
         SUM(gate NOT IN ('wall1_limited', 'other', 'unknown')) AS past
  FROM shots
  WHERE proxied = 0 AND ep_window_id IS NOT NULL AND ep_age_ms >= 0
  GROUP BY run_id, tcin, ep_window_id
),
x AS (
  SELECT w.*, (SELECT ww.first_read_ms FROM windows ww
               WHERE ww.run_id = w.run_id AND ww.tcin = w.tcin AND ww.window_id = w.wid) AS fr
  FROM w
)
SELECT x.run_id AS run, x.tcin, x.wid,
       strftime('%H:%M:%S', x.fr / 1000 + 60 * (SELECT r.utc_offset_min FROM runs r WHERE r.run_id = x.run_id),
                'unixepoch') AS local_t,
       x.first_lag, x.accts, x.n, x.past,
       (SELECT COUNT(*) FROM windows o WHERE o.run_id = x.run_id AND o.tcin <> x.tcin AND o.first_read_ms < x.fr
          AND COALESCE(o.end_ms, o.last_read_ms) > x.fr) AS open_others,
       (SELECT GROUP_CONCAT(o.tcin) FROM windows o WHERE o.run_id = x.run_id AND o.tcin <> x.tcin
          AND o.first_read_ms < x.fr AND COALESCE(o.end_ms, o.last_read_ms) > x.fr) AS which
FROM x
WHERE x.fr IS NOT NULL
  AND (SELECT COUNT(*) FROM windows o WHERE o.run_id = x.run_id AND o.tcin <> x.tcin AND o.first_read_ms < x.fr
         AND COALESCE(o.end_ms, o.last_read_ms) > x.fr) > 0
ORDER BY x.run_id, x.fr;

-- 4) (q12) per run: first-shot lag of OVERLAPPED windows (another TCIN's window open at the start) vs PLAIN ones.
--    unclassified = windows with a shot but no windows row; windows = overlapped + plain + unclassified.
--    plain_slow_lags_ms lists the plain windows whose first shot took more than 100 ms (run_20261009_000934: 757, 1076).
WITH w AS (
  SELECT run_id, tcin, ep_window_id AS wid, MIN(ep_age_ms) AS first_lag
  FROM shots
  WHERE proxied = 0 AND ep_window_id IS NOT NULL AND ep_age_ms >= 0
  GROUP BY run_id, tcin, ep_window_id
),
x AS (
  SELECT w.*, (SELECT ww.first_read_ms FROM windows ww
               WHERE ww.run_id = w.run_id AND ww.tcin = w.tcin AND ww.window_id = w.wid) AS fr
  FROM w
),
c4 AS (
  SELECT x.*, CASE WHEN x.fr IS NULL THEN NULL ELSE
           (SELECT COUNT(*) FROM windows o WHERE o.run_id = x.run_id AND o.tcin <> x.tcin AND o.first_read_ms < x.fr
              AND COALESCE(o.end_ms, o.last_read_ms) > x.fr) END AS open_others
  FROM x
)
SELECT run_id AS run,
       COUNT(*) AS windows,
       SUM(open_others > 0) AS overlapped,
       SUM(open_others > 0 AND first_lag > 500) AS ov_lag_gt_500ms,
       SUM(open_others = 0) AS plain,
       SUM(open_others = 0 AND first_lag <= 100) AS plain_lag_le_100ms,
       SUM(open_others = 0 AND first_lag > 100) AS plain_lag_gt_100ms,
       GROUP_CONCAT(CASE WHEN open_others = 0 AND first_lag > 100 THEN first_lag END) AS plain_slow_lags_ms,
       SUM(open_others IS NULL) AS unclassified
FROM c4
GROUP BY run_id
ORDER BY run_id;

-- 5) (q22) early admitted tickets per window: windows whose first 3 s produced N cart-service answers (admitted_fs +
--    cart), and the carts among them. windows_with_cart = windows with >= 1 cart.
WITH w AS (
  SELECT run_id, tcin, ep_window_id AS wid, COUNT(*) AS tickets, SUM(gate = 'cart') AS carts
  FROM shots
  WHERE proxied = 0 AND ep_age_ms >= 0 AND ep_age_ms <= 3000 AND gate IN ('admitted_fs', 'cart')
    AND run_id >= 'run_20260906'
  GROUP BY run_id, tcin, ep_window_id
)
SELECT tickets, COUNT(*) AS windows, SUM(carts) AS carts, SUM(carts > 0) AS windows_with_cart
FROM w
GROUP BY tickets
ORDER BY tickets;

-- 6) (q4) shots in the first 5 s of a window (ep_age_ms <= 5000), by cred_source x the account's own shots in the prior
--    60 s (prior60: idle0 / p1-5 / p6-10 / busy11+). past = past the limiter, adm = admitted_fs + cart.
WITH f AS (
  SELECT s.*, (SELECT COUNT(*) FROM shots p WHERE p.run_id = s.run_id AND p.ident = s.ident
                 AND p.ts_ms < s.ts_ms AND p.ts_ms >= s.ts_ms - 60000) AS prior60
  FROM shots s
  WHERE s.proxied = 0 AND s.cred_source <> '' AND s.ep_age_ms <= 5000
)
SELECT SUBSTR(run_id, 5, 8) AS d,
       CASE WHEN prior60 = 0 THEN 'idle0' WHEN prior60 < 6 THEN 'p1-5' WHEN prior60 < 11 THEN 'p6-10' ELSE 'busy11+' END AS b,
       cred_source AS cs,
       COUNT(*) AS n,
       SUM(gate NOT IN ('wall1_limited', 'other', 'unknown')) AS past,
       SUM(gate = 'wall2_denied') AS d401,
       SUM(gate IN ('admitted_fs', 'cart')) AS adm
FROM f
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- 7) wire-order coverage: how many first volleys (flip-opened races, >= 2 accounts with a joined POST) each run has.
--    No [ATC_NET] rows (TARGET_ATC_NET_META off) => the run is absent and statements 8-9 print nothing for it.
SELECT run_id AS run, COUNT(DISTINCT race_seq) AS volleys, COUNT(*) AS joined_first_posts
FROM atc_net
WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
  AND (run_id, race_seq) IN (SELECT run_id, race_seq FROM atc_net
                             WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
                             GROUP BY run_id, race_seq HAVING COUNT(*) >= 2)
GROUP BY run_id
ORDER BY run_id;

-- 8) the FIRST VOLLEY of each flip-opened race in wire order. One row per account's first shot (is_first = 1) whose
--    POST is joined, volleys with >= 2 such rows. wire_rank = order by atc_net.send_ms (Chrome's wall clock when the
--    request left; a same-ms tie shares a rank), t0_rank = order by the shot's atc_t0 (shots.ts_ms); order_differs = 1
--    where the two disagree for that row. behind = ms behind the volley's first in that order. gate = the shot's wall;
--    pass = past the edge limiter. A re-shot never sits in a volley (it exists only because the previous shot failed).
WITH v AS (
  SELECT run_id, race_seq, tcin, ident, send_ms, shot_ts_ms AS t0_ms, shot_gate AS gate, reused
  FROM atc_net
  WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
),
k AS (
  SELECT v.*,
         RANK() OVER (PARTITION BY run_id, race_seq ORDER BY send_ms) AS wire_rank,
         RANK() OVER (PARTITION BY run_id, race_seq ORDER BY t0_ms) AS t0_rank,
         send_ms - MIN(send_ms) OVER (PARTITION BY run_id, race_seq) AS wire_behind_ms,
         t0_ms - MIN(t0_ms) OVER (PARTITION BY run_id, race_seq) AS t0_behind_ms,
         COUNT(*) OVER (PARTITION BY run_id, race_seq) AS volley_n
  FROM v
)
SELECT run_id AS run, race_seq, tcin, ident, wire_rank, t0_rank, wire_behind_ms, t0_behind_ms,
       gate,
       CASE WHEN gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 1
            WHEN gate = 'wall1_limited' THEN 0 END AS pass,
       reused,
       CASE WHEN wire_rank <> t0_rank THEN 1 ELSE 0 END AS order_differs
FROM k
WHERE volley_n >= 2
ORDER BY run_id, race_seq, wire_rank, ident;

-- 9) per run: volleys, volleys where the wire order and the atc_t0 order disagree for at least one account, and -- among
--    MIXED volleys (>= 1 past-limiter pass AND >= 1 limited) -- how often the first account on the wire / first by
--    atc_t0 is a passer. A pass is 'first' if any account holding rank 1 passed (a same-ms tie shares rank 1).
WITH v AS (
  SELECT run_id, race_seq, ident, send_ms, shot_ts_ms AS t0_ms, shot_gate AS gate
  FROM atc_net
  WHERE method = 'POST' AND shot_line IS NOT NULL AND flip_opened_race = 1 AND is_first = 1
),
k AS (
  SELECT v.*,
         RANK() OVER (PARTITION BY run_id, race_seq ORDER BY send_ms) AS wire_rank,
         RANK() OVER (PARTITION BY run_id, race_seq ORDER BY t0_ms) AS t0_rank,
         COUNT(*) OVER (PARTITION BY run_id, race_seq) AS volley_n,
         CASE WHEN gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 1 ELSE 0 END AS is_pass
  FROM v
),
g AS (
  SELECT run_id, race_seq,
         SUM(is_pass) AS passes, SUM(gate = 'wall1_limited') AS limited,
         SUM(wire_rank <> t0_rank) AS rank_diff,
         MAX(CASE WHEN wire_rank = 1 THEN is_pass ELSE 0 END) AS wire_first_pass,
         MAX(CASE WHEN t0_rank = 1 THEN is_pass ELSE 0 END) AS t0_first_pass
  FROM k
  WHERE volley_n >= 2
  GROUP BY run_id, race_seq
)
SELECT run_id AS run,
       COUNT(*) AS volleys,
       SUM(rank_diff > 0) AS order_differs,
       SUM(passes > 0 AND limited > 0) AS mixed,
       SUM(passes > 0 AND limited > 0 AND wire_first_pass = 1) AS mixed_wire_first_is_pass,
       SUM(passes > 0 AND limited > 0 AND t0_first_pass = 1) AS mixed_t0_first_is_pass
FROM g
GROUP BY run_id
ORDER BY run_id;

-- 10) R-BANK 1/4: fresh-flip first shots (flip_opened_race = 1, is_first = 1) per account: cred_source x "the account
--     fired a shot on ANOTHER TCIN in the prior 110 s" x "harvest live" (a banked CAPTURED in the prior 300 s). A shot
--     with no cred line shows (none). unk_tcin_prior = prior-110 s shots of that account whose TCIN is NULL (they cannot
--     be called 'another TCIN'; shown, never folded in).
WITH f AS (
  SELECT s.run_id, s.ident, s.cred_source, s.gate,
         (SELECT COUNT(*) FROM shots p WHERE p.run_id = s.run_id AND p.ident = s.ident AND p.tcin IS NOT NULL
            AND s.tcin IS NOT NULL AND p.tcin <> s.tcin AND p.ts_ms < s.ts_ms AND p.ts_ms >= s.ts_ms - 110000) AS other_tcin,
         (SELECT COUNT(*) FROM shots p WHERE p.run_id = s.run_id AND p.ident = s.ident AND p.tcin IS NULL
            AND p.ts_ms < s.ts_ms AND p.ts_ms >= s.ts_ms - 110000) AS unk_tcin_prior,
         (SELECT COUNT(*) FROM harvest h WHERE h.run_id = s.run_id AND h.acct = s.ident AND h.event = 'captured'
            AND h.banked = 1 AND h.ts_ms <= s.ts_ms AND h.ts_ms >= s.ts_ms - 300000) AS live
  FROM shots s
  WHERE s.flip_opened_race = 1 AND s.is_first = 1 AND s.ts_ms IS NOT NULL
)
SELECT run_id AS run, ident,
       CASE WHEN other_tcin > 0 THEN 'yes' ELSE 'no' END AS other_tcin_110s,
       COALESCE(cred_source, '(none)') AS cred_source,
       CASE WHEN live > 0 THEN 'yes' ELSE 'no' END AS harvest_live,
       COUNT(*) AS n,
       SUM(gate NOT IN ('wall1_limited', 'other', 'unknown')) AS past,
       SUM(unk_tcin_prior > 0) AS unk_tcin_prior
FROM f
GROUP BY run_id, ident, 3, 4, 5
ORDER BY run_id, ident, 3, 4, 5;

-- 11) R-BANK 2/4: re-shots (is_first = 0) per account: how many went page-signed. (A re-shot exists only because the
--     account's previous shot failed -- the selection effect of agent-context 2B: read the share, not a dose-response.)
--     Runs from run_20260906 on, as statement 2 (earlier runs carry no cred line at all).
SELECT run_id AS run, ident,
       COUNT(*) AS reshots,
       SUM(cred_source = 'page_signed') AS page_signed,
       SUM(cred_source = 'banked_replay') AS banked_replay,
       SUM(cred_source IS NULL) AS no_cred_line,
       ROUND(100.0 * SUM(cred_source = 'page_signed') / NULLIF(SUM(cred_source IS NOT NULL), 0), 1) AS page_signed_pct
FROM shots
WHERE is_first = 0 AND ident IS NOT NULL AND run_id >= 'run_20260906'
GROUP BY run_id, ident
ORDER BY run_id, ident;

-- 12) R-BANK 3/4: past-limiter shots by cred_source x outcome (all shots of the selected runs, any phase, from
--     run_20260906 on; every line, home or not).
SELECT run_id AS run, COALESCE(cred_source, '(none)') AS cred_source,
       COUNT(*) AS past,
       SUM(gate = 'wall2_denied') AS d401,
       SUM(gate IN ('admitted_fs', 'cart')) AS adm,
       SUM(gate = 'cart') AS carts,
       SUM(gate IN ('inventory', 'cart_limit')) AS other
FROM shots
WHERE gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit') AND run_id >= 'run_20260906'
GROUP BY run_id, 2
ORDER BY run_id, 2;

-- 13) R-BANK 4/4: the verdicts (pre-registered rules in the header). n_pagesigned_live = fresh-flip first shots that went
--     page-signed while the account's harvest was live (>= 3 => MET). n_..._any_capture counts a CAPTURED of either kind.
--     ps_past_limiter = page-signed past-limiter shots (>= 20 => C-1015-04 may move). The rules are per DROP NIGHT:
--     scope says 'POOLED over k runs' when the selection holds more than one run with shots -- then pass --run for the
--     night (a pooled history is not a verdict; the 2026-09-06..10-14 store pools 42 page-signed past-limiter shots).
WITH f AS (
  SELECT s.cred_source,
         (SELECT COUNT(*) FROM harvest h WHERE h.run_id = s.run_id AND h.acct = s.ident AND h.event = 'captured'
            AND h.banked = 1 AND h.ts_ms <= s.ts_ms AND h.ts_ms >= s.ts_ms - 300000) AS live,
         (SELECT COUNT(*) FROM harvest h WHERE h.run_id = s.run_id AND h.acct = s.ident AND h.event = 'captured'
            AND h.ts_ms <= s.ts_ms AND h.ts_ms >= s.ts_ms - 300000) AS live_any
  FROM shots s
  WHERE s.flip_opened_race = 1 AND s.is_first = 1 AND s.ts_ms IS NOT NULL
),
t AS (
  SELECT (SELECT COUNT(DISTINCT run_id) FROM shots) AS n_runs,
         (SELECT COUNT(*) FROM f) AS n_first,
         (SELECT COUNT(*) FROM harvest) AS n_harvest_rows,
         (SELECT COUNT(*) FROM harvest WHERE event = 'captured') AS n_captured,
         (SELECT COUNT(*) FROM f WHERE cred_source = 'page_signed' AND live > 0) AS n_pagesigned_live,
         (SELECT COUNT(*) FROM f WHERE cred_source = 'page_signed' AND live_any > 0) AS n_pagesigned_live_any_capture,
         (SELECT COUNT(*) FROM shots WHERE cred_source = 'page_signed'
            AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS ps_past_limiter
)
SELECT CASE WHEN n_runs > 1 THEN 'POOLED over ' || n_runs || ' runs: pass --run for one night'
            ELSE 'one run' END AS scope,
       n_first, n_captured, n_pagesigned_live, n_pagesigned_live_any_capture,
       CASE WHEN n_first = 0 THEN 'NO DATA (no fresh-flip first shots)'
            WHEN n_harvest_rows = 0 THEN 'NO DATA (no harvest rows: rebuild the store with parser v5)'
            WHEN n_pagesigned_live >= 3 THEN 'R-BANK MET: build the flag-gated BANK-RESERVE (offline tests + Opus verifier)'
            ELSE 'R-BANK NOT MET (< 3)' END AS r_bank,
       ps_past_limiter,
       CASE WHEN ps_past_limiter >= 20 THEN 'C-1015-04 may move (>= 20 page-signed past-limiter shots)'
            ELSE 'C-1015-04 stays NOT ESTABLISHED (< 20 page-signed past-limiter shots)' END AS c_1015_04
FROM t;
