-- walls: per run x ident x proxied x first/later shot -- where each add-to-cart stopped
--
-- w1_pass     passed the hot-item limiter = any gate except wall1_limited/other/unknown. A 401 COUNTS as a
--             wall-1 pass (it is past the limiter, denied at the SSX hop). So does cart_limit (parser v2,
--             2026-10-01: a 400 MAX_PURCHASE_LIMIT_EXCEEDED is a cart-service answer, x-ssx-hop=1; it was
--             'other' before v2). n = w1_limited + w1_pass + other + unknown;
--             w1_pass = w2_denied + fs + inv + carts + climit.
-- w1_pass_pct over the classified shots (n - other - unknown); other/unknown are shown, never folded in.
-- w2_denied   401s; w2_deny_pct = 401s among the wall-1 passes.
-- line        'home' / 'proxied' from [EXPOSURE] proxied= (09-17+) or the boot [FORWARDER] binds; '?' unknown.
-- shot        'first' = shot_idx 1 of that ident in that race; '?' = no race/ident (resp_only rows).
SELECT s.run_id AS run,
       COALESCE(s.ident, '?') AS ident,
       CASE s.proxied WHEN 1 THEN 'proxied' WHEN 0 THEN 'home' ELSE '?' END AS line,
       CASE s.is_first WHEN 1 THEN 'first' WHEN 0 THEN 'later' ELSE '?' END AS shot,
       COUNT(*) AS n,
       SUM(s.gate = 'wall1_limited') AS w1_limited,
       SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')) AS w1_pass,
       ROUND(100.0 * SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit'))
             / NULLIF(SUM(s.gate NOT IN ('other', 'unknown')), 0), 1) AS w1_pass_pct,
       SUM(s.gate = 'wall2_denied') AS w2_denied,
       ROUND(100.0 * SUM(s.gate = 'wall2_denied')
             / NULLIF(SUM(s.gate IN ('wall2_denied', 'admitted_fs', 'cart', 'inventory', 'cart_limit')), 0), 1) AS w2_deny_pct,
       SUM(s.gate = 'admitted_fs') AS fs,
       SUM(s.gate = 'inventory') AS inv,
       SUM(s.gate = 'cart') AS carts,
       SUM(s.gate = 'cart_limit') AS climit,
       SUM(s.gate = 'other') AS other,
       SUM(s.gate = 'unknown') AS unknown
FROM shots s
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 4;
