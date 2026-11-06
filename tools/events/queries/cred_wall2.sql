-- cred_wall2: per run x credential source -- of the shots that PASSED the edge limiter,
-- how many reached the cart service vs died at the Shape keyless-401 (wall 2), split by cred_source.
-- The regime-vs-degradation discriminator (FX E1, landed 2026-11-06, post-run wf_1656c678-6c2).
--
-- WHY THIS EXISTS: on 2026-11-06 the limiter admitted 273 shots (a first for the zero-night era) but
-- ALL 273 were the page_signed fallback (the harvest bank was PX-walled empty 04:35-09:31), and
-- page_signed is denied at the SSX hop ~99% historically. Read naively, 273/273 keyless-401 looks like
-- Shape hardening; split by cred_source it is the page_signed base rate with ZERO admitted banked_replay
-- to compare against. This query makes that split explicit so the confound can never be laundered again.
--
-- wall-1 PASS = gate IN (wall2_denied, admitted_fs, cart, inventory, cart_limit)  [same taxonomy as walls.sql]
-- reached_cart = wall-1 pass MINUS wall2_denied = gate IN (admitted_fs, cart, inventory, cart_limit)
--
-- READOUT RULE (pre-registered; n = the banked_replay past_limiter count on the run):
--   REGIME-HARDENING  claimable ONLY if banked_replay past_limiter n>=20 AND its reached_pct falls below the
--                     historical home-line banked reach-cart band (~29-43%; home 65/151, 09-11..10-29).
--   CONFOUNDED        if >90% of the past-limiter shots are page_signed -> the bank was empty/walled, every
--                     admitted shot used the fallback Shape rejects ~99%; the 401 rate is the page_signed
--                     base rate, NOT measured Shape hardening. (run_20261106_001109 returns this: 273/273.)
--   INCONCLUSIVE      otherwise (too few admitted banked_replay shots to read a rate).
SELECT COALESCE(s.cred_source, '?') AS cred_source,
       SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS past_limiter,
       SUM(s.gate = 'wall2_denied') AS w2_denied,
       SUM(s.gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit')) AS reached_cart,
       ROUND(100.0 * SUM(s.gate IN ('admitted_fs', 'cart', 'inventory', 'cart_limit'))
             / NULLIF(SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')), 0), 1) AS reached_pct,
       SUM(s.gate = 'cart') AS carts,
       SUM(s.gate IN ('wall1_limited', 'wall1_keyless')) AS wall1_blocked
FROM shots s
GROUP BY 1
ORDER BY past_limiter DESC, cred_source;
