#!/usr/bin/env python3
"""Offline test for tools/ops/order_watch.py (OPS-ORDER-PING-1015, 2026-10-15): the read-only
order / ambiguous-commit watcher. Tools only: no repo flag, nothing in src/ or app.py changed.
Synthetic log lines, temp directories and (when the main tree's logs/runs exists) replays of
real run logs. Nothing is launched, no network, no bot.

    python tests/test_order_watch_1015.py
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import io
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ops"))
sys.path.insert(0, str(ROOT / "tools" / "predrop"))
import order_watch as OW  # noqa: E402

TOOL = ROOT / "tools" / "ops" / "order_watch.py"
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def watch(lines, **kw):
    """Feed physical lines (CRLF, as the real logs have them); return (watcher, event lines)."""
    out = []
    w = OW.Watcher(out.append, **kw)
    w.begin_file("run_20261015_000000.log")
    for ln in lines:
        w.feed((ln + "\r\n").encode("utf-8"))
    return w, out


def events(out, cls=None):
    ev = [l for l in out if not l.startswith("WATCH ")]
    return [l for l in ev if l.startswith(cls + " ")] if cls else ev


OID1 = "8cba94c1-8cc5-11f1-adbd-9de2787bfd93"
OID2 = "1124a2f0-12aa-11f1-8096-07248c745cbb"
FL = "[FAST_LANE] *** ORDER PLACED *** HTTP 200 at t=3.08s — order_id="
CHAIN = ("[FAST_LANE] chain done in 3.00s — atc=201 pre=201 po=200 skip=none ident=primary "
         "atc_t0=1790844515216 atc_rt=455 cart_qty=2")
CHK = "[PURCHASE] Checkout complete (t=3.1s)"
LOGGER = "2026-10-01 03:48:%02d,%03d INFO src.session.purchase_executor: "


def lg(sec, text, ms=314):
    return (LOGGER % (sec, ms)) + text


def main() -> int:
    # ---- 1. the fast-lane order: one ORDER, "Checkout complete" absorbed, account hint ------
    w, out = watch([CHAIN, FL + OID1, "[PURCHASE] ATC fetch status: 201 (t=3.08s)", CHK])
    ev = events(out)
    check("fl_one_order", len(ev) == 1 and ev[0].startswith("ORDER "), out)
    check("fl_oid_and_near", f"oid={OID1}" in ev[0] and "near=primary(-1L)" in ev[0], ev)
    check("fl_raw_kept", ev[0].endswith("| " + FL + OID1), ev)
    check("fl_checkout_absorbed", w.stats["checkout_absorbed"] == 1 and w.stats["checkout_fallback"] == 0, w.stats)
    check("fl_line_number", ":L2 " in ev[0], ev)
    check("fl_no_cr", "\r" not in ev[0])

    # ---- 2. print + logger duplicate forms and repeats count once --------------------------
    w, out = watch([FL + OID1, lg(37, FL + OID1), CHK])
    check("dup_form_pair_once", len(events(out, "ORDER")) == 1 and w.stats["dup_forms"] == 1, (out, w.stats))
    w, out = watch([FL + OID1, lg(37, "[FAST_LANE] *** ORDER PLACED *** HTTP 200 t=3.1s order_id=" + OID1)])
    check("dup_order_id_once", len(events(out, "ORDER")) == 1 and w.stats["dup_order_id"] == 1, (out, w.stats))
    w, out = watch([FL + OID1, "[x] noise", FL + OID1])           # the same id twice, two prints
    check("same_id_repeat_once", len(events(out, "ORDER")) == 1, out)
    w, out = watch([FL + OID1, FL + OID2])
    check("two_orders_same_instant_both", len(events(out, "ORDER")) == 2
          and OID1 in events(out)[0] and OID2 in events(out)[1], out)

    # ---- 3. glued lines ---------------------------------------------------------------------
    w, out = watch(["[PURCHASE] Fetch ATC succeeded (201)" + FL + "abcdef12-0001" + CHK])
    check("glued_print_markers_once_each", len(events(out, "ORDER")) == 1 and "oid=abcdef12-0001" in out[0]
          and w.stats["checkout_absorbed"] == 1, out)
    w, out = watch([FL + "abcdef12-0002" + (LOGGER % (38, 216)) + "[PURCHASE] ATC fetch status: 201 (t=3.1s)"])
    check("glued_logger_stamp_cuts_the_id", len(events(out, "ORDER")) == 1 and "oid=abcdef12-0002 |" in out[0], out)
    amb = "[AMBIGUOUS_COMMIT] primary 1011960739 latched 1800s — check order history (why=po_5xx)"
    w, out = watch([(LOGGER % (38, 216)) + "noise" + amb])
    check("glued_after_logger_ambiguous", len(events(out, "AMBIGUOUS")) == 1
          and out[0].startswith("AMBIGUOUS 2026-10-01 03:48:38 ") and "~" not in out[0].split(" run_")[0]
          and out[0].endswith("| " + amb), out)
    w, out = watch([amb + (LOGGER % (39, 5)) + amb])                # print form glued to its logger form
    check("glued_pair_once", len(events(out, "AMBIGUOUS")) == 1 and w.stats["dup_forms"] == 1, (out, w.stats))

    # ---- 4. "Checkout complete" alone is an ORDER (DOM path / id-less), never absorbed twice --
    w, out = watch([CHK])
    check("checkout_only_is_order", len(events(out, "ORDER")) == 1 and "src=checkout-complete-only" in out[0], out)
    w, out = watch([FL + OID1, CHK, CHK])
    check("second_checkout_not_absorbed", len(events(out, "ORDER")) == 2
          and "src=checkout-complete-only" in events(out)[1], out)
    w, out = watch([lg(30, "boot"), FL + OID1, lg(37, "x"), lg(59, "y", 1), "2026-10-01 03:50:10,000 INFO m: z", CHK])
    check("credit_expires_with_log_time", len(events(out, "ORDER")) == 2, out)
    w, out = watch([lg(37, CHK), lg(37, CHK, 900)])                 # a logger-only run of two orders
    check("two_checkouts_two_orders", len(events(out, "ORDER")) == 2, out)
    w, out = watch([FL + OID1, FL + OID2, CHK, CHK])
    check("interleaved_orders_pair_up", len(events(out, "ORDER")) == 2 and w.stats["checkout_absorbed"] == 2, out)

    # ---- 5. the API variants ---------------------------------------------------------------
    api = f"[API_PLACE_ORDER] Order placed — order_id={OID2} reference_id=102003637784771 (t=0.97s)"
    w, out = watch([api, CHK])
    check("api_order", len(events(out, "ORDER")) == 1 and f"oid={OID2}" in out[0]
          and w.stats["checkout_absorbed"] == 1, out)
    nf = ("[API_PLACE_ORDER] Order placed (HTTP 200) but order_id NOT FOUND in response — check "
          "logs/api_capture.log if OBSERVE was on. Body preview: '{\"orders\":[]}'")
    w, out = watch([nf, CHK])
    check("api_not_found_is_order", len(events(out, "ORDER")) == 1 and "oid=NOT_FOUND" in out[0]
          and w.stats["checkout_absorbed"] == 1, out)
    w, out = watch([nf, lg(40, nf), CHK])
    check("api_not_found_pair_once", len(events(out, "ORDER")) == 1, out)

    # ---- 6. AMBIGUOUS is never suppressed or rate-limited ----------------------------------
    w, out = watch([amb, amb])                                      # two real events, same text
    check("ambiguous_two_prints_both", len(events(out, "AMBIGUOUS")) == 2, out)
    many = [f"[AMBIGUOUS_COMMIT] alt-1 {1011960700 + i} latched 1800s — check order history (why=x)"
            for i in range(25)]
    w, out = watch([lg(37, m) for m in many])
    check("ambiguous_25_in_one_second_all", len(events(out, "AMBIGUOUS")) == 25, len(out))
    w, out = watch([FL + f"{i:08d}-aaaa-bbbb" for i in range(25)])
    check("orders_25_in_one_second_all", len(events(out, "ORDER")) == 25, len(out))
    w, out = watch([f"[AMBIGUOUS_COMMIT] restored 2 latch(es) from the previous process: x",
                    "[AMBIGUOUS_COMMIT] 1011960739 in stock but every candidate identity is latched (a 1700s) "
                    "— not dispatching; check order history"])
    check("ambiguous_every_variant", len(events(out, "AMBIGUOUS")) == 2, out)

    # ---- 7. WONCART / REBUY: start+end lines only, limited 1/class/min with a count ----------
    wc_s = "[WON_CART_DIRECT] start entry=first ident=primary tcin=1010892076 qty=2 pre0=201 key=FS verified=True"
    wc_e = ("[WON_CART_DIRECT] end reason=call_cap verdict=done tickets_call=38 tickets_cart=38 live=True "
            "oos=0 held=yes ident=alt-1")
    noise = ["[WON_CART_DIRECT] ticket n=3 mode=x", "[WON_CART_DIRECT] cart delete (t) — 1 line(s) deleted",
             "[WON_CART_DIRECT] could NOT delete the line", "[WON_CART_DIRECT] our line may still be in the cart"]
    w, out = watch(noise + [wc_s])
    check("woncart_only_start_end", len(events(out, "WONCART")) == 1 and w.stats["wc_start"] == 1, out)
    w, out = watch([lg(10, wc_s), lg(11, wc_e), lg(12, wc_s), lg(13, wc_e), lg(14, wc_s)])
    check("woncart_limited", len(events(out, "WONCART")) == 1 and w.stats["woncart_suppressed"] == 4, (out, w.stats))
    check("woncart_acct_from_own_line", "acct=primary" in out[0], out)
    w, out = watch([lg(10, wc_s), lg(11, wc_e), lg(12, wc_e), "2026-10-01 03:49:20,000 INFO m: x",
                    wc_s.replace("1010892076", "1010892099")])
    ev = events(out, "WONCART")
    check("woncart_count_on_next", len(ev) == 2 and "+2 suppressed" in ev[1], out)
    w, out = watch([lg(10, wc_s), lg(11, wc_e), lg(12, wc_e)], rate_s=0)
    check("rate_off_prints_all", len(events(out, "WONCART")) == 3, out)
    w, out = watch([lg(10, wc_s), lg(11, wc_e), lg(12, wc_e)])
    w.flush_suppressed(time.time() + 3600)
    check("flush_reports_orphan_count", any(l.startswith("WATCH suppressed WONCART=2") for l in out), out)
    check("flush_not_event_class", not any(l.startswith("WONCART") and "suppressed WONCART" in l for l in out), out)
    rb = "[REBUY] 1011960739: kept 'purchased' -- absent from this event"
    w, out = watch([lg(10, rb), lg(11, rb + " b"), lg(12, wc_s), lg(13, rb + " c")])
    check("rebuy_limited_per_class", len(events(out, "REBUY")) == 1 and w.stats["rebuy_suppressed"] == 2
          and len(events(out, "WONCART")) == 1, out)
    w, out = watch([lg(10, wc_s)] * 6 + [FL + OID1, amb, FL + OID2, amb])
    check("limiter_never_touches_order_or_ambiguous",
          len(events(out, "ORDER")) == 2 and len(events(out, "AMBIGUOUS")) == 2, out)

    # ---- 8. output contract -----------------------------------------------------------------
    w, out = watch([CHAIN, FL + OID1, amb, wc_s, rb, CHK])
    tags = {l.split(" ")[0] for l in events(out)}
    check("class_tags_only", tags <= {"ORDER", "AMBIGUOUS", "WONCART", "REBUY"} and len(tags) == 4, tags)
    w.status("x")
    check("watch_lines_never_class_tagged", out[-1].startswith("WATCH ")
          and not any(out[-1].startswith(t) for t in tags))
    w, out = watch([CHAIN, FL + OID1 + " " + "Y" * 900])
    raw = out[0].split(" | ", 1)[1]
    check("raw_trimmed_300", len(raw) == 300 and raw.endswith("..."), len(raw))
    far = ["[FORWARDER] W3/alt-1 → 1.2.3.4:8000 (exit via account proxy)"] + ["noise"] * 40 + [FL + OID1]
    w, out = watch(far)
    check("far_tag_is_unknown_not_inherited", "near=?" in out[0] and w.stats["near_unknown"] == 1, out)
    roster = "[RACE] 1011960739: racing 3 accounts → ['W1/primary', 'W2/business', 'W3/alt-1']"
    w, out = watch([roster, FL + OID1])
    check("multi_account_line_sets_no_tag", "near=?" in out[0], out)
    w, out = watch(["[EXPOSURE] ident=W2/business tcin=1 kind=pass run_shots=1", FL + OID1])
    check("exposure_tag_w_and_name", "near=W2/business(-1L)" in out[0], out)
    w, out = watch(["[X] ident=None thing", FL + OID1])
    check("ident_none_ignored", "near=?" in out[0], out)
    w, out = watch(["[REAL_PURCHASE_THREAD] Marked thread as completing: 1011960739#W1", FL + OID1])
    check("thread_mark_tag", "near=#W1(-1L)" in out[0], out)

    # ---- 9. real logs (when the main tree's logs/runs is reachable) --------------------------
    runs = None
    cands = [ROOT / "logs" / "runs"]
    if ".build_wt" in ROOT.parts:
        cands.append(Path(*ROOT.parts[:ROOT.parts.index(".build_wt")]) / "logs" / "runs")
    for c in cands:
        if (c / "run_20260930_233818.log").exists():
            runs = c
            break
    if runs is None:
        print("[SKIP] real-log replays: logs/runs/run_20260930_233818.log not found (gitignored)")
    else:
        store = runs.parent / "events" / "events.sqlite"

        def store_orders(run):
            if not store.exists():
                return None
            try:
                db = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
                try:
                    return db.execute("select line, order_id from orders where run_id=? order by line",
                                      (run,)).fetchall()
                finally:
                    db.close()
            except sqlite3.Error:
                return None

        def raw_ids(path):
            ids = []
            with open(path, "rb") as fh:
                for i, b in enumerate(fh, 1):
                    s = b.decode("utf-8", "replace")
                    if "ORDER PLACED" in s or "Order placed —" in s:
                        m = re.search(r"order_id=([0-9A-Za-z-]{6,40})", s)
                        ids.append((i, m.group(1)))
            return ids

        # 10-01: the primary's order + the won-cart ride lines
        p = runs / "run_20260930_233818.log"
        out = []
        w = OW.Watcher(out.append)
        OW.replay(p, w)
        ev = events(out, "ORDER")
        check("real_1001_one_order", len(ev) == 1 and "oid=8cba94c1-8cc5-11f1-adbd-9de2787bfd93" in ev[0]
              and ":L51256 " in ev[0] and "near=primary" in ev[0], ev)
        wc = events(out, "WONCART")
        check("real_1001_woncart_accounted", w.stats["wc_start"] == 7 and w.stats["wc_end"] == 7
              and len(wc) + w.stats["woncart_suppressed"] == 14, (w.stats, len(wc)))
        check("real_1001_woncart_only_start_end", all(re.search(r"\| \[WON_CART_DIRECT\] (start entry=|end reason=)", l)
                                                      for l in wc) and len(wc) >= 1, wc[:2])
        n_wc_lines = sum(1 for b in open(p, "rb") if b"[WON_CART_DIRECT]" in b)
        check("real_1001_not_the_firehose", n_wc_lines > 200 and len(wc) < 20, (n_wc_lines, len(wc)))
        check("real_1001_summary_line", out[-1].startswith("WATCH summary lines=") and "ORDER=1" in out[-1], out[-1])
        check("real_1001_no_ambiguous", not events(out, "AMBIGUOUS") and not events(out, "REBUY"))
        out2 = []
        w2 = OW.Watcher(out2.append, rate_s=0)
        OW.replay(p, w2)
        check("real_1001_rate_off_all_14", len(events(out2, "WONCART")) == 14, len(events(out2, "WONCART")))

        for run, want in (("run_20260731_004924", 9), ("run_20260804_000646", 4)):
            p = runs / (run + ".log")
            if not p.exists():
                print(f"[SKIP] {run}: not found")
                continue
            out = []
            w = OW.Watcher(out.append)
            OW.replay(p, w)
            ev = events(out, "ORDER")
            ids = [re.search(r"oid=(\S+)", l).group(1) for l in ev]
            lines = [int(re.search(r":L(\d+) ", l).group(1)) for l in ev]
            raw = raw_ids(p)
            check(f"real_{run[4:12]}_count_{want}", len(ev) == want, len(ev))
            check(f"real_{run[4:12]}_ids_and_lines_match_raw", ids == [r[1] for r in raw]
                  and lines == [r[0] for r in raw], (ids, raw))
            check(f"real_{run[4:12]}_nothing_extra", w.stats["checkout_fallback"] == 0
                  and w.stats["checkout_absorbed"] == want and w.stats["dup_order_id"] == 0, w.stats)
            so = store_orders(run)
            if so is None:
                print(f"[SKIP] {run}: no event store to compare against")
            else:
                check(f"real_{run[4:12]}_equals_store_orders", len(so) == len(ev)
                      and [s[0] for s in so] == lines and {s[1] for s in so} == set(ids), (len(so), len(ev)))

    # ---- 10. follow mode: start at END, partial lines, newer file, truncation ----------------
    def append(path, text, crlf=True):
        data = text.replace("\n", "\r\n") if crlf else text
        with open(path, "ab") as fh:
            fh.write(data.encode("utf-8"))

    # the CLI (--replay) end to end, on a small synthetic file; a dead reader ends the watch
    with tempfile.TemporaryDirectory() as d:
        f_ = Path(d) / "run_20261015_000000.log"
        append(f_, CHAIN + "\n" + FL + OID1 + "\n" + CHK + "\n" + amb + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = OW.main(["--replay", str(f_)])
        txt = buf.getvalue().splitlines()
        check("cli_replay", rc == 0 and [l.split(" ")[0] for l in txt] == ["ORDER", "AMBIGUOUS", "WATCH"]
              and txt[-1].startswith("WATCH summary lines=4 ORDER=1 ") and "AMBIGUOUS=1" in txt[-1], txt)

    class DeadPipe:
        def write(self, s):
            raise BrokenPipeError()

        def flush(self):
            raise BrokenPipeError()

    real_stdout = sys.stdout
    sys.stdout = DeadPipe()
    try:
        try:
            OW._out("ORDER x")
            gone = False
        except SystemExit as e:
            gone = e.code == 0
    finally:
        sys.stdout = real_stdout
    check("dead_reader_ends_watch_quietly", gone)

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = d / "run_20261015_230000.log"
        append(a, "old line\n" + FL + OID2 + "\n" + CHK + "\n")           # an old order: must NOT replay
        out = []
        w = OW.Watcher(out.append)
        f = OW.Follower(d, w, newer_every=30)
        f.start(now=1000.0)
        check("follow_banner_end_of_file", out and out[0].startswith("WATCH following run_20261015_230000.log")
              and "end of file" in out[0], out)
        f.step(now=1001.0)
        check("follow_no_old_alerts", not events(out), out)
        append(a, FL + OID1 + "\n")
        n = f.step(now=1002.0)
        ev = events(out)
        check("follow_new_order_once", n == 1 and len(ev) == 1 and OID1 in ev[0], (n, out))
        check("follow_line_number_is_absolute", ":L4 " in ev[0], ev)
        f.step(now=1003.0)
        check("follow_no_repeat", len(events(out)) == 1)
        # a half-written last line is read whole when it completes
        append(a, "[FAST_LANE] *** ORDER PLA", crlf=False)
        f.step(now=1004.0)
        check("follow_partial_held", len(events(out)) == 1)
        append(a, "CED *** HTTP 200 at t=2.0s — order_id=" + "abcdef12-0003\n")
        f.step(now=1005.0)
        check("follow_partial_completed", len(events(out)) == 2 and "oid=abcdef12-0003" in events(out)[1], out)
        # a newer run_*.log: found within newer_every, the old file drained first, new one from byte 0
        append(a, amb + "\n")                                             # still the old file's
        b = d / "run_20261015_233100.log"
        append(b, "boot\n" + FL + "abcdef12-0004\n")
        f.step(now=1020.0)                                                # < 30 s since start: not yet
        check("follow_newer_not_before_interval", not any("switched" in l for l in out)
              and not any("abcdef12-0004" in l for l in out), out)
        f.step(now=1031.0)
        sw = [l for l in out if l.startswith("WATCH switched")]
        evs = events(out)
        check("follow_switches_within_interval", len(sw) == 1 and "run_20261015_233100.log" in sw[0], out)
        check("follow_old_file_event_kept", any(l.startswith("AMBIGUOUS ") for l in evs), evs)
        check("follow_new_file_from_byte_0", any("oid=abcdef12-0004" in l and ":L2 " in l for l in evs), evs)
        append(b, FL + "abcdef12-0005\n")
        f.step(now=1032.0)
        check("follow_continues_on_new_file", any("oid=abcdef12-0005" in l for l in events(out)), out)
        # relaunches that came and went between two checks are read in full, the newest followed
        c = d / "run_20261015_233200.log"
        e = d / "run_20261015_233300.log"
        append(c, FL + "abcdef12-0006\n")
        append(e, "boot\n")
        f.step(now=1062.0)
        evs = events(out)
        check("follow_intermediate_logs_read", any("oid=abcdef12-0006" in l for l in evs)
              and any(l.startswith("WATCH switched") and "run_20261015_233300.log" in l for l in out), out)
        # truncation in place
        append(e, "x" * 5000 + "\n")
        f.step(now=1063.0)
        e.write_bytes(b"")
        append(e, FL + "abcdef12-0007\n")
        f.step(now=1064.0)
        check("follow_truncation_restarts", any("oid=abcdef12-0007" in l for l in events(out))
              and any("shrank" in l for l in out), out)
        # idle: a suppressed count that no later line carried is reported by flush on a step
        append(e, "\n".join(lg(10, wc_s) for _ in range(3)) + "\n")
        f.step(now=1065.0)
        f.step(now=time.time() + 7200)
        check("follow_idle_flush_reports_count", any(l.startswith("WATCH suppressed WONCART=2") for l in out), out)

    # the old file's last bytes, written after the step's read but before the newer-file check,
    # are drained before the switch (_check_newer called straight after the append)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = d / "run_20261015_230000.log"
        append(a, "boot\n")
        out = []
        w = OW.Watcher(out.append)
        f = OW.Follower(d, w, newer_every=30)
        f.start(now=0.0)
        append(a, FL + "abcdef12-0009\n" + "[FAST_LANE] *** ORDER PLACED *** HTTP 200 at t=2.0s — order_id=abcdef12-0010")
        append(d / "run_20261015_233100.log", "boot\n")
        f._check_newer()
        evs = events(out)
        check("follow_old_tail_drained_before_switch", any("oid=abcdef12-0009" in l for l in evs)
              and any("oid=abcdef12-0010" in l for l in evs), out)       # incl. the unterminated last line

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = []
        w = OW.Watcher(out.append)
        f = OW.Follower(d, w, newer_every=30)
        f.start(now=0.0)
        check("empty_dir_waits", out and "no run_*.log" in out[0], out)
        f.step(now=1.0)
        append(d / "run_20261015_000001.log", "boot\n" + FL + "abcdef12-0008\n")
        f.step(now=2.0)
        check("first_log_after_start_read_from_byte_0", any("oid=abcdef12-0008" in l for l in events(out)), out)

    # ---- 11. read-only: only 'rb' opens, only inside the logs dir, nothing written ----------
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = d / "run_20261015_000000.log"
        append(a, "boot\n")
        out = []
        w = OW.Watcher(out.append)
        f = OW.Follower(d, w, newer_every=0)
        f.start(now=0.0)
        append(a, FL + OID1 + "\n")
        append(d / "run_20261015_000100.log", "boot\n" + FL + OID2 + "\n")
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir()}
        opened = []
        real_open = builtins.open

        def spy(file, mode="r", *args, **kw):
            opened.append((str(file), mode))
            return real_open(file, mode, *args, **kw)

        builtins.open = spy
        try:
            f.step(now=1.0)                                   # reads a, switches to the newer log
            f.step(now=2.0)
            OW.replay(a, OW.Watcher(out.append))
            with contextlib.redirect_stdout(io.StringIO()):
                OW.main(["--replay", str(a)])
        finally:
            builtins.open = real_open
        check("readonly_watched_both", any(OID1 in l for l in events(out)) and any(OID2 in l for l in events(out)), out)
        check("readonly_modes", opened and all(m == "rb" for _, m in opened), opened)
        check("readonly_only_logs_dir", all(Path(p).resolve().parent == d.resolve() for p, _ in opened), opened)
        after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir()}
        check("readonly_dir_unchanged_by_watcher", after == before, (before, after))

    src = TOOL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad_calls, open_modes = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name == "open":
                mode = node.args[1] if len(node.args) > 1 else None
                open_modes.append(mode.value if isinstance(mode, ast.Constant) else None)
            if name in ("write", "write_text", "write_bytes", "unlink", "rename", "replace", "touch",
                        "mkdir", "truncate", "remove", "rmdir", "system", "Popen", "run", "copy", "move"):
                bad_calls.append(name)
    imports = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
              {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    check("source_open_is_rb_only", open_modes and all(m == "rb" for m in open_modes), open_modes)
    check("source_no_write_or_spawn_calls", not bad_calls, bad_calls)
    check("source_no_subprocess_socket_shutil", not (imports & {"subprocess", "socket", "shutil", "os"}), imports)
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    code_strings = [s for s in strings if len(s) < 200]
    check("source_never_names_config_or_jars", not any(re.search(r"config[/\\]|target[-\d]*\.json|state[/\\]", s)
                                                       for s in code_strings), [s for s in code_strings if "config" in s])

    # ---- 12. same newest-log rule as boot_check ---------------------------------------------
    import boot_check as BC
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        runs_d = d / "logs" / "runs"
        runs_d.mkdir(parents=True)
        for n in ("run_20261014_231617.log", "run_20261015_000100.log", "run_20261015_000099.log.bak",
                  "notes.txt", "run_20261015_0001.log", "run_20261013_235959.log"):
            (runs_d / n).write_text("x")
        check("newest_matches_boot_check", BC.newest_run_log(d).name == OW.newest_run_log(runs_d).name
              == "run_20261015_000100.log", (BC.newest_run_log(d), OW.newest_run_log(runs_d)))
        check("newest_none_when_empty", OW.newest_run_log(d / "nope") is None)

    # ---- 13. live subprocess: flushed lines, class tag first, start at end, switch -----------
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = d / "run_20261015_230000.log"
        append(a, FL + OID2 + "\n")                                        # old: never reported
        proc = subprocess.Popen([sys.executable, "-u", str(TOOL), "--logs", str(d), "--poll", "0.1",
                                 "--newer-every", "1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", bufsize=1)
        q: "queue.Queue[str]" = queue.Queue()

        def pump():
            for ln in proc.stdout:
                q.put(ln.rstrip("\r\n"))
        t = threading.Thread(target=pump, daemon=True)
        t.start()

        def wait_for(pred, secs=15.0):
            end, seen = time.time() + secs, []
            while time.time() < end:
                try:
                    ln = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                seen.append(ln)
                if pred(ln):
                    return ln, seen
            return None, seen

        try:
            ln, seen = wait_for(lambda s: s.startswith("WATCH following"))
            check("proc_banner", ln is not None, seen)
            time.sleep(0.5)
            append(a, FL + OID1 + "\n")
            ln, seen = wait_for(lambda s: s.startswith("ORDER "))
            check("proc_order_line_flushed", ln is not None and OID1 in ln and "seen=" in ln, seen)
            check("proc_old_order_not_replayed", not any(OID2 in s for s in seen), seen)
            b = d / "run_20261015_233100.log"
            append(b, "boot\n" + amb + "\n")
            ln, seen = wait_for(lambda s: s.startswith("AMBIGUOUS "), secs=15.0)
            check("proc_switched_and_alerted", ln is not None, seen)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for n in FAILED:
        print(f"  FAIL: {n}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
