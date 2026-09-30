-- checkout: won-cart tickets per cart (totals, then by status/key), then the won-cart loop end reasons
--
-- Tickets are [FS_TICKET] lines (one per checkout ticket fired at a WON cart, TARGET_FS_TICKET_LOG=1,
-- first data 2026-09-17); loop ends are "[WON_CART_DIRECT] end reason=" lines. cart_id is truncated
-- to 12 chars by the bot; mode=legacy tickets carry cart_id '-'.

-- 1) per cart
SELECT run_id AS run, ident, tcin, cart_id,
       COUNT(*) AS tickets,
       SUM(key LIKE '%FAST_SELLING%') AS fast_selling,
       SUM(key LIKE '%RESERVATION%') AS reservation_fail,
       SUM(status IN (200, 201)) AS ok_2xx,
       MIN(ms_since_201) AS first_ms_since_201,
       MAX(ms_since_201) AS last_ms_since_201,
       MIN(line) AS first_line,
       MAX(line) AS last_line
FROM tickets
GROUP BY run_id, ident, tcin, cart_id
ORDER BY run_id, first_line;

-- 2) per cart x layer x mode x status x key
SELECT run_id AS run, ident, tcin, cart_id, layer, mode, status, key,
       COUNT(*) AS tickets, MIN(n) AS first_n, MAX(n) AS last_n
FROM tickets
GROUP BY run_id, ident, tcin, cart_id, layer, mode, status, key
ORDER BY run_id, MIN(line);

-- 3) loop end reasons
SELECT run_id AS run, line, ident, tcin, reason, verdict, tickets_call, tickets_cart,
       live, oos, verified, cvv_put, held, dl_left_s
FROM loop_ends
ORDER BY run_id, line;
