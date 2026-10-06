-- gate_events: per run, the 10-05 gate markers by kind (BOOT_SKIP / PO2XX guard / foreign keep / foreign bail)
--
-- Usage:  python tools/events/q.py gate_events [--run 20261006]
-- Source: print-only lines, parser v3 (2026-10-05); kinds in build.py's module doc, GATE_EVENTS:
--   boot_skip_skipping       FX-1005-BOOTSKIP (TARGET_BOOT_SKIP_FAILED_W1=1): this boot ran WITHOUT that account
--   boot_skip_recorded / _cleared / _none   what a failed Worker-1 boot login probe did to the skip list just
--                            before exit 87 (recorded = next boot skips it; cleared = a skip was already in
--                            force, so the list was dropped and the next boot runs the full fleet)
--   boot_skip_ignored_all / _ignored_paths  a skip list that was NOT applied; boot_skip_error = bookkeeping failed
--   boot_skip_login_*        relogin_one.py cleared an entry (rarely in a run log)
--   po2xx_guard              FX-1005-PO2XX (TARGET_PO_2XX_AMBIGUOUS=1): a place-order 2xx other than 200/201 was
--                            held unresolved and the purchase bailed terminal -- the order MAY exist: check order
--                            history at once
--   po_gateway_guard / po_noresp_guard      the same [DOUBLE-BUY GUARD] on a 408/5xx / on no response
--   foreign_keep_po_only / _pre_po          FX-1005-FOREIGN-KEEP: a won cart that once held another TCIN's line
--                            was re-read before a po_only ticket (kept po_only / fell back to pre_po)
--   late_add_po_only / _pre_po              the same re-read for another suspect (detail sus=: harvest_add,
--                            orphan_atc)
--   foreign_bail             [FAST_LANE] found another item in the cart and cleared the WHOLE cart (09-09 bail);
--                            foreign_bail_clear_failed = that clear raised
-- who = the accounts named (NULL for the guard lines, which name none). first_ts = the last logger stamp
--   before the first line, local time (bare prints carry no time: agent-context 2C-bis); lines are exact.
-- A kind that never fired prints no row. Its absence is a zero only if its flag was armed: check the bat.
SELECT g.run_id AS run,
       g.kind,
       COUNT(*) AS n,
       COUNT(DISTINCT g.ident_or_acct) AS accts,
       group_concat(DISTINCT g.ident_or_acct) AS who,
       strftime('%Y-%m-%d %H:%M:%S', MIN(g.ts_ms) / 1000.0 + r.utc_offset_min * 60, 'unixepoch') AS first_ts,
       MIN(g.line) AS first_line,
       MAX(g.line) AS last_line,
       (SELECT x.detail FROM gate_events x WHERE x.run_id = g.run_id AND x.kind = g.kind
         ORDER BY x.line LIMIT 1) AS first_detail
FROM gate_events g
JOIN runs r ON r.run_id = g.run_id
GROUP BY g.run_id, g.kind
ORDER BY g.run_id, g.kind;
