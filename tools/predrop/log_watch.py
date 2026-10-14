#!/usr/bin/env python3
"""Overnight log watch -- one line per event that matters, nothing else. READ-ONLY.

    python -u tools/predrop/log_watch.py            # wait for the next run log, then follow it
    python -u tools/predrop/log_watch.py --replay logs/runs/run_20261007_002557.log   # offline test

Follows the newest logs/runs/run_*.log (waiting for a NEW one to appear if --after is given or
the newest is older than --max-age) and logs/bot_restart_wrapper.log. Emits:
  [CRIT]  the bot cannot buy or money needs a look: Exception in thread, STALL, exit 87 / crash /
          relaunch loop, AMBIGUOUS_COMMIT, DOUBLE-BUY GUARD, login check False, BOOT_SKIP,
          STATS with 403/429 > 0 or < 15 sessions, log silent > --silent-s, run log stopped
  [ORDER] an order placed / checkout complete (check order history within 2 min)
  [STOCK] IN STOCK flips, races, NOW VISIBLE, held-line strikes, won carts (first line per
          TCIN per 60 s)
  [BOOT]  each boot marker once (worker pool, logged in, threads, pool ready, first STATS,
          ground truth, visibility banner, FX1006 flags, watchdog)
  [WARN]  WRITE-AUTH DEAD persisting > 5 min on an account, non-benign tracebacks (type, once
          per 10 min per type), decoy 5xx bursts, [STOCK][OTHER] > 2%/min
  [ALIVE] a heartbeat every --heartbeat-s (default 1800) with the latest STATS + HEALTH
Never launches, never writes. Designed for the Monitor tool: every stdout line is an event.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
RUN_NAME = re.compile(r"run_(\d{8})_(\d{6})\.log$")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d{3}")
BENIGN_TRACE = re.compile(r"ConnectionResetError|WinError 10054|_call_connection_lost|ConnectionAbortedError|WinError 10053")

BOOT_ONCE = [
    ("worker_pool", re.compile(r"\[WORKER_POOL\] sized from target_accounts\.json: (\d+) account\(s\)")),
    ("login_check", re.compile(r"\[BACKGROUND\] Login check result: (True|False)")),
    ("logged_in", re.compile(r"LOGGED IN TO TARGET\.COM")),
    ("stock_thread", re.compile(r"\[MONITORING_LOOP\] \[OK\] stock_thread started")),
    ("purchase_thread", re.compile(r"\[MONITORING_LOOP\] \[OK\] purchase_thread started")),
    ("all_threads", re.compile(r"All threads started successfully")),
    ("fx1006", re.compile(r"\[FX1006\] multi-SKU dispatch flags: (.*)$")),
    ("watchdog", re.compile(r"\[STALL_WATCHDOG\] armed")),
    ("stock_started", re.compile(r"\[STOCK\] started: (.*)$")),
    ("pool_ready", re.compile(r"\[MULTI_SESSION\] started .{1,3} (\d+)/(\d+) sessions ready")),
    ("first_stats", re.compile(r"\[STOCK STATS\] t=\S+ sweeps=\d+ \([\d.]+/s\) 200=\d+ 403=(\d+) 429=(\d+) other=\d+.*sessions=(\d+)r")),
    ("ground_truth", re.compile(r"\[GROUND-TRUTH\] pool cache-bust ok: in_stock=\[([^\]]*)\] \((\d+) TCINs\)")),
    ("visibility", re.compile(r"\[TCIN-VISIBILITY\] (\d+) of (\d+) configured TCIN\(s\) are INVISIBLE[^\[]*(\[[^\]]*\])")),
    ("cart_audit", re.compile(r"\[BOOT_CART_AUDIT\] (cart empty[^\n]{0,40}|adopt[^\n]{0,80}|delet[^\n]{0,80})")),
]
STATS = re.compile(r"\[STOCK STATS\] t=(\S+) sweeps=(\d+) \(([\d.]+)/s\) 200=(\d+) 403=(\d+) 429=(\d+) other=(\d+).*sessions=(\d+)r/(\d+)c")
HEALTH = re.compile(r"\[HEALTH\] (rss=\S+) .*?(handles=\S+) .*?(chrome_procs_all=\S+) .*?(sys_mem=\S+)")
WRITE_AUTH = re.compile(r"\[WARMUP#\d+/([^\]]+)\] (dummy POST status \d+ .{1,3} write-auth alive|WRITE-AUTH DEAD)")
OTHER_LINE = re.compile(r"\[STOCK\]\[OTHER\] (.{0,160})")
TCIN = re.compile(r"\b(\d{8,10})\b")

CRIT = [
    (re.compile(r"^Exception in thread"), "exception in thread"),
    (re.compile(r"\[STALL_WATCHDOG\] STALL:"), "stall"),
    (re.compile(r"\[AMBIGUOUS_COMMIT\]"), "AMBIGUOUS_COMMIT -- check order history now"),
    (re.compile(r"\[DOUBLE-BUY GUARD\]"), "DOUBLE-BUY GUARD -- check order history now"),
    (re.compile(r"\[BOOT_SKIP\] skipping"), "account skipped at boot"),
    (re.compile(r"\[CVV\]\[ERROR\]"), "CVV config error"),
    (re.compile(r"not logged in|NOT LOGGED IN|exit code 87|exiting 87", re.I), "not logged in"),
    (re.compile(r"\[TCIN-VISIBILITY\] UNKNOWN"), "ground-truth reads failing"),
    (re.compile(r"STOCK-AWARE RESET VERIFICATION FAILED"), "reset verification failed"),
    (re.compile(r"\[MULTI_SKU_FAIR_SHARE\].*plan failed"), "fair-share plan failed"),
]
ORDER = re.compile(r"ORDER PLACED|Checkout complete|order placed|\[HELD_LINE_STRIKE\] order placed|\[WON_CART_DIRECT\].*(placed|ORDER)", re.I)
STOCK = [
    (re.compile(r"\[STOCK\] IN STOCK:(.*)$"), "IN STOCK"),
    (re.compile(r"\[STOCK\]\[FLIP\].*new_window=1(.*)$"), "flip"),
    (re.compile(r"\[RACE\] (.*)$"), "race"),
    (re.compile(r"\[TCIN-VISIBILITY\] NOW VISIBLE(.*)$"), "NOW VISIBLE"),
    (re.compile(r"\[HELD_LINE_STRIKE\](.*)$"), "held-line strike"),
    (re.compile(r"\[WON_CART_DIRECT\](.*)$"), "won cart"),
    (re.compile(r"\[FAST_LANE\] Firing(.*)$"), "fast lane"),
    (re.compile(r"status=201|HTTP 201"), "ATC 201 (cart)"),
    (re.compile(r"\[MULTI_SKU_MISS\](.*)$"), "multi-SKU miss"),
    (re.compile(r"\[MULTI_SKU_FAIR_SHARE\](.*)$"), "fair share"),
    (re.compile(r"\[WAVE_YIELD\](.*)$"), "wave yield"),
    (re.compile(r"\[FS_TICKET\](.*)$"), "FS ticket"),
]
WRAPPER_EVENTS = re.compile(r"app\.py exited code=|exit 87|clean exit|launch #\d+|guard TRIPPED|cooling down|account relogin")


def now_s() -> str:
    return datetime.now().strftime("%H:%M:%S")


def newest_run_log(root: Path):
    runs = root / "logs" / "runs"
    best = None
    try:
        for p in runs.iterdir():
            if RUN_NAME.search(p.name) and (best is None or p.name > best.name):
                best = p
    except OSError:
        return None
    return best


class Watch:
    def __init__(self, heartbeat_s: int, silent_s: int, emit=print):
        self.emit = emit
        self.heartbeat_s = heartbeat_s
        self.silent_s = silent_s
        self.seen_boot = set()
        self.last_stats = ""
        self.last_health = ""
        self.last_line_t = time.time()
        self.last_heartbeat = time.time()
        self.write_auth = {}         # acct -> (state, since)
        self.wa_alerted = set()
        self.trace_pending = False
        self.trace_seen = {}         # type -> last emitted t
        self.stock_last = {}         # (kind, tcin) -> t
        self.decoy_5xx = []
        self.bad_stats_last = 0.0
        self.silent_alerted = False
        self.log_stamp = ""

    def _ts(self, line: str) -> str:
        m = STAMP.search(line)
        if m:
            self.log_stamp = m.group(1)
        return self.log_stamp or now_s()

    def line(self, raw: str, t: float | None = None):
        t = time.time() if t is None else t
        self.last_line_t = t
        self.silent_alerted = False
        raw = raw.rstrip("\r\n")
        if not raw:
            return
        ts = self._ts(raw)
        # tracebacks: emit the exception type (non-benign), throttled per type
        if raw.startswith("Traceback"):
            self.trace_pending = True
            return
        if self.trace_pending and re.match(r"^[A-Za-z_.]*(Error|Exception|Exit)\b", raw):
            self.trace_pending = False
            if not BENIGN_TRACE.search(raw):
                key = raw[:70]
                if t - self.trace_seen.get(key, 0) > 600:
                    self.trace_seen[key] = t
                    self.emit(f"[WARN] {ts} traceback: {raw[:160]}")
            return
        for rx, why in CRIT:
            if rx.search(raw):
                self.emit(f"[CRIT] {ts} {why}: {raw[:200]}")
                break
        if ORDER.search(raw) and "[DOUBLE-BUY GUARD]" not in raw:
            self.emit(f"[ORDER] {ts} {raw[:220]}  <- check Target order history within 2 min")
        for key, rx in BOOT_ONCE:
            if key in self.seen_boot:
                continue
            m = rx.search(raw)
            if m:
                self.seen_boot.add(key)
                if key == "login_check" and m.group(1) == "False":
                    self.emit(f"[CRIT] {ts} login check False -- Worker 1 not logged in")
                elif key == "first_stats":
                    bad = int(m.group(1)) or int(m.group(2))
                    self.emit(f"[{'CRIT' if bad else 'BOOT'}] {ts} first STATS: {raw[raw.find('[STOCK STATS]'):][:150]}")
                elif key == "visibility":
                    self.emit(f"[BOOT] {ts} visibility banner: {m.group(1)} of {m.group(2)} invisible: {m.group(3)[:200]}")
                else:
                    self.emit(f"[BOOT] {ts} {key}: {raw[raw.find('['):][:160] if '[' in raw else raw[:160]}")
                break
        m = STATS.search(raw)
        if m:
            tt, sweeps, rate, ok, e403, e429, other, sr, sc = m.groups()
            self.last_stats = f"t={tt} sweeps={sweeps} {rate}/s 200={ok} 403={e403} 429={e429} other={other} sessions={sr}r/{sc}c"
            if (int(e403) > 0 or int(e429) > 0 or int(sr) < 15) and t - self.bad_stats_last > 300:
                self.bad_stats_last = t
                self.emit(f"[CRIT] {ts} monitor trouble: {self.last_stats}")
        m = HEALTH.search(raw)
        if m:
            self.last_health = " ".join(m.groups())
        m = WRITE_AUTH.search(raw)
        if m:
            acct, what = m.group(1), m.group(2)
            state = "alive" if "alive" in what else "DEAD"
            prev = self.write_auth.get(acct)
            if prev is None or prev[0] != state:
                self.write_auth[acct] = (state, t)
                if state == "alive" and acct in self.wa_alerted:
                    self.wa_alerted.discard(acct)
                    self.emit(f"[OK] {ts} {acct}: write-auth back alive")
        for rx, kind in STOCK:
            m = rx.search(raw)
            if m:
                tm = TCIN.search(raw)
                key = (kind, tm.group(1) if tm else "")
                if t - self.stock_last.get(key, 0) > 60:
                    self.stock_last[key] = t
                    self.emit(f"[STOCK] {ts} {kind}: {raw[:200]}")
                break
        m = OTHER_LINE.search(raw)
        if m and re.search(r"5\d\d|timeout", m.group(1)):
            mm = re.search(r"(\d+)\s*(?:other|of)\D{0,10}(\d+)", m.group(1))
            if mm and int(mm.group(2)) and int(mm.group(1)) / int(mm.group(2)) > 0.02 and t - self.bad_stats_last > 300:
                self.bad_stats_last = t
                self.emit(f"[WARN] {ts} monitor other > 2%/min: {m.group(1)[:140]}")
        if "[ATC_RESP]" in raw and re.search(r"status=5\d\d|HTTP 5\d\d", raw):
            self.decoy_5xx = [x for x in self.decoy_5xx if t - x < 300] + [t]
            if len(self.decoy_5xx) >= 5:
                self.decoy_5xx = []
                self.emit(f"[WARN] {ts} 5 decoy 5xx in 5 min: {raw[:140]}")

    def tick(self, t: float | None = None):
        t = time.time() if t is None else t
        for acct, (state, since) in self.write_auth.items():
            if state == "DEAD" and t - since > 300 and acct not in self.wa_alerted:
                self.wa_alerted.add(acct)
                self.emit(f"[WARN] {now_s()} {acct}: WRITE-AUTH DEAD for > 5 min (a short burst is normal Shape burn; persistent = token problem)")
        if self.heartbeat_s and t - self.last_heartbeat >= self.heartbeat_s:
            self.last_heartbeat = t
            self.emit(f"[ALIVE] {now_s()} {self.last_stats or 'no STATS yet'} | {self.last_health or 'no HEALTH yet'} | write-auth {', '.join(f'{a}={s[0]}' for a, s in sorted(self.write_auth.items())) or '-'}")
        if self.silent_s and t - self.last_line_t > self.silent_s and not self.silent_alerted:
            self.silent_alerted = True
            self.emit(f"[CRIT] {now_s()} run log silent for {int(t - self.last_line_t)} s -- the process may be dead or wedged (check the bat window; tools/predrop/boot_check.py)")

    def wrapper_line(self, raw: str):
        raw = raw.rstrip("\r\n")
        if WRAPPER_EVENTS.search(raw):
            sev = "CRIT" if re.search(r"exited code=(?!0\b)|exit 87|TRIPPED|cooling", raw) else "INFO"
            if "exited code=0" in raw or "clean exit" in raw:
                sev = "CRIT"   # a clean stop overnight is still the bot being down
            self.emit(f"[{sev}] {now_s()} wrapper: {raw[:180]}")


def follow(path: Path, start_at_end: bool):
    f = open(path, "r", encoding="utf-8", errors="replace")
    if start_at_end:
        f.seek(0, os.SEEK_END)
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--replay", help="process this run log offline and exit (test mode)")
    ap.add_argument("--after", default="", help="only follow a run log whose name sorts after this one (e.g. run_20261007_002557.log)")
    ap.add_argument("--max-age", type=int, default=3600, help="without --after: wait for a run log newer than this many seconds")
    ap.add_argument("--heartbeat-s", type=int, default=1800)
    ap.add_argument("--silent-s", type=int, default=180)
    ap.add_argument("--wait-s", type=int, default=7200, help="how long to wait for a new run log before giving up")
    ap.add_argument("--from-end", action="store_true", help="re-arm mode: start at the end of the run log (no boot replay)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    w = Watch(a.heartbeat_s if not a.replay else 0, a.silent_s if not a.replay else 0)

    if a.replay:
        text = Path(a.replay).read_text(encoding="utf-8", errors="replace")
        for ln in text.splitlines():
            w.line(ln)
        w.tick()
        print(f"[REPLAY] done: {len(text.splitlines())} lines; boot markers seen: {sorted(w.seen_boot)}")
        return 0

    print(f"[WATCH] {now_s()} waiting for a run log{(' after ' + a.after) if a.after else ''} ...")
    t0 = time.time()
    log = None
    while time.time() - t0 < a.wait_s:
        cand = newest_run_log(root)
        if cand and (cand.name > a.after if a.after else (time.time() - cand.stat().st_mtime) < a.max_age):
            log = cand
            break
        time.sleep(5)
    if log is None:
        print(f"[CRIT] {now_s()} no new run log appeared within {a.wait_s} s -- the bat was not started, or it died before app.py wrote a log (check logs/bot_restart_wrapper.log)")
        return 1
    print(f"[WATCH] {now_s()} following {log.name} (+ bot_restart_wrapper.log){' from the live end' if a.from_end else ''}")
    rf = follow(log, start_at_end=a.from_end)
    wpath = root / "logs" / "bot_restart_wrapper.log"
    wf = follow(wpath, start_at_end=True) if wpath.exists() else None
    w.last_line_t = time.time()
    while True:
        progressed = False
        for ln in rf:
            w.line(ln)
            progressed = True
        if wf:
            for ln in wf:
                w.wrapper_line(ln)
                progressed = True
        w.tick()
        # a newer run log = the wrapper relaunched app.py: switch to it
        cand = newest_run_log(root)
        if cand and cand.name > log.name:
            print(f"[CRIT] {now_s()} a NEW run log appeared ({cand.name}) -- app.py was relaunched; boot markers follow")
            log = cand
            rf.close()
            rf = follow(log, start_at_end=False)
            w.seen_boot.clear()
            w.last_line_t = time.time()
        if not progressed:
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
