-- late_carts: every add-to-cart 201 by its window age, with its FIRST place-order's outcome and delay
--
-- Usage:  python tools/events/q.py late_carts [--run 20260930_233818]
-- A cart = a shots row with gate 'cart' (any src: chain, legacy, legacy_retry = the 401 ladder's retry
--   adds, parser v2). cart_age_s = shots.ep_age_ms: the shot's own time (atc_t0, else the last logger
--   stamp before its line) minus its in-stock episode open -- the [STOCK][FLIP] window when one covers it,
--   else the read-based episode (shots.ep_src says which).
-- First place-order = the place_orders row with cart_line = the shot's line and po_idx = 1. link says how
--   the POST was tied to the cart: 'chain' (the chain's own POST) and 'ident' (the account's latest cart)
--   are direct; 'fifo_first' (oldest cart with no POST yet, <= 60 s) and 'unique_recent' (the only cart in
--   180 s) are heuristics for lines that carry no ident (mostly pre-September). A cart with no link shows
--   a NULL first POST -- never guessed.
-- delay_ms = place_orders.ms_since_201 of that first POST: chain atc_t0+atc_rt, the bot's own [FS_TICKET]
--   ms_since_201, or the linked cart's 201 time (ms201_src); NULL when no 201 time exists.
--
-- PRE-REGISTERED READING (2026-10-01), per run by result 3, on the next restock: "a late cart does not
--   convert" is REPLICATED when >= 3 carts have cart_age > 30 s with a DIRECT (chain / ident) first POST
--   and none of those first POSTs returns 200; NOT REPLICATED when any cart with cart_age > 30 s gets a
--   200 on its first POST (any link); otherwise INCONCLUSIVE.

-- 1) every cart
SELECT s.run_id AS run, s.line AS cart_line, s.src, COALESCE(s.ident, '?') AS ident, s.tcin,
       strftime('%H:%M:%f', s.ts_ms / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS t_local,
       ROUND(s.ep_age_ms / 1000.0, 1) AS cart_age_s, s.ep_src,
       p.line AS po1_line, p.path AS po1_path, p.status AS po1_status, p.err_key AS po1_key,
       p.ms_since_201 AS po1_delay_ms, p.ms201_src, p.cart_src AS link,
       (SELECT COUNT(*) FROM place_orders q WHERE q.run_id = s.run_id AND q.cart_line = s.line) AS n_po,
       (SELECT MAX(q.status = 200) FROM place_orders q WHERE q.run_id = s.run_id AND q.cart_line = s.line) AS any_200,
       (SELECT q.order_id FROM place_orders q WHERE q.run_id = s.run_id AND q.cart_line = s.line
          AND q.order_id IS NOT NULL) AS order_id
FROM shots s
JOIN runs r ON r.run_id = s.run_id
LEFT JOIN place_orders p ON p.run_id = s.run_id AND p.cart_line = s.line AND p.po_idx = 1
WHERE s.gate = 'cart'
ORDER BY s.run_id, s.line;

-- 2) pooled by cart-age bin (whole seconds, truncated)
WITH c AS (
  SELECT s.run_id, s.line,
         CASE WHEN s.ep_age_ms IS NULL THEN '9 unknown' WHEN s.ep_age_ms < 0 THEN '0 neg'
              WHEN s.ep_age_ms / 1000 <= 5 THEN '1 <=5s' WHEN s.ep_age_ms / 1000 <= 30 THEN '2 6-30s'
              WHEN s.ep_age_ms / 1000 <= 120 THEN '3 31-120s' ELSE '4 >120s' END AS bin,
         p.status AS po1_status, p.cart_src AS link, p.ms_since_201 AS delay
  FROM shots s
  LEFT JOIN place_orders p ON p.run_id = s.run_id AND p.cart_line = s.line AND p.po_idx = 1
  WHERE s.gate = 'cart'
)
SELECT 'ALL ' || (SELECT COUNT(DISTINCT run_id) FROM shots WHERE gate = 'cart') || ' runs' AS run, bin,
       COUNT(*) AS carts,
       SUM(po1_status IS NOT NULL) AS with_po1,
       SUM(link IN ('chain', 'ident')) AS direct_link,
       SUM(po1_status = 200) AS po1_200,
       (SELECT COUNT(*) FROM c c2 WHERE c2.bin = c.bin AND EXISTS (
          SELECT 1 FROM place_orders q WHERE q.run_id = c2.run_id AND q.cart_line = c2.line AND q.status = 200)) AS any_200,
       CAST(AVG(delay) AS INTEGER) AS avg_po1_delay_ms
FROM c
GROUP BY bin
ORDER BY bin;

-- 3) the pre-registered reading, per run
WITH c AS (
  SELECT s.run_id, s.ep_age_ms AS age, p.status AS st, p.cart_src AS link
  FROM shots s
  LEFT JOIN place_orders p ON p.run_id = s.run_id AND p.cart_line = s.line AND p.po_idx = 1
  WHERE s.gate = 'cart'
)
SELECT u.run_id AS run,
       COUNT(c.run_id) AS carts,
       SUM(c.age IS NULL AND c.run_id IS NOT NULL) AS age_unknown,
       COALESCE(SUM(c.age / 1000 > 30), 0) AS late_carts,
       COALESCE(SUM(c.age / 1000 > 30 AND c.link IN ('chain', 'ident')), 0) AS late_direct,
       COALESCE(SUM(c.age / 1000 > 30 AND c.st = 200), 0) AS late_po1_200,
       CASE WHEN SUM(c.age / 1000 > 30 AND c.st = 200) > 0 THEN 'NOT REPLICATED'
            WHEN SUM(c.age / 1000 > 30 AND c.link IN ('chain', 'ident')) >= 3 THEN 'REPLICATED'
            ELSE 'INCONCLUSIVE' END AS reading
FROM runs u
LEFT JOIN c ON c.run_id = u.run_id
GROUP BY u.run_id
ORDER BY u.run_id;
