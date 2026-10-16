#!/usr/bin/env python3
"""Offline tests: CHK-1 (2026-10-15), TARGET_CHAIN_NET_META, log-only [CHAIN_NET] wire stamps.

Flag off (unset / junk) = the INS-ATC-NET handlers behave exactly as before. On = the SAME two
Network handlers also print one [CHAIN_NET] line per pre_checkout (u=pre), place-order (u=po) and
CVV payment_instructions (u=cvv) POST / PUT and their OPTIONS preflight, capped per cart and per
process, in a record map of its own (chain traffic can never evict a cart_items record), every
branch inside the handlers' own try/except. Also pins the two saved queries
(tools/events/queries/first_po_age.sql runs on today's tables; chain_net.sql is written ahead of
its table and is run here on a synthetic one) and that build.py ignores the new lines.

No browser, no network, no bot. Temp files only.
Run: venv/Scripts/python.exe tests/test_chain_net_meta.py
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FLAGS = ("TARGET_CHAIN_NET_META", "TARGET_ATC_NET_META")
for _k in FLAGS:
    os.environ.pop(_k, None)

import src.session.purchase_executor as pe          # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402
from zendriver import cdp                           # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {str(detail)[:600]}")


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


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


PE_SRC = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")


def _src_const(name):
    m = re.search(r"const " + name + r" = '([^']+)';", PE_SRC)
    assert m, name
    return m.group(1)


# the URLs the bot really fires (won-cart ticket JS constants; the fast-lane chain builds the same strings)
URL = {
    "atc": "https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY",
    "pre": _src_const("PRE_URL"),
    "po": _src_const("PO_URL"),
    "cvv": "https://carts.target.com/checkout_payments/v1/payment_instructions/"
           "pi-123?key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14",
    "cart_get": "https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS",
    "page": "https://www.target.com/p/-/A-1010892076",
}


class _SM:
    account_id = "primary"


class _NetTab:
    def __init__(self):
        self.sent = []
        self.handlers = {}

    async def send(self, cmd):
        self.sent.append(cmd)

    def add_handler(self, ev, fn):
        self.handlers.setdefault(ev, []).append(fn)


def timing(send_start=5.0, connect=(-1, -1), req_time=50.010, send_end=6.0, hdr_end=146.0):
    return SimpleNamespace(request_time=req_time, send_start=send_start, send_end=send_end,
                           receive_headers_end=hdr_end, connect_start=connect[0], connect_end=connect[1],
                           ssl_start=-1, ssl_end=-1)


def W(rid, url, method, wall=1000.0, mono=50.0):
    return ("will", SimpleNamespace(request_id=rid, request=SimpleNamespace(url=url, method=method),
                                    wall_time=wall, timestamp=mono))


def R(rid, url, status, reused=True, conn=37.0, tm=None):
    return ("resp", SimpleNamespace(request_id=rid, response=SimpleNamespace(
        url=url, status=status, connection_id=conn, connection_reused=reused, remote_ip_address="151.101.2.187",
        remote_port=443, protocol="h2", timing=tm or timing())))


def drive(steps, flag, after_install=None):
    """Install the handlers on a fresh stub tab with TARGET_CHAIN_NET_META=flag, await each step."""
    ex = object.__new__(PurchaseExecutor)
    ex.session_manager = _SM()
    tab = _NetTab()

    async def go():
        await ex._install_atc_net_meta(tab)
        if after_install:
            after_install()
        will_h = tab.handlers[cdp.network.RequestWillBeSent][0]
        resp_h = tab.handlers[cdp.network.ResponseReceived][0]
        for kind, ev in steps:
            await (will_h if kind == "will" else resp_h)(ev)
        await asyncio.sleep(0)
    with _Env(TARGET_CHAIN_NET_META=flag):
        _, out = quiet(asyncio.run, go())
    return out, tab


def lines_of(out, tag):
    return [l for l in out.splitlines() if l.startswith(tag)]


def kv(line):
    return dict(re.findall(r"(\w+)=(\S+)", line))


def pair(rid, key, method, status=200, **kw):
    return [W(rid, URL[key], method), R(rid, URL[key], status, **kw)]


# ─────────────────────────────────────── pure helpers ───────────────────────────────────────
def test_pure():
    c = pe.chain_net_class
    check("class_pre", c(URL["pre"], "POST") == "pre" and c(URL["pre"], "OPTIONS") == "pre")
    check("class_po", c(URL["po"], "POST") == "po" and c(URL["po"], "OPTIONS") == "po")
    check("class_cvv", c(URL["cvv"], "PUT") == "cvv" and c(URL["cvv"], "OPTIONS") == "cvv")
    check("class_method_case", c(URL["pre"], "post") == "pre" and c(URL["cvv"], "put") == "cvv")
    check("class_cart_items_is_not_chain", c(URL["atc"], "POST") is None and c(URL["atc"], "OPTIONS") is None)
    check("class_cart_get_ignored", c(URL["cart_get"], "GET") is None and c(URL["cart_get"], "PUT") is None)
    check("class_wrong_method_ignored", c(URL["pre"], "GET") is None and c(URL["po"], "PUT") is None
          and c(URL["cvv"], "POST") is None and c(URL["cvv"], "GET") is None)
    check("class_page_ignored", c(URL["page"], "GET") is None)
    check("class_strict_path", c("https://carts.target.com/web_checkouts/v1/checkout_other", "POST") is None
          and c("https://carts.target.com/web_checkouts/v1/precheckout", "POST") is None
          and c("https://carts.target.com/web_checkouts/v1/checkout/", "POST") == "po")
    check("class_junk_never_raises", c(None, None) is None and c(123, object()) is None and c("", "") is None)
    s = pe.chain_net_sampled
    check("sample_head", all(s(n) for n in range(1, 21)) and not s(0) and not s(-3))
    check("sample_tail_every_10th", [n for n in range(21, 101) if s(n)] == [30, 40, 50, 60, 70, 80, 90, 100])
    check("sample_junk", s(None) is False and s("x") is False)
    p = pe.chain_net_pause_ms
    check("pause_math", p(1000.0, 1000015) == 15 and p(1000.0, 1000000) == 0, p(1000.0, 1000015))
    check("pause_missing_or_junk", p(None, 5) is None and p(1.0, None) is None and p("x", 1) is None)
    send = pe.atc_net_send_wall_ms(1000.0, 50.0, 50.010, 5.0)
    d = pe.chain_net_fields(R("x", URL["pre"], 200)[1].response, (1000.0, 50.0, "POST", "pre", 3), "x")
    check("fields_values", (d["u"], d["method"], d["status"], d["send_ms"], d["pause_ms"], d["ttfb_ms"], d["conn"],
                            d["reused"], d["proto"], d["connect_ms"], d["n"], d["rid"])
          == ("pre", "POST", 200, send, 15, 140, 37.0, True, "h2", None, 3, "x"), d)
    check("fields_send_derivation_is_atc_net_send_wall_ms", send == 1000015, send)
    d2 = pe.chain_net_fields(None, (None, None, "POST", "po", 1), "r")
    check("fields_none_response", d2["send_ms"] is None and d2["pause_ms"] is None and d2["status"] is None, d2)
    check("fields_bad_rec_is_empty", pe.chain_net_fields(object(), ("short",), "r") == {})
    d3 = pe.chain_net_fields(SimpleNamespace(), (1.0, 1.0, "PUT", "cvv", 7), 9)
    check("fields_bare_response_keeps_identity", (d3["u"], d3["method"], d3["n"], d3["rid"], d3.get("send_ms"))
          == ("cvv", "PUT", 7, "9", None), d3)
    line = pe.chain_net_line("primary", d)
    check("line_format", line == ("[CHAIN_NET] ident=primary u=pre m=POST st=200 pause_ms=15 send_ms=1000015 "
                                  "ttfb_ms=140 conn=37 reused=1 proto=h2 connect_ms=- n=3 rid=x"), line)
    check("line_missing_prints_dash", pe.chain_net_line("a", {"u": "po"}) == (
        "[CHAIN_NET] ident=a u=po m=- st=- pause_ms=- send_ms=- ttfb_ms=- conn=- reused=- proto=- "
        "connect_ms=- n=- rid=-"))
    check("line_never_raises", pe.chain_net_line("x", None).startswith("[CHAIN_NET] ident=x u=-")
          and pe.chain_net_line(object(), 5).startswith("[CHAIN_NET]")
          and pe.chain_net_line("x", {"u": object()}).startswith("[CHAIN_NET] ident=x"))
    check("line_does_not_match_atc_net_prefix", not line.startswith("[ATC_NET]") and "[ATC_NET]" not in line)
    with _Env():
        check("flag_default_off", pe.chain_net_meta_on() is False)
    with _Env(TARGET_CHAIN_NET_META="1"):
        check("flag_on", pe.chain_net_meta_on() is True)
    with _Env(TARGET_CHAIN_NET_META=" 1 "):
        check("flag_on_strips", pe.chain_net_meta_on() is True)
    for junk in ("0", "", "true", "yes", "2", "on", "junk"):
        with _Env(TARGET_CHAIN_NET_META=junk):
            check(f"flag_junk_is_off[{junk!r}]", pe.chain_net_meta_on() is False)


# ─────────────────────────────────── the handlers ─────────────────────────────────────────
def full_script():
    s = []
    s += pair("A1", "atc", "POST", 429)
    s += pair("P1", "pre", "OPTIONS", 204)
    s += pair("P2", "pre", "POST", 200)
    s += pair("O1", "po", "OPTIONS", 204)
    s += pair("O2", "po", "POST", 200)
    s += pair("C1", "cvv", "OPTIONS", 204)
    s += pair("C2", "cvv", "PUT", 200)
    # noise that must print nothing: a /cart GET, a pre_checkout GET, a page load, a cvv GET
    s += pair("N1", "cart_get", "GET", 200)
    s += pair("N2", "pre", "GET", 200)
    s += pair("N3", "page", "GET", 200)
    s += pair("N4", "cvv", "GET", 200)
    return s


def chain_free(script):
    chain_rids = {"P1", "P2", "O1", "O2", "C1", "C2", "N1", "N2", "N3", "N4"}
    return [(k, ev) for k, ev in script if ev.request_id not in chain_rids]


def test_handlers():
    out_on, tab = drive(full_script(), "1")
    out_off, _ = drive(full_script(), None)
    out_off_free, _ = drive(chain_free(full_script()), None)
    ch = lines_of(out_on, "[CHAIN_NET] ident=")
    check("on_six_chain_lines", len(ch) == 6, out_on)
    got = [(kv(l)["u"], kv(l)["m"], kv(l)["st"]) for l in ch]
    check("on_classes_and_methods", got == [("pre", "OPTIONS", "204"), ("pre", "POST", "200"),
                                            ("po", "OPTIONS", "204"), ("po", "POST", "200"),
                                            ("cvv", "OPTIONS", "204"), ("cvv", "PUT", "200")], got)
    check("on_values", all(kv(l)["pause_ms"] == "15" and kv(l)["send_ms"] == "1000015" and kv(l)["ttfb_ms"] == "140"
                           and kv(l)["conn"] == "37" and kv(l)["reused"] == "1" and kv(l)["proto"] == "h2"
                           and kv(l)["ident"] == "primary" for l in ch), ch)
    check("on_rids_and_ordinals", [(kv(l)["rid"], kv(l)["n"]) for l in ch]
          == [("P1", "1"), ("P2", "1"), ("O1", "1"), ("O2", "1"), ("C1", "1"), ("C2", "1")], ch)
    check("on_no_noise_lines", not any(f"rid={r}" in out_on for r in ("N1", "N2", "N3", "N4")), out_on)
    check("on_one_install_line", len(lines_of(out_on, "[CHAIN_NET] chain wire stamps on")) == 1
          and "ident=primary" in lines_of(out_on, "[CHAIN_NET] chain wire stamps on")[0])
    check("on_still_two_handlers", len(tab.handlers[cdp.network.RequestWillBeSent]) == 1
          and len(tab.handlers[cdp.network.ResponseReceived]) == 1)
    atc_on = lines_of(out_on, "[ATC_NET]")
    atc_off = lines_of(out_off, "[ATC_NET]")
    check("atc_net_lines_unchanged_by_flag", atc_on == atc_off and len(atc_on) == 2, (atc_on, atc_off))
    check("atc_net_line_still_the_old_format", any(l == (
        "[ATC_NET] ident=primary m=POST st=429 conn=37 reused=1 ip=151.101.2.187:443 proto=h2 send_ms=1000015 "
        "ttfb_ms=140 connect_ms=- ssl_ms=- rid=A1") for l in atc_on), atc_on)
    check("off_prints_no_chain_net", "[CHAIN_NET]" not in out_off, out_off)
    check("off_chain_traffic_prints_nothing", out_off == out_off_free, (out_off, out_off_free))
    for junk in ("0", "true", ""):
        o, _ = drive(full_script(), junk)
        check(f"junk_flag_{junk!r}_is_off", o == out_off, o)

    # a new connection: connect_ms printed, reused=0
    new_conn = [W("P9", URL["pre"], "POST"),
                R("P9", URL["pre"], 200, reused=False, conn=41.0,
                  tm=timing(send_start=30.0, connect=(2.0, 22.0)))]
    o, _ = drive(new_conn, "1")
    l = lines_of(o, "[CHAIN_NET] ident=")[0]
    check("on_new_connection_fields", kv(l)["reused"] == "0" and kv(l)["connect_ms"] == "20"
          and kv(l)["pause_ms"] == "40" and kv(l)["conn"] == "41", l)

    # a response with no remembered request (an unsampled one / an evicted one) prints nothing
    o, _ = drive([R("Z1", URL["po"], 200)], "1")
    check("on_response_without_request_is_silent", not lines_of(o, "[CHAIN_NET] ident="), o)


def test_exceptions_swallowed():
    class _Boom:
        @property
        def url(self):
            raise RuntimeError("boom")

        @property
        def timing(self):
            raise RuntimeError("boom timing")

    class _BadResp:
        url = URL["pre"]

        @property
        def timing(self):
            raise RuntimeError("boom timing")

    steps = [("will", SimpleNamespace(request_id="B1", request=_Boom(), wall_time=1.0, timestamp=1.0)),
             ("will", SimpleNamespace(request_id="B2")),                                   # no .request at all
             ("resp", SimpleNamespace(request_id="B3", response=None)),
             ("resp", SimpleNamespace(request_id="B4")),                                   # no .response at all
             W("B5", URL["pre"], "POST"),
             ("resp", SimpleNamespace(request_id="B5", response=_BadResp())),              # .timing raises
             W("B6", URL["pre"], "POST"),
             ("resp", SimpleNamespace(request_id="B6", response=SimpleNamespace(
                 url=URL["pre"], status="2xx", connection_id=object(), connection_reused="x",
                 remote_ip_address=None, remote_port=None, protocol=None, timing=timing()))),
             ] + pair("OK1", "pre", "POST", 200)
    out, _ = drive(steps, "1")
    ok = [l for l in lines_of(out, "[CHAIN_NET] ident=") if "rid=OK1" in l]
    check("exceptions_swallowed_and_later_events_still_log", len(ok) == 1, out)
    b5 = [l for l in lines_of(out, "[CHAIN_NET] ident=") if "rid=B5" in l]
    check("unreadable_response_still_prints_what_is_known",
          len(b5) == 1 and "u=pre m=POST st=- pause_ms=- send_ms=- ttfb_ms=- conn=- reused=- proto=- connect_ms=- n=1 "
          in b5[0], b5)

    # an install that fails (add_handler raises) with the flag on still never raises
    class _BadTab(_NetTab):
        def add_handler(self, ev, fn):
            raise RuntimeError("no handler")
    ex = object.__new__(PurchaseExecutor)
    ex.session_manager = _SM()

    async def go():
        await ex._install_atc_net_meta(_BadTab())
    with _Env(TARGET_CHAIN_NET_META="1"):
        _, o = quiet(asyncio.run, go())
    check("install_failure_is_swallowed_with_flag_on", "install failed" in o and "instrument off" in o, o)


def test_flag_read_once():
    def _off():
        os.environ["TARGET_CHAIN_NET_META"] = "0"
    out, _ = drive(pair("P1", "pre", "POST"), "1", after_install=_off)
    check("flag_read_once_on_stays_on", len(lines_of(out, "[CHAIN_NET] ident=")) == 1, out)

    def _on():
        os.environ["TARGET_CHAIN_NET_META"] = "1"
    out, _ = drive(pair("P1", "pre", "POST"), None, after_install=_on)
    check("flag_read_once_off_stays_off", "[CHAIN_NET]" not in out, out)


def test_cap_and_reset():
    steps = []
    for i in range(1, 101):
        steps += pair(f"P{i}", "pre", "POST", 200)
    steps += pair("PO1", "pre", "OPTIONS", 204) + pair("PO2", "pre", "OPTIONS", 204) + pair("PO3", "pre", "OPTIONS", 204)
    out, _ = drive(steps, "1")
    ch = [kv(l) for l in lines_of(out, "[CHAIN_NET] ident=")]
    pre_post = [int(d["n"]) for d in ch if d["u"] == "pre" and d["m"] == "POST"]
    check("cap_first_20_then_every_10th", pre_post == list(range(1, 21)) + [30, 40, 50, 60, 70, 80, 90, 100], pre_post)
    check("cap_options_counted_apart", [int(d["n"]) for d in ch if d["m"] == "OPTIONS"] == [1, 2, 3])
    check("cap_lines_per_cart_bounded", len(ch) == 28 + 3, len(ch))

    # a FAILED add-to-cart does not start a new cart; a 201 does
    more = pair("PA", "atc", "POST", 429) + pair("P101", "pre", "POST", 200)
    more2 = pair("PB", "atc", "POST", 201) + pair("P102", "pre", "POST", 200) + pair("PC", "po", "POST", 200)
    out, _ = drive(steps + more, "1")
    ch2 = lines_of(out, "[CHAIN_NET] ident=")
    check("failed_atc_does_not_reset", not any("rid=P101" in l for l in ch2) and any("rid=PA" in o for o in
                                                                                       lines_of(out, "[ATC_NET]")))
    out, _ = drive(steps + more + more2, "1")
    l102 = [l for l in lines_of(out, "[CHAIN_NET] ident=") if "rid=P102" in l]
    lpc = [l for l in lines_of(out, "[CHAIN_NET] ident=") if "rid=PC" in l]
    check("successful_atc_resets_the_ordinals", len(l102) == 1 and kv(l102[0])["n"] == "1"
          and len(lpc) == 1 and kv(lpc[0])["n"] == "1", out[-600:])
    out, _ = drive(steps + pair("PB", "atc", "POST", 200) + pair("P103", "pre", "POST", 200), "1")
    check("a_200_atc_resets_too", any("rid=P103" in l and "n=1 " in l + " " for l in lines_of(out, "[CHAIN_NET] ident=")))
    # an ATC OPTIONS preflight (any status) is not a cart
    out, _ = drive(steps + pair("PD", "atc", "OPTIONS", 200) + pair("P104", "pre", "POST", 200), "1")
    check("atc_options_does_not_reset", not any("rid=P104" in l for l in lines_of(out, "[CHAIN_NET] ident=")))


def test_no_eviction_of_cart_items_records():
    """2,500 unanswered pre POSTs fill (and clear) the chain record map several times over; the
    cart_items request sent BEFORE them still gets its [ATC_NET] line with the wire send time."""
    steps = [W("ATC1", URL["atc"], "POST")]
    steps += [W(f"U{i}", URL["pre"], "POST") for i in range(2500)]
    steps += [R("ATC1", URL["atc"], 429)]
    out, _ = drive(steps, "1")
    atc = lines_of(out, "[ATC_NET] ident=")
    check("cart_items_record_survives_chain_traffic", len(atc) == 1 and "send_ms=1000015" in atc[0]
          and "m=POST" in atc[0], atc)
    # the same chain traffic with the flag OFF leaves the ATC map exactly as before
    out_off, _ = drive(steps, None)
    check("flag_off_atc_line_identical", lines_of(out_off, "[ATC_NET] ident=") == atc)
    # interleaved: a cart_items record is never counted against the chain cap
    steps2 = []
    for i in range(30):
        steps2 += pair(f"X{i}", "atc", "POST", 429)
    out, _ = drive(steps2, "1")
    check("atc_traffic_alone_logs_no_chain_lines", "[CHAIN_NET] ident=" not in out
          and len(lines_of(out, "[ATC_NET] ident=")) == 30)


def test_hard_ceiling():
    old = pe._CHAIN_NET_MAX_LINES
    pe._CHAIN_NET_MAX_LINES = 5
    try:
        steps = []
        for i in range(15):
            steps += pair(f"H{i}", "po", "POST", 200)
        out, _ = drive(steps, "1")
    finally:
        pe._CHAIN_NET_MAX_LINES = old
    req = [l for l in lines_of(out, "[CHAIN_NET] ident=") if " u=" in l]
    cap = [l for l in lines_of(out, "[CHAIN_NET] ident=") if " cap=" in l]
    check("ceiling_stops_the_request_lines", len(req) == 5, len(req))
    check("ceiling_note_once", len(cap) == 1 and "cap=5" in cap[0], cap)
    check("ceiling_note_is_not_a_request_row", all(" u=" not in l for l in cap), cap)


# ───────────────────────────── source pins / arming ──────────────────────────────────
def test_pins():
    check("install_guard_unchanged",
          "if label == 'main' and atc_net_meta_on():\n" in PE_SRC)
    check("no_third_handler", PE_SRC.count("tab.add_handler(cdp.network.RequestWillBeSent, _on_will_send)") == 1
          and PE_SRC.count("tab.add_handler(cdp.network.ResponseReceived, _on_response)") == 1
          and "cdp.network.RequestWillBeSent, _chain" not in PE_SRC)
    body = PE_SRC[PE_SRC.index("async def _install_atc_net_meta"):PE_SRC.index("def _consume_fresh_capture")]
    new = body[body.index("chain_on = chain_net_meta_on()"):]
    check("chain_branches_never_await", "await " not in new.split("async def _on_will_send")[0], "await in chain helpers")
    check("flag_read_once_in_install", body.count("chain_net_meta_on()") == 1)
    golden = ROOT / "tests" / "fixtures" / "fast_lane_js_golden.txt"
    check("golden_fixture_present", golden.exists())
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("utf-8", "replace").splitlines()
    sets = [l for l in bat if l.strip().lower().startswith("set target_chain_net_meta=")]
    check("bat_arms_at_most_once", sets in ([], ["set TARGET_CHAIN_NET_META=1"]), sets)
    atc = [l for l in bat if l.strip() == "set TARGET_ATC_NET_META=1"]
    check("bat_chain_flag_needs_atc_net_armed", (not sets) or len(atc) == 1, (sets, atc))


# ─────────────────────────────── saved queries (synthetic tables) ───────────────────────────
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


evq = _load("events_q_chk1", ROOT / "tools" / "events" / "q.py")
evb = _load("events_build_chk1", ROOT / "tools" / "events" / "build.py")


def run_q(name, db):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()) as err:
        rc = evq.main([name, "--db", str(db), "--json"])
    return rc, (json.loads(buf.getvalue()) if buf.getvalue().strip() else None), err.getvalue()


PO_COLS = ("run_id TEXT, line INTEGER, ts_ms INTEGER, ts_src TEXT, ts_lo_ms INTEGER, ts_hi_ms INTEGER, "
           "status INTEGER, ident TEXT, window_age_ms INTEGER, po_idx INTEGER, cart_line INTEGER")
T0 = 1_790_000_000_000


def _mk_db(path, with_chain):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE runs (run_id TEXT)")
    con.execute(f"CREATE TABLE place_orders ({PO_COLS})")
    con.execute("CREATE TABLE atc_net (run_id TEXT, line INTEGER, ident TEXT, method TEXT, status INTEGER, "
                "send_ms INTEGER, ttfb_ms INTEGER)")
    for r in ("run_A", "run_B", "run_C"):
        con.execute("INSERT INTO runs VALUES (?)", (r,))
    # run_A: 6 first POs, 2 wins (logger_prior; age_hi 2.5 s and 3.8 s), 4 rejections (resp_date, age 6-9 s)
    # window opens at T0; the PO stamps sit at T0 + age
    def po(run, line, st, src, lo_age, hi_age, ident="primary", cart=1, idx=1):
        w0 = T0
        con.execute("INSERT INTO place_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (run, line, w0 + (lo_age if src == "logger_prior" else (lo_age + hi_age) // 2), src,
                     None if lo_age is None else w0 + lo_age, None if hi_age is None else w0 + hi_age, st, ident,
                     (lo_age if src == "logger_prior" else (lo_age + hi_age) // 2), idx, cart))
    po("run_A", 100, 200, "logger_prior", 1500, 2500)
    po("run_A", 200, 200, "logger_prior", 2900, 3800, ident="business")
    for i, (lo, hi) in enumerate([(5900, 6100), (6900, 7100), (7900, 8100), (8900, 9100)]):
        po("run_A", 300 + i, 429, "resp_date", lo, hi, ident="alt-1")
    po("run_A", 999, 429, "resp_date", 20000, 20200, idx=2)        # a later PO: not a first PO
    # run_B: 5 first POs, a win with an UNKNOWN upper bound -> INCONCLUSIVE; run_C: a win over 4.4 s
    po("run_B", 100, 200, "logger_prior", 1500, None)
    for i in range(4):
        po("run_B", 200 + i, 429, "resp_date", 5000, 5200)
    po("run_C", 100, 200, "logger_prior", 4000, 5200)
    for i in range(4):
        po("run_C", 200 + i, 429, "resp_date", 5000, 5200)
    # run_B/run_C ident NULL rows are fine for first_po_age
    if with_chain:
        con.execute("CREATE TABLE chain_net (run_id TEXT, line INTEGER, ident TEXT, u TEXT, method TEXT, status INTEGER, "
                    "pause_ms INTEGER, send_ms INTEGER, ttfb_ms INTEGER, conn INTEGER, reused INTEGER, proto TEXT, "
                    "connect_ms INTEGER, n INTEGER, rid TEXT)")
        # the winning chain of run_A / primary: ATC 201 answered at T0+450, pre sent +700 (ttfb 300), po sent +2200
        con.execute("INSERT INTO atc_net VALUES ('run_A', 90, 'primary', 'POST', 201, ?, 450)", (T0,))
        rows = [("run_A", 91, "primary", "pre", "POST", 200, 20, T0 + 700, 300, 1, 1, "h2", None, 1, "r1"),
                ("run_A", 92, "primary", "po", "POST", 200, 30, T0 + 2200, 250, 1, 1, "h2", None, 1, "r2"),
                ("run_A", 93, "primary", "po", "OPTIONS", 204, 40, T0 + 2150, 20, 1, 0, "h2", 10, 1, "r3"),
                ("run_A", 94, "business", "po", "POST", 200, 30, T0 + 3300, 250, 1, 1, "h2", None, 1, "r4")]
        # 24 pre / po requests at queue_ms 120 and pre ttfb 700 in run_B -> BUILD_CHK3 + RUN_CHK2
        for i in range(24):
            rows.append(("run_B", 300 + i, "primary", "pre" if i % 2 == 0 else "po", "POST", 200,
                         120, T0 + 10_000 + i * 50, 700, 1, 1, "h2", None, i + 1, f"b{i}"))
        # run_D: 12 cheap pre requests -> the negative branch of both rules
        for i in range(12):
            rows.append(("run_D", 500 + i, "primary", "pre", "POST", 200, 10, T0 + i, 100, 1, 1, "h2", None, i + 1, f"d{i}"))
        # run_C: 3 requests -> INSUFFICIENT
        for i in range(3):
            rows.append(("run_C", 400 + i, "primary", "pre", "POST", 200, 500, T0 + i, 900, 1, 1, "h2", None, i + 1, f"c{i}"))
        con.executemany("INSERT INTO chain_net VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_first_po_age_sql():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ev.sqlite"
        _mk_db(db, with_chain=False)                      # NO chain_net table: first_po_age must run on today's store
        rc, res, err = run_q("first_po_age", db)
        check("first_po_age_runs_without_chain_net", rc == 0 and res is not None and len(res) == 3, (rc, err))
        if not res:
            return
        by_src = {(r["ts_src"], r["outcome"]): r for r in res[0]}
        check("fpa_1_ts_src_split", by_src[("logger_prior", "win")]["n"] == 4
              and by_src[("resp_date", "rejected")]["n"] == 12 and ("resp_date", "win") not in by_src
              and sum(r["n"] for r in res[0]) == 16, res[0])        # the later PO (po_idx 2) is excluded
        check("fpa_1_bounds", by_src[("logger_prior", "win")]["max_age_hi_ms"] == 5200
              and by_src[("logger_prior", "win")]["age_unknown"] == 1, by_src[("logger_prior", "win")])
        bins = {(r["clock"], r["bin"]): r["wins_of_first_pos"] for r in res[1]}
        check("fpa_2_both_bounds_printed", {c for c, _ in bins} == {"lo", "est", "hi"}, bins)
        check("fpa_2_hi_bound_ages_the_win_up", bins.get(("hi", "3 2-3s")) == "1/1" and bins.get(("hi", "4 3-4.4s")) == "1/1"
              and bins.get(("lo", "3 2-3s")) == "1/1" and bins.get(("lo", "1 <=1s")) is None, bins)
        check("fpa_2_unknown_is_its_own_bin", bins.get(("hi", "9 unknown")) == "1/1", bins)
        reading = {r["run"]: r["reading"] for r in res[2]}
        check("fpa_3_reading", reading == {"run_A": "EVERY_WIN_LE_4_4S", "run_B": "INCONCLUSIVE",
                                           "run_C": "SOME_WIN_OVER_4_4S"}, reading)
        a = [r for r in res[2] if r["run"] == "run_A"][0]
        check("fpa_3_numbers", (a["first_pos"], a["wins"], a["max_win_age_hi_ms"], a["wins_over_3s"], a["wins_over_4_4s"])
              == (6, 2, 3800, 1, 0), a)
    # the real store, when present (read-only): the 18 wins' upper-bound ages are 2,626-4,379 ms
    for cand in (ROOT / "logs" / "events" / "events.sqlite", Path(r"C:\Users\elric\Desktop\testScraper\logs\events\events.sqlite")):
        if cand.exists():
            rc, res, err = run_q("first_po_age", cand)
            if rc == 0 and res:
                w = [r for r in res[0] if r["outcome"] == "win"]
                check("fpa_real_store_if_present_runs", len(w) >= 1 and all(r["ts_src"] == "logger_prior" for r in w), w)
            break


def test_chain_net_sql():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ev.sqlite"
        _mk_db(db, with_chain=False)
        rc, res, err = run_q("chain_net", db)
        check("chain_net_errors_cleanly_without_its_table", rc == 1 and "no such table: chain_net" in err, (rc, err))
        db2 = Path(tmp) / "ev2.sqlite"
        _mk_db(db2, with_chain=True)
        rc, res, err = run_q("chain_net", db2)
        check("chain_net_runs_on_the_synthetic_table", rc == 0 and res is not None and len(res) == 5, (rc, err))
        if not res:
            return
        cov = {(r["run"], r["ident"], r["u"], r["method"]): r["lines"] for r in res[0]}
        check("cn_1_coverage", cov[("run_A", "primary", "po", "POST")] == 1 and cov[("run_A", "business", "po", "POST")] == 1
              and cov[("run_A", "primary", "po", "OPTIONS")] == 1 and cov[("run_B", "primary", "pre", "POST")] == 12
              and cov[("run_B", "primary", "po", "POST")] == 12 and cov[("run_D", "primary", "pre", "POST")] == 12, cov)
        lat = {(r["u"], r["method"], r["conn"]): r for r in res[1]}
        pre = lat[("pre", "POST", "reused")]
        check("cn_2_percentiles", (pre["n"], pre["queue_p50"], pre["queue_p90"], pre["ttfb_p50"], pre["ttfb_p90"])
              == (28, 120, 500, 700, 900), pre)
        opt = lat[("po", "OPTIONS", "new")]
        check("cn_2_new_connection_queue_subtracts_connect", (opt["n"], opt["queue_p50"], opt["ttfb_p50"]) == (1, 30, 20), opt)
        rules = {r["run"]: (r["chk3"], r["chk2"]) for r in res[2]}
        check("cn_3_rules", rules == {"run_A": ("INSUFFICIENT", "INSUFFICIENT"), "run_B": ("BUILD_CHK3", "RUN_CHK2"),
                                      "run_C": ("INSUFFICIENT", "INSUFFICIENT"),
                                      "run_D": ("NOT_WORTH_CHK3", "NOT_WORTH_CHK2")}, rules)
        leg = {r["ident"]: r for r in res[3] if r["run"] == "run_A"}
        pl = leg["primary"]
        check("cn_4_leg_split", (pl["gap_atc_to_po_ms"], pl["gap_atc_to_pre_ms"], pl["pre_ttfb_ms"],
                                 pl["gap_pre_resp_to_po_ms"], pl["po_ttfb_ms"]) == (1750, 250, 300, 1200, 250), pl)
        bl = leg["business"]
        check("cn_4_missing_rows_stay_null_never_zero", bl["gap_atc_to_po_ms"] is None and bl["gap_atc_to_pre_ms"] is None
              and bl["pre_ttfb_ms"] is None and bl["gap_pre_resp_to_po_ms"] is None and bl["po_ttfb_ms"] == 250, bl)
        wire = {r["run"]: r for r in res[4]}
        check("cn_5_runs_with_a_wire_stamp_only", set(wire) == {"run_A"}, wire)
        w = wire.get("run_A", {})
        check("cn_5_wire_age_of_the_wins", (w.get("first_pos"), w.get("no_wire"), w.get("wins"), w.get("wins_no_wire"),
                                            w.get("max_win_wire_age_ms"), w.get("wins_over_3s"), w.get("wins_over_4_4s"),
                                            w.get("reading")) == (6, 4, 2, 0, 3300, 1, 0, "EVERY_WIN_LE_4_4S"), w)
    sql = (ROOT / "tools" / "events" / "queries" / "chain_net.sql").read_text(encoding="utf-8")
    head = sql.split("\n\n")[0]
    check("chain_net_header_says_table_missing", "DOES NOT EXIST YET" in head and "no such table" in head)
    fpa = (ROOT / "tools" / "events" / "queries" / "first_po_age.sql").read_text(encoding="utf-8")
    code = "\n".join(l for l in fpa.splitlines() if not l.lstrip().startswith("--"))
    check("first_po_age_code_has_no_chain_net_dependency", "chain_net" not in code)


# ──────────────────────────── build.py must ignore [CHAIN_NET] ───────────────────────────
def _strip_lines(x):
    if isinstance(x, dict):
        return {k: _strip_lines(v) for k, v in x.items()
                if not (k in ("line", "lines", "first_line") or k.endswith("_line"))}
    if isinstance(x, list):
        return [_strip_lines(v) for v in x]
    return x


def test_build_ignores_chain_net():
    pool = "[WORKER_POOL] sized from target_accounts.json: 3 account(s) -> ['primary', 'business', 'alt-1']"
    inst = "[ATC_NET] Network meta instrument installed on the main tab ident="

    def net(ident, st, send, rid):
        return pe.atc_net_line(ident, {"method": "POST", "status": st, "conn": 55, "reused": True,
                                       "ip": "151.101.2.187", "port": 443, "proto": "h2", "send_ms": send,
                                       "ttfb_ms": 120, "rid": rid})
    base = [pool, inst + "primary", inst + "business", net("primary", 429, T0 + 100, "1.1"),
            net("business", 201, T0 + 150, "2.1"), net("primary", 429, T0 + 9000, "1.2")]
    d = pe.chain_net_fields(R("x", URL["pre"], 200)[1].response, (1000.0, 50.0, "POST", "pre", 1), "1.9")
    chain_lines = [
        "[CHAIN_NET] chain wire stamps on (u=pre|po|cvv, first 20 per cart then every 10th, cap 3000) ident=primary",
        pe.chain_net_line("primary", d),
        pe.chain_net_line("primary", dict(d, u="po", method="POST")),
        pe.chain_net_line("business", dict(d, u="cvv", method="PUT")),
        pe.chain_net_line("?", None),
        "[CHAIN_NET] ident=primary cap=3000 reached - further requests unlogged",
    ]
    with_chain = []
    for i, ln in enumerate(base):
        with_chain.append(ln)
        with_chain.append(chain_lines[i % len(chain_lines)])
    with_chain += chain_lines
    a = evb.parse_lines("run_x", base)
    b = evb.parse_lines("run_x", with_chain)
    check("build_atc_net_regex_does_not_match_chain_net", len(a["atc_net"]) == 3 and len(b["atc_net"]) == 3,
          (len(a["atc_net"]), len(b["atc_net"])))
    na, nb = _strip_lines(a), _strip_lines(b)
    diff = [k for k in na if na[k] != nb[k]]
    check("build_every_table_unchanged_by_chain_net_lines", not diff, diff)
    # appended at the end: nothing at all moves, line numbers included
    c = evb.parse_lines("run_x", base + chain_lines)
    diff2 = [k for k in a if k != "run" and a[k] != c[k]]
    check("build_appended_chain_net_lines_change_no_row", not diff2, diff2)
    check("build_counts_the_physical_lines_only", c["run"]["lines"] == a["run"]["lines"] + len(chain_lines)
          and _strip_lines(a["run"]) == _strip_lines(c["run"]), (a["run"], c["run"]))


if __name__ == "__main__":
    test_pure()
    test_handlers()
    test_exceptions_swallowed()
    test_flag_read_once()
    test_cap_and_reset()
    test_no_eviction_of_cart_items_records()
    test_hard_ceiling()
    test_pins()
    test_first_po_age_sql()
    test_chain_net_sql()
    test_build_ignores_chain_net()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
