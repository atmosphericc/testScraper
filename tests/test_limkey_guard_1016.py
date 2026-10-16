#!/usr/bin/env python3
"""Offline test: LIMKEY-GUARD-1016 (2026-10-16), the money-safety guard around S2-LIMKEY-PROBE.

WHY: the off-window limiter-key probe POSTs an add-to-cart for a HOT TCIN T on a real account.
If that POST ever LANDS a line of T, the armed purchase path would treat it as the bot's own line
(silent-hold checkout, FX-1001-A strike, boot cart audit adoption, dirty / TTL strike). The guard:
  1. any answer that is not a PROVEN non-landing -> durable marker FIRST, stop probing, delete T's
     line, confirm with a cart read, clear the marker only when the read proves the line gone;
  2. while a marker names (account, T): _check_cart_hold is False, the held-line strike is off,
     the boot audit DELETES (never adopts) the line, the purchase entry deletes it before any shot;
  3. a marker at probe time = no probe for the whole process;
  4. the purchase / won-cart / held / ride guards are re-read AFTER the probe evaluate returns.
Fix round (evaluator 10-16):
  5. an answer that never came back (evaluate error / timeout / unparseable) may still LAND its POST
     (wait_for cancels only the Python await): the marker is not clearable until a cart read >= the
     late-add window after its ts, at all three clear sites;
  6. the stop is latched at START (before the boot audit can clear the marker), not at the first tick;
  7. an 'in_flight' marker entry is written BEFORE the evaluate; only the probe clears it;
  8. a marker the disk would not take stays in an in-process fallback + a loud ALERT.
Fix round 4 (evaluator):
  9. a gateway 5xx / 408 answer is late too (the cart service may still land the add): marker kept
     until the late-add window has passed;
 10. the whole-cart legacy checkout runs its sink guard while a marker names the account, even with
     the held-line strike killed (TARGET_HELD_LINE_FLIP_STRIKE=0).
With the probe unset and no marker, behaviour is byte-identical (differential against the
pre-change executor loaded from git).

Drives the REAL executor code with stub tabs that simulate a cart. No browser, no network, no bot.
The marker is redirected to a temp dir; the real state/ is never touched.

    python tests/test_limkey_guard_1016.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["TARGET_API_CAPTURE_CHECKOUT_STEPS"] = "false"
os.environ["TARGET_API_CAPTURE_PLACE_ORDER"] = "false"
os.environ.setdefault("TARGET_API_PLACE_ORDER", "true")

import test_limkey_probe_1015 as L  # noqa: E402  (import only; redirects the marker to a temp dir)
import test_won_cart_direct_smoke as S  # noqa: E402  (import only; its main() does not run)
import src.session.purchase_executor as pe  # noqa: E402

PRE_COMMIT = "ee1d653baf89975fb7d1d40bcaaee67a2873ab09"     # HEAD before LIMKEY-GUARD-1016 (probe, no guard)
PASSED, FAILED = [], []
FLAG = "TARGET_WARMUP_PROBE_TCINS"
A, B = "1010892076", "1011960739"
OTHER = "1010892067"
BOGUS = "81926151"
ENV_ON = {FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}
STRIKE_ENV = {"TARGET_HELD_LINE_FLIP_STRIKE": "1", "TARGET_FASTLANE_QTY_GUARD": "1",
              "TARGET_HELD_CART_REENTRY": "1"}
BOOT_ENV = {"TARGET_HELD_CART_REENTRY": "1", "TARGET_BOOT_CART_AUDIT": "1"}
ERR_A2C = "ERR_A2C_TCIN_RATE_LIMITED"


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def arun(coro):
    """Run a coroutine, capture its stdout. -> (result, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = asyncio.run(coro)
    return r, buf.getvalue()


env = L.env


def marker_entries():
    return pe._lk_marker_load()[0]


def marker_file_exists():
    return os.path.exists(pe._LIMKEY_MARKER_PATH)


def fresh():
    pe.limkey_probe_reset()          # the 1015 test's wrapper also removes the temp marker file


# ───────────────────────────── a tab that simulates the cart ─────────────────────────────
class CartTab:
    """Heartbeat (bogus TCIN) -> hb_status. Probe POST -> the configured answer (and, with
    lands=True, a line of the probe TCIN appears in the simulated cart). Cart read / DELETE /
    silent-hold read run against the simulated cart. `log` records (kind, marker_existed_then)."""

    def __init__(self, ex, probe=None, lands=False, probe_raises=None, probe_raw=None, hb_status=424,
                 delete_status=200, read_mode="ok", lines=None, on_probe=None, dead_after_probe=False,
                 on_probe_async=None):
        self.url = "https://www.target.com/cart"
        self.ex = ex
        self.probe = probe or {"status": 429, "key": ERR_A2C, "hop": "-", "n": 0}
        self.lands = lands
        self.probe_raises = probe_raises
        self.probe_raw = probe_raw
        self.hb_status = hb_status
        self.delete_status = delete_status
        self.read_mode = read_mode                  # ok | fail | no_array
        self.lines = list(lines or [])              # [{'id','tcin','qty'}]
        self.on_probe = on_probe
        self.on_probe_async = on_probe_async
        self.marker_snap = None
        self.dead_after_probe = dead_after_probe
        self.dead = False
        self.log = []
        self.scripts = []
        self._seq = 0

    async def get(self, url):
        return None

    def add_line(self, tcin, qty=1):
        self._seq += 1
        self.lines.append({"id": f"CI{self._seq:04d}", "tcin": str(tcin), "qty": qty})

    def tcins(self):
        return [l["tcin"] for l in self.lines]

    async def evaluate(self, script, await_promise=False):
        self.scripts.append(script)
        if "readyState" in script:
            return "complete"
        mk = marker_file_exists()
        if f"tcin: '{BOGUS}'" in script:
            self.ex._cached_cart_headers_ts = time.time()
            return self.hb_status
        if self.dead:
            raise RuntimeError("tab dead")
        if "JSON.stringify({s: resp.status" in script:
            self.log.append(("probe", mk))
            self.marker_snap = marker_entries()
            if self.on_probe:
                self.on_probe(self)
            if self.on_probe_async:
                await self.on_probe_async(self)
            if self.dead_after_probe:
                self.dead = True
            if self.probe_raises is not None:
                raise self.probe_raises
            p = self.probe
            w = getattr(self.ex, "_limkey_wait", None)
            if w is not None:
                w.append((p["status"], p["key"], p["hop"]))
            if self.lands:
                self.add_line(re.search(r"tcin: '(\d+)'", script).group(1))
            if self.probe_raw is not None:
                return self.probe_raw
            if p["status"] == 0:
                return 0
            return json.dumps({"s": p["status"], "k": "", "n": p["n"], "b": ""})
        if script == pe._CART_ITEMS_READ_JS:
            self.log.append(("read", mk))
            if self.read_mode == "fail":
                return {"ok": False, "status": 429, "items": []}
            out = {"ok": True, "status": 200, "items": [dict(l) for l in self.lines]}
            if self.read_mode != "no_array":
                out["has_items"] = True
            return out
        if "method: 'DELETE'" in script:
            self.log.append(("delete", mk))
            cid = re.search(r"cart_items/([A-Za-z0-9_-]+)'", script).group(1)
            if self.delete_status in (200, 204):
                self.lines = [l for l in self.lines if l["id"] != cid]
            return self.delete_status
        if "field_groups=CART,CART_ITEMS" in script:
            self.log.append(("hold", mk))
            return {"ok": True, "tcins": self.tcins()}
        return None

    def kinds(self):
        return [k for k, _ in self.log]


def make(acct="primary", **tab_kw):
    sm = L.FakeSM(acct)
    ex = pe.PurchaseExecutor(session_manager=sm)
    tab = CartTab(ex, **tab_kw)
    ex._warmup_browser_ref = sm.browser
    ex._warmup_tabs = [tab]
    ex._warmup_tab_cart_ts = {0: time.time() - 5.2}
    ex._stock_live_fn = L.stock_all("oos_never", time.time())
    L.seed_seen([A], now=time.time())
    return ex, tab, sm


def tick(ex, now=None):
    now = time.time() if now is None else now
    return arun(ex._limkey_probe_tick(0, now=now))[1]


def probes_sent(tab):
    return sum(1 for k in tab.kinds() if k == "probe")


# ───────────────────────────── pure helpers ─────────────────────────────
def test_classifier():
    P = pe.limkey_proven_non_landing
    proven = [(429, ERR_A2C, 0), (401, "-", 231), (401, "", 5), (401, "?", 5), (424, "X", 90), (400, "-", 10),
              (403, "-", 10), (404, "-", 10), (0, "-", 0)]
    for s, k, n in proven:
        check(f"classify_proven_{s}_{k}_{n}", P({"status": s, "n": n}, k) is True)
    unproven = [(200, "-", 0), (201, "-", 900), (204, "-", 0), (500, "-", 0), (503, "-", 0), (302, "-", 0),
                (429, ERR_A2C, 90), (429, ERR_A2C, -1), (429, "OTHER_KEY", 0), (429, "-", 0), (429, "?", 0),
                (429, "", 0), (401, "_ERR_AUTH_DENIED", 231), (-1, "-", -1), (409, "-", 0), (422, "-", 0)]
    for s, k, n in unproven:
        check(f"classify_unproven_{s}_{k}_{n}", P({"status": s, "n": n}, k) is False)
    check("classify_error_is_unproven", P(None, "-") is False and P({"status": "z"}, "-") is False
          and P({}, "-") is False)


def test_marker_io():
    fresh()
    check("marker_absent_at_start", not marker_file_exists() and not pe.limkey_marker_any()
          and not pe.limkey_marker_has("primary", A))
    check("marker_write_ok", pe.limkey_marker_write("Primary", A, "status_201") is True)
    e = marker_entries()
    check("marker_entry_fields", len(e) == 1 and e[0]["acct"] == "primary" and e[0]["tcin"] == A
          and e[0]["reason"] == "status_201" and abs(float(e[0]["ts"]) - time.time()) < 5, str(e))
    raw = json.loads(Path(pe._LIMKEY_MARKER_PATH).read_text(encoding="utf-8"))
    check("marker_file_is_a_json_list_with_the_four_keys", isinstance(raw, list)
          and set(raw[0]) == {"acct", "tcin", "ts", "reason"}, str(raw))
    check("marker_has_is_account_and_tcin_exact", pe.limkey_marker_has("primary", A) and pe.limkey_marker_has("PRIMARY", A)
          and not pe.limkey_marker_has("alt-1", A) and not pe.limkey_marker_has("primary", B)
          and not pe.limkey_marker_has("primary", None) and not pe.limkey_marker_has(None, A))
    pe.limkey_marker_write("alt-1", B, "x")
    pe.limkey_marker_write("primary", A, "refreshed")
    e = marker_entries()
    check("marker_two_entries_refresh_not_duplicate", len(e) == 2
          and sorted((x["acct"], x["tcin"]) for x in e) == [("alt-1", B), ("primary", A)]
          and [x for x in e if x["acct"] == "primary"][0]["reason"] == "refreshed", str(e))
    check("marker_clear_one_keeps_the_other", pe.limkey_marker_clear("primary", A) is True
          and not pe.limkey_marker_has("primary", A) and pe.limkey_marker_has("alt-1", B) and marker_file_exists())
    check("marker_clear_last_removes_the_file", pe.limkey_marker_clear("alt-1", B) is True and not marker_file_exists())
    check("marker_clear_absent_is_true", pe.limkey_marker_clear("alt-1", B) is True)
    check("marker_no_tmp_left_behind", not os.path.exists(pe._LIMKEY_MARKER_PATH + ".tmp"))
    # unreadable: blank / corrupt / wrong shape -> stops the probe, matches no consumer, never cleared by guess
    for label, body in (("blank", ""), ("corrupt", "{oops"), ("empty_list", "[]"), ("wrong_shape", '["x"]'),
                        ("no_tcin", '[{"acct":"primary"}]')):
        Path(pe._LIMKEY_MARKER_PATH).write_text(body, encoding="utf-8")
        check(f"marker_unreadable_{label}_stops_the_probe_only",
              pe.limkey_marker_any() is True and pe.limkey_marker_has("primary", A) is False
              and pe.limkey_marker_clear("primary", A) is False)
    fresh()
    # never raises: a path under a regular file cannot be written
    real = pe._LIMKEY_MARKER_PATH
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "afile"
        f.write_text("x")
        pe._LIMKEY_MARKER_PATH = str(f / "sub" / "m.json")
        try:
            check("marker_write_failure_returns_false", pe.limkey_marker_write("primary", A, "x") is False)
            # fix 4: the failed write is kept in the in-process fallback, which limkey_marker_has consults
            check("marker_has_on_bad_path_true_via_the_in_process_fallback",
                  pe.limkey_marker_has("primary", A) is True and pe.limkey_marker_any() is True
                  and pe.limkey_marker_entry("primary", A)["reason"] == "x")
            check("marker_fallback_is_account_and_tcin_exact", not pe.limkey_marker_has("alt-1", A)
                  and not pe.limkey_marker_has("primary", B))
            check("marker_fallback_cleared_by_a_clearable_clear", pe.limkey_marker_clear("primary", A) is True
                  and pe.limkey_marker_has("primary", A) is False and pe.limkey_marker_any() is False)
        finally:
            pe._LIMKEY_MARKER_PATH = real
    fresh()
    # fix 7 / fix 1: entry states
    pe.limkey_marker_write("primary", A, "in_flight", state="in_flight")
    raw = json.loads(Path(pe._LIMKEY_MARKER_PATH).read_text(encoding="utf-8"))
    check("marker_in_flight_entry_carries_state_and_pid", raw[0].get("state") == "in_flight"
          and raw[0].get("pid") == os.getpid() and "late" not in raw[0], str(raw))
    check("marker_in_flight_has_is_true_for_the_consumers", pe.limkey_marker_has("primary", A))
    check("marker_in_flight_not_cleared_by_a_non_owner", pe.limkey_marker_clear("primary", A) is False
          and pe.limkey_marker_block("primary", A) == "in_flight" and pe.limkey_marker_has("primary", A))
    check("marker_in_flight_cleared_by_the_owner_probe", pe.limkey_marker_clear("primary", A, owner=True) is True
          and not marker_file_exists())
    # an in_flight entry of ANOTHER pid is an orphan: it follows the late rule, not the in_flight block
    Path(pe._LIMKEY_MARKER_PATH).write_text(json.dumps([{"acct": "primary", "tcin": A, "ts": time.time(), "reason": "r",
                                                          "state": "in_flight", "pid": os.getpid() + 1}]), encoding="utf-8")
    check("marker_orphan_in_flight_inside_the_window_is_blocked_as_late",
          pe.limkey_marker_block("primary", A).startswith("late_add_window_") and pe.limkey_marker_clear("primary", A) is False)
    Path(pe._LIMKEY_MARKER_PATH).write_text(json.dumps([{"acct": "primary", "tcin": A,
                                                          "ts": time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 5, "reason": "r",
                                                          "state": "in_flight", "pid": os.getpid() + 1}]), encoding="utf-8")
    check("marker_orphan_in_flight_past_the_window_clears", pe.limkey_marker_block("primary", A) == ""
          and pe.limkey_marker_clear("primary", A) is True and not marker_file_exists())
    # late entries: held for the window, then clearable
    pe.limkey_marker_write("primary", A, "evaluate_TimeoutError", late=True)
    raw = json.loads(Path(pe._LIMKEY_MARKER_PATH).read_text(encoding="utf-8"))
    check("marker_late_entry_is_flagged_in_the_file", raw[0].get("late") is True and "state" not in raw[0], str(raw))
    check("marker_late_entry_blocked_inside_the_window", pe.limkey_marker_clear("primary", A) is False
          and pe.limkey_marker_block("primary", A).startswith("late_add_window_") and pe.limkey_marker_has("primary", A))
    ents = marker_entries()
    ents[0]["ts"] = time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 1
    pe._lk_marker_store(ents)
    check("marker_late_entry_clears_after_the_window", pe.limkey_marker_clear("primary", A) is True
          and not marker_file_exists())
    # a late entry with no readable ts stays (fail closed)
    Path(pe._LIMKEY_MARKER_PATH).write_text(json.dumps([{"acct": "primary", "tcin": A, "ts": "junk", "reason": "r",
                                                          "late": True}]), encoding="utf-8")
    check("marker_late_entry_with_bad_ts_never_clears", pe.limkey_marker_clear("primary", A) is False)
    fresh()


# ───────────────────────────── step 1: cleanup ─────────────────────────────
def test_cleanup_after_a_landed_line():
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True)
        out = tick(ex)
    check("landed_line_probe_fired_once", probes_sent(tab) == 1)
    check("landed_marker_in_place_before_the_probe_and_before_any_cart_call", tab.log[0] == ("probe", True)
          and all(mk for k, mk in tab.log[1:]), str(tab.log))
    check("landed_in_flight_entry_was_down_when_the_probe_went_out", tab.marker_snap and len(tab.marker_snap) == 1
          and tab.marker_snap[0]["state"] == "in_flight" and tab.marker_snap[0]["pid"] == os.getpid()
          and tab.marker_snap[0]["acct"] == "primary" and tab.marker_snap[0]["tcin"] == A, str(tab.marker_snap))
    check("landed_line_deleted", A not in tab.tcins() and "delete" in tab.kinds(), str(tab.lines))
    check("landed_marker_cleared_after_the_read_proved_it_gone", not marker_file_exists()
          and tab.kinds()[-1] == "read", str(tab.log))
    check("landed_stopped_fleet_wide", pe._LIMKEY_STATE["stopped"] is True)
    order = [out.find(s) for s in ("event=STOP reason=probe_2xx", "event=cleanup step=marker ok=True",
                                   "event=cleanup step=stopped", "event=cleanup step=delete ok=True",
                                   "event=cleanup step=marker_cleared ok=True")]
    check("landed_every_step_logged_in_order", all(i >= 0 for i in order) and order == sorted(order), out)
    check("landed_cleanup_lines_are_limkey_probe_events",
          all(ln.startswith("[LIMKEY_PROBE]") for ln in out.splitlines() if "cleanup" in ln))

    # other lines in the cart are never deleted (only_tcin)
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True,
                           lines=[{"id": "KEEP1", "tcin": OTHER, "qty": 1}])
        tick(ex)
    check("landed_foreign_line_survives_the_cleanup", tab.tcins() == [OTHER], str(tab.lines))

    # the 2xx wait: second probe never fires fleet-wide
    with env(**ENV_ON):
        tick(ex, time.time() + 500)
    check("landed_no_second_probe", probes_sent(tab) == 1)


LATE_STATUS_LABELS = ("500", "503", "502", "504", "408")
UNPROVEN = [
    ("200", {"status": 200, "key": "-", "hop": "-", "n": 10}),
    ("201", {"status": 201, "key": "-", "hop": "-", "n": 900}),
    ("204", {"status": 204, "key": "-", "hop": "-", "n": 0}),
    ("500", {"status": 500, "key": "-", "hop": "-", "n": 0}),
    ("503", {"status": 503, "key": "-", "hop": "-", "n": 0}),
    ("502", {"status": 502, "key": "-", "hop": "-", "n": 0}),
    ("504", {"status": 504, "key": "-", "hop": "-", "n": 0}),
    ("408", {"status": 408, "key": "-", "hop": "-", "n": 0}),
    ("429_with_body", {"status": 429, "key": ERR_A2C, "hop": "-", "n": 90}),
    ("429_other_key", {"status": 429, "key": "ERR_OTHER", "hop": "-", "n": 0}),
    ("429_no_key", {"status": 429, "key": "-", "hop": "-", "n": 0}),
    ("401_keyed", {"status": 401, "key": "_ERR_AUTH_DENIED", "hop": "1", "n": 231}),
    ("302", {"status": 302, "key": "-", "hop": "-", "n": 0}),
]
PROVEN = [
    ("429_empty_err_a2c", {"status": 429, "key": ERR_A2C, "hop": "-", "n": 0}),
    ("401_keyless", {"status": 401, "key": "-", "hop": "1", "n": 231}),
    ("424", {"status": 424, "key": "ITEM_NOT_READY_FOR_LAUNCH", "hop": "-", "n": 90}),
    ("400", {"status": 400, "key": "-", "hop": "-", "n": 40}),
    ("403", {"status": 403, "key": "-", "hop": "-", "n": 40}),
    ("404", {"status": 404, "key": "-", "hop": "-", "n": 40}),
    ("status_0", {"status": 0, "key": "-", "hop": "-", "n": 0}),
]


def test_cleanup_matrix():
    for label, probe in UNPROVEN:
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe=probe, lands=True)
            out = tick(ex)
        # fix round 4: a gateway 5xx / 408 may still be working on the add -> late=True (the marker is
        # kept until the late-add window has passed), exactly like an evaluate timeout
        _late = label in LATE_STATUS_LABELS
        e = marker_entries()
        check(f"unproven_{label}_cleans_up", probes_sent(tab) == 1 and "event=cleanup step=marker ok=True" in out
              and A not in tab.tcins() and pe._LIMKEY_STATE["stopped"] is True
              and ((len(e) == 1 and e[0]["late"] is True and e[0]["state"] == ""
                    and "step=marker_kept reason=late_add_window_" in out and "step=marker_cleared" not in out)
                   if _late else (not marker_file_exists() and "late=True" not in out)),
              f"{tab.log} {tab.lines} {e} {out[-300:]}")
        check(f"unproven_{label}_marker_first", tab.log and tab.log[0] == ("probe", True)
              and tab.marker_snap and tab.marker_snap[0]["state"] == "in_flight"
              and all(mk for k, mk in tab.log[1:]), str(tab.log))
    # an answer we could not read at all, an evaluate error, a timeout: the POST may STILL land after the
    # cleanup's cart read (wait_for cancels only the Python await), so the line is deleted but the marker
    # (late=True) is kept until the late-add window has passed
    cases = [("unparseable", dict(probe_raw="oops-not-json"), True),
             ("none", dict(probe_raw=None, probe_raises=RuntimeError("x")), False),
             ("evaluate_error", dict(probe_raises=RuntimeError("socket closed")), False)]
    for label, kw, lands in cases:
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(lands=lands, **kw)
            out = tick(ex)
        e = marker_entries()
        check(f"unproven_{label}_deletes_but_keeps_the_marker_for_the_late_window",
              "event=cleanup step=marker ok=True" in out and A not in tab.tcins() and pe._LIMKEY_STATE["stopped"] is True
              and len(e) == 1 and e[0]["late"] is True and e[0]["state"] == ""
              and "step=marker_kept reason=late_add_window_" in out and "step=marker_cleared" not in out,
              f"{tab.log} {e} {out[-300:]}")
        # ... and past the window the next clear works
        e[0]["ts"] = time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 1
        pe._lk_marker_store(e)
        check(f"unproven_{label}_marker_clears_after_the_window", pe.limkey_marker_clear("primary", A) is True
              and not marker_file_exists())
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(lands=True)

        async def hang_probe(self_, script, await_promise=False, _orig=CartTab.evaluate):
            if "JSON.stringify({s: resp.status" in script:
                tab.log.append(("probe", marker_file_exists()))
                tab.add_line(A)                              # it landed, but the answer never came back
                await asyncio.sleep(30)
            return await _orig(self_, script, await_promise)
        tab.evaluate = lambda script, await_promise=False: hang_probe(tab, script, await_promise)
        real_wait_for = pe.asyncio.wait_for
        pe.asyncio.wait_for = lambda aw, timeout=None: real_wait_for(aw, timeout=0.3)
        try:
            out = tick(ex)
        finally:
            pe.asyncio.wait_for = real_wait_for
    e = marker_entries()
    check("unproven_timeout_cleans_up_and_keeps_the_late_marker", "err=TimeoutError" in out
          and "event=cleanup step=marker ok=True" in out and A not in tab.tcins() and pe._LIMKEY_STATE["stopped"] is True
          and len(e) == 1 and e[0]["late"] is True, f"{tab.log} {e} {out[-300:]}")
    # nothing landed (cart has no line of T) on a status that is NOT late (200 with no line: the cart read
    # proves it): cleanup finds nothing to delete, proves it, clears the marker at once
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 302, "key": "-", "hop": "-", "n": 0}, lands=False)
        out = tick(ex)
    check("unproven_but_nothing_landed_clears_cleanly", "delete" not in tab.kinds() and not marker_file_exists()
          and "step=marker_cleared ok=True" in out and pe._LIMKEY_STATE["stopped"] is True, f"{tab.log} {out[-300:]}")
    # fix round 4 (evaluator 1): a 500 where nothing has landed YET is NOT cleared at once -- the marker is kept
    # with late=True and clears only after _WONCART_LATE_ADD_WINDOW_S
    for st in (500, 502, 503, 504, 408):
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe={"status": st, "key": "-", "hop": "-", "n": 0}, lands=False)
            out = tick(ex)
        e = marker_entries()
        check(f"status_{st}_nothing_landed_yet_keeps_the_marker_late", "delete" not in tab.kinds() and marker_file_exists()
              and len(e) == 1 and e[0]["late"] is True and e[0]["acct"] == "primary" and e[0]["tcin"] == A
              and "step=marker_cleared" not in out and "step=marker_kept reason=late_add_window_" in out
              and f"late=True" in out and pe._LIMKEY_STATE["stopped"] is True, f"{tab.log} {e} {out[-300:]}")
        check(f"status_{st}_marker_not_clearable_inside_the_window", pe.limkey_marker_clear("primary", A, owner=True) is False
              and pe.limkey_marker_has("primary", A))
        e[0]["ts"] = time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 1
        pe._lk_marker_store(e)
        check(f"status_{st}_marker_clears_after_the_window", pe.limkey_marker_clear("primary", A, owner=True) is True
              and not marker_file_exists())
    # ... and the 503 whose line lands AFTER the cleanup's cart read: the line sits in the cart, the marker is
    # still there, so the buy path (strike, silent hold, boot adoption) still treats it as not-ours
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 503, "key": "-", "hop": "-", "n": 0}, lands=False)
        real_ev = tab.evaluate
        state = {"reads_after_probe": 0}

        async def land_after_read(script, await_promise=False):
            r = await real_ev(script, await_promise)
            if script == pe._CART_ITEMS_READ_JS and probes_sent(tab) >= 1:
                state["reads_after_probe"] += 1
                if state["reads_after_probe"] == 1:
                    tab.add_line(A)                    # the cart service finishes the add just after the read
            return r
        tab.evaluate = land_after_read
        out = tick(ex)
    e = marker_entries()
    check("503_line_lands_after_the_cleanup_read_marker_still_there", A in tab.tcins() and marker_file_exists()
          and len(e) == 1 and e[0]["late"] is True and "step=marker_cleared" not in out,
          f"{tab.log} {tab.lines} {e} {out[-300:]}")
    with env(**STRIKE_ENV):
        check("503_late_line_not_adoptable_strike_inactive_for_it", ex._held_line_strike_active(A) is False
              and pe.limkey_marker_acct("primary") is True)
    fresh()


def test_proven_non_landing_does_nothing():
    for label, probe in PROVEN:
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe=probe, lands=False)
            out = tick(ex)
        check(f"proven_{label}_no_cleanup", probes_sent(tab) == 1 and tab.kinds() == ["probe"]
              and not marker_file_exists() and pe._LIMKEY_STATE["stopped"] is False and "cleanup" not in out
              and "[LIMKEY_PROBE] acct=primary" in out, f"{tab.log} {out[-300:]}")
        with env(**ENV_ON):
            check(f"proven_{label}_can_probe_again", pe.limkey_reserve(pe.limkey_probe_cfg(), time.time() + 10_000) == "")


def test_cleanup_failures_keep_the_marker():
    for label, kw in (("delete_500", dict(delete_status=500)), ("delete_404", dict(delete_status=404)),
                      ("read_fails", dict(read_mode="fail")), ("read_has_no_array", dict(read_mode="no_array"))):
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True, **kw)
            out = tick(ex)
        e = marker_entries()
        check(f"cleanup_{label}_marker_stays", len(e) == 1 and e[0]["acct"] == "primary" and e[0]["tcin"] == A
              and e[0]["reason"].startswith("status_201"), f"{e} {out[-300:]}")
        check(f"cleanup_{label}_still_stopped_and_says_so", pe._LIMKEY_STATE["stopped"] is True
              and "step=marker_kept reason=line_not_proven_gone" in out and "step=marker_cleared" not in out, out[-400:])
    # a delete that "worked" but a re-read that still shows the line -> NOT proven gone
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True, delete_status=204)
        real = tab.evaluate

        async def sticky(script, await_promise=False):
            r = await real(script, await_promise)
            if "method: 'DELETE'" in script:
                tab.add_line(A)                               # the delete reported ok; the line is still there
            return r
        tab.evaluate = sticky
        tick(ex)
    check("cleanup_ok_delete_but_line_still_there_keeps_marker", marker_file_exists() and pe.limkey_marker_has("primary", A))


def test_cleanup_never_raises():
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True, dead_after_probe=True)
        try:
            out = tick(ex)
            ok = True
        except BaseException as e:               # noqa: BLE001
            out, ok = repr(e), False
    check("dead_tab_after_probe_never_raises", ok, out)
    check("dead_tab_marker_written_and_kept_stopped", marker_file_exists() and pe._LIMKEY_STATE["stopped"] is True)
    # the in_flight marker cannot be written BEFORE the probe: no probe at all (no durable guard = no POST)
    fresh()
    real = pe._LIMKEY_MARKER_PATH
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "afile"
        f.write_text("x")
        pe._LIMKEY_MARKER_PATH = str(f / "sub" / "m.json")
        try:
            with env(**ENV_ON):
                ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True)
                out = tick(ex)
            leftover = pe.limkey_marker_any()
        finally:
            pe._LIMKEY_MARKER_PATH = real
    check("in_flight_marker_unwritable_means_no_probe", probes_sent(tab) == 0 and pe._LIMKEY_STATE["stopped"] is True
          and "event=skip reason=marker_write_failed" in out and not leftover, out[-300:])
    # fix 4: the marker is writable for the in_flight entry, then the disk goes away mid-flight; the cleanup's write fails
    S201 = {"status": 201, "key": "-", "hop": "-", "n": 900}
    for label, kw, fb_after, alert2 in (
            ("line_deleted_not_late", dict(probe=S201, lands=True), False, False),
            ("line_not_provable", dict(probe=S201, lands=True, delete_status=500), True, True),
            ("late_window_open", dict(probe_raises=RuntimeError("x")), True, True)):
        fresh()
        real = pe._LIMKEY_MARKER_PATH
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "afile"
            f.write_text("x")

            def break_disk(t, _bad=str(f / "sub" / "m.json")):
                pe._LIMKEY_MARKER_PATH = _bad
            try:
                with env(**ENV_ON):
                    ex, tab, sm = make(on_probe=break_disk, **kw)
                    out = tick(ex)
                fb_has = pe.limkey_marker_has("primary", A)
                fb_entry = pe.limkey_marker_entry("primary", A)
            finally:
                pe._LIMKEY_MARKER_PATH = real
        check(f"marker_unwritable_at_cleanup_{label}_stops_and_alerts", pe._LIMKEY_STATE["stopped"] is True
              and "event=cleanup step=marker ok=False ALERT" in out, out[-500:])
        check(f"marker_unwritable_at_cleanup_{label}_fallback_{'kept' if fb_after else 'released'}",
              fb_has is fb_after and (fb_entry is not None) is fb_after, f"{fb_entry}")
        check(f"marker_unwritable_at_cleanup_{label}_second_alert_{'yes' if alert2 else 'no'}",
              ("NOTHING durable guards" in out) is alert2, out[-600:])
        if fb_after:
            ex2 = pe.PurchaseExecutor(session_manager=L.FakeSM("primary"))
            check(f"marker_unwritable_at_cleanup_{label}_consumers_see_the_fallback",
                  pe.limkey_marked_for(ex2, A) is True and pe.limkey_marker_any() is True)
    fresh()
    # an exception inside the cleanup itself is swallowed
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True)

        async def boom(*a, **k):
            raise ValueError("boom")
        ex._delete_cart_items = boom
        out = tick(ex)
    check("cleanup_internal_error_is_logged_not_raised", "event=cleanup step=error ValueError" in out
          and marker_file_exists() and pe._LIMKEY_STATE["stopped"] is True, out[-400:])


# ───────────────────────────── step 4: the guards after the await ─────────────────────────────
def test_guards_reread_after_the_await():
    cases = [
        ("purchase_in_progress", lambda t: setattr(t.ex.session_manager, "purchase_in_progress", True)),
        ("woncart_quiet", lambda t: setattr(t.ex, "_woncart_active_until", time.time() + 60)),
        ("held_cart", lambda t: setattr(t.ex, "_held_cart", {"created": time.time(), "tcin": OTHER})),
        ("won_cart_ride", lambda t: setattr(t.ex, "_won_cart_ride_until", time.time() + 60)),
    ]
    for label, mutate in cases:
        # not a proven non-landing + a guard became active during the await: marker, stop, NO delete
        # (the purchase / ride owns the cart; its own line of T is not ours to delete)
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True, on_probe=mutate)
            out = tick(ex)
        e = marker_entries()
        check(f"raced_{label}_marker_written_and_kept", len(e) == 1 and f"raced_" in e[0]["reason"], f"{e} {out[-300:]}")
        check(f"raced_{label}_no_delete_while_the_cart_is_owned", "delete" not in tab.kinds(), str(tab.log))
        check(f"raced_{label}_probing_stopped", pe._LIMKEY_STATE["stopped"] is True and "step=stopped" in out)
        # a proven non-landing is untouched by a raced guard
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe={"status": 429, "key": ERR_A2C, "hop": "-", "n": 0}, on_probe=mutate)
            out = tick(ex)
        check(f"raced_{label}_proven_non_landing_is_left_alone", tab.kinds() == ["probe"] and not marker_file_exists()
              and pe._LIMKEY_STATE["stopped"] is False and "cleanup" not in out, f"{tab.log} {out[-200:]}")
    # the pre-evaluate guards still refuse before the await (unchanged)
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make()
        ex.session_manager.purchase_in_progress = True
        out = tick(ex)
    check("pre_await_guard_still_refuses", probes_sent(tab) == 0 and "reason=purchase_live" in out)
    # a guard that raises counts as busy
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True)
        orig = ex._woncart_quiet
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] >= 2:                   # the pre-await guard passes; the post-await read raises
                raise RuntimeError("x")
            return orig()
        ex._woncart_quiet = flaky
        out = tick(ex)
    check("post_await_guard_error_counts_as_busy", "delete" not in tab.kinds() and marker_file_exists()
          and "guard_error" in marker_entries()[0]["reason"], f"{tab.log} {marker_entries()}")


# ───────────────────────────── step 3: the stop persists ─────────────────────────────
def test_marker_stops_the_probe_for_the_process():
    fresh()
    pe.limkey_marker_write("primary", A, "left_by_a_previous_process")
    with env(**ENV_ON):
        ex, tab, sm = make()
        out1 = tick(ex)
        out2 = tick(ex, time.time() + 500)
        ex2, tab2, _ = make(acct="primary")
        out3 = tick(ex2, time.time() + 900)
    check("marker_present_no_probe", probes_sent(tab) == 0 and probes_sent(tab2) == 0 and tab.kinds() == [])
    check("marker_present_logged_once", out1.count("[LIMKEY_PROBE] event=skip reason=marker_present") == 1
          and "marker_present" not in out2 and "marker_present" not in out3, f"{out1!r} {out2!r} {out3!r}")
    check("marker_present_sets_stopped", pe._LIMKEY_STATE["stopped"] is True)
    # the marker is cleared by the purchase entry / boot audit -> this process still does not probe
    os.remove(pe._LIMKEY_MARKER_PATH)
    with env(**ENV_ON):
        out4 = tick(ex, time.time() + 2000)
    check("stop_persists_after_the_marker_is_cleared", probes_sent(tab) == 0 and out4 == "", out4)
    # a new process (reset) with no marker probes again
    fresh()
    with env(**ENV_ON):
        ex3, tab3, _ = make()
        tick(ex3)
    check("new_process_without_marker_probes", probes_sent(tab3) == 1)
    # an unreadable marker file also stops the probe
    fresh()
    Path(pe._LIMKEY_MARKER_PATH).write_text("{corrupt", encoding="utf-8")
    with env(**ENV_ON):
        ex4, tab4, _ = make()
        out5 = tick(ex4)
    check("unreadable_marker_stops_the_probe", probes_sent(tab4) == 0 and "marker_present" in out5)
    fresh()
    # another account's marker also stops it (the whole process)
    pe.limkey_marker_write("alt-1", B, "x")
    with env(**ENV_ON):
        ex5, tab5, _ = make()
        tick(ex5)
    check("any_accounts_marker_stops_the_probe", probes_sent(tab5) == 0)
    fresh()
    # probe unset + marker: the tick is a no-op (cfg None returns first) - and the loop never calls it
    pe.limkey_marker_write("primary", A, "x")
    with env(**{FLAG: None}):
        ex6, tab6, _ = make()
        out6 = tick(ex6)
    check("probe_unset_marker_present_tick_is_silent", out6 == "" and tab6.kinds() == [])
    fresh()


# ───────────────────────────── step 2: the marker consumers ─────────────────────────────
def test_check_cart_hold():
    fresh()
    for label, marked, expect in (("no_marker", None, True), ("marker_this_acct_tcin", ("primary", A), False),
                                  ("marker_other_acct", ("alt-1", A), True), ("marker_other_tcin", ("primary", B), True)):
        fresh()
        if marked:
            pe.limkey_marker_write(*marked, "x")
        ex, tab, sm = make(lines=[{"id": "L1", "tcin": A, "qty": 1}])
        ex._cart_hold_check_on = True
        ex._last_cart_hold_check_ts = 0.0
        ex._cart_hold_check_interval_s = 0.0
        r, out = arun(ex._check_cart_hold(tab, A, 429))
        check(f"cart_hold_{label}", r is expect, f"{r} {out}")
        if not expect:
            check(f"cart_hold_{label}_reads_nothing", "hold" not in tab.kinds(), str(tab.log))
    fresh()
    # the marker is gone once the line is proven gone -> the silent-hold check works again
    pe.limkey_marker_write("primary", A, "x")
    pe.limkey_marker_clear("primary", A)
    ex, tab, sm = make(lines=[{"id": "L1", "tcin": A, "qty": 1}])
    ex._cart_hold_check_on = True
    ex._last_cart_hold_check_ts = 0.0
    ex._cart_hold_check_interval_s = 0.0
    r, _ = arun(ex._check_cart_hold(tab, A, 429))
    check("cart_hold_works_again_after_the_marker_clears", r is True)


def test_strike_inactive_under_a_marker():
    fresh()
    with env(**STRIKE_ENV):
        ex, tab, sm = make()
        check("strike_active_without_marker", ex._held_line_strike_active(A) is True)
        pe.limkey_marker_write("primary", A, "x")
        check("strike_inactive_for_the_marked_tcin", ex._held_line_strike_active(A) is False)
        check("strike_active_for_another_tcin", ex._held_line_strike_active(B) is True)
        ex2, _, _ = make(acct="alt-1")
        check("strike_active_for_another_account", ex2._held_line_strike_active(A) is True)
        pe.limkey_marker_clear("primary", A)
        check("strike_active_again_after_the_marker_clears", ex._held_line_strike_active(A) is True)
    fresh()


def _boot(ex, tab):
    async def go():
        ex._warmup_pool_lock = asyncio.Lock()

        async def ens(idx):
            return tab
        ex._ensure_warmup_tab = ens
        ex._woncart_armed = lambda: True
        ex._woncart_api_path_on = lambda: True
        return await ex._boot_cart_audit(delay_s=0.0)
    return arun(go())


def test_boot_audit_deletes_instead_of_adopting():
    one = [{"id": "CI0001", "tcin": A, "qty": 1}]
    # differential: no marker -> the single clean line is ADOPTED (today's behaviour)
    fresh()
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=list(one))
        r, out = _boot(ex, tab)
    check("boot_no_marker_adopts_as_before", r == "held" and isinstance(ex._held_cart, dict)
          and ex._held_cart["tcin"] == A and tab.tcins() == [A] and "delete" not in tab.kinds(), f"{r} {out}")
    # marker -> deleted, not adopted
    fresh()
    pe.limkey_marker_write("primary", A, "status_201")
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=list(one))
        r, out = _boot(ex, tab)
    check("boot_marker_deletes_not_adopts", r == "limkey_deleted" and ex._held_cart is None and tab.tcins() == []
          and "DELETED, not adopted" in out, f"{r} {out}")
    check("boot_marker_cleared_once_proven_gone", not marker_file_exists())
    # a marker for another account / TCIN does not stop adoption
    for label, marked in (("other_account", ("alt-1", A)), ("other_tcin", ("primary", B))):
        fresh()
        pe.limkey_marker_write(*marked, "x")
        with env(**BOOT_ENV):
            ex, tab, sm = make(lines=list(one))
            r, out = _boot(ex, tab)
        check(f"boot_{label}_marker_still_adopts", r == "held" and ex._held_cart["tcin"] == A and tab.tcins() == [A], f"{r}")
    # probe line + one other clean line: the probe line goes, the rest is judged as before (adopted)
    fresh()
    pe.limkey_marker_write("primary", A, "x")
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[{"id": "CI0001", "tcin": A, "qty": 1}, {"id": "CI0002", "tcin": OTHER, "qty": 1}])
        r, out = _boot(ex, tab)
    check("boot_marker_plus_other_line_probe_deleted_other_adopted", r == "held" and ex._held_cart["tcin"] == OTHER
          and tab.tcins() == [OTHER] and not marker_file_exists(), f"{r} {tab.lines} {out}")
    # delete that cannot be proven: nothing adopted, marker stays
    for label, kw in (("delete_500", dict(delete_status=500)), ("read_after_fails", None)):
        fresh()
        pe.limkey_marker_write("primary", A, "x")
        with env(**BOOT_ENV):
            ex, tab, sm = make(lines=list(one), **(kw or {}))
            if kw is None:
                real = tab.evaluate
                state = {"reads": 0}

                async def flaky(script, await_promise=False):
                    if script == pe._CART_ITEMS_READ_JS:
                        state["reads"] += 1
                        if state["reads"] >= 2:
                            tab.read_mode = "fail"
                    return await real(script, await_promise)
                tab.evaluate = flaky
            r, out = _boot(ex, tab)
        check(f"boot_marker_{label}_adopts_nothing_keeps_marker", r == "limkey_delete_failed" and ex._held_cart is None
              and pe.limkey_marker_has("primary", A), f"{r} {out}")
    fresh()


# ───────────────────────────── step 2: the purchase entry ─────────────────────────────
class SimCart:
    def __init__(self, lines):
        self.lines = list(lines)
        self.events = []
        self.fail_delete = False
        self.fail_read = False


def _impl_ex(cart, ident="primary"):
    c = S.Clock()
    ex, tab = S.impl_ex(c, S.FL_PRE429)
    ex.session_manager.account_id = ident

    async def _read(t, timeout=2.5):
        cart.events.append("read")
        if cart.fail_read:
            return {"ok": False, "status": 429, "items": []}
        return {"ok": True, "status": 200, "has_items": True, "items": [dict(l) for l in cart.lines]}

    async def _del(t, only_tcin=None, keep_tcin=None, budget_s=15.0, ids=None, abort_fn=None):
        cart.events.append(f"delete:{only_tcin}")
        if cart.fail_delete:
            return False, 0
        n = len([l for l in cart.lines if l["tcin"] == str(only_tcin)])
        cart.lines = [l for l in cart.lines if l["tcin"] != str(only_tcin)]
        return True, n

    ex._cart_items_read = _read
    ex._delete_cart_items = _del
    real_fl = ex._api_fast_lane

    async def _fl(t, tcin, qty, hdrs):
        cart.events.append("SHOT")
        return await real_fl(t, tcin, qty, hdrs)
    ex._api_fast_lane = _fl
    return ex, c


def _impl_run(ex):
    try:
        return S.run_impl(ex)
    except BaseException as e:                    # noqa: BLE001  (the stub chain ends in LegacyReached)
        return {"_raised": type(e).__name__}


def test_purchase_entry():
    T = S.TCIN
    # no marker: nothing is read or deleted before the shot (differential)
    fresh()
    cart = SimCart([])
    ex, c = _impl_ex(cart)
    _impl_run(ex)
    check("entry_no_marker_goes_straight_to_the_shot", cart.events[:1] == ["SHOT"] and "delete:%s" % T not in cart.events,
          str(cart.events))
    # marker + probe line: deleted and proven gone BEFORE the shot, marker cleared
    fresh()
    pe.limkey_marker_write("primary", T, "status_201")
    cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
    ex, c = _impl_ex(cart)
    ex._held_cart = {"tcin": T, "created": c.t, "source": "boot"}
    _impl_run(ex)
    check("entry_marker_deletes_the_line_before_the_shot", cart.events[:3] == [f"delete:{T}", "read", "SHOT"]
          and not cart.lines, str(cart.events))
    check("entry_marker_cleared_once_proven", not marker_file_exists())
    check("entry_marker_drops_a_held_marker_of_that_tcin_so_no_re_entry", ex._held_cart is None
          and "SHOT" in cart.events)
    # the dirty (strike) flag for that TCIN is dropped too
    fresh()
    pe.limkey_marker_write("primary", T, "x")
    cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
    ex, c = _impl_ex(cart)
    ex._woncart_dirty = {"tcin": T, "ts": c.t, "why": "x", "cvv_put": "none"}
    _impl_run(ex)
    check("entry_marker_drops_the_dirty_flag_of_that_tcin", getattr(ex, "_woncart_dirty", None) is None
          and cart.events[0] == f"delete:{T}", str(cart.events))
    # marker + delete cannot be proven: NOTHING fires, a skip result, marker stays
    for label, setup in (("delete_fails", lambda cart: setattr(cart, "fail_delete", True)),
                         ("read_fails", lambda cart: setattr(cart, "fail_read", True))):
        fresh()
        pe.limkey_marker_write("primary", T, "x")
        cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
        setup(cart)
        ex, c = _impl_ex(cart)
        r = _impl_run(ex)
        check(f"entry_marker_{label}_nothing_fired", "SHOT" not in cart.events and r.get("success") is False
              and r.get("reason") == "held_cart_release_failed" and pe.limkey_marker_has("primary", T), f"{r} {cart.events}")
        check(f"entry_marker_{label}_error_text_has_no_digits", not re.search(r"\d", str(r.get("error", ""))), str(r))
    # a marker for another account / another TCIN does not touch this purchase
    for label, marked in (("other_account", ("alt-1", T)), ("other_tcin", ("primary", OTHER))):
        fresh()
        pe.limkey_marker_write(*marked, "x")
        cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
        ex, c = _impl_ex(cart)
        _impl_run(ex)
        check(f"entry_{label}_marker_ignored", cart.events[:1] == ["SHOT"] and cart.lines, str(cart.events))
        pe.limkey_probe_reset()
    # source order: the guard sits before every path that could ride or strike the line
    src = Path(pe.__file__).read_text(encoding="utf-8")
    i = src.index("_lk_res = await self._limkey_purge_entry(tab, tcin, start_time)")
    after = src[i:]
    check("entry_guard_precedes_dirty_release_held_entry_and_fast_lane",
          0 < after.index("_woncart_dirty_release(tab, tcin, start_time, quantity)")
          < after.index("_held_cart_entry(tab, tcin, quantity, start_time)")
          < after.index("_api_fast_lane(tab, tcin, quantity, extra_headers_js)"))
    fresh()


# ───────────────────────────── flag off / no marker: byte-identical ─────────────────────────────
def _norm(text):
    return [re.sub(r"\d+(\.\d+)?s\b", "T", ln) for ln in text.splitlines()]


def test_differential_against_the_pre_change_executor():
    L.PRE_COMMIT = PRE_COMMIT
    old = L.load_pre_change_module()
    if old is None:
        # the byte-identical claim is only as good as this comparison: an unloadable pre-change object is a FAIL,
        # never a pass (the object exists in any clone that has PRE_COMMIT; fetch it before running the suite)
        print("[FAIL] differential: the pre-change executor could not be loaded from git "
              f"({PRE_COMMIT}); the flag-off / no-marker byte-identical claim is UNCHECKED")
        check("differential_pre_change_object_loaded_from_git", False, f"git show {PRE_COMMIT}:src/session/purchase_executor.py failed")
        return
    check("differential_old_module_has_no_guard", not hasattr(old, "limkey_marker_has"))

    # 1. heartbeat cycle, probe unset
    res = {}
    for label, mod in (("old", old), ("new", pe)):
        fresh()
        with env(**{FLAG: None, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
            ex, tab, sm = L.make_ex(mod.PurchaseExecutor)
            out, sleeps = asyncio.run(L.run_loop(ex, mod=mod if mod is old else None))
        res[label] = (list(tab.scripts), _norm(out), sleeps, sm.repairs)
    check("diff_probe_unset_scripts_identical", res["old"][0] == res["new"][0] and len(res["old"][0]) == 1)
    check("diff_probe_unset_prints_identical", res["old"][1] == res["new"][1], f"{res['old'][1]}\n{res['new'][1]}")
    check("diff_probe_unset_no_marker_file_created", not marker_file_exists())

    # 2. probe ON, answers that are proven non-landing: same scripts, same prints, no marker
    for label, probe in PROVEN:
        res = {}
        for which, mod in (("old", old), ("new", pe)):
            fresh()
            old._LIMKEY_STATE.update({"last_ts": 0.0, "total": 0, "stopped": False, "rr": 0, "first_seen": {},
                                      "skip_ts": {}, "errs": 0})
            old._LIMKEY_STATE["times"].clear()
            with env(**ENV_ON):
                sm = L.FakeSM("primary")
                ex = mod.PurchaseExecutor(session_manager=sm)
                tab = L.FakeTab(ex, 424, probe)
                ex._warmup_browser_ref = sm.browser
                ex._warmup_tabs = [tab]
                ex._warmup_tab_cart_ts = {0: time.time() - 5.2}
                now = time.time()
                ex._stock_live_fn = L.stock_all("oos_never", now)
                for t in (A,):
                    mod._LIMKEY_STATE["first_seen"][t] = now - 700.0
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    asyncio.run(ex._limkey_probe_tick(0, now=now))
            res[which] = (list(tab.scripts), _norm(buf.getvalue()), mod._LIMKEY_STATE["stopped"])
        check(f"diff_probe_on_{label}_identical", res["old"] == res["new"] and not marker_file_exists(),
              f"{res['old']}\n{res['new']}")

    # 3. no marker: _check_cart_hold, strike gate, boot audit behave exactly as before
    holds = {}
    for which, mod in (("old", old), ("new", pe)):
        fresh()
        sm = L.FakeSM("primary")
        ex = mod.PurchaseExecutor(session_manager=sm)
        tab = CartTab(ex, lines=[{"id": "L1", "tcin": A, "qty": 1}])
        ex._cart_hold_check_on = True
        ex._last_cart_hold_check_ts = 0.0
        ex._cart_hold_check_interval_s = 0.0
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = asyncio.run(ex._check_cart_hold(tab, A, 429))
            r2 = asyncio.run(ex._check_cart_hold(tab, B, 429))
        holds[which] = (r, r2, [s for s in tab.scripts], _norm(buf.getvalue()))
    check("diff_check_cart_hold_identical", holds["old"] == holds["new"] and holds["new"][0] is True and holds["new"][1] is False,
          str(holds))
    gates = {}
    for which, mod in (("old", old), ("new", pe)):
        with env(**STRIKE_ENV):
            ex = mod.PurchaseExecutor(session_manager=L.FakeSM("primary"))
            ex._ordered_tcins = {B}
            gates[which] = [ex._held_line_strike_active(t) for t in (A, B, OTHER)]
    check("diff_strike_gate_identical", gates["old"] == gates["new"] == [True, False, True], str(gates))
    boots = {}
    for which, mod in (("old", old), ("new", pe)):
        fresh()
        for lines in ([{"id": "CI1", "tcin": A, "qty": 1}],
                      [{"id": "CI1", "tcin": A, "qty": 1}, {"id": "CI2", "tcin": OTHER, "qty": 1}],
                      [{"id": "CI1", "tcin": A, "qty": 3}]):
            with env(**BOOT_ENV):
                sm = L.FakeSM("primary")
                ex = mod.PurchaseExecutor(session_manager=sm)
                tab = CartTab(ex, lines=lines)
                r, out = _boot(ex, tab)
            boots.setdefault(which, []).append((r, (ex._held_cart or {}).get("tcin"), tab.tcins(), tab.kinds(), _norm(out)))
    check("diff_boot_audit_identical", boots["old"] == boots["new"], f"{boots['old']}\n{boots['new']}")
    fresh()


# ───────────────────────────── fix 1: a POST that lands after the timeout ─────────────────────────────
def test_late_landing_after_a_timeout():
    fresh()
    with env(**{**ENV_ON, **STRIKE_ENV, **BOOT_ENV}):
        ex, tab, sm = make(lands=False)

        async def hang_probe(self_, script, await_promise=False, _orig=CartTab.evaluate):
            if "JSON.stringify({s: resp.status" in script:
                tab.log.append(("probe", marker_file_exists()))
                await asyncio.sleep(30)                      # the answer never comes back; the fetch lives on
            return await _orig(self_, script, await_promise)
        tab.evaluate = lambda script, await_promise=False: hang_probe(tab, script, await_promise)
        real_wait_for = pe.asyncio.wait_for
        pe.asyncio.wait_for = lambda aw, timeout=None: real_wait_for(aw, timeout=0.3)
        try:
            out = tick(ex)
        finally:
            pe.asyncio.wait_for = real_wait_for
        check("late_cleanup_read_found_nothing_and_deleted_nothing", "delete" not in tab.kinds()
              and "proven_gone=True" in out, f"{tab.log} {out[-300:]}")
        check("late_marker_survives_a_proven_gone_read_inside_the_window", marker_file_exists()
              and pe.limkey_marker_has("primary", A) and "step=marker_kept reason=late_add_window_" in out
              and "step=marker_cleared" not in out, out[-400:])
        # NOW the POST lands (after the timeout and after the cleanup's read)
        tab.add_line(A)
        tab.restore = tab.evaluate
        tab.evaluate = lambda script, await_promise=False: CartTab.evaluate(tab, script, await_promise)
        ex._cart_hold_check_on = True
        ex._last_cart_hold_check_ts = 0.0
        ex._cart_hold_check_interval_s = 0.0
        r, out2 = arun(ex._check_cart_hold(tab, A, 429))
        check("late_line_is_not_a_silent_hold", r is False and "hold" not in tab.kinds(), f"{r} {tab.log}")
        check("late_line_strike_inactive", ex._held_line_strike_active(A) is False)
        r, out3 = _boot(ex, tab)
        check("late_line_boot_audit_does_not_adopt_it", ex._held_cart is None and r == "limkey_deleted"
              and tab.tcins() == [] and "DELETED, not adopted" in out3, f"{r} {out3}")
        check("late_marker_still_there_after_the_boot_audit_deleted_the_line", marker_file_exists()
              and pe.limkey_marker_has("primary", A) and "marker_kept=" in out3, out3)
        # a second late landing, then the purchase entry: deletes before the shot but keeps the marker
        tab.add_line(A)
        res, out4 = arun(ex._limkey_purge_entry(tab, A, time.time()))
        check("late_purchase_entry_deletes_the_line_keeps_the_marker_and_fires_nothing",
              isinstance(res, dict) and res.get("reason") == "limkey_window_open" and res.get("success") is False
              and A not in tab.tcins() and pe.limkey_marker_has("primary", A)
              and "marker KEPT (late_add_window_" in out4, f"{res} {out4}")
        # the window passes: a proven-gone read now clears the marker
        ents = marker_entries()
        ents[0]["ts"] = time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 1
        pe._lk_marker_store(ents)
        res, out5 = arun(ex._limkey_purge_entry(tab, A, time.time()))
        check("late_marker_clears_once_the_window_has_passed", res is None and not marker_file_exists(), out5)
    fresh()
    # a 2xx / body-429 (an answer that DID come back) is not late: cleared at once on a proven-gone read.
    # (a gateway 5xx / 408 is late since fix round 4: see test_cleanup_matrix)
    for label, probe in (("201", {"status": 201, "key": "-", "hop": "-", "n": 900}),
                         ("429_body", {"status": 429, "key": ERR_A2C, "hop": "-", "n": 90})):
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe=probe, lands=(label == "201"))
            tick(ex)
        check(f"answered_{label}_is_not_late_cleared_at_once", not marker_file_exists())
    # an unparseable answer WITH a landed line also holds the marker
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe_raw="oops", lands=True)
        tick(ex)
    e = marker_entries()
    check("unparseable_status_minus_one_holds_the_marker", len(e) == 1 and e[0]["late"] is True and A not in tab.tcins())
    fresh()


# ───────────────────────────── fix 2: the stop latches at start ─────────────────────────────
def test_stop_latched_at_start():
    fresh()
    pe.limkey_marker_write("primary", A, "left_by_a_previous_process")
    with env(**ENV_ON):
        ex, tab, sm = make()
        order = []
        ex._start_harvest = lambda: order.append("harvest")
        ex._start_boot_cart_audit = lambda: order.append(("audit", pe._LIMKEY_STATE["stopped"]))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ex._start_background_refill()           # no running loop: returns after the audit is spawned
        out0 = buf.getvalue()
    check("start_latch_runs_before_the_boot_audit_is_spawned", order == ["harvest", ("audit", True)], str(order))
    check("start_latch_logs_marker_present_once", out0.count("[LIMKEY_PROBE] event=skip reason=marker_present") == 1, out0)
    # the boot audit (or a purchase entry) deletes the line and clears the marker BEFORE the first probe tick
    os.remove(pe._LIMKEY_MARKER_PATH)
    with env(**ENV_ON):
        out1 = tick(ex)
        out2 = tick(ex, time.time() + 900)
    check("marker_cleared_before_the_first_tick_still_no_probe", probes_sent(tab) == 0 and out1 == "" and out2 == ""
          and pe._LIMKEY_STATE["stopped"] is True, f"{tab.log} {out1!r} {out2!r}")
    # a second executor's start does not re-log and does not re-evaluate
    with env(**ENV_ON):
        ex2, tab2, _ = make()
        ex2._start_harvest = lambda: None
        ex2._start_boot_cart_audit = lambda: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ex2._start_background_refill()
        out3 = buf.getvalue()
        tick(ex2, time.time() + 1500)
    check("second_executor_start_is_quiet_and_still_stopped", "marker_present" not in out3 and probes_sent(tab2) == 0)
    # no marker at start: nothing latches, the probe works as before
    fresh()
    with env(**ENV_ON):
        ex3, tab3, _ = make()
        ex3._start_harvest = lambda: None
        ex3._start_boot_cart_audit = lambda: None
        ex3._start_background_refill()
        stopped_after_start = pe._LIMKEY_STATE["stopped"]
        tick(ex3)
    check("no_marker_at_start_no_latch_and_the_probe_fires", stopped_after_start is False and probes_sent(tab3) == 1)
    # probe unset + marker: latched silently (the stop is harmless, the log stays quiet)
    fresh()
    pe.limkey_marker_write("primary", A, "x")
    with env(**{FLAG: None}):
        ex4, tab4, _ = make()
        ex4._start_harvest = lambda: None
        ex4._start_boot_cart_audit = lambda: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ex4._start_background_refill()
    check("probe_unset_marker_present_start_is_silent", buf.getvalue() == "" and pe._LIMKEY_STATE["stopped"] is True)
    # never raises
    fresh()
    ex5, _, _ = make()
    ex5.session_manager = None
    pe.limkey_marker_write("primary", A, "x")
    with env(**ENV_ON):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ex5._limkey_latch_stop_at_start()
            ok = True
        except BaseException:        # noqa: BLE001
            ok = False
    check("start_latch_never_raises", ok)
    fresh()


# ───────────────────────────── fix 3: the in_flight entry ─────────────────────────────
def test_in_flight_entry():
    seen = {}

    async def mid_flight(t):
        ex = t.ex
        seen["entry"] = pe.limkey_marker_entry("primary", A)
        t.add_line(A)                                # the probe's POST has just landed
        ex._cart_hold_check_on = True
        ex._last_cart_hold_check_ts = 0.0
        ex._cart_hold_check_interval_s = 0.0
        seen["hold"] = await ex._check_cart_hold(t, A, 429)
        seen["hold_reads"] = "hold" in t.kinds()
        seen["strike"] = ex._held_line_strike_active(A)
        # a purchase entry mid-flight: deletes the line, must NOT clear the in_flight entry
        seen["purge"] = await ex._limkey_purge_entry(t, A, time.time())
        seen["entry_after_purge"] = pe.limkey_marker_entry("primary", A)
        t.add_line(A)                                # ... and the probe lands one more (a retry inside the browser)
        # the boot audit mid-flight: deletes, never adopts, never clears the in_flight entry
        async def ens(idx):
            return t
        ex._warmup_pool_lock = asyncio.Lock()
        ex._ensure_warmup_tab = ens
        ex._woncart_armed = lambda: True
        ex._woncart_api_path_on = lambda: True
        seen["boot"] = await ex._boot_cart_audit(delay_s=0.0)
        seen["held"] = ex._held_cart
        seen["entry_after_boot"] = pe.limkey_marker_entry("primary", A)
        t.add_line(A)

    fresh()
    with env(**{**ENV_ON, **STRIKE_ENV, **BOOT_ENV}):
        ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, on_probe_async=mid_flight)
        out = tick(ex)
    check("in_flight_entry_down_during_the_evaluate", seen.get("entry") and seen["entry"]["state"] == "in_flight", str(seen.get("entry")))
    check("in_flight_check_cart_hold_is_false_and_reads_nothing", seen.get("hold") is False and seen.get("hold_reads") is False,
          str(seen))
    check("in_flight_strike_inactive", seen.get("strike") is False)
    check("in_flight_purchase_entry_deleted_the_line_fired_nothing_and_did_not_clear_the_entry",
          isinstance(seen.get("purge"), dict) and seen["purge"].get("reason") == "limkey_window_open"
          and seen.get("entry_after_purge") and seen["entry_after_purge"]["state"] == "in_flight", str(seen.get("entry_after_purge")))
    check("in_flight_boot_audit_did_not_adopt_and_kept_the_entry", seen.get("boot") == "limkey_deleted"
          and seen.get("held") is None and seen.get("entry_after_boot") and seen["entry_after_boot"]["state"] == "in_flight",
          f"{seen.get('boot')} {seen.get('entry_after_boot')}")
    # the probe's own verdict (201 = landed) then cleans up the line it landed and replaces the entry
    check("in_flight_probe_verdict_cleans_up_afterwards", A not in tab.tcins() and not marker_file_exists()
          and "event=cleanup step=marker_cleared ok=True" in out, f"{tab.lines} {out[-300:]}")

    # proven non-landing: the probe clears its own in_flight entry; nothing else
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 429, "key": ERR_A2C, "hop": "-", "n": 0})
        tick(ex)
    check("in_flight_proven_non_landing_clears_the_entry", not marker_file_exists() and pe._LIMKEY_STATE["stopped"] is False)

    # an error between the in_flight write and the verdict never leaves a stuck in_flight entry
    fresh()
    real_line = pe.limkey_result_line
    pe.limkey_result_line = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    try:
        with env(**ENV_ON):
            ex, tab, sm = make(probe={"status": 429, "key": ERR_A2C, "hop": "-", "n": 0})
            out = tick(ex)
    finally:
        pe.limkey_result_line = real_line
    e = marker_entries()
    check("in_flight_tick_error_converts_the_entry_to_a_late_landed_marker", len(e) == 1 and e[0]["state"] == ""
          and e[0]["late"] is True and e[0]["reason"].startswith("tick_error_") and pe._LIMKEY_STATE["stopped"] is True,
          f"{e} {out[-200:]}")

    # a cancelled task mid-flight: the entry becomes a late landed marker, CancelledError propagates
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe_raises=asyncio.CancelledError())
        try:
            tick(ex)
            raised = False
        except BaseException as exc:        # noqa: BLE001
            raised = isinstance(exc, asyncio.CancelledError)
    e = marker_entries()
    check("in_flight_cancel_propagates_and_leaves_a_late_marker", raised and len(e) == 1 and e[0]["state"] == ""
          and e[0]["late"] is True and pe._LIMKEY_STATE["stopped"] is True, f"{raised} {e}")
    fresh()



# ───────────────────── fix round 2 (10-16 evaluator): items 1-6 ─────────────────────
TERMINAL_TOKENS = ("oos", "out_of_stock", "sold_out", "reservation", "unavailable")


def test_woncart_suspect_while_a_marker_names_the_account():
    # (1) the evaluator's harness: a probe answers 429 WITH a body (n=120, no key) while a won-cart ride
    # becomes active during the await. Nothing recorded the cart as suspect before this fix.
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 429, "key": "-", "hop": "-", "n": 120}, lines=[{"id": "L1", "tcin": A, "qty": 1}],
                           on_probe=lambda t: setattr(t.ex, "_won_cart_ride_until", time.time() + 60))
        out = tick(ex)
    check("suspect_harness_cart_still_holds_T_the_delete_was_aborted", A in tab.tcins() and "delete" not in tab.kinds(),
          f"{tab.log} {out[-300:]}")
    check("suspect_harness_marker_exists_and_dummy_ts_is_unset", marker_file_exists()
          and not float(getattr(ex, "_dummy_landed_suspect_ts", 0.0) or 0.0))
    check("suspect_harness_woncart_cart_suspect_names_the_marker", ex._woncart_cart_suspect() == "limkey_marker",
          repr(ex._woncart_cart_suspect()))
    # any TCIN on that account, any state: the loop may be for another TCIN U
    for label, writer in (("late_entry", lambda: pe.limkey_marker_write("primary", B, "x", late=True)),
                          ("in_flight_entry", lambda: pe.limkey_marker_write("primary", B, "in_flight", state="in_flight")),
                          ("plain_entry", lambda: pe.limkey_marker_write("primary", B, "x"))):
        fresh()
        ex, tab, sm = make()
        check(f"suspect_clean_before_{label}", ex._woncart_cart_suspect() == "")
        writer()
        check(f"suspect_{label}_other_tcin_same_account", ex._woncart_cart_suspect() == "limkey_marker")
        ex2, _, _ = make(acct="alt-1")
        check(f"suspect_{label}_other_account_untouched", ex2._woncart_cart_suspect() == "")
        ents = marker_entries()
        ents[0]["ts"] = time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 1
        pe._lk_marker_store(ents)
        pe.limkey_marker_clear("primary", B, owner=True)
        check(f"suspect_clears_with_the_marker_{label}", ex._woncart_cart_suspect() == "" and not marker_file_exists())
    # the in-process fallback (a failed disk write) and an unreadable file count too
    fresh()
    ex, tab, sm = make()
    pe._LIMKEY_FALLBACK[("primary", B)] = {"acct": "primary", "tcin": B, "ts": time.time(), "reason": "x",
                                          "state": "", "pid": None, "late": False}
    check("suspect_fallback_entry", ex._woncart_cart_suspect() == "limkey_marker")
    fresh()
    Path(pe._LIMKEY_MARKER_PATH).write_text("{corrupt", encoding="utf-8")
    check("suspect_unreadable_file_fails_closed", ex._woncart_cart_suspect() == "limkey_marker")
    fresh()
    # the other suspect reasons still win, and no marker file = '' (differential)
    ex, tab, sm = make()
    ex._orphan_atc_ts = time.time()
    pe.limkey_marker_write("primary", B, "x")
    check("suspect_orphan_atc_keeps_its_own_name", ex._woncart_cart_suspect() == "orphan_atc")
    fresh()
    ex, tab, sm = make()
    check("suspect_no_marker_is_empty", ex._woncart_cart_suspect() == "" and not marker_file_exists())
    fresh()


def test_purchase_entry_waits_out_an_open_window():
    T = S.TCIN
    # (2) late marker, window open, line present: deleted, marker KEPT, NOTHING fires
    fresh()
    pe.limkey_marker_write("primary", T, "evaluate_TimeoutError", late=True)
    cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
    ex, c = _impl_ex(cart)
    r = _impl_run(ex)
    check("window_open_late_nothing_fired", "SHOT" not in cart.events and r.get("success") is False
          and r.get("reason") == "limkey_window_open", f"{r} {cart.events}")
    check("window_open_late_line_deleted_marker_kept", not cart.lines and pe.limkey_marker_has("primary", T))
    check("window_open_result_is_digit_free_and_not_terminal",
          not re.search(r"\d", str(r.get("error", ""))) and "1011" not in str(r)
          and not any(t in str(r.get("reason", "")).lower() or t in str(r.get("error", "")).lower()
                      for t in TERMINAL_TOKENS), str(r))
    # in_flight (the probe has not answered): same
    fresh()
    pe.limkey_marker_write("primary", T, "in_flight", state="in_flight")
    cart = SimCart([])
    ex, c = _impl_ex(cart)
    r = _impl_run(ex)
    check("window_open_in_flight_nothing_fired", "SHOT" not in cart.events and r.get("reason") == "limkey_window_open"
          and pe.limkey_marker_entry("primary", T)["state"] == "in_flight", f"{r} {cart.events}")
    # the window passes: the next dispatch purges, clears and the shot goes out
    fresh()
    pe.limkey_marker_write("primary", T, "evaluate_TimeoutError", late=True)
    ents = marker_entries()
    ents[0]["ts"] = time.time() - pe._WONCART_LATE_ADD_WINDOW_S - 1
    pe._lk_marker_store(ents)
    cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
    ex, c = _impl_ex(cart)
    _impl_run(ex)
    check("window_passed_purge_clears_and_the_shot_fires", cart.events[:3] == [f"delete:{T}", "read", "SHOT"]
          and not marker_file_exists(), str(cart.events))
    # window open but the delete cannot be proven: still nothing fires (release_failed, as before)
    fresh()
    pe.limkey_marker_write("primary", T, "x", late=True)
    cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
    cart.fail_delete = True
    ex, c = _impl_ex(cart)
    r = _impl_run(ex)
    check("window_open_unproven_delete_still_fires_nothing", "SHOT" not in cart.events
          and r.get("reason") == "held_cart_release_failed", f"{r} {cart.events}")
    # the held marker of that TCIN is dropped even while the window is open
    fresh()
    pe.limkey_marker_write("primary", T, "x", late=True)
    cart = SimCart([{"id": "CI1", "tcin": T, "qty": 1}])
    ex, c = _impl_ex(cart)
    ex._held_cart = {"tcin": T, "created": c.t, "source": "boot"}
    _impl_run(ex)
    check("window_open_drops_the_held_marker_and_fires_nothing", ex._held_cart is None and "SHOT" not in cart.events)
    # another TCIN / account on an open window is untouched
    for label, marked in (("other_account", ("alt-1", T)), ("other_tcin", ("primary", OTHER))):
        fresh()
        pe.limkey_marker_write(*marked, "x", late=True)
        cart = SimCart([])
        ex, c = _impl_ex(cart)
        _impl_run(ex)
        check(f"window_open_{label}_does_not_block_this_purchase", cart.events[:1] == ["SHOT"], str(cart.events))
    fresh()


def test_other_executors_in_flight_entry_does_not_stop_the_probe():
    # helper semantics
    fresh()
    pe.limkey_marker_write("business", A, "in_flight", state="in_flight")
    check("other_any_ignores_this_process_in_flight", pe.limkey_marker_other_any() is False and pe.limkey_marker_any() is True)
    fresh()
    Path(pe._LIMKEY_MARKER_PATH).write_text(json.dumps([{"acct": "business", "tcin": A, "ts": time.time(), "reason": "in_flight",
                                                          "state": "in_flight", "pid": os.getpid() + 1}]), encoding="utf-8")
    check("other_any_counts_a_previous_process_in_flight", pe.limkey_marker_other_any() is True)
    for label, kw in (("late", dict(late=True)), ("plain", {})):
        fresh()
        pe.limkey_marker_write("business", A, "x", **kw)
        check(f"other_any_counts_a_{label}_entry", pe.limkey_marker_other_any() is True)
    fresh()
    pe.limkey_marker_write("business", A, "in_flight", state="in_flight")
    pe.limkey_marker_write("alt-1", B, "x", late=True)
    check("other_any_counts_a_second_entry_that_is_not_in_flight", pe.limkey_marker_other_any() is True)
    fresh()
    Path(pe._LIMKEY_MARKER_PATH).write_text("{corrupt", encoding="utf-8")
    check("other_any_unreadable_fails_closed", pe.limkey_marker_other_any() is True)
    fresh()
    check("other_any_no_marker_false", pe.limkey_marker_other_any() is False)

    # (3) the evaluator's two-executor harness: account primary's probe is mid-flight when 'business' ticks
    fresh()
    seen = {}
    with env(**{**ENV_ON, "TARGET_WARMUP_PROBE_IDENTS": "primary,business"}):
        exb, tabb, smb = make(acct="business")

        async def mid(t):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                await exb._limkey_probe_tick(0, now=time.time() + 1)
            seen["b_out"] = buf.getvalue()
            seen["stopped_mid"] = pe._LIMKEY_STATE["stopped"]
            seen["file_mid"] = marker_file_exists()
        ex, tab, sm = make(probe={"status": 429, "key": ERR_A2C, "hop": "-", "n": 0}, on_probe_async=mid)
        out = tick(ex)
        stopped_after = pe._LIMKEY_STATE["stopped"]
        out2 = tick(ex, time.time() + 120)
    check("two_exec_business_tick_mid_flight_did_not_stop_the_process", seen.get("stopped_mid") is False, str(seen))
    check("two_exec_business_tick_logged_no_false_marker_present", "marker_present" not in seen.get("b_out", "x")
          and "marker_present" not in out, f"{seen.get('b_out')!r} {out!r}")
    check("two_exec_marker_file_was_down_then_cleared_by_the_proven_non_landing", seen.get("file_mid") is True
          and not marker_file_exists() and stopped_after is False)
    check("two_exec_next_tick_120s_later_still_probes", probes_sent(tab) == 2, f"{tab.kinds()} {out2!r}")
    # a PREVIOUS process's in_flight entry still stops everything (the start latch / tick check)
    fresh()
    Path(pe._LIMKEY_MARKER_PATH).write_text(json.dumps([{"acct": "primary", "tcin": A, "ts": time.time(), "reason": "in_flight",
                                                          "state": "in_flight", "pid": os.getpid() + 1}]), encoding="utf-8")
    with env(**ENV_ON):
        ex, tab, sm = make()
        out = tick(ex)
    check("previous_process_in_flight_still_stops_the_probe", probes_sent(tab) == 0 and "marker_present" in out
          and pe._LIMKEY_STATE["stopped"] is True)
    # this process's landing (a non-in_flight entry) still stops a tick
    fresh()
    pe.limkey_marker_write("alt-1", B, "status_201")
    with env(**ENV_ON):
        ex, tab, sm = make()
        out = tick(ex)
    check("this_process_landed_entry_still_stops_the_probe", probes_sent(tab) == 0 and "marker_present" in out)
    fresh()


def _age(acct, tcin, ts):
    ents = marker_entries()
    for e in ents:
        if e["acct"] == acct and e["tcin"] == tcin:
            e["ts"] = ts
    pe._lk_marker_store(ents)
    return True


def _stale(acct="primary", tcin=A):
    pe.limkey_marker_write(acct, tcin, "status_201", late=True)
    return _age(acct, tcin, time.time() - 3 * 86400)


def test_stale_marker_lifecycle():
    old_ts = time.time() - 3 * 86400
    # (4) boot audit, EMPTY cart: the 3-day-old late marker is cleared (it returned 'empty' with the marker kept before)
    fresh()
    _stale()
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[])
        r, out = _boot(ex, tab)
    check("stale_boot_empty_cart_returns_empty", r == "empty", f"{r} {out}")
    check("stale_boot_empty_cart_clears_the_marker", not marker_file_exists() and "stale probe marker(s) cleared" in out, out)
    # cart with ANOTHER TCIN: still 'held', marker cleared
    fresh()
    _stale()
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[{"id": "CI9", "tcin": OTHER, "qty": 1}])
        r, out = _boot(ex, tab)
    check("stale_boot_other_tcin_cart_still_held_and_marker_cleared", r == "held" and ex._held_cart["tcin"] == OTHER
          and not marker_file_exists(), f"{r} {out}")
    # a marker whose window is still open stays (the POST may still land)
    fresh()
    pe.limkey_marker_write("primary", A, "evaluate_TimeoutError", late=True)
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[])
        r, out = _boot(ex, tab)
    check("stale_boot_open_window_marker_stays", r == "empty" and pe.limkey_marker_has("primary", A))
    # a live in_flight entry of this process stays
    fresh()
    pe.limkey_marker_write("primary", A, "in_flight", state="in_flight")
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[])
        r, out = _boot(ex, tab)
    check("stale_boot_live_in_flight_stays", pe.limkey_marker_entry("primary", A)["state"] == "in_flight")
    # an in_flight entry of a dead process past the window is an orphan: cleared
    fresh()
    Path(pe._LIMKEY_MARKER_PATH).write_text(json.dumps([{"acct": "primary", "tcin": A, "ts": old_ts, "reason": "in_flight",
                                                          "state": "in_flight", "pid": os.getpid() + 1}]), encoding="utf-8")
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[])
        r, out = _boot(ex, tab)
    check("stale_boot_orphan_in_flight_past_the_window_cleared", not marker_file_exists())
    # another account's stale marker is untouched; this account's is cleared
    fresh()
    _stale("alt-1", B)
    _stale("primary", A)
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[])
        _boot(ex, tab)
    check("stale_boot_only_this_accounts_entries_cleared", not pe.limkey_marker_has("primary", A)
          and pe.limkey_marker_has("alt-1", B))
    # a read that carries no cart_items array proves nothing: marker stays
    fresh()
    _stale()
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[], read_mode="no_array")
        r, out = _boot(ex, tab)
    check("stale_boot_read_without_the_array_keeps_the_marker", pe.limkey_marker_has("primary", A), f"{r} {out}")
    # a failed read keeps it too
    fresh()
    _stale()
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[], read_mode="fail")
        r, out = _boot(ex, tab)
    check("stale_boot_failed_read_keeps_the_marker", r == "read_failed" and pe.limkey_marker_has("primary", A))
    # an entry written AFTER the read started is not swept (its line may be invisible to that read)
    fresh()
    _stale()
    t_read = old_ts - 10
    rd = {"ok": True, "status": 200, "items": [], "has_items": True}
    check("sweep_skips_entries_newer_than_the_read", pe.limkey_sweep_stale("primary", rd, t_read) == []
          and pe.limkey_marker_has("primary", A))
    check("sweep_clears_entries_older_than_the_read", pe.limkey_sweep_stale("primary", rd, time.time()) == [A]
          and not marker_file_exists())
    # a 50-line read proves nothing
    fresh()
    _stale()
    big = {"ok": True, "status": 200, "has_items": True,
           "items": [{"id": f"I{i}", "tcin": f"20{i:06d}", "qty": 1} for i in range(50)]}
    check("sweep_50_line_cap_proves_nothing", pe.limkey_sweep_stale("primary", big, time.time()) == []
          and pe.limkey_marker_has("primary", A))
    # the line is still in the cart: the sweep leaves it (the purchase entry / boot delete handle it)
    rd_has = {"ok": True, "status": 200, "has_items": True, "items": [{"id": "L1", "tcin": A, "qty": 1}]}
    check("sweep_keeps_a_marker_whose_line_is_still_there", pe.limkey_sweep_stale("primary", rd_has, time.time()) == []
          and pe.limkey_marker_has("primary", A))
    fresh()
    # no marker: the boot audit prints and reads exactly as before (differential covers adoption)
    with env(**BOOT_ENV):
        ex, tab, sm = make(lines=[])
        r, out = _boot(ex, tab)
    check("stale_boot_no_marker_no_sweep_output", r == "empty" and "stale probe" not in out and tab.kinds() == ["read"],
          str(tab.log))

    # the refill loop's sweep (the boot audit runs once; a late marker written after it needs this)
    fresh()
    _stale()
    with env(**{FLAG: None}):
        ex, tab, sm = make(lines=[])
        lout, _n = arun(L.run_loop(ex))
    check("loop_sweep_clears_a_stale_marker_with_the_probe_off", not marker_file_exists()
          and "stale probe marker(s) cleared" in lout[0], str(lout)[-300:])
    fresh()
    _stale()
    with env(**{FLAG: None}):
        ex, tab, sm = make(lines=[])
        arun(ex._limkey_stale_tick(0))
        reads1 = tab.kinds().count("read")
        _stale()
        arun(ex._limkey_stale_tick(0))                 # inside 300 s: rate limited, no second read
        reads2 = tab.kinds().count("read")
    check("loop_sweep_reads_once_per_5_minutes", reads1 == 1 and reads2 == 1, f"{reads1} {reads2}")
    fresh()
    _stale()
    with env(**{FLAG: None}):
        ex, tab, sm = make(lines=[])
        ex.session_manager.purchase_in_progress = True
        arun(ex._limkey_stale_tick(0))
        busy_reads = tab.kinds().count("read")
        ex.session_manager.purchase_in_progress = False
        arun(ex._limkey_stale_tick(1))
        idx1_reads = tab.kinds().count("read")
    check("loop_sweep_skips_while_a_purchase_owns_the_cart_and_on_other_tabs", busy_reads == 0 and idx1_reads == 0
          and pe.limkey_marker_has("primary", A))
    fresh()
    pe.limkey_marker_write("primary", A, "x", late=True)         # window open: nothing clearable -> no read at all
    with env(**{FLAG: None}):
        ex, tab, sm = make(lines=[])
        arun(ex._limkey_stale_tick(0))
    check("loop_sweep_no_read_when_nothing_is_clearable", tab.kinds() == [] and pe.limkey_marker_has("primary", A))
    fresh()
    with env(**{FLAG: None}):
        ex, tab, sm = make(lines=[])
        arun(ex._limkey_stale_tick(0))
    check("loop_sweep_no_marker_no_read", tab.kinds() == [])
    fresh()
    _stale()
    with env(**{FLAG: None}):
        ex, tab, sm = make(lines=[{"id": "L1", "tcin": A, "qty": 1}])
        arun(ex._limkey_stale_tick(0))
    check("loop_sweep_leaves_a_marker_whose_line_is_present", pe.limkey_marker_has("primary", A) and A in tab.tcins()
          and "delete" not in tab.kinds())
    src = Path(pe.__file__).read_text(encoding="utf-8")
    check("loop_calls_the_sweep_after_the_probe_tick",
          src.index("await self._limkey_probe_tick(idx)") < src.index("await self._limkey_stale_tick(idx)"))
    fresh()


def test_unreadable_hand_made_file_advice():
    # (5) the ALERT prints the exact content the loader accepts; the printed line round-trips
    fresh()
    real = pe._LIMKEY_MARKER_PATH
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "afile"
        f.write_text("x")

        def break_disk(t, _bad=str(f / "sub" / "m.json")):
            pe._LIMKEY_MARKER_PATH = _bad           # the in_flight entry went down; now the disk goes away
        try:
            with env(**ENV_ON):
                ex, tab, sm = make(probe={"status": 201, "key": "-", "hop": "-", "n": 900}, lands=True,
                                   delete_status=500, on_probe=break_disk)
                out = tick(ex)
        finally:
            pe._LIMKEY_MARKER_PATH = real
    check("alert_printed", "ALERT" in out and "NOTHING durable guards" in out, out[-500:])
    check("alert_no_longer_suggests_creating_a_free_form_file", "(or create" not in out)
    m = re.search(r"write EXACTLY this content to \S+: (\[.*?\]) --", out)
    check("alert_prints_the_exact_json_to_write", m is not None, out[-600:])
    if m:
        body = json.loads(m.group(1))
        pe._LIMKEY_FALLBACK.clear()
        Path(real).write_text(m.group(1), encoding="utf-8")
        check("alert_json_loads_as_a_readable_entry", pe._lk_marker_load()[1] == "ok"
              and pe.limkey_marker_has("primary", A) and body[0]["acct"] == "primary" and body[0]["tcin"] == A,
              str(body))
        ex2 = pe.PurchaseExecutor(session_manager=L.FakeSM("primary"))
        check("alert_json_guards_the_buy_path_consumers", pe.limkey_marked_for(ex2, A) is True)
    fresh()


def test_status_zero_with_a_seen_status():
    # (6) the page's fetch threw (status 0) but the interceptor captured the POST's real status
    cases = [
        # (label, interceptor-seen answer, expect_cleanup)
        ("seen_201", {"status": 201, "key": "-", "hop": "-", "n": 0}, True),
        ("seen_500", {"status": 500, "key": "-", "hop": "-", "n": 0}, True),
        ("seen_429_a2c_body_unknown", {"status": 429, "key": ERR_A2C, "hop": "-", "n": 0}, True),
        ("seen_401_keyless", {"status": 401, "key": "-", "hop": "231", "n": 0}, False),
        ("seen_424", {"status": 424, "key": "X", "hop": "-", "n": 0}, False),
    ]
    for label, p, cleanup in cases:
        fresh()
        with env(**ENV_ON):
            ex, tab, sm = make(probe=p, probe_raw=0, lands=(label == "seen_201"))
            out = tick(ex)
        got_cleanup = "event=cleanup" in out
        check(f"status0_{label}_reclassified_in_the_log", f"event=reclassify acct=primary tcin={A} page_status=0 "
              f"seen_status={p['status']}" in out and f"status={p['status']} " in out, out[-400:])
        # seen_500 is late (fix round 4): its marker is kept until the late-add window has passed
        check(f"status0_{label}_cleanup_is_{cleanup}",
              got_cleanup is cleanup and (marker_file_exists() is (label == "seen_500")),
              f"{got_cleanup} {out[-400:]}")
        if label == "seen_201":
            check("status0_seen_201_hits_the_stop_branch_and_deletes_the_line", "event=STOP reason=probe_2xx" in out
                  and A not in tab.tcins() and pe._LIMKEY_STATE["stopped"] is True)
        if not cleanup:
            check(f"status0_{label}_proven_leaves_probing_on", pe._LIMKEY_STATE["stopped"] is False)
    # nothing seen: status 0 is still the page-threw / preflight case = proven, as before
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 0, "key": "-", "hop": "-", "n": 0})
        out = tick(ex)
    check("status0_seen_0_stays_proven", "reclassify" not in out and "event=cleanup" not in out
          and pe._LIMKEY_STATE["stopped"] is False and not marker_file_exists(), out[-300:])
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe={"status": 0, "key": "-", "hop": "-", "n": 0})
        orig = tab.evaluate

        async def quiet(script, await_promise=False):
            r = await orig(script, await_promise)
            if "JSON.stringify({s: resp.status" in script and ex._limkey_wait:
                ex._limkey_wait.clear()         # the interceptor saw nothing
            return r
        tab.evaluate = quiet
        out = tick(ex)
    check("status0_nothing_seen_is_still_proven", "reclassify" not in out and "event=cleanup" not in out
          and pe._LIMKEY_STATE["stopped"] is False and not marker_file_exists(), out[-300:])
    # an evaluate error is not reclassified (err branch is late, as before)
    fresh()
    with env(**ENV_ON):
        ex, tab, sm = make(probe_raises=RuntimeError("x"))
        out = tick(ex)
    check("status0_evaluate_error_unchanged", "reclassify" not in out and marker_entries()[0]["late"] is True)
    fresh()


def test_legacy_checkout_guard_runs_under_a_marker():
    """Fix round 4 (evaluator 2): the whole-cart legacy checkout ran its sink guard only
    `if held_line_strike_on(ident)`. With the strike killed (TARGET_HELD_LINE_FLIP_STRIKE=0) a purchase of
    another TCIN X on the probe account was cart-confirmed and the legacy checkout then bought the whole
    cart, including the marked probe line of T. The guard now also runs while a marker names the account."""
    import test_held_line_strike as H            # import only (its main() does not run)
    T, X = H.ST, H.SO
    added = {"status": 201, "body": "{}", "cart_items": [{"tcin": X, "quantity": 2}]}

    def run_purchase(strike, marker, cart, route):
        fresh()
        if marker:
            pe.limkey_marker_write("primary", T, "status_201")
        c, ex, tab = H._world_ex(H.ATC401_5, [], cart)
        ex.session_manager.account_id = "primary"
        tab.throttled = False
        if route == "legacy201":                  # ATC 201 on the legacy add (fast lane cooling down)
            tab.legacy = [dict(added)]
            ex._fast_selling_until = c.t + 300
        with S.in_tmp_cwd(), S.env(**dict(H.ENV5, TARGET_HELD_LINE_FLIP_STRIKE=strike)), S.fake_time(c):
            r = S.run(ex._execute_purchase_impl(X, quantity=2))
        return r, ex, tab

    for route in ("legacy201", "silent_hold"):
        # armed config reference: strike on + marker -> guard ran (T line gone), as before
        r, ex, tab = run_purchase("1", True, [T, X], route)
        cart = [i["tcin"] for i in tab.cart_items]
        check(f"legacy_guard_{route}_strike_on_marker_removes_the_T_line", "checking_out" in ex.statuses
              and cart == [X], f"r={r} cart={cart} statuses={ex.statuses}")
        # the evaluator's case: strike OFF + marker (primary, T), purchase of X with a T line in the cart
        r, ex, tab = run_purchase("0", True, [T, X], route)
        cart = [i["tcin"] for i in tab.cart_items]
        skipped = "checking_out" not in ex.statuses
        check(f"legacy_guard_{route}_strike_off_marker_T_line_deleted_or_checkout_skipped",
              (T not in cart) or skipped, f"r={r} cart={cart} statuses={ex.statuses}")
        check(f"legacy_guard_{route}_strike_off_marker_deleted_the_T_line_and_checked_out_clean",
              "checking_out" in ex.statuses and cart == [X], f"r={r} cart={cart} statuses={ex.statuses}")
        check(f"legacy_guard_{route}_strike_off_marker_guard_issued_a_delete", bool(ex.deletes), str(ex.deletes))
        # differential: strike OFF, NO marker -> head behaviour (guard does not run, the other line is bought)
        r, ex, tab = run_purchase("0", False, [T, X], route)
        cart = [i["tcin"] for i in tab.cart_items]
        check(f"legacy_guard_{route}_strike_off_no_marker_unchanged", "checking_out" in ex.statuses and T in cart
              and not ex.deletes, f"r={r} cart={cart} deletes={ex.deletes}")
        # a marker for another account does not switch the guard on
        fresh()
        pe.limkey_marker_write("alt-1", T, "status_201")
        c, ex, tab = H._world_ex(H.ATC401_5, [], [T, X])
        ex.session_manager.account_id = "primary"
        tab.throttled = False
        if route == "legacy201":
            tab.legacy = [dict(added)]
            ex._fast_selling_until = c.t + 300
        with S.in_tmp_cwd(), S.env(**dict(H.ENV5, TARGET_HELD_LINE_FLIP_STRIKE="0")), S.fake_time(c):
            S.run(ex._execute_purchase_impl(X, quantity=2))
        check(f"legacy_guard_{route}_other_accounts_marker_does_not_switch_it_on",
              "checking_out" in ex.statuses and not ex.deletes, str(ex.deletes))
    # strike off + marker + the delete cannot be proven: NOTHING is bought
    fresh()
    pe.limkey_marker_write("primary", T, "status_201")
    c, ex, tab = H._world_ex(H.ATC401_5, [], [T, X], fail_delete=True)
    ex.session_manager.account_id = "primary"
    tab.throttled = False
    tab.legacy = [dict(added)]
    ex._fast_selling_until = c.t + 300
    with S.in_tmp_cwd(), S.env(**dict(H.ENV5, TARGET_HELD_LINE_FLIP_STRIKE="0")), S.fake_time(c):
        r = S.run(ex._execute_purchase_impl(X, quantity=2))
    check("legacy_guard_strike_off_marker_delete_fails_no_checkout", "checking_out" not in ex.statuses
          and r.get("reason") == "held_cart_release_failed", f"r={r} statuses={ex.statuses}")
    # source: the call site is gated on the strike OR the marker
    src = Path(pe.__file__).read_text(encoding="utf-8")
    i = src.index("_lg = await self._legacy_checkout_cart_guard(tab, tcin, start_time)")
    check("legacy_guard_call_site_is_gated_on_strike_or_marker",
          "held_line_strike_on(self._ident_tag()) or limkey_marker_acct(self._ident_tag())" in src[i - 400:i])
    fresh()


def test_source_hygiene():
    src = Path(pe.__file__).read_text(encoding="utf-8")
    for name in ("_limkey_busy_why", "_limkey_cleanup", "_limkey_purge_entry", "_limkey_boot_delete",
                 "limkey_marked_for", "limkey_marker_write", "limkey_marker_clear", "limkey_marker_has",
                 "limkey_proven_non_landing", "limkey_marker_entry", "limkey_marker_block", "_lk_clear_block",
                 "_limkey_latch_stop_at_start", "limkey_marker_acct", "limkey_marker_other_any", "limkey_sweep_stale",
                 "limkey_marker_entries_for", "_limkey_sweep_stale_log", "_limkey_stale_tick"):
        m = re.search(rf"def {name}\(.*?(?=\n    (?:async )?def |\n(?:async )?def |\Z)", src, re.S)
        body = m.group(0) if m else ""
        check(f"{name}_is_try_guarded", "try:" in body and "except" in body, body[:120])
    check("tick_cleanup_call_is_inside_the_ticks_outer_guard",
          src.index("await self._limkey_cleanup(tab, acct, tcin") < src.index("async def _background_refill_loop"))
    check("marker_path_is_state_limkey_probe_line_json",
          pe._LIMKEY_MARKER_PATH.replace("\\", "/").endswith("/state/limkey_probe_line.json")
          or pe._LIMKEY_MARKER_PATH.endswith("limkey_probe_line.json"))


def main():
    for fn in (test_classifier, test_marker_io, test_cleanup_after_a_landed_line, test_cleanup_matrix,
               test_late_landing_after_a_timeout, test_stop_latched_at_start, test_in_flight_entry,
               test_proven_non_landing_does_nothing, test_cleanup_failures_keep_the_marker, test_cleanup_never_raises,
               test_guards_reread_after_the_await, test_marker_stops_the_probe_for_the_process, test_check_cart_hold,
               test_strike_inactive_under_a_marker, test_boot_audit_deletes_instead_of_adopting,
               test_purchase_entry, test_differential_against_the_pre_change_executor,
               test_woncart_suspect_while_a_marker_names_the_account, test_purchase_entry_waits_out_an_open_window,
               test_other_executors_in_flight_entry_does_not_stop_the_probe, test_stale_marker_lifecycle,
               test_unreadable_hand_made_file_advice, test_status_zero_with_a_seen_status, test_legacy_checkout_guard_runs_under_a_marker,
               test_source_hygiene):
        try:
            fn()
        except Exception as e:                 # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__}_ran_clean", False, repr(e))
    fresh()
    print(f"\n===== test_limkey_guard_1016: {len(PASSED)} passed, {len(FAILED)} failed =====")
    for f in FAILED:
        print("  FAILED:", f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
