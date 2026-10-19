-- cart_limit_empty: did Target 400 (MAX_PURCHASE_LIMIT_EXCEEDED) an add-to-cart into an EMPTY cart? (limit-1 first-add readout)
--
-- Source: readiness audit 2026-10-18/19 (gap1.1 F2). We ship qty 2 on the first add. If a limit-1 PDP TCIN is clamped or
--   refused, which one it is only shows up on the FIRST add of a TCIN into an account's cart.
--
-- PRE-REGISTERED RULE (read statement 2, one row per cart_limit shot):
--   * ONE row with first_add = 1 (a cart_limit shot with NO earlier 201 on the same run + ident + TCIN) = Target 400s a qty-2
--     add into an empty cart: the qty-2 first add is refused on a limit-1 TCIN.
--   * a 201 for that TCIN whose cart_qty = 1 = Target CLAMPED the qty-2 add to 1 (the add worked, the quantity was cut).
--   * a 201 for that TCIN whose cart_qty = 2 on a PDP limit-1 TCIN = the limit is PERMISSIVE at add time (then watch the account's
--     order history for "Quantity Limit" cancels after the order).
--   * zero rows with first_add = 1 and no 201 for the TCIN = no evidence either way (do not read it as "permissive").
--
-- DEFINITIONS
--   cart_limit shot  gate = 'cart_limit' (HTTP 400, err_key MAX_PURCHASE_LIMIT_EXCEEDED; parser v2+, 2026-10-01).
--   earlier          lower log line within the same run_id (one run = one log file, lines are monotonic).
--   ident_state      'NULL_IDENT' when the shot row carries no ident (legacy / resp_only rows: the per-ident test cannot be made, so
--                    first_add is NULL and the row is listed apart, never counted as a first add); else 'ident'.
--   first_add        1 / 0 as above (NULL for NULL_IDENT). Carts of an EARLIER RUN are invisible here (a cart outlives the bot), so a
--                    first_add = 1 is an UPPER bound on "empty cart": a cart left by a previous boot could already hold the line.
--   prior_201_ident  201 shots earlier in the run for this ident + TCIN;  run_201_tcin = 201 shots in the whole run for this TCIN
--                    (any ident);  run_201_lines = their 'ident@line' list.
--   cart_qty         the cart quantity Target reported in the 201 body. THE STORE DOES NOT CARRY IT (build.py does not parse the
--                    chain-done "cart_qty=" field), so the column is NULL by construction. To fill it by hand: open
--                    logs/runs/<run>.log at each run_201_lines line and read the "[FAST_LANE] chain done ... cart_qty=<n>" line
--                    that follows it (cart_qty=- means not logged / 429).
-- Read-only. Statement 1 = summary, statement 2 = one row per cart_limit shot (oldest first).
SELECT COUNT(*) AS cart_limit_shots,
       SUM(first_add = 1) AS first_add_rows,
       SUM(first_add = 0) AS after_201_rows,
       SUM(first_add IS NULL) AS null_ident_rows
FROM (
  SELECT (CASE WHEN cl.ident IS NULL THEN NULL
               WHEN EXISTS (SELECT 1 FROM shots p WHERE p.run_id = cl.run_id AND p.ident = cl.ident AND p.tcin = cl.tcin
                              AND p.gate = 'cart' AND p.line < cl.line) THEN 0 ELSE 1 END) AS first_add
  FROM shots cl
  WHERE cl.gate = 'cart_limit'
);

SELECT cl.run_id AS run,
       cl.line,
       cl.ident,
       cl.tcin,
       CASE WHEN cl.ident IS NULL THEN 'NULL_IDENT' ELSE 'ident' END AS ident_state,
       CASE WHEN cl.ident IS NULL THEN NULL
            WHEN EXISTS (SELECT 1 FROM shots p WHERE p.run_id = cl.run_id AND p.ident = cl.ident AND p.tcin = cl.tcin
                           AND p.gate = 'cart' AND p.line < cl.line) THEN 0 ELSE 1 END AS first_add,
       (SELECT COUNT(*) FROM shots p WHERE p.run_id = cl.run_id AND p.ident = cl.ident AND p.tcin = cl.tcin
          AND p.gate = 'cart' AND p.line < cl.line) AS prior_201_ident,
       (SELECT COUNT(*) FROM shots p WHERE p.run_id = cl.run_id AND p.tcin = cl.tcin AND p.gate = 'cart') AS run_201_tcin,
       (SELECT group_concat(COALESCE(p.ident, '?') || '@' || p.line, ' ') FROM shots p
         WHERE p.run_id = cl.run_id AND p.tcin = cl.tcin AND p.gate = 'cart') AS run_201_lines,
       NULL AS cart_qty,
       cl.status,
       cl.err_key,
       cl.cred_source,
       cl.proxied
FROM shots cl
WHERE cl.gate = 'cart_limit'
ORDER BY cl.run_id, cl.line;
