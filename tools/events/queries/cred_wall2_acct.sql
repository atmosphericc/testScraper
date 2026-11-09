-- cred_wall2_acct: cred_wall2 split by ACCOUNT (ident) -- the per-account regime-vs-degradation
-- discriminator. Needed because the harvest-survival fixes arm on ONE account (post-run skill: scope
-- uncertain changes to one account so the others are same-window controls); reading worked/failed then
-- REQUIRES splitting the armed account from the controls. Feeds the EXP-HARVEST-DURINGDROP readout and
-- the Q1 guest-sensor portability test (docs/experiments/HARVEST_PX_DURING_DROP_READOUT.md).
--
-- Optional filter:  -p acct=alt-1   (unset = every account). Same gate taxonomy as cred_wall2.sql / walls.sql.
-- Scope to a run as usual:  --run run_20261106_001109
--
-- wall-1 PASS = gate IN (wall2_denied, admitted_fs, cart, inventory, cart_limit)
-- reached_cart = wall-1 pass MINUS wall2_denied
--
-- READOUT RULE (per armed account, pre-registered 2026-11-08):
--   WORKED        banked_replay past_limiter >= 20 AND reached_pct in the historical home band ~29-43%
--                 (the armed account's bank survived the PX wall and its admitted shots reached the cart
--                 service, unlike the page_signed fallback's ~0.35%).
--   FAILED        banked_replay past_limiter still ~0 (the bank never refilled through the wall), OR
--                 reached_pct ~0 with w2_denied ~100% (the swapped sensor was rejected at wall-2 -- i.e.
--                 the guest/cross-browser sensor is not portable; see the Q1 E-SHOOTER gap).
--   INCONCLUSIVE  banked_replay past_limiter < 20 on the armed account (too few admits to read a rate).
SELECT COALESCE(s.ident, '?') AS ident,
       COALESCE(s.cred_source, '?') AS cred_source,
       SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS past_limiter,
       SUM(s.gate = 'wall2_denied') AS w2_denied,
       SUM(s.gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit')) AS reached_cart,
       ROUND(100.0 * SUM(s.gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit'))
             / NULLIF(SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')), 0), 1) AS reached_pct,
       SUM(s.gate = 'cart') AS carts,
       SUM(s.gate IN ('wall1_limited', 'wall1_keyless')) AS wall1_blocked
FROM shots s
WHERE (:acct IS NULL OR s.ident = :acct)
GROUP BY 1, 2
ORDER BY ident, past_limiter DESC, cred_source;
