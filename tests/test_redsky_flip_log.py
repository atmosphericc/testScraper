#!/usr/bin/env python3
"""Offline tests: RESILIENT_FLIP_LOG -- a millisecond [STOCK][FLIP] line per flip (2026-09-25).

Every edge admission since September was a first-volley shot (09-25 admission forensics:
hot first volley 29/207 vs 59/6,943 for every later shot, all history), yet the in-stock
read that starts a volley had only a print-only '[STOCK] IN STOCK' line and a whole-second
[API_CYCLE] stamp. Whether faster detection buys admissions could not be measured, and
RedSky's raw ATP / purchase-limit values were never seen: the parser keeps only ints, and
RedSky sends floats. RESILIENT_FLIP_LOG=1 adds ONE line per out->in transition.

Pins:
  - flip_raw_fields: raw reprs (a float stays 2.0), '-' for a missing field, the keys
    present, tcin absent, odd bodies; never raises
  - flip_line: read_ms / last_oos_ms / since_oos_ms arithmetic, new_window, the exit
    masked to x.x, never raises on a bad stamp
  - _ingest_bulk_response end to end on a real ResilientStockChecker: the callbacks and
    the resulting stock state are identical with the flag off and on; the line appears
    only with the flag on (mutation: drop the gate -> red), one per transition, never on
    a still-in-stock read, new_window=0 on a flicker inside the hysteresis, and every
    line is written AFTER every on_in_stock callback of that read (mutation: log before
    the callbacks -> red)
  - cap: FLIP_LOG_CAP_PER_TCIN lines per TCIN per run, then one suppression line
  - the gate is read once at construction; the wrapper arms it exactly once

No browser, no network. Run: venv/Scripts/python.exe tests/test_redsky_flip_log.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitoring import stock_check_resilient as sr  # noqa: E402
from src.monitoring.stock_check_resilient import ResilientStockChecker  # noqa: E402
from src.monitoring.tab_dispatcher import BulkResult  # noqa: E402

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


EVENTS = []


class _Cap(logging.Handler):
    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("[STOCK][FLIP]"):
            m = re.search(r"tcin=([^\s:]+)", msg)
            EVENTS.append(("log", m.group(1) if m else "?", msg))


sr.logger.addHandler(_Cap())
sr.logger.setLevel(logging.INFO)

A, B, C = "1012644665", "1012644666", "1010892076"


def _entry(tcin, status="OUT_OF_STOCK", atp=0, mq="absent", pl="absent", ful="ok"):
    so = {"availability_status": status, "available_to_promise_quantity": atp,
          "services": [{"shipping_method_id": "STANDARD"}] if status == "IN_STOCK" else []}
    e = {"tcin": tcin, "item": {"product_description": {"title": f"T{tcin}"},
                                "relationship_type_code": "SA"}}
    if ful == "ok":
        f = {"shipping_options": so}
        if mq != "absent":
            f["maximum_order_quantity"] = mq
        if pl != "absent":
            f["purchase_limit"] = pl
        e["fulfillment"] = f
    elif ful == "null":
        e["fulfillment"] = None
    return e


def _body(*entries):
    return {"data": {"product_summaries": list(entries)}}


def _res(raw, sid="s3", ip="192.0.2.34", rt=412):
    return BulkResult(session_id=sid, pinned_ip=ip, http_status=200, latency_ms=rt, raw=raw, error=None)


def _flips():
    return [e for e in EVENTS if e[0] == "log"]


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_raw_fields():
    got = sr.flip_raw_fields(_body(_entry(A, "IN_STOCK", 12.0, mq={"shipping": {"value": 2.0}})), A)
    check("raw_float_atp_kept", "atp=12.0 " in got, got)
    check("raw_nested_float_limit_kept", "max_order_qty={'shipping': {'value': 2.0}} " in got, got)
    check("raw_missing_is_dash", "purchase_limit=- " in got, got)
    check("raw_services_counted", "services=1 " in got, got)
    check("raw_keys_listed", "ship_keys=availability_status,available_to_promise_quantity,services "
          in got and got.endswith("ful_keys=maximum_order_quantity,shipping_options"), got)
    got = sr.flip_raw_fields(_body(_entry(A, "IN_STOCK", 3, pl=1)), A)
    check("raw_int_limit_kept", "purchase_limit=1 " in got and "atp=3 " in got, got)
    check("raw_tcin_absent", sr.flip_raw_fields(_body(_entry(B)), A) == "raw=tcin_absent")
    check("raw_ps_none", sr.flip_raw_fields({"data": {"product_summaries": None}}, A)
          == "raw=product_summaries:NoneType")
    check("raw_body_none", sr.flip_raw_fields(None, A) == "raw=product_summaries:NoneType")
    check("raw_body_string", sr.flip_raw_fields("oops", A) == "raw=product_summaries:NoneType")
    got = sr.flip_raw_fields(_body(_entry(A, ful="null")), A)
    check("raw_fulfillment_null", "atp=- " in got and "ship_keys=- " in got and got.endswith("ful_keys=-"), got)
    got = sr.flip_raw_fields(_body(_entry(A, "IN_STOCK", 5, pl={"k": "x" * 500})), A)
    check("raw_long_repr_truncated", "..." in got and len(got) < 900, len(got))


def test_line():
    ln = sr.flip_line(A, 1, 1790325167.8144, 1790325167.5, True, "IN_STOCK",
                      _body(_entry(A, "IN_STOCK", 12.0)), 412, "s3", "192.0.2.34")
    check("line_prefix", ln.startswith(f"[STOCK][FLIP] tcin={A} #1 "), ln)
    check("line_read_ms", "read_ms=1790325167814 " in ln, ln)
    check("line_oos_and_gap", "last_oos_ms=1790325167500 since_oos_ms=314 " in ln, ln)
    check("line_rt_and_window", "rt_ms=412 " in ln and "new_window=1 " in ln, ln)
    check("line_net_masked", "via=s3 (192.0.x.x)" in ln and "192.0.2.34" not in ln, ln)
    check("line_raw_fields", "status=IN_STOCK atp=12.0 " in ln, ln)
    ln = sr.flip_line(A, 2, 1790325167.8, 0.0, False, "IN_STOCK", None, 9, "s0", None)
    check("line_no_prior_oos", "last_oos_ms=0 since_oos_ms=- new_window=0 " in ln
          and "(?.x.x)" in ln, ln)
    ln = sr.flip_line(A, 3, None, 1.0, True, "IN_STOCK", None, 9, "s0", "198.51.100.4")
    check("line_bad_stamp_no_raise", "could not format" in ln and ln.startswith("[STOCK][FLIP]"), ln)


# ── end to end on a real checker ──────────────────────────────────────────────

def _run(flag_on):
    """OOS read, then a read where A and B flip, then A still in stock and B out, then
    B back in (a flicker inside the 20 s hysteresis). Returns (events, state, stamps)."""
    EVENTS.clear()
    old = os.environ.get("RESILIENT_FLIP_LOG")
    if flag_on:
        os.environ["RESILIENT_FLIP_LOG"] = "1"
    else:
        os.environ.pop("RESILIENT_FLIP_LOG", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            chk = ResilientStockChecker(
                proxy_urls=[], tcins=[A, B, C], state_dir=Path(tmp),
                on_in_stock=lambda s: EVENTS.append(("cb", s.tcin, s.in_stock, s.availability_status,
                                                     s.max_qty)),
                log_per_request=False, preflight=False)
            stamps = {}
            asyncio.run(chk._ingest_bulk_response(_res(_body(_entry(A), _entry(B), _entry(C)))))
            stamps["oos_A"] = chk._tcin_status[A].last_checked_at
            asyncio.run(chk._ingest_bulk_response(_res(_body(
                _entry(A, "IN_STOCK", 12.0, mq={"shipping": {"value": 2.0}}),
                _entry(B, "IN_STOCK", 4), _entry(C)))))
            stamps["in_A"] = chk._tcin_status[A].last_checked_at
            stamps["n_after_flip"] = len([e for e in EVENTS if e[0] == "log"])
            asyncio.run(chk._ingest_bulk_response(_res(_body(
                _entry(A, "IN_STOCK", 11.0), _entry(B), _entry(C)))))
            stamps["n_after_still_in"] = len([e for e in EVENTS if e[0] == "log"])
            asyncio.run(chk._ingest_bulk_response(_res(_body(
                _entry(A, "IN_STOCK", 10.0), _entry(B, "IN_STOCK", 2), _entry(C)))))
            state = {t: (st.in_stock, st.availability_status, st.max_qty, st.last_status_code)
                     for t, st in chk._tcin_status.items()}
            state["ever"] = sorted(chk._ever_seen_in_stock)
            return list(EVENTS), state, stamps
    finally:
        if old is None:
            os.environ.pop("RESILIENT_FLIP_LOG", None)
        else:
            os.environ["RESILIENT_FLIP_LOG"] = old


def test_ingest_off_vs_on():
    off_ev, off_state, _ = _run(False)
    on_ev, on_state, st = _run(True)
    off_cb = [e for e in off_ev if e[0] == "cb"]
    on_cb = [e for e in on_ev if e[0] == "cb"]
    check("e2e_callbacks_identical", off_cb == on_cb and len(on_cb) == 3, (off_cb, on_cb))
    check("e2e_state_identical", off_state == on_state, (off_state, on_state))
    check("e2e_off_no_flip_line", [e for e in off_ev if e[0] == "log"] == [], off_ev)
    logs = [e for e in on_ev if e[0] == "log"]
    check("e2e_on_one_line_per_transition", [e[1] for e in logs] == [A, B, B], [e[1] for e in logs])
    check("e2e_still_in_stock_no_line", st["n_after_flip"] == 2 and st["n_after_still_in"] == 2, st)
    # every line of a read comes after every callback of that read
    idx_cb_B1 = next(i for i, e in enumerate(on_ev) if e[0] == "cb" and e[1] == B)
    idx_log_A = next(i for i, e in enumerate(on_ev) if e[0] == "log" and e[1] == A)
    check("e2e_lines_after_all_callbacks", idx_cb_B1 < idx_log_A, on_ev)
    la = logs[0][2]
    check("e2e_read_ms_is_the_read", f"read_ms={int(round(st['in_A'] * 1000))} " in la, (la, st))
    check("e2e_last_oos_is_prior_read", f"last_oos_ms={int(round(st['oos_A'] * 1000))} " in la, (la, st))
    check("e2e_first_flip_new_window", "new_window=1 " in la and " #1 " in la, la)
    check("e2e_raw_float_fields", "atp=12.0 " in la and "max_order_qty={'shipping': {'value': 2.0}}" in la, la)
    check("e2e_flicker_same_window", "new_window=0 " in logs[2][2] and " #2 " in logs[2][2], logs[2][2])


def test_cap():
    EVENTS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        chk = ResilientStockChecker(proxy_urls=[], tcins=[A], state_dir=Path(tmp),
                                    log_per_request=False, preflight=False)
        res = _res(_body(_entry(A, "IN_STOCK", 1)))
        for i in range(sr.FLIP_LOG_CAP_PER_TCIN + 5):
            chk._log_flips([(A, 1790325167.0 + i, 1790325166.0 + i, False, "IN_STOCK")], res)
        chk._log_flips([(B, 1790325170.0, 0.0, True, "IN_STOCK")], res)
    logs = _flips()
    a_lines = [e[2] for e in logs if e[1] == A]
    check("cap_lines_then_one_notice", len(a_lines) == sr.FLIP_LOG_CAP_PER_TCIN + 1
          and "not logged" in a_lines[-1] and f" #{sr.FLIP_LOG_CAP_PER_TCIN} " in a_lines[-2], len(a_lines))
    check("cap_is_per_tcin", [e[1] for e in logs].count(B) == 1, logs[-1])


def test_never_raises():
    EVENTS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        chk = ResilientStockChecker(proxy_urls=[], tcins=[A], state_dir=Path(tmp),
                                    log_per_request=False, preflight=False)
        try:
            chk._log_flips([(A, None, None, True, None)], _res("not a dict"))
            chk._log_flips([("bad-tuple",)], _res(None))
            ok = True
        except Exception as e:  # pragma: no cover - the point is it never raises
            ok = repr(e)
    check("never_raises", ok is True, ok)


def test_gate_and_wrapper():
    src = (ROOT / "src" / "monitoring" / "stock_check_resilient.py").read_text(encoding="utf-8")
    check("gate_env_exact", "os.environ.get('RESILIENT_FLIP_LOG', '0').strip() == '1'" in src)
    check("gate_read_once_in_init", "self._flip_log = flip_log_on()" in src)
    i_cb = src.find("self.on_in_stock(s)")
    i_log = src.find("self._log_flips(_flips, result)")
    check("gate_log_after_callbacks_in_source", 0 < i_cb < i_log, (i_cb, i_log))
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("ascii", "replace").split("\r\n")
    check("bat_armed_once", bat.count("set RESILIENT_FLIP_LOG=1") == 1,
          [l for l in bat if "RESILIENT_FLIP_LOG" in l])


if __name__ == "__main__":
    test_raw_fields()
    test_line()
    test_ingest_off_vs_on()
    test_cap()
    test_never_raises()
    test_gate_and_wrapper()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
