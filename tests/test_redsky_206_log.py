#!/usr/bin/env python3
"""Offline tests: RESILIENT_206_LOG -- log-only summaries of RedSky 206 bodies (2026-09-25).

On the 09-25 drop RedSky answered 102 monitor reads with HTTP 206 in 17 bursts
(02:10-04:49, up to 95% of sweeps; docs/CLAIMS.md C-0925-03). The raw app channel
read every 206 body in full and discarded it -- no 206 body has ever been seen, so
nothing can be designed around one yet. RESILIENT_206_LOG=1 adds ONE rate-limited
[STOCK][206] line; it must never change the result, the session or any counter.

Pins:
  - TabDispatcher._log_206 summarises keys, RedSky's errors, and which requested
    TCINs came back complete / absent / incomplete (complete = availability_status
    AND relationship_type_code present -- the two fields the parser defaults to
    out of stock when missing)
  - shapes: full + errors, some TCINs absent, fulfillment null / {} / empty
    shipping_options, product_summaries null, a non-dict body, an unparsed body
  - rate limit: one line per 60 s, with the count of 206s since the last line
  - _fire_raw_on end to end (stub session, stubbed urllib): the BulkResult and the
    session state are identical with the flag off and on; the line appears only
    with the flag on (mutation: remove the gate and the off-run logs -> red)
  - the gate is `status == 206 and RESILIENT_206_LOG == '1'`; the wrapper arms it once

No browser, no network. Run: venv/Scripts/python.exe tests/test_redsky_206_log.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitoring import tab_dispatcher as td  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {str(detail)[:300]}")


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


CAP = _Cap()
td.logger.addHandler(CAP)
td.logger.setLevel(logging.INFO)

TCINS = [str(1010892000 + i) for i in range(10)]


def _entry(tcin, status="OUT_OF_STOCK", rel="SA", fulfillment="ok"):
    e = {"tcin": tcin, "item": {"relationship_type_code": rel,
                                "product_description": {"title": f"T{tcin}"}}}
    if fulfillment == "ok":
        e["fulfillment"] = {"shipping_options": {"availability_status": status}}
    elif fulfillment == "null":
        e["fulfillment"] = None
    elif fulfillment == "empty":
        e["fulfillment"] = {}
    elif fulfillment == "no_status":
        e["fulfillment"] = {"shipping_options": {}}
    return e


def _disp():
    return td.TabDispatcher(session_pool=SimpleNamespace(harvest_via_local_ip=False), tcins=TCINS)


def _sess():
    return SimpleNamespace(id="s7", proxy_ip="192.0.2.34", busy_lock=asyncio.Lock(), state="ready",
                           in_flight=False, last_request_at=0.0, local_port=24007,
                           consecutive_errors=0, recent_4xx=[], rate_parks=0, raw_ok_seen=True)


def _last206():
    got = [l for l in CAP.lines if l.startswith("[STOCK][206]")]
    return got[-1] if got else ""


def test_full_with_errors():
    CAP.lines.clear()
    d = _disp()
    body = {"data": {"product_summaries": [_entry(t) for t in TCINS]},
            "errors": [{"message": "Downstream timeout", "path": ["product_summaries", 3, "fulfillment"]},
                       {"message": "second"}, {"message": "third"}, {"message": "fourth"}]}
    d._log_206(_sess(), TCINS, body, json.dumps(body))
    ln = _last206()
    check("full_line_emitted", ln != "", CAP.lines)
    check("full_complete_10_of_10", "complete=10/10" in ln, ln)
    check("full_nothing_absent", "absent=[]" in ln and "incomplete=[]" in ln, ln)
    check("full_errors_listed_first3", "Downstream timeout" in ln and "second" in ln and "third" in ln
          and "fourth" not in ln and "(+1 more)" in ln, ln)
    check("full_path_shown", "path=['product_summaries', 3, 'fulfillment']" in ln, ln)
    check("full_keys", "keys=data,errors" in ln, ln)
    check("full_net_masked", "(192.0.x.x)" in ln and "192.0.2.34" not in ln, ln)
    check("full_head_bounded", "head=" in ln and len(ln) < 1600, len(ln))


def test_partial_shapes():
    CAP.lines.clear()
    d = _disp()
    ents = [_entry(t) for t in TCINS[:7]]                 # 3 absent
    ents[1] = _entry(TCINS[1], fulfillment="null")
    ents[2] = _entry(TCINS[2], fulfillment="empty")
    ents[3] = _entry(TCINS[3], fulfillment="no_status")
    ents[4] = _entry(TCINS[4], rel=None)
    ents[4]["item"].pop("relationship_type_code")
    ents.append({"no_tcin": True})                        # skipped, not counted
    body = {"data": {"product_summaries": ents}}
    d._log_206(_sess(), TCINS, body, json.dumps(body))
    ln = _last206()
    check("partial_complete_3_of_10", "complete=3/10" in ln, ln)
    check("partial_absent_named", f"absent={TCINS[7:]}" in ln, ln)
    check("partial_incomplete_named", f"incomplete={[TCINS[1], TCINS[2], TCINS[3], TCINS[4]]}" in ln, ln)
    check("partial_errors_dash", "errors=-" in ln, ln)


def test_odd_bodies():
    for label, body, want in (("ps_null", {"data": {"product_summaries": None}}, "product_summaries=NoneType"),
                              ("data_missing", {"errors": [{"message": "x"}]}, "product_summaries=NoneType"),
                              ("list_body", [1, 2, 3], "body=unparsed"),
                              ("unparsed", None, "body=unparsed"),
                              ("errors_not_list", {"errors": "boom", "data": {}}, "errors=-")):
        CAP.lines.clear()
        d = _disp()
        try:
            d._log_206(_sess(), TCINS, body, "not json" if body is None else json.dumps(body))
            ok = True
        except Exception as e:  # pragma: no cover - the point is it never raises
            ok, want = False, repr(e)
        ln = _last206()
        check(f"odd_{label}_no_raise_and_summary", ok and want in ln, ln or want)


def test_rate_limit_and_count():
    CAP.lines.clear()
    d = _disp()
    body = {"data": {"product_summaries": [_entry(t) for t in TCINS]}}
    d._log_206(_sess(), TCINS, body, "{}")
    first = len([l for l in CAP.lines if l.startswith("[STOCK][206]")])
    for _ in range(4):
        d._log_206(_sess(), TCINS, body, "{}")
    within = len([l for l in CAP.lines if l.startswith("[STOCK][206]")])
    check("rate_one_line_in_60s", first == 1 and within == 1, (first, within))
    check("rate_first_line_counts_1", "1 partial-content read(s)" in _last206(), _last206())
    d._last_206_log_at = time.time() - 61.0
    d._log_206(_sess(), TCINS, body, "{}")
    check("rate_next_line_counts_all_5", "5 partial-content read(s)" in _last206(), _last206())


def _run_fire(flag_on, status=206):
    body = {"data": {"product_summaries": [_entry(t) for t in TCINS[:8]]},
            "errors": [{"message": "partial"}]}
    text = json.dumps(body)
    d = _disp()
    s = _sess()
    orig = td.TabDispatcher._raw_apps_get
    td.TabDispatcher._raw_apps_get = staticmethod(lambda url, headers, proxy, timeout_s: (status, text))
    old_env = os.environ.get("RESILIENT_206_LOG")
    if flag_on:
        os.environ["RESILIENT_206_LOG"] = "1"
    else:
        os.environ.pop("RESILIENT_206_LOG", None)
    CAP.lines.clear()
    try:
        res = asyncio.run(d._fire_raw_on(s, TCINS))
    finally:
        td.TabDispatcher._raw_apps_get = orig
        if old_env is None:
            os.environ.pop("RESILIENT_206_LOG", None)
        else:
            os.environ["RESILIENT_206_LOG"] = old_env
    snap = dict(status=res.http_status, error=res.error, raw=res.raw, sid=res.session_id,
                ip=res.pinned_ip if hasattr(res, "pinned_ip") else None,
                ce=s.consecutive_errors, r4=list(s.recent_4xx), rp=s.rate_parks,
                ok_seen=s.raw_ok_seen, in_flight=s.in_flight,
                state=s.state)
    lines = [l for l in CAP.lines if l.startswith("[STOCK][206]")]
    return snap, lines


def test_fire_raw_on_off_vs_on_identical():
    off, off_lines = _run_fire(False)
    on, on_lines = _run_fire(True)
    check("fire_off_no_206_line", off_lines == [], off_lines)
    check("fire_on_206_line", len(on_lines) == 1 and "complete=8/10" in on_lines[0]
          and f"absent={TCINS[8:]}" in on_lines[0] and "partial" in on_lines[0], on_lines)
    check("fire_result_and_session_identical", off == on, (off, on))
    check("fire_still_counts_as_failure", off["status"] == 206 and off["raw"] is None and off["ce"] == 1, off)
    # a 200 never reaches the 206 branch, flag on or off
    ok200, lines200 = _run_fire(True, status=200)
    check("fire_200_untouched", lines200 == [] and ok200["status"] == 200 and ok200["raw"] is not None, ok200)


def test_gate_and_wrapper():
    src = (ROOT / "src" / "monitoring" / "tab_dispatcher.py").read_text(encoding="utf-8")
    check("gate_exact", "elif status == 206 and os.environ.get('RESILIENT_206_LOG', '0') == '1':" in src)
    check("gate_calls_logger_only", "self._log_206(s, tcins, body, text)" in src)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("ascii", "replace").split("\r\n")
    check("bat_armed_once", bat.count("set RESILIENT_206_LOG=1") == 1,
          [l for l in bat if "RESILIENT_206_LOG" in l])


if __name__ == "__main__":
    test_full_with_errors()
    test_partial_shapes()
    test_odd_bodies()
    test_rate_limit_and_count()
    test_fire_raw_on_off_vs_on_identical()
    test_gate_and_wrapper()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
