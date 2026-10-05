#!/usr/bin/env python3
"""Offline tests: RESILIENT_STATUS_LOG and RESILIENT_206_INGEST=shadow (2026-10-05 post-run).

10-05 (run_20261005_002518, wf_9651193b-efc): no TCIN was ever read in stock, the binder
1010892074 had ONE status line (02:00:08 OUT_OF_STOCK) so "did it go sellable" was
undecidable, and 02:00-03:34 RedSky answered 73.3% of sweeps with HTTP 206 whose bodies
held the stock fields for 17/17 TCINs -- and the sweep discarded every one. Both changes
here are LOG-ONLY and default off.

Pins:
  INS-STATUS-LOG
  - stock_monitor._process_response: flag off = no sd_* keys; flag on = the same core
    keys and values plus exactly sd_rtc / sd_services / sd_loyalty / sd_oos_reason
  - sd_oos_reason mirrors the parser verdict: no_services, atp_zero, not_target_direct,
    loyalty_only, combined reasons, '' for a real in-stock and for a plain OOS
  - _ingest_bulk_response on a real checker: callbacks and stock state identical off vs
    on; [STOCK STATUS] first-sighting + change lines only with the flag on, never on an
    unchanged key, written AFTER every on_in_stock callback; per-TCIN cap; the
    SELLABLE-PARSED-OOS rate limit (new reason / 300 s) and cap
  FS-206-SHADOW
  - shadow_filter_206: store_positions-only errors keep every complete summary; a
    fulfillment / item error drops that index; an error with no attributable path
    disqualifies the body; incomplete summaries and non-dict bodies are skipped
  - _fire_raw_on: partial_raw is set only for a 206 with the flag on; the BulkResult's
    other fields and the session are identical off vs on (and independent of
    RESILIENT_206_LOG)
  - _dispatch_one: _shadow_206 runs only when partial_raw is set; counters identical
  - _shadow_206: pairs only with a 200-sourced read <= 2 s old, counts agree/disagree,
    never mutates _tcin_status, never calls on_in_stock; one line per 60 s, counters reset
  - the wrapper arms each flag exactly once

No browser, no network. Run: venv/Scripts/python.exe tests/test_status_log_and_206_shadow.py
"""
from __future__ import annotations

import asyncio
import copy
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

from src.monitoring import stock_monitor as sm                     # noqa: E402
from src.monitoring import stock_check_resilient as sr             # noqa: E402
from src.monitoring import tab_dispatcher as td                    # noqa: E402
from src.monitoring.stock_check_resilient import ResilientStockChecker  # noqa: E402

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


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


CAP = _Cap()
for _lg in (sr.logger, td.logger):
    _lg.addHandler(CAP)
    _lg.setLevel(logging.INFO)

FLAGS = ("RESILIENT_STATUS_LOG", "RESILIENT_206_INGEST", "RESILIENT_206_LOG")


class _Env:
    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in FLAGS}
        for k in FLAGS:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in self.kv.items() if v is not None})

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _entry(tcin, status="OUT_OF_STOCK", rel="SA", svc=1, loy=None, atp=5, title=None):
    so = {"availability_status": status, "available_to_promise_quantity": atp}
    if svc is not None:
        so["services"] = [{"shipping_method_id": "STANDARD"}] * svc
    if loy:
        so["loyalty_availability_status"] = loy
    return {"tcin": tcin,
            "item": {"relationship_type_code": rel,
                     "product_description": {"title": title or f"T{tcin}"}},
            "fulfillment": {"shipping_options": so}}


def _body(*ents, errors=None):
    b = {"data": {"product_summaries": list(ents)}}
    if errors is not None:
        b["errors"] = errors
    return b


def _strip(d):
    return {t: {k: v for k, v in i.items() if k != "last_checked"} for t, i in d.items()}


SD_KEYS = {"sd_rtc", "sd_services", "sd_loyalty", "sd_oos_reason"}


# ─────────────────────────── INS-STATUS-LOG: the parser ───────────────────────────
def test_parser_keys_off_vs_on():
    body = _body(_entry("1", "PRE_ORDER_SELLABLE", svc=0), _entry("2", "IN_STOCK"),
                 _entry("3", "OUT_OF_STOCK", loy="PRE_ORDER_SELLABLE"),
                 _entry("4", "IN_STOCK", rel="VPC"), _entry("5", "IN_STOCK", atp=0),
                 _entry("6", "PRE_ORDER_SELLABLE", svc=0, rel="VPC"), _entry("7"),
                 _entry("8", "PRE_ORDER_SELLABLE", svc=None))
    m = sm.StockMonitor()
    with _Env():
        off = m._process_response(copy.deepcopy(body), 0)
    with _Env(RESILIENT_STATUS_LOG="1"):
        on = m._process_response(copy.deepcopy(body), 0)
    check("parser_off_no_sd_keys", not any(SD_KEYS & set(i) for i in off.values()), off)
    core_same = all({k: v for k, v in on[t].items() if k not in SD_KEYS and k != "last_checked"}
                    == {k: v for k, v in off[t].items() if k != "last_checked"} for t in off)
    check("parser_on_core_identical", core_same and set(on) == set(off))
    check("parser_on_exactly_4_new_keys",
          all(set(on[t]) - set(off[t]) == SD_KEYS for t in on), {t: set(on[t]) - set(off[t]) for t in on})
    want = {"1": "no_services", "2": "", "3": "loyalty_only", "4": "not_target_direct",
            "5": "atp_zero", "6": "no_services+not_target_direct", "7": "", "8": "no_services"}
    got = {t: on[t]["sd_oos_reason"] for t in on}
    check("parser_reasons", got == want, got)
    check("parser_in_stock_unchanged", [on[t]["in_stock"] for t in sorted(on)]
          == [off[t]["in_stock"] for t in sorted(off)] and on["2"]["in_stock"] is True)
    # a missing services key reaches the helper as the parser's own default [] -> 0 (the
    # verdict treats it exactly like an empty list); -1 is only a non-list (odd-inputs test)
    check("parser_services_counts", on["8"]["sd_services"] == 0 and on["1"]["sd_services"] == 0
          and on["2"]["sd_services"] == 1, (on["8"], on["1"]))
    check("parser_loyalty_and_rtc", on["3"]["sd_loyalty"] == "PRE_ORDER_SELLABLE" and on["4"]["sd_rtc"] == "VPC")


def test_status_fields_odd_inputs():
    try:
        r = sm.redsky_status_fields(None, None, None, False, False, -1, "notalist")
        ok = r == {"sd_rtc": "", "sd_services": -1, "sd_loyalty": "", "sd_oos_reason": ""}
    except Exception as e:      # pragma: no cover
        ok, r = False, repr(e)
    check("fields_none_inputs", ok, r)
    r = sm.redsky_status_fields({"relationship_type_code": "SA"}, {}, "IN_STOCK", True, True, 3, [1])
    check("fields_real_in_stock_no_reason", r["sd_oos_reason"] == "", r)
    r = sm.redsky_status_fields({"relationship_type_code": "SA"}, {}, "IN_STOCK", False, True, 3, [1])
    check("fields_unexplained_is_other", r["sd_oos_reason"] == "other", r)


# ─────────────────────── INS-STATUS-LOG: the sweep ingest ──────────────────────────
A, B, C = "1010892074", "1010892076", "1012422107"


def _res(raw, sid="s5"):
    return td.BulkResult(sid, "192.0.2.5", 200, 120, raw=raw)


def _status_lines():
    return [l for l in CAP.lines if l.startswith("[STOCK STATUS]")]


def _selloos_lines():
    return [l for l in CAP.lines if l.startswith("[STOCK] SELLABLE-PARSED-OOS")]


def _run_ingest(flag_on):
    events = []
    CAP.lines.clear()
    with _Env(RESILIENT_STATUS_LOG="1" if flag_on else None), tempfile.TemporaryDirectory() as tmp:

        class _H(logging.Handler):
            def emit(self, rec):
                msg = rec.getMessage()
                if msg.startswith("[STOCK STATUS]") or msg.startswith("[STOCK] SELLABLE"):
                    events.append(("log", msg.split()[1]))

        h = _H()
        sr.logger.addHandler(h)
        try:
            chk = ResilientStockChecker(
                proxy_urls=[], tcins=[A, B, C], state_dir=Path(tmp),
                on_in_stock=lambda s: events.append(("cb", s.tcin, s.in_stock, s.availability_status)),
                log_per_request=False, preflight=False)
            reads = [
                _body(_entry(B), _entry(C)),                                  # A unpublished
                _body(_entry(A), _entry(B), _entry(C)),                       # A first sighting
                _body(_entry(A), _entry(B), _entry(C)),                       # nothing changed
                _body(_entry(A, "PRE_ORDER_SELLABLE", svc=0), _entry(B), _entry(C)),   # sellable, parsed OOS
                _body(_entry(A, "PRE_ORDER_SELLABLE"), _entry(B, "IN_STOCK"), _entry(C)),  # both in stock
            ]
            for raw in reads:
                asyncio.run(chk._ingest_bulk_response(_res(raw)))
            state = {t: (st.in_stock, st.availability_status, st.max_qty, st.last_status_code)
                     for t, st in chk._tcin_status.items()}
            state["ever"] = sorted(chk._ever_seen_in_stock)
            return events, state, list(_status_lines()), list(_selloos_lines()), chk
        finally:
            sr.logger.removeHandler(h)


def test_ingest_off_vs_on():
    ev_off, st_off, sl_off, so_off, _ = _run_ingest(False)
    ev_on, st_on, sl_on, so_on, chk = _run_ingest(True)
    check("ingest_off_no_lines", sl_off == [] and so_off == [], (sl_off, so_off))
    check("ingest_state_identical", st_off == st_on, (st_off, st_on))
    check("ingest_callbacks_identical", [e for e in ev_off if e[0] == "cb"] == [e for e in ev_on if e[0] == "cb"],
          (ev_off, ev_on))
    # first sightings: B,C on read 1; A on read 2; A changes on 4 and 5; B changes on 5
    by_tcin = {t: [l for l in sl_on if f"tcin={t} " in l] for t in (A, B, C)}
    check("ingest_line_counts", [len(by_tcin[A]), len(by_tcin[B]), len(by_tcin[C])] == [3, 2, 1],
          {t: len(v) for t, v in by_tcin.items()})
    check("ingest_first_sighting_form", "#1 old=(first) new=OUT_OF_STOCK|SA|svc1|-" in by_tcin[A][0], by_tcin[A])
    check("ingest_change_form", "old=OUT_OF_STOCK|SA|svc1|- new=PRE_ORDER_SELLABLE|SA|svc0|-" in by_tcin[A][1]
          and "reason=no_services" in by_tcin[A][1] and "in_stock=0" in by_tcin[A][1], by_tcin[A])
    check("ingest_back_in_stock_line", "new=PRE_ORDER_SELLABLE|SA|svc1|-" in by_tcin[A][2]
          and "in_stock=1" in by_tcin[A][2], by_tcin[A])
    check("ingest_selloos_once", len(so_on) == 1 and f"tcin={A} " in so_on[0] and "reason=no_services" in so_on[0],
          so_on)
    # the last read flips A and B: both callbacks fire BEFORE its two status lines
    check("ingest_logs_after_callbacks", [e[0] for e in ev_on[-4:]] == ["cb", "cb", "log", "log"]
          and [e[0] for e in ev_off] == ["cb", "cb"], (ev_on[-4:], ev_off))


def test_status_caps_and_rate_limit():
    with tempfile.TemporaryDirectory() as tmp:
        chk = ResilientStockChecker(proxy_urls=[], tcins=[A], state_dir=Path(tmp),
                                    log_per_request=False, preflight=False)
        CAP.lines.clear()
        res = _res({})
        for i in range(sr.STATUS_LOG_CAP_PER_TCIN + 5):
            info = {"availability_status": "OUT_OF_STOCK" if i % 2 else "IN_STOCK", "sd_rtc": "SA",
                    "sd_services": 1, "sd_loyalty": "", "sd_oos_reason": "", "in_stock": False}
            chk._log_status_changes({A: info}, res)
        sl = _status_lines()
        check("cap_status_lines", len(sl) == sr.STATUS_LOG_CAP_PER_TCIN + 1
              and "changes logged this run" in sl[-1], (len(sl), sl[-1:]))
        # no sd_* keys (flag-off parse) -> nothing
        CAP.lines.clear()
        chk._log_status_changes({A: {"availability_status": "IN_STOCK", "in_stock": True}}, res)
        check("no_sd_keys_no_line", _status_lines() == [], CAP.lines)
        # SELLABLE-PARSED-OOS: new reason -> line; same reason inside 300 s -> none
        CAP.lines.clear()
        base = {"availability_status": "PRE_ORDER_SELLABLE", "sd_rtc": "SA", "sd_services": 0,
                "sd_loyalty": "", "sd_oos_reason": "no_services", "in_stock": False}
        chk._selloos_last.clear()
        chk._selloos_n.clear()
        chk._log_status_changes({A: dict(base)}, res)
        chk._log_status_changes({A: dict(base)}, res)
        n1 = len(_selloos_lines())
        chk._log_status_changes({A: dict(base, sd_oos_reason="not_target_direct")}, res)
        n2 = len(_selloos_lines())
        chk._selloos_last[A] = (chk._selloos_last[A][0], time.time() - sr.SELLOOS_REPEAT_S - 1)
        chk._log_status_changes({A: dict(base, sd_oos_reason="not_target_direct")}, res)
        n3 = len(_selloos_lines())
        check("selloos_rate_limit", (n1, n2, n3) == (1, 2, 3), (n1, n2, n3))
        for _ in range(sr.SELLOOS_CAP_PER_TCIN + 10):
            chk._selloos_last[A] = ("x", 0.0)
            chk._log_status_changes({A: dict(base)}, res)
        check("selloos_cap", len(_selloos_lines()) == sr.SELLOOS_CAP_PER_TCIN, len(_selloos_lines()))


# ───────────────────────────── FS-206-SHADOW ──────────────────────────────────────
def test_shadow_filter():
    ents = [_entry(str(i)) for i in range(5)]
    sp = [{"message": "Exception while fetching data", "path": ["product_summaries", i, "store_positions"]}
          for i in range(5)]
    kept, segs, ok = sr.shadow_filter_206(_body(*ents, errors=sp))
    check("filter_store_positions_keeps_all", ok and [p["tcin"] for p in kept] == ["0", "1", "2", "3", "4"]
          and segs == {"store_positions": 5}, (kept, segs))
    errs = sp + [{"message": "x", "path": ["product_summaries", 1, "fulfillment", "shipping_options"]},
                 {"message": "y", "path": ["product_summaries", 3, "item"]}]
    kept, segs, ok = sr.shadow_filter_206(_body(*ents, errors=errs))
    check("filter_drops_fulfillment_and_item_idx", [p["tcin"] for p in kept] == ["0", "2", "4"]
          and segs.get("fulfillment") == 1 and segs.get("item") == 1, (kept, segs))
    for label, e in (("no_path", {"message": "z"}), ("short_path", {"path": ["product_summaries", 1]}),
                     ("other_root", {"path": ["something", 1, "store_positions"]}),
                     ("bool_index", {"path": ["product_summaries", True, "store_positions"]}),
                     ("str_error", "boom")):
        kept, segs, ok = sr.shadow_filter_206(_body(*ents, errors=[e]))
        check(f"filter_unattributable_{label}_disqualifies", kept == [] and ok is False, (kept, segs))
    inc = [_entry("0"), {"tcin": "1", "item": {"relationship_type_code": "SA"}, "fulfillment": None},
           {"tcin": "2", "fulfillment": {"shipping_options": {"availability_status": "IN_STOCK"}}}, {"no": 1}]
    kept, segs, ok = sr.shadow_filter_206(_body(*inc))
    check("filter_skips_incomplete", [p["tcin"] for p in kept] == ["0"] and ok, kept)
    for label, b in (("none", None), ("list", [1]), ("ps_null", {"data": {"product_summaries": None}})):
        check(f"filter_odd_{label}", sr.shadow_filter_206(b)[0] == [] and sr.shadow_filter_206(b)[2] is False)


def _sess():
    return SimpleNamespace(id="s7", proxy_ip="192.0.2.34", busy_lock=asyncio.Lock(), state="ready",
                           in_flight=False, last_request_at=0.0, local_port=24007,
                           consecutive_errors=0, recent_4xx=[], rate_parks=0, raw_ok_seen=True)


def _fire(status, env, text=None):
    body = _body(_entry(A), _entry(B), errors=[{"message": "e", "path": ["product_summaries", 0, "store_positions"]}])
    text = json.dumps(body) if text is None else text
    d = td.TabDispatcher(session_pool=SimpleNamespace(harvest_via_local_ip=False), tcins=[A, B])
    s = _sess()
    orig = td.TabDispatcher._raw_apps_get
    td.TabDispatcher._raw_apps_get = staticmethod(lambda url, headers, proxy, timeout_s: (status, text))
    try:
        with _Env(**env):
            res = asyncio.run(d._fire_raw_on(s, [A, B]))
    finally:
        td.TabDispatcher._raw_apps_get = orig
    snap = dict(status=res.http_status, error=res.error, raw=res.raw, sid=res.session_id, ip=res.pinned_ip,
                ce=s.consecutive_errors, r4=list(s.recent_4xx), rp=s.rate_parks, ok=s.raw_ok_seen,
                fl=s.in_flight, st=s.state)
    return snap, res.partial_raw, body


def test_fire_raw_on_partial_raw():
    off, pr_off, _ = _fire(206, {})
    on, pr_on, body = _fire(206, {"RESILIENT_206_INGEST": "shadow"})
    on_log, pr_on_log, _ = _fire(206, {"RESILIENT_206_INGEST": "shadow", "RESILIENT_206_LOG": "1"})
    check("fire_off_partial_raw_none", pr_off is None)
    check("fire_on_partial_raw_is_body", pr_on == body and pr_on_log == body)
    check("fire_rest_identical", off == on == on_log, (off, on, on_log))
    _, pr_one, _ = _fire(206, {"RESILIENT_206_INGEST": "1"})
    check("fire_value_1_is_off", pr_one is None)
    ok200, pr200, _ = _fire(200, {"RESILIENT_206_INGEST": "shadow"})
    check("fire_200_no_partial_raw", pr200 is None and ok200["raw"] is not None)
    _, pr_bad, _ = _fire(206, {"RESILIENT_206_INGEST": "shadow"}, text="not json")
    check("fire_unparsed_body_no_partial_raw", pr_bad is None)


def _dispatch_stub(result):
    calls = []

    async def _sweep():
        return result

    stub = SimpleNamespace(
        dispatcher=SimpleNamespace(dispatch_one_sweep=_sweep), _total_dispatched=0, _total_200=0,
        _total_403=0, _total_429=0, _total_other=0, _split_429=True, _outstanding=1,
        _note_exit=lambda ip, st: calls.append(("exit", st)),
        proxy_state=SimpleNamespace(record_status=lambda ip, st: calls.append(("rec", st))),
        log_per_request=False, _shadow_206=lambda r: calls.append(("shadow", r.http_status)))
    asyncio.run(ResilientStockChecker._dispatch_one(stub, False))
    return calls, (stub._total_dispatched, stub._total_other, stub._total_429, stub._outstanding)


def test_dispatch_hook():
    r_off = td.BulkResult("s1", "192.0.2.1", 206, 90, error="partial")
    r_on = td.BulkResult("s1", "192.0.2.1", 206, 90, error="partial", partial_raw={"data": {}})
    c_off, n_off = _dispatch_stub(r_off)
    c_on, n_on = _dispatch_stub(r_on)
    check("dispatch_off_no_shadow", ("shadow", 206) not in c_off, c_off)
    check("dispatch_on_shadow_once", c_on.count(("shadow", 206)) == 1, c_on)
    check("dispatch_counters_identical", n_off == n_on and [c for c in c_on if c[0] != "shadow"] == c_off,
          (n_off, n_on, c_off, c_on))
    c429, _ = _dispatch_stub(td.BulkResult("s1", "192.0.2.1", 429, 90, partial_raw={"x": 1}))
    check("dispatch_non206_no_shadow", ("shadow", 429) not in c429, c429)


def test_shadow_compare():
    with tempfile.TemporaryDirectory() as tmp:
        cbs = []
        chk = ResilientStockChecker(proxy_urls=[], tcins=[A, B, C], state_dir=Path(tmp),
                                    on_in_stock=lambda s: cbs.append(s.tcin),
                                    log_per_request=False, preflight=False)
        now = time.time()
        sa, sb, sc = chk._tcin_status[A], chk._tcin_status[B], chk._tcin_status[C]
        sa.last_status_code, sa.last_checked_at, sa.in_stock = 200, now - 0.5, False   # paired, agrees
        sb.last_status_code, sb.last_checked_at, sb.in_stock = 200, now - 0.5, False   # paired, DISAGREES
        sc.last_status_code, sc.last_checked_at, sc.in_stock = 200, now - 5.0, False   # too old: unpaired
        before = {t: dict(vars(s)) for t, s in chk._tcin_status.items()}
        body = _body(_entry(A), _entry(B, "IN_STOCK"), _entry(C, "IN_STOCK"),
                     errors=[{"message": "e", "path": ["product_summaries", i, "store_positions"]} for i in range(3)])
        CAP.lines.clear()
        chk._shadow206_log_at = time.time()
        chk._shadow_206(td.BulkResult("s2", "192.0.2.2", 206, 80, partial_raw=body))
        st = dict(chk._shadow206)
        check("shadow_counts", (st["bodies"], st["qualifying"], st["reads"], st["paired"], st["agree"],
                                st["disagree"]) == (1, 1, 3, 2, 1, 1), st)
        check("shadow_in206", st["in206"] == {B, C}, st["in206"])
        check("shadow_first_disagree", st["first"] and f"tcin={B} 206=1 200=0" in st["first"], st["first"])
        check("shadow_no_line_inside_60s", not [l for l in CAP.lines if "[STOCK][206-SHADOW]" in l])
        check("shadow_never_mutates_status", {t: dict(vars(s)) for t, s in chk._tcin_status.items()} == before)
        check("shadow_never_calls_on_in_stock", cbs == [] and chk._ever_seen_in_stock == set())
        # a body with an unattributable error: counted, not parsed
        chk._shadow_206(td.BulkResult("s2", "192.0.2.2", 206, 80, partial_raw=_body(_entry(A), errors=[{"m": 1}])))
        chk._shadow206_log_at = time.time() - 61.0
        chk._shadow_206(td.BulkResult("s2", "192.0.2.2", 206, 80, partial_raw={"data": {"product_summaries": []}}))
        lines = [l for l in CAP.lines if l.startswith("[STOCK][206-SHADOW]")]
        check("shadow_line_after_60s", len(lines) == 1 and "bodies=3 qualifying=1 tcin_reads=3 paired=2 agree=1 "
              "disagree=1" in lines[0] and f"in_stock_206=['{B}', '{C}']" in lines[0]
              and "err_segs=store_positions:3,?:1" in lines[0], lines)
        check("shadow_counters_reset", chk._shadow206 is None)
        # never raises on garbage
        try:
            chk._shadow_206(SimpleNamespace(partial_raw=object()))
            ok = True
        except Exception:      # pragma: no cover
            ok = False
        check("shadow_never_raises", ok)


def test_wrapper_and_gates():
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("ascii", "replace").split("\r\n")
    check("bat_status_log_armed_once", bat.count("set RESILIENT_STATUS_LOG=1") == 1,
          [l for l in bat if "RESILIENT_STATUS_LOG" in l and not l.startswith("REM")])
    check("bat_206_shadow_armed_once", bat.count("set RESILIENT_206_INGEST=shadow") == 1,
          [l for l in bat if "RESILIENT_206_INGEST" in l and not l.startswith("REM")])
    src = (ROOT / "src" / "monitoring" / "tab_dispatcher.py").read_text(encoding="utf-8")
    check("gate_partial_raw", "if status == 206 and isinstance(body, dict) and shadow_206_on():" in src)
    check("gate_206_log_unchanged",
          "elif status == 206 and os.environ.get('RESILIENT_206_LOG', '0') == '1':" in src)


if __name__ == "__main__":
    test_parser_keys_off_vs_on()
    test_status_fields_odd_inputs()
    test_ingest_off_vs_on()
    test_status_caps_and_rate_limit()
    test_shadow_filter()
    test_fire_raw_on_partial_raw()
    test_dispatch_hook()
    test_shadow_compare()
    test_wrapper_and_gates()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
