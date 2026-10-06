#!/usr/bin/env python3
"""Offline tests: the 2026-10-05 gate-hardening audit fixes. All flag-gated, default off.

  FX-1005-PO2XX       TARGET_PO_2XX_AMBIGUOUS=1: a place-order 2xx other than 200/201 is
                      unresolved (double-buy guard, AC-1 tag) instead of a "rejection" that
                      falls through to a second place-order.
  FX-1005-FOREIGN-KEEP TARGET_FOREIGN_KEEP_WON=1: a won chain stopped at foreign_cart_item
                      enters the won-cart loop (selective delete keep_tcin=T) instead of the
                      foreign bail's WHOLE-cart clear.
  FX-1005-BOOTSKIP    TARGET_BOOT_SKIP_FAILED_W1=1: a failed Worker-1 boot login probe is
                      recorded; the next boot builds the fleet without that account (like
                      "enabled": false); a real login clears it.
  INS-ATC-NET         TARGET_ATC_NET_META=1: log-only [ATC_NET] line per main-tab
                      cart_items request from CDP Network events.

No browser, no network, no bot. Temp files only.
Run: venv/Scripts/python.exe tests/test_gate_hardening_1005.py
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NEW_FLAGS = ("TARGET_PO_2XX_AMBIGUOUS", "TARGET_FOREIGN_KEEP_WON", "TARGET_BOOT_SKIP_FAILED_W1",
             "TARGET_BOOT_SKIP_TTL_H", "TARGET_ATC_NET_META")
OTHER = ("TARGET_AMBIGUOUS_COMMIT_LATCH", "TARGET_PO_5XX_AMBIGUOUS")
for _k in NEW_FLAGS + OTHER:
    os.environ.pop(_k, None)

import src.session.purchase_executor as pe          # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402
import src.purchasing.worker_pool as wp             # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {str(detail)[:400]}")


class _Env:
    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in NEW_FLAGS + OTHER}
        for k in NEW_FLAGS + OTHER:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in self.kv.items() if v is not None})

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


PE_SRC = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")
TCIN = "1010892069"


class _SM:
    account_id = "primary"


def bare_pe():
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex.session_manager = _SM()
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._fastlane_placed = False
    ex._cvv_required = False
    ex._atc_referrer_pdp = False
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    ex._fast_selling_until = 0.0
    ex._won_cart_ride_until = 0.0
    ex._cached_cart_headers = {}
    ex._cached_cart_headers_ts = 0.0
    ex._shot_ttl_refresh_on = False
    ex._persist_cvv_challenge_flag = lambda: None
    ex._note_fast_selling_throttle = lambda: None
    ex._po_inflight = False
    ex._po_ambiguous = False
    ex._woncart_po_unresolved = False
    return ex


# ───────────────────────────────── FX-1005-PO2XX ─────────────────────────────────
def test_po2xx_helper():
    u = pe.po_status_unresolved
    with _Env():
        check("po2xx_off_202_resolved", u(202) is False and u(202, ticket=True) is False)
        check("po2xx_off_204_resolved", u(204) is False and u(204, ticket=True) is False)
        check("po2xx_off_5xx_ticket_unchanged", u(502, ticket=True) is True and u(502) is False)
    with _Env(TARGET_AMBIGUOUS_COMMIT_LATCH="1"):
        check("po2xx_off_5xx_ac1_unchanged", u(503) is True and u(408) is True and u(202) is False)
    with _Env(TARGET_PO_2XX_AMBIGUOUS="1"):
        check("po2xx_on_202_unresolved", u(202) is True and u(202, ticket=True) is True)
        check("po2xx_on_204_299_unresolved", u(204) is True and u(299) is True)
        check("po2xx_on_200_201_still_placed", u(200) is False and u(201) is False
              and u(200, ticket=True) is False and u(201, ticket=True) is False)
        check("po2xx_on_non2xx_unchanged", u(429) is False and u(424) is False and u(400) is False
              and u(0) is False and u(502) is False and u(502, ticket=True) is True)
        check("po2xx_on_odd_inputs", u(True) is False and u(None) is False and u("x") is False
              and u("202") is True)
    with _Env(TARGET_PO_2XX_AMBIGUOUS="0"):
        check("po2xx_explicit_0_off", u(202) is False)


def test_po2xx_apply_result():
    fl = {"atc": {"status": 201}, "pre": {"status": 200},
          "po": {"status": 202, "fired": True, "body": ""}, "skip": ""}
    ex = bare_pe()
    with _Env():
        (v, term), out = quiet(ex._apply_fast_lane_result, dict(fl), TCIN, time.time())
    check("apply_off_202_falls_through", v == "fallthrough" and ex._po_ambiguous is False, (v, term))
    ex = bare_pe()
    with _Env(TARGET_PO_2XX_AMBIGUOUS="1"):
        (v, term), out = quiet(ex._apply_fast_lane_result, dict(fl), TCIN, time.time())
    check("apply_on_202_terminal", v == "terminal" and ex._po_ambiguous is True, (v, term))
    check("apply_on_202_no_ac1_tag_without_latch", "ambiguous_commit" not in (term or {}), term)
    check("apply_on_202_message", "unexpected 2xx" in out and "DOUBLE-BUY GUARD" in out, out)
    ex = bare_pe()
    with _Env(TARGET_PO_2XX_AMBIGUOUS="1", TARGET_AMBIGUOUS_COMMIT_LATCH="1"):
        (v, term), out = quiet(ex._apply_fast_lane_result, dict(fl), TCIN, time.time())
    check("apply_on_202_ac1_tagged", v == "terminal" and (term or {}).get("ambiguous_commit") is True, term)
    ex = bare_pe()
    with _Env(TARGET_PO_2XX_AMBIGUOUS="1"):
        (v, term), out = quiet(ex._apply_fast_lane_result, dict(fl), TCIN, time.time(), ticket=True)
    check("apply_ticket_202_terminal", v == "terminal", (v, term))
    fl5 = dict(fl, po={"status": 502, "fired": True, "body": ""})
    ex = bare_pe()
    with _Env(TARGET_AMBIGUOUS_COMMIT_LATCH="1"):
        (v, term), out = quiet(ex._apply_fast_lane_result, fl5, TCIN, time.time())
    check("apply_5xx_message_unchanged", v == "terminal"
          and "a gateway error does not prove the order failed" in out, out)


def test_po2xx_call_sites():
    check("site_fast_lane", "_po_5xx = bool(po.get('fired')) and po_status_unresolved(status, ticket=ticket)"
          in PE_SRC)
    check("site_ticket", "if fired and (pst == 0 or po_status_unresolved(pst, ticket=True)):" in PE_SRC)
    check("site_legacy", "_legacy_po_5xx = po_status_unresolved(api_result.get('status', 0))" in PE_SRC)
    i_guard = PE_SRC.find("if po.get('fired') and (status == 0 or _po_5xx):")
    i_placed = PE_SRC.find("        if status in (200, 201):\r\n            # ORDER PLACED")
    if i_placed < 0:
        i_placed = PE_SRC.find("        if status in (200, 201):\n            # ORDER PLACED")
    check("site_guard_before_placed", 0 < i_guard < i_placed, (i_guard, i_placed))


# ────────────────────────────── FX-1005-FOREIGN-KEEP ─────────────────────────────
def test_foreign_keep_eligible():
    el = pe.woncart_eligible
    won = {"atc": {"status": 201}, "pre": {"status": 201, "tcins": [TCIN, "999"]},
           "po": {}, "skip": "foreign_cart_item"}
    strike = {"atc": {"status": 400, "strike": True}, "pre": {"status": 200},
              "po": {}, "skip": "foreign_cart_item"}
    with _Env():
        check("fk_off_not_eligible", el(won) is False and el(strike) is False)
    with _Env(TARGET_FOREIGN_KEEP_WON="1"):
        check("fk_on_without_po2xx_inert", el(won) is False and el(strike) is False
              and pe.foreign_keep_won_on() is False)
    with _Env(TARGET_FOREIGN_KEEP_WON="1", TARGET_PO_2XX_AMBIGUOUS="1"):
        check("fk_on_won_eligible", el(won) is True)
        check("fk_on_strike_eligible", el(strike) is True)
        check("fk_on_po_fired_not_eligible", el(dict(won, po={"fired": True, "status": 424})) is False)
        check("fk_on_failed_atc_not_eligible", el(dict(won, atc={"status": 429})) is False
              and el(dict(won, atc={"status": 400})) is False)
        check("fk_on_other_skip_unchanged", el(dict(won, skip="cart_qty_over")) is False
              and el(dict(won, skip="pre_429")) is True)
    with _Env(TARGET_FOREIGN_KEEP_WON="0", TARGET_PO_2XX_AMBIGUOUS="1"):
        check("fk_explicit_0_off", el(won) is False)


def test_foreign_keep_ledger_and_loop():
    ex = bare_pe()
    fl0 = {"atc": {"status": 201}, "pre": {"status": 201, "tcins": [TCIN, "999"], "qty": 2},
           "po": {}, "skip": "foreign_cart_item"}
    L = ex._woncart_new_ledger(TCIN, 2, fl0, time.time())
    check("fk_ledger_unverified_with_foreign_line", L.get("verified") is False, L)
    fl_ok = dict(fl0, pre={"status": 201, "tcins": [TCIN], "qty": 2}, skip="")
    check("fk_ledger_verified_own_only", ex._woncart_new_ledger(TCIN, 2, fl_ok, time.time())["verified"] is True)
    i_loop = PE_SRC.find("and woncart_eligible(_fl, self._checkout_reject_status,")
    i_bail = PE_SRC.find("and _fl.get('skip') == 'foreign_cart_item'")
    check("fk_loop_entry_precedes_bail", 0 < i_loop < i_bail, (i_loop, i_bail))
    check("fk_loop_foreign_handler_selective",
          "if skip == 'foreign_cart_item':" in PE_SRC
          and "await self._delete_cart_items(tab, keep_tcin=T, budget_s=_b)" in PE_SRC)


def test_foreign_keep_through_the_loop():
    """The real _won_cart_ticket_loop (test_won_cart_direct_smoke harness): a
    foreign entry deletes only the other lines, and every po_only ticket of that
    cart re-reads it first (verifier, 10-05: po_only buys the cart unread)."""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_won_cart_direct_smoke as w
    fl0 = {"atc": {"status": 201, "body": "", "cart_items": [{"tcin": w.TCIN, "quantity": 2}]},
           "pre": {"status": 201, "parsed": True, "n": 2, "tcins": [w.TCIN, w.OTHER], "qty": 3},
           "po": {"status": 0, "body": "", "fired": False}, "cvv": {"put": -1}, "skip": "foreign_cart_item"}
    fs = (429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION")
    script = [w.t_skip("foreign_cart_item", tcins=[w.TCIN, w.OTHER]),
              w.t_prepo(429, w.FS_BODY, rej=fs), w.t_po(200, w.ORDER_BODY)]
    c = w.Clock()
    ex = w.bare(c)
    tab = w.ReadTab(ex, list(script), c, [w.R2_EXACT])
    (v, r), _ = w.loop_run(ex, tab, c, fl0)
    modes = [m for _, m, _ in tab.tickets]
    out = w.run.last_out
    check("loop_fk_placed", v == "placed" and modes == ["pre_po", "pre_po", "po_only"], (v, r, modes))
    check("loop_fk_selective_delete", len(ex.deletes) == 1 and ex.deletes[0]["keep"] == w.TCIN
          and ex.deletes[0]["only"] is None, ex.deletes)
    check("loop_fk_reads_before_po_only", len(tab.read_ts) == 1
          and "(foreign_entry)" in out and "po_only kept" in out, (tab.read_ts, out[-500:]))
    # the read shows a stray line again -> strict gate, never a blind place-order
    c = w.Clock()
    ex = w.bare(c)
    stray = {"ok": True, "status": 200, "has_items": True,
             "items": [{"id": "CI-1", "tcin": w.TCIN, "qty": 2}, {"id": "CI-9", "tcin": w.OTHER, "qty": 1}]}
    tab = w.ReadTab(ex, list(script[:2]) + [w.t_prepo(200, w.ORDER_BODY)], c, [stray])
    (v, r), _ = w.loop_run(ex, tab, c, fl0)
    modes = [m for _, m, _ in tab.tickets]
    check("loop_fk_stray_line_strict_gate", modes == ["pre_po", "pre_po", "pre_po"]
          and "pre_po instead of po_only" in w.run.last_out, (modes, w.run.last_out[-400:]))
    # control: a non-foreign entry keeps today's po_only with no read
    c = w.Clock()
    ex = w.bare(c)
    tab = w.ReadTab(ex, list(script[1:]), c, [w.R2_EXACT])
    (v, r), _ = w.loop_run(ex, tab, c, w.FL_PRE429)
    check("loop_control_no_read", v == "placed" and len(tab.read_ts) == 0
          and [m for _, m, _ in tab.tickets] == ["pre_po", "po_only"], (v, tab.read_ts, tab.tickets))
    # the delete fails -> foreign_stuck, no place-order fired
    c = w.Clock()
    ex = w.bare(c)
    ex.delete_result = (False, 0)
    tab = w.ReadTab(ex, list(script), c, [w.R2_EXACT])
    (v, r), _ = w.loop_run(ex, tab, c, fl0)
    check("loop_fk_delete_fails_no_order", v != "placed" and len(tab.tickets) == 1
          and "foreign_stuck" in w.run.last_out, (v, r, tab.tickets))


def test_foreign_entry_survives_strike_and_dirty():
    """Verifier 10-05 (W4): a held foreign-entry marker struck in a new window, or
    a foreign-entry cart left dirty, must keep the re-read-before-po_only rule."""
    seen = []

    async def _loop(tab, T, Q, fl0, st, entry="first", **kw):
        seen.append((entry, kw))
        return "done", {"success": False}
    for old, want in (({"tcin": TCIN, "foreign_entry": True}, {"foreign_entry": True}),
                      ({"tcin": TCIN}, {}), (None, {})):
        ex = bare_pe()
        ex._won_cart_ticket_loop = _loop
        seen.clear()
        quiet(asyncio.run, ex._held_line_strike_loop(None, TCIN, 2, time.time(), "test", old=old,
                                                     old_attr="_held_cart", cvv_put="none"))
        check(f"strike_passes_foreign_entry[{bool(old and old.get('foreign_entry'))}]",
              seen == [("strike", want)], seen)
    src = PE_SRC.replace(chr(13) + chr(10), chr(10))
    check("dirty_flag_carries_foreign_entry",
          "self._woncart_dirty['foreign_entry'] = True   # FX-1005-FOREIGN-KEEP" in src)
    check("loop_marks_foreign_entry_kwarg",
          "if foreign_entry or (isinstance(fl0, dict) and fl0.get('skip') == 'foreign_cart_item'):" in src)
    check("dirty_strike_site_passes_old_dirty",
          "old=d, old_attr='_woncart_dirty', cvv_put=d.get('cvv_put', 'unknown'))" in src)


# ──────────────────────────────── FX-1005-BOOTSKIP ───────────────────────────────
ACCS = [
    {"account_id": "primary", "enabled": True, "session_path": "target.json",
     "profile_dir": "nodriver-profile", "proxy_url": None},
    {"account_id": "business", "enabled": True, "session_path": "target-2.json",
     "profile_dir": "nodriver-profile-2", "proxy_url": None},
    {"account_id": "alt-1", "enabled": True, "session_path": "target-3.json",
     "profile_dir": "nodriver-profile-3", "proxy_url": None},
]


def _build(tmp, accounts, skips=None):
    cfg = Path(tmp) / "target_accounts.json"
    cfg.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    sk = Path(tmp) / "boot_skip_accounts.json"
    if skips is not None:
        sk.write_text(json.dumps({"accounts": skips}), encoding="utf-8")
    elif sk.exists():
        sk.unlink()
    old = wp.BOOT_SKIP_FILE
    wp.BOOT_SKIP_FILE = sk
    try:
        cs, out = quiet(wp._build_worker_configs_from_accounts, cfg)
    finally:
        wp.BOOT_SKIP_FILE = old
    return [(c.worker_id, c.account_id, c.session_path, c.profile_dir) for c in cs], out


def test_bootskip_pool():
    now = time.time()
    fresh = {"ts": now - 60, "reason": "boot login probe failed (timeout)", "count": 1}
    with tempfile.TemporaryDirectory() as tmp:
        with _Env():
            got, out = _build(tmp, ACCS, {"primary": fresh})
        check("bs_off_file_ignored", [g[1] for g in got] == ["primary", "business", "alt-1"]
              and "BOOT_SKIP" not in out, (got, out))
        with _Env(TARGET_BOOT_SKIP_FAILED_W1="1"):
            got, out = _build(tmp, ACCS)
            check("bs_on_no_file_full_fleet", [g[1] for g in got] == ["primary", "business", "alt-1"], got)
            got, out = _build(tmp, ACCS, {"primary": fresh})
            check("bs_on_primary_skipped", [g[1] for g in got] == ["business", "alt-1"], got)
            check("bs_on_business_first_own_paths_same_slot",
                  got[0] == (2, "business", "target-2.json", "nodriver-profile-2")
                  and got[1] == (3, "alt-1", "target-3.json", "nodriver-profile-3"), got)
            check("bs_on_loud_line", "[BOOT_SKIP] skipping primary this boot" in out, out)
            got, out = _build(tmp, ACCS, {"primary": fresh, "business": fresh})
            check("bs_on_two_skipped_keeps_slot", [g[1] for g in got] == ["alt-1"] and got[0][0] == 3, got)
            got, out = _build(tmp, ACCS, {a["account_id"]: fresh for a in ACCS})
            check("bs_on_all_skipped_ignored", [g[1] for g in got] == ["primary", "business", "alt-1"]
                  and "IGNORING it" in out, (got, out))
            old = {"ts": now - 13 * 3600, "reason": "x", "count": 1}
            got, out = _build(tmp, ACCS, {"primary": old})
            check("bs_on_expired_entry_ignored", [g[1] for g in got] == ["primary", "business", "alt-1"], got)
            with _Env(TARGET_BOOT_SKIP_FAILED_W1="1", TARGET_BOOT_SKIP_TTL_H="24"):
                got, out = _build(tmp, ACCS, {"primary": old})
            check("bs_on_ttl_knob", [g[1] for g in got] == ["business", "alt-1"], got)
            no_paths = [dict(a) for a in ACCS]
            no_paths[2].pop("session_path")
            got, out = _build(tmp, no_paths, {"primary": fresh})
            check("bs_on_positional_paths_ignored", [g[1] for g in got] == ["primary", "business", "alt-1"]
                  and "positional defaults" in out, (got, out))
            dis = [dict(a) for a in ACCS]
            dis[0]["enabled"] = False
            got, out = _build(tmp, dis, {"business": fresh})
            check("bs_on_with_disabled_account", [(g[0], g[1]) for g in got] == [(2, "alt-1")], got)
            got, out = _build(tmp, dis, {"business": fresh, "alt-1": fresh})
            check("bs_on_disabled_plus_all_skipped_ignored", [g[1] for g in got] == ["business", "alt-1"], got)
            (Path(tmp) / "target_accounts.json").write_text(json.dumps({"accounts": ACCS}), encoding="utf-8")
            (Path(tmp) / "boot_skip_accounts.json").write_text("{not json", encoding="utf-8")
            old_f = wp.BOOT_SKIP_FILE
            wp.BOOT_SKIP_FILE = Path(tmp) / "boot_skip_accounts.json"
            try:
                cs, out = quiet(wp._build_worker_configs_from_accounts, Path(tmp) / "target_accounts.json")
            finally:
                wp.BOOT_SKIP_FILE = old_f
            check("bs_on_corrupt_file_full_fleet", [c.account_id for c in cs] == ["primary", "business", "alt-1"])


def test_bootskip_record_clear():
    with tempfile.TemporaryDirectory() as tmp, _Env(TARGET_BOOT_SKIP_FAILED_W1="1"):
        p = Path(tmp) / "state" / "boot_skip_accounts.json"
        check("rec_first", wp.record_boot_skip("primary", "r1", path=p, now=1000.0) is True)
        wp.record_boot_skip("primary", "r2", path=p, now=1100.0)
        d = json.loads(p.read_text(encoding="utf-8"))["accounts"]
        check("rec_count_and_reason", d["primary"]["count"] == 2 and d["primary"]["reason"] == "r2"
              and d["primary"]["ts"] == 1100.0, d)
        wp.record_boot_skip("business", "r", path=p, now=1100.0)
        check("load_unexpired", set(wp.load_boot_skip(p, now=1200.0)) == {"primary", "business"})
        check("load_expired_pruned", wp.load_boot_skip(p, now=1100.0 + 12 * 3600 + 1) == {})
        check("clear_removes_one", wp.clear_boot_skip("primary", path=p) is True
              and set(json.loads(p.read_text(encoding="utf-8"))["accounts"]) == {"business"})
        check("clear_absent_false", wp.clear_boot_skip("primary", path=p) is False)
        with _Env(TARGET_BOOT_SKIP_TTL_H="999"):
            check("ttl_clamped_high", wp.boot_skip_ttl_s() == 48 * 3600)
        with _Env(TARGET_BOOT_SKIP_TTL_H="0"):
            check("ttl_clamped_low", wp.boot_skip_ttl_s() == 3600)
        with _Env(TARGET_BOOT_SKIP_TTL_H="junk"):
            check("ttl_default_on_junk", wp.boot_skip_ttl_s() == 12 * 3600)
        bad = Path(tmp) / "nodir" / "x" / "f.json"
        check("record_creates_dirs", wp.record_boot_skip("alt-1", "r", path=bad) is True and bad.exists())


def test_bootskip_relogin_and_app():
    src = (ROOT / "relogin_one.py").read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_clear_boot_skip")
    code = ast.get_source_segment(src, fn)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "state").mkdir()
        f = Path(tmp) / "state" / "boot_skip_accounts.json"
        f.write_text(json.dumps({"accounts": {"primary": {"ts": 1}, "business": {"ts": 1}}}), encoding="utf-8")
        ns = {"Path": Path, "os": os, "__file__": str(Path(tmp) / "relogin_one.py")}
        exec(code, ns)
        _, out = quiet(ns["_clear_boot_skip"], "primary")
        d = json.loads(f.read_text(encoding="utf-8"))["accounts"]
        check("relogin_clear_inline_works", set(d) == {"business"} and "cleared" in out, (d, out))
        _, out = quiet(ns["_clear_boot_skip"], "nobody")
        check("relogin_clear_absent_silent", out == "", out)
        f.write_text("{bad", encoding="utf-8")
        _, out = quiet(ns["_clear_boot_skip"], "business")
        check("relogin_clear_never_raises", "could not clear" in out, out)
    # where it is called: after a real login only
    i_val = src.find("already logged in ✅ (cookies refreshed")
    i_val_ret = src.find("return True", i_val)
    check("relogin_validate_first_does_not_clear", "_clear_boot_skip" not in src[src.rfind("if not force and", 0, i_val):i_val_ret])
    check("relogin_manual_clears_only_real_login", "if force or not _was_in:\n                    _clear_boot_skip(acc_id)" in src)
    i_auto = src.find("'NOT logged in ❌ (after 3 attempts)'")
    check("relogin_scripted_success_clears", "_clear_boot_skip(acc_id)" in src[i_auto:i_auto + 600])
    app = (ROOT / "app.py").read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    i87 = app.find("[SYSTEM] Exiting with code 87")
    i_rec = app.find("boot_skip_note_probe_failure(", i87)
    i_exit = app.find("os._exit(87)", i87)
    check("app_records_before_exit87", 0 < i87 < i_rec < i_exit, (i87, i_rec, i_exit))
    check("app_record_gated", "if boot_skip_on() and _wp is not None:" in app
          and "pool_size=_wp.size, definitive=(login_check_error is None))" in app)
    check("app_record_guarded", "except Exception as _bs_err:" in app)


def test_bootskip_cascade_and_labels():
    """The verifier's cascade (10-05): a probe that fails for EVERY account must not strip the
    fleet one account per relaunch; and a skip must not renumber the others (AC-1 latch keys)."""
    with tempfile.TemporaryDirectory() as tmp, _Env(TARGET_BOOT_SKIP_FAILED_W1="1"):
        cfg = Path(tmp) / "target_accounts.json"
        cfg.write_text(json.dumps({"accounts": ACCS}), encoding="utf-8")
        sk = Path(tmp) / "boot_skip_accounts.json"
        old = wp.BOOT_SKIP_FILE
        wp.BOOT_SKIP_FILE = sk
        try:
            fleets, acts = [], []
            for _ in range(6):                       # every Worker-1 probe fails (a global cause)
                cs, _ = quiet(wp._build_worker_configs_from_accounts, cfg)
                fleets.append([c.account_id for c in cs])
                act, _ = quiet(wp.boot_skip_note_probe_failure, cs[0].account_id, "probe", len(cs))
                acts.append(act)
            check("cascade_never_below_two", min(len(f) for f in fleets) >= 2, fleets)
            check("cascade_alternates_record_clear", acts == ["recorded", "cleared"] * 3, acts)
            check("cascade_full_fleet_after_clear", fleets[2] == ["primary", "business", "alt-1"], fleets)
            sk.unlink()
            cs, _ = quiet(wp._build_worker_configs_from_accounts, cfg)
            act, _ = quiet(wp.boot_skip_note_probe_failure, cs[0].account_id, "guest", len(cs))
            cs2, _ = quiet(wp._build_worker_configs_from_accounts, cfg)
            check("single_bad_account_one_cycle", act == "recorded"
                  and [c.account_id for c in cs2] == ["business", "alt-1"], [c.account_id for c in cs2])
            check("labels_unchanged_by_skip", [f"W{c.worker_id}/{c.account_id}" for c in cs2]
                  == ["W2/business", "W3/alt-1"], cs2)
            act1, _ = quiet(wp.boot_skip_note_probe_failure, "alt-1", "x", 1)
            check("second_failure_clears_even_at_one_worker", act1 == "cleared"
                  and wp.load_boot_skip(sk) == {}, act1)
            act1b, _ = quiet(wp.boot_skip_note_probe_failure, "alt-1", "x", 1)
            check("never_skip_last_account", act1b == "none" and wp.load_boot_skip(sk) == {})
            # non-definitive (init failure / 90 s timeout / exception): nothing recorded or cleared
            quiet(wp.boot_skip_note_probe_failure, "primary", "guest", 3)
            nd, _ = quiet(wp.boot_skip_note_probe_failure, "business", "timeout", 2, definitive=False)
            check("non_definitive_leaves_list", nd == "none" and set(wp.load_boot_skip(sk)) == {"primary"})
            sk.unlink()
            nd2, _ = quiet(wp.boot_skip_note_probe_failure, "primary", "init", 3, definitive=False)
            check("non_definitive_never_records", nd2 == "none" and wp.load_boot_skip(sk) == {})
            # verifier 10-05: a 2-account fleet (10-04's) must recover, not stay at 1 racer
            two = [a for a in ACCS if a["account_id"] != "primary"]
            cfg.write_text(json.dumps({"accounts": two}), encoding="utf-8")
            fleets2, acts2 = [], []
            for _ in range(4):                       # every Worker-1 probe fails
                cs, _ = quiet(wp._build_worker_configs_from_accounts, cfg)
                fleets2.append([c.account_id for c in cs])
                a, _ = quiet(wp.boot_skip_note_probe_failure, cs[0].account_id, "probe", len(cs))
                acts2.append(a)
            check("two_fleet_recovers_after_second_failure",
                  acts2 == ["recorded", "cleared"] * 2 and fleets2[2] == ["business", "alt-1"], (fleets2, acts2))
            sk.unlink()
            cs, _ = quiet(wp._build_worker_configs_from_accounts, cfg)
            quiet(wp.boot_skip_note_probe_failure, cs[0].account_id, "guest", len(cs))
            cs2, _ = quiet(wp._build_worker_configs_from_accounts, cfg)
            check("two_fleet_single_bad_account_leaves_one", [c.account_id for c in cs2] == ["alt-1"], cs2)
        finally:
            wp.BOOT_SKIP_FILE = old
    import src.purchasing.bulletproof_purchase_manager as bpm
    with tempfile.TemporaryDirectory() as tmp:
        lp = os.path.join(tmp, "latch.json")
        now = time.time()
        ok = bpm._ac_latch_save(lp, {("W2/business", TCIN): now}, now, 1800.0)
        got = bpm._ac_latch_load(lp, now + 1, 1800.0)
        check("ac1_latch_key_matches_kept_slot_label", ok and ("W2/business", TCIN) in got, got)


def test_readiness_shows_boot_skip():
    import check_session_readiness as csr
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "boot_skip_accounts.json"
        check("readiness_no_file_silent", csr.boot_skip_report(path=f) == [])
        now = time.time()
        f.write_text(json.dumps({"accounts": {"alt-1": {"ts": now - 3600, "reason": "boot login probe failed (x)",
                                                        "count": 2},
                                              "business": {"ts": now - 13 * 3600, "reason": "old"}}}),
                     encoding="utf-8")
        with _Env():
            lines = csr.boot_skip_report(path=f, now=now)
        check("readiness_lists_unexpired_only", len(lines) == 1 and "alt-1 will be LEFT OUT" in lines[0]
              and "11.0 h" in lines[0] and "hand_login_alt1_force.bat" in lines[0], lines)
        f.write_text("{bad", encoding="utf-8")
        check("readiness_bad_file_reported", csr.boot_skip_report(path=f)[0].startswith("BOOT SKIP: could not"))


# ────────────────────────────────── INS-ATC-NET ──────────────────────────────────
def test_atc_net_pure():
    f = pe.atc_net_send_wall_ms
    check("net_send_ms_math", f(1000.0, 50.0, 50.010, 5.0) == 1000015, f(1000.0, 50.0, 50.010, 5.0))
    check("net_send_ms_missing", f(None, 50.0, 50.0, 1.0) is None and f(1.0, 1.0, None, 1.0) is None)
    check("net_send_ms_negative_start", f(1000.0, 50.0, 50.0, -1) is None)
    check("net_send_ms_junk", f("x", 1, 1, 1) is None)
    line = pe.atc_net_line("primary", {"method": "POST", "status": 429, "conn": 37.0, "reused": True,
                                       "ip": "151.101.2.187", "port": 443, "proto": "h2",
                                       "send_ms": 1759650000123, "ttfb_ms": 141.6, "rid": "1234.56"})
    check("net_line_format", line == ("[ATC_NET] ident=primary m=POST st=429 conn=37 reused=1 "
                                      "ip=151.101.2.187:443 proto=h2 send_ms=1759650000123 ttfb_ms=142 "
                                      "connect_ms=- ssl_ms=- rid=1234.56"), line)
    check("net_line_bad_input", pe.atc_net_line("x", None).startswith("[ATC_NET] ident=x m=-"))
    with _Env():
        check("net_flag_default_off", pe.atc_net_meta_on() is False)
    with _Env(TARGET_ATC_NET_META="1"):
        check("net_flag_on", pe.atc_net_meta_on() is True)
    check("net_install_gated_main_only",
          "if label == 'main' and atc_net_meta_on():\r\n" in PE_SRC
          or "if label == 'main' and atc_net_meta_on():\n" in PE_SRC)


class _NetTab:
    def __init__(self):
        self.sent = []
        self.handlers = {}

    async def send(self, cmd):
        self.sent.append(cmd)

    def add_handler(self, ev, fn):
        self.handlers.setdefault(ev, []).append(fn)


def test_atc_net_install():
    from zendriver import cdp
    ex = object.__new__(PurchaseExecutor)
    ex.session_manager = _SM()
    tab = _NetTab()
    async def _install_and_settle(t):
        await ex._install_atc_net_meta(t)
        await asyncio.sleep(0)                       # let the fire-and-forget enable run
        await asyncio.sleep(0)
    _, out = quiet(asyncio.run, _install_and_settle(tab))
    check("net_install_registers_two", len(tab.handlers.get(cdp.network.RequestWillBeSent, [])) == 1
          and len(tab.handlers.get(cdp.network.ResponseReceived, [])) == 1, tab.handlers)
    check("net_install_enables_network", len(tab.sent) == 1 and "installed" in out, (tab.sent, out))
    quiet(asyncio.run, _install_and_settle(tab))
    check("net_install_once_per_tab", len(tab.handlers[cdp.network.RequestWillBeSent]) == 1 and len(tab.sent) == 1)
    will = tab.handlers[cdp.network.RequestWillBeSent][0]
    resp = tab.handlers[cdp.network.ResponseReceived][0]
    req = SimpleNamespace(url="https://carts.target.com/web_checkouts/v1/cart_items?x=1", method="POST")
    timing = SimpleNamespace(request_time=50.010, send_start=5.0, send_end=6.0, receive_headers_end=146.0,
                             connect_start=-1, connect_end=-1, ssl_start=-1, ssl_end=-1)
    r = SimpleNamespace(url=req.url, status=429, connection_id=37.0, connection_reused=True,
                        remote_ip_address="151.101.2.187", remote_port=443, protocol="h2", timing=timing)

    async def _go():
        await will(SimpleNamespace(request_id="R1", request=req, wall_time=1000.0, timestamp=50.0))
        await resp(SimpleNamespace(request_id="R1", response=r))
        await will(SimpleNamespace(request_id="R2", request=SimpleNamespace(url="https://www.target.com/x",
                                                                             method="GET"),
                                   wall_time=1.0, timestamp=1.0))
        await resp(SimpleNamespace(request_id="R2", response=SimpleNamespace(url="https://www.target.com/x")))
        await resp(SimpleNamespace(request_id="R3", response=None))          # malformed: swallowed
    _, out = quiet(asyncio.run, _go())
    lines = [l for l in out.splitlines() if l.startswith("[ATC_NET]")]
    check("net_one_line_for_cart_items_only", len(lines) == 1, out)
    check("net_line_values", lines and "m=POST st=429 conn=37 reused=1 ip=151.101.2.187:443 proto=h2 "
          "send_ms=1000015 ttfb_ms=140 connect_ms=- ssl_ms=- rid=R1" in lines[0], lines)
    bad = _NetTab()

    async def _boom(cmd):
        raise RuntimeError("no network")
    bad.send = _boom
    ex2 = object.__new__(PurchaseExecutor)
    ex2.session_manager = _SM()
    async def _install_bad():
        await ex2._install_atc_net_meta(bad)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    _, out = quiet(asyncio.run, _install_bad())
    check("net_enable_failure_logged_handlers_kept", "network.enable failed" in out
          and len(bad.handlers) == 2, (out, bad.handlers))

    class _HangTab(_NetTab):
        async def send(self, cmd):
            await asyncio.Event().wait()             # a CDP call that never answers
    hang = _HangTab()
    ex3 = object.__new__(PurchaseExecutor)
    ex3.session_manager = _SM()

    async def _timed():
        t0 = time.perf_counter()
        await asyncio.wait_for(ex3._install_atc_net_meta(hang), timeout=1.0)
        return time.perf_counter() - t0
    dt, _ = quiet(asyncio.run, _timed())
    check("net_install_never_awaits_cdp", dt < 0.2 and len(hang.handlers) == 2, (dt, hang.handlers))


def test_atc_net_no_enable_on_purchase_path():
    """Verifier 10-05: zendriver's Connection._register_handlers (run inside the NEXT
    send) awaits <domain>.enable() for a handler whose domain is not listed in
    tab.enabled_domains. The install lists Network first, so the real function sends
    nothing even when the tab had no Network handler before."""
    import collections
    from zendriver import cdp
    from zendriver.core.connection import Connection

    def mk(pre_network):
        c = object.__new__(Connection)
        c.handlers = collections.defaultdict(list)
        c.enabled_domains = [cdp.fetch]
        c.handlers[cdp.fetch.RequestPaused].append(lambda e: None)
        if pre_network:
            c.handlers[cdp.network.ResponseReceived].append(lambda e: None)
            c.enabled_domains.append(cdp.network)
        c.calls = []

        async def fake_send(obj, _is_update=False):
            m = next(obj)
            c.calls.append((m.get("method"), _is_update))
        c.send = fake_send
        return c

    for pre in (False, True):
        c = mk(pre)
        ex = object.__new__(PurchaseExecutor)
        ex.session_manager = _SM()

        async def _go():
            await ex._install_atc_net_meta(c)            # registers handlers, schedules the enable
            before = list(c.calls)
            await Connection._register_handlers(c)       # what the next purchase-path send runs
            after = list(c.calls)
            await asyncio.sleep(0)
            await asyncio.sleep(0)                       # the fire-and-forget enable runs
            return before, after, list(c.calls)
        (before, after, final), _ = quiet(asyncio.run, _go())
        check(f"net_register_handlers_sends_nothing[pre_network={pre}]", after == before, (before, after))
        check(f"net_enable_still_sent_in_background[pre_network={pre}]",
              ("Network.enable", False) in final and cdp.network in c.enabled_domains, final)


# ───────────────────────────────── wrapper / arming ──────────────────────────────
def report_arming():
    """Informational only: which of the new flags the wrapper arms (arming is the
    operator's step; the tests above pin behaviour with each flag on and off)."""
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("ascii", "replace").splitlines()
    armed = [l.strip() for l in bat if l.strip().lower().startswith("set ")
             and any(f in l for f in NEW_FLAGS)]
    print(f"  info armed in the wrapper: {armed or 'none'}")


if __name__ == "__main__":
    test_po2xx_helper()
    test_po2xx_apply_result()
    test_po2xx_call_sites()
    test_foreign_keep_eligible()
    test_foreign_keep_ledger_and_loop()
    test_foreign_keep_through_the_loop()
    test_foreign_entry_survives_strike_and_dirty()
    test_bootskip_pool()
    test_bootskip_record_clear()
    test_bootskip_relogin_and_app()
    test_bootskip_cascade_and_labels()
    test_readiness_shows_boot_skip()
    test_atc_net_pure()
    test_atc_net_install()
    test_atc_net_no_enable_on_purchase_path()
    report_arming()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
