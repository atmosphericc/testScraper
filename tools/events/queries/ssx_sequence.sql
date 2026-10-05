-- ssx_sequence: per run x TCIN on the HOME line, the time-ordered shots past the limiter -- do
--   cart-service admits stop once the SSX hop starts answering keyless 401s?
--
-- Usage:  python tools/events/q.py ssx_sequence [--run 20260930_233818]
-- Shots: proxied = 0 (home-line buyer shots; NULL proxied is left out) with a gate past the edge limiter
--   (C-0930-03): wall2_denied (401 at the SSX hop), admitted_fs (FAST_SELLING 429), cart (201),
--   inventory (424), cart_limit (400 MAX_PURCHASE_LIMIT_EXCEEDED, parser v2). Ordered by ts_ms, then line.
-- kind: admit = a cart-service response (admitted_fs / cart / inventory / cart_limit); k401 = a keyless
--   401 (wall2_denied with err_key NULL or '-'); keyed401 = a 401 that carries a key (none seen to date;
--   it breaks a keyless run and is never counted as either).
-- switch = the first keyless 401 that follows an admit. admits_before = admits before it;
--   k401_from = keyless 401s from it on; admits_after = admits after it; run_after_last_admit = the
--   consecutive keyless 401s right after the LAST admit.
-- Reference (2026-10-01): run_20260930_233818 TCIN 1010892076 = 6 admits (4 FAST_SELLING + 2 x 201,
--   02:15:19-02:20:00), then 70 keyless 401s, then 0 admits.
--
-- PRE-REGISTERED READING (2026-10-01), on the next restock (result 2): the switch is REPLICATED if some
--   TCIN with >= 20 past-limiter shots shows >= 1 admit followed by >= 15 consecutive keyless 401s and 0
--   later admits (rep = 1); NOT REPLICATED if a TCIN with >= 20 past-limiter shots has an admit after its
--   first post-admit 401 (not_rep = 1); otherwise INCONCLUSIVE. When both hold (on different TCINs, or
--   on one TCIN that re-admits and then switches) the verdict prints MIXED -- the rule does not rank them.
--   tcin '?' (no TCIN) rows are shown but never scored.

-- 1) per run x TCIN
WITH p AS (
  SELECT run_id, COALESCE(tcin, '?') AS tcin, line, ts_ms, gate,
         CASE WHEN gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 'admit'
              WHEN COALESCE(err_key, '-') IN ('-', '') THEN 'k401' ELSE 'keyed401' END AS kind
  FROM shots
  WHERE proxied = 0 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')
),
q AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY run_id, tcin ORDER BY ts_ms, line) AS k FROM p
),
a AS (
  SELECT run_id, tcin, COUNT(*) AS n, MIN(CASE WHEN kind = 'admit' THEN k END) AS fa,
         MAX(CASE WHEN kind = 'admit' THEN k END) AS la
  FROM q GROUP BY run_id, tcin
),
s AS (
  SELECT a.*,
         (SELECT MIN(k) FROM q WHERE q.run_id = a.run_id AND q.tcin = a.tcin AND q.kind = 'k401' AND q.k > a.fa) AS sw,
         (SELECT MIN(k) FROM q WHERE q.run_id = a.run_id AND q.tcin = a.tcin AND q.kind <> 'k401' AND q.k > a.la) AS nk
  FROM a
),
t AS (
  SELECT s.*,
         (SELECT COUNT(*) FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.kind = 'admit'
            AND (s.sw IS NULL OR q.k < s.sw)) AS admits_before,
         (SELECT COUNT(*) FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.kind = 'k401'
            AND s.sw IS NOT NULL AND q.k >= s.sw) AS k401_from,
         (SELECT COUNT(*) FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.kind = 'admit'
            AND s.sw IS NOT NULL AND q.k > s.sw) AS admits_after,
         CASE WHEN s.la IS NULL THEN NULL ELSE COALESCE(s.nk, s.n + 1) - s.la - 1 END AS run_after_last_admit,
         (SELECT COUNT(*) FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.kind = 'keyed401') AS keyed401,
         (SELECT ts_ms FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.k = s.fa) AS t_fa,
         (SELECT MAX(ts_ms) FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.kind = 'admit'
            AND (s.sw IS NULL OR q.k < s.sw)) AS t_lb,
         (SELECT ts_ms FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.k = s.sw) AS t_sw
  FROM s
)
SELECT t.run_id AS run, t.tcin, t.n,
       t.admits_before, t.k401_from, t.admits_after, t.run_after_last_admit, t.keyed401,
       strftime('%H:%M:%S', t.t_fa / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS first_admit,
       strftime('%H:%M:%S', t.t_lb / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS last_admit_before,
       strftime('%H:%M:%S', t.t_sw / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS switch_401,
       CASE WHEN t.tcin <> '?' AND t.n >= 20 AND t.la IS NOT NULL AND t.run_after_last_admit >= 15
            THEN 1 ELSE 0 END AS rep,
       CASE WHEN t.tcin <> '?' AND t.n >= 20 AND t.admits_after > 0 THEN 1 ELSE 0 END AS not_rep,
       CASE WHEN t.tcin = '?' THEN 'not scored: no TCIN'
            WHEN t.n < 20 THEN 'not scored: n<20'
            WHEN t.fa IS NULL THEN 'no admit'
            WHEN t.admits_after > 0 AND t.run_after_last_admit >= 15 THEN 're-admit, then switch'
            WHEN t.admits_after > 0 THEN 'admit after a post-admit 401'
            WHEN t.sw IS NULL THEN 'admits, no keyless 401 after'
            WHEN t.run_after_last_admit >= 15 THEN 'switch'
            ELSE 'switch run < 15' END AS reading
FROM t
JOIN runs r ON r.run_id = t.run_id
ORDER BY t.run_id, t.n DESC;

-- 2) the pre-registered verdict, per run
WITH p AS (
  SELECT run_id, COALESCE(tcin, '?') AS tcin, line, ts_ms, gate,
         CASE WHEN gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit') THEN 'admit'
              WHEN COALESCE(err_key, '-') IN ('-', '') THEN 'k401' ELSE 'keyed401' END AS kind
  FROM shots
  WHERE proxied = 0 AND gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')
),
q AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY run_id, tcin ORDER BY ts_ms, line) AS k FROM p
),
a AS (
  SELECT run_id, tcin, COUNT(*) AS n, MIN(CASE WHEN kind = 'admit' THEN k END) AS fa,
         MAX(CASE WHEN kind = 'admit' THEN k END) AS la
  FROM q GROUP BY run_id, tcin
),
s AS (
  SELECT a.*,
         (SELECT MIN(k) FROM q WHERE q.run_id = a.run_id AND q.tcin = a.tcin AND q.kind = 'k401' AND q.k > a.fa) AS sw,
         (SELECT MIN(k) FROM q WHERE q.run_id = a.run_id AND q.tcin = a.tcin AND q.kind <> 'k401' AND q.k > a.la) AS nk
  FROM a
),
f AS (
  SELECT run_id, tcin, n,
         CASE WHEN tcin <> '?' AND n >= 20 AND la IS NOT NULL AND COALESCE(nk, n + 1) - la - 1 >= 15
              THEN 1 ELSE 0 END AS rep,
         CASE WHEN tcin <> '?' AND n >= 20 AND sw IS NOT NULL AND EXISTS (
                SELECT 1 FROM q WHERE q.run_id = s.run_id AND q.tcin = s.tcin AND q.kind = 'admit' AND q.k > s.sw)
              THEN 1 ELSE 0 END AS not_rep
  FROM s
)
SELECT r.run_id AS run,
       COUNT(f.tcin) AS tcins_past_limiter,
       COALESCE(SUM(f.n >= 20 AND f.tcin <> '?'), 0) AS scored_tcins,
       COALESCE(SUM(f.rep), 0) AS rep_tcins,
       COALESCE(SUM(f.not_rep), 0) AS not_rep_tcins,
       CASE WHEN SUM(f.rep) > 0 AND SUM(f.not_rep) > 0 THEN 'MIXED'
            WHEN SUM(f.rep) > 0 THEN 'REPLICATED'
            WHEN SUM(f.not_rep) > 0 THEN 'NOT REPLICATED'
            ELSE 'INCONCLUSIVE' END AS verdict
FROM runs r
LEFT JOIN f ON f.run_id = r.run_id
GROUP BY r.run_id
ORDER BY r.run_id;
