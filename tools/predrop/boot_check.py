#!/usr/bin/env python3
"""Boot check -- did the bot that just started come up ABLE TO BUY?

READ-ONLY. Reads the newest logs/runs/run_*.log (or --log), logs/bot_restart_wrapper.log
and logs/relogin.log; never launches, never writes, never touches the network beyond a
local socket probe of the dashboard port. Exit code 0 = GREEN, 1 = RED, 2 = PENDING
(--follow turns PENDING into a wait).

Why (2026-10-07 00:21): a diagnostics change raised an AttributeError inside
StockMonitorThread.start(); the MonitoringLoop thread died BEFORE purchase_thread.start(),
so the bot monitored stock all night but could never buy, and the process looked alive.
Nothing in the boot checklist named that case. This script checks every marker that a
healthy boot prints, in order, with the elapsed seconds since boot, and FAILS on the
signatures that have actually broken boots here:

  - `Exception in thread` / a Traceback between "Starting bulletproof event-driven
    architecture" and "All threads started successfully" (10-07)
  - `[BACKGROUND] Login check result: False` / wrapper `exit 87` (the guest Worker-1 loop)
  - `[WORKER_POOL] sized ... N account(s)` != enabled accounts; `[BOOT_SKIP] skipping`
  - first `[STOCK STATS]` with 403 > 0 or 429 > 0; `[MULTI_SESSION] started -- n/N` short
  - `[GROUND-TRUTH] ... (N TCINs)` != enabled TCINs (08-24: 9 of 13); `[TCIN-VISIBILITY]`
    naming an enabled TCIN
  - the wrapper recorded `app.py exited code=` after this launch (the process is gone)

Usage:
    python tools/predrop/boot_check.py                 # newest run log, one pass
    python tools/predrop/boot_check.py --follow        # re-check every 15 s until GREEN/RED
    python tools/predrop/boot_check.py --log logs/runs/run_20261007_002557.log --json

Print-only markers carry no timestamp: their time is the last logger stamp before them
(the event store's rule). A marker that is not yet due prints PENDING with its deadline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]

STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}),(\d{3})")
RUN_NAME = re.compile(r"run_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.log$")
WRAP_TS = re.compile(r"^\[\w{3} (\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\.(\d{2})\]\s*(.*)$")
RELOGIN_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2}) (.*)$")

# (id, regex, deadline_s, required) -- the healthy 10-07 00:25:57 boot printed them at
# +1 s, +11 s, +12 s, +12 s, +12 s, +216 s, +246 s, +247 s. Deadlines are generous.
MARKERS = [
    ("worker_pool", re.compile(r"\[WORKER_POOL\] sized from target_accounts\.json: (\d+) account\(s\)"), 90, True),
    ("login_check", re.compile(r"\[BACKGROUND\] Login check result: (True|False)"), 240, True),
    ("logged_in", re.compile(r"LOGGED IN TO TARGET\.COM"), 240, True),
    ("stock_thread", re.compile(r"\[MONITORING_LOOP\] \[OK\] stock_thread started"), 300, True),
    ("purchase_thread", re.compile(r"\[MONITORING_LOOP\] \[OK\] purchase_thread started \(is_alive: True\)"), 300, True),
    ("all_threads", re.compile(r"\[MONITORING_LOOP\] .*All threads started successfully"), 300, True),
    ("pool_ready", re.compile(r"\[MULTI_SESSION\] started .{1,3} (\d+)/(\d+) sessions ready"), 480, True),
    ("first_stats", re.compile(r"\[STOCK STATS\] t=\S+ sweeps=(\d+) \(([\d.]+)/s\) 200=(\d+) 403=(\d+) 429=(\d+) other=(\d+)(?: beh=\d+)? outstanding=\d+ sessions=(\d+)r/(\d+)c"), 540, True),
    ("ground_truth", re.compile(r"\[GROUND-TRUTH\] pool cache-bust ok: in_stock=\[([^\]]*)\] \((\d+) TCINs\)"), 600, True),
]
BENIGN_TRACE = re.compile(r"ConnectionResetError|WinError 10054|_call_connection_lost|ConnectionAbortedError|WinError 10053")
# Only the first 15 min of a log are "the boot": per-account write-auth / harvest state,
# tracebacks and stalls are read from this window, so a day-long log reports its boot,
# not its afternoon. The ordered MARKERS are first-occurrence and unaffected.
BOOT_WINDOW_S = 900


def _local(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "?"


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


def boot_ts_from_name(path: Path):
    m = RUN_NAME.search(path.name)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    return datetime(y, mo, d, h, mi, s).timestamp()


def stamp_ts(m) -> float:
    y, mo, d, h, mi, s, ms = (int(x) for x in m.groups())
    return datetime(y, mo, d, h, mi, s, ms * 1000).timestamp()


def enabled_tcins(root: Path):
    try:
        cfg = json.loads((root / "config" / "product_config.json").read_text(encoding="utf-8"))
        return [str(p["tcin"]) for p in cfg.get("products", []) if p.get("enabled", True)]
    except Exception:
        return None


def enabled_accounts(root: Path):
    try:
        cfg = json.loads((root / "config" / "target_accounts.json").read_text(encoding="utf-8"))
        return [a["account_id"] for a in cfg.get("accounts", []) if a.get("enabled", True)]
    except Exception:
        return None


def armed_flags(root: Path) -> dict:
    out = {}
    try:
        for raw in (root / "run_bot_with_nightly_restart.bat").read_bytes().decode("utf-8", "replace").splitlines():
            m = re.match(r"^\s*set\s+([A-Za-z0-9_]+)=(.*?)\s*$", raw)
            if m:
                out[m.group(1)] = m.group(2)
    except Exception:
        pass
    return out


# -- wrapper + relogin logs ---------------------------------------------------

def parse_wrapper(text: str, boot_ts: float) -> dict:
    """The wrapper lines around THIS launch: the last 'launch #N app.py' at or before
    boot_ts (+90 s slack, the wrapper stamps before app.py names its log), the login /
    readiness codes just before it, and anything the wrapper wrote after it."""
    events = []
    for line in text.splitlines():
        m = WRAP_TS.match(line.strip())
        if not m:
            continue
        mo, d, y, h, mi, s, cs, rest = m.groups()
        try:
            ts = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), int(cs) * 10000).timestamp()
        except ValueError:
            continue
        events.append((ts, rest.strip()))
    launch_i = None
    for i, (ts, rest) in enumerate(events):
        if re.search(r"launch #\d+ app\.py", rest) and ts <= boot_ts + 90:
            launch_i = i
    res = {"found": launch_i is not None, "launch": None, "login_code": None, "readiness_code": None,
           "after": [], "exited_code": None, "exit87": False, "guard_tripped": False}
    if launch_i is None:
        return res
    res["launch"] = events[launch_i][1]
    for ts, rest in events[max(0, launch_i - 8):launch_i]:
        m = re.search(r"account login done code=(-?\d+)", rest)
        if m:
            res["login_code"] = int(m.group(1))
        m = re.search(r"drop-readiness check done code=(-?\d+)", rest)
        if m:
            res["readiness_code"] = int(m.group(1))
    for ts, rest in events[launch_i + 1:]:
        res["after"].append(rest)
        m = re.search(r"app\.py exited code=(-?\d+)", rest)
        if m and res["exited_code"] is None:
            res["exited_code"] = int(m.group(1))
        if "exit 87" in rest:
            res["exit87"] = True
        if "guard TRIPPED" in rest:
            res["guard_tripped"] = True
    return res


def parse_relogin(text: str, boot_ts: float) -> dict:
    """Per-account result of the last relogin pass that started before boot_ts."""
    blocks = []
    cur = None
    for line in text.splitlines():
        m = RELOGIN_TS.match(line.strip())
        if not m:
            continue
        y, mo, d, h, mi, s, rest = m.groups()
        try:
            ts = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)).timestamp()
        except ValueError:
            continue
        if "[PLAN]" in rest and "account(s)" in rest:
            cur = {"ts": ts, "accounts": {}, "n": None}
            mm = re.search(r"(\d+) account\(s\)", rest)
            cur["n"] = int(mm.group(1)) if mm else None
            blocks.append(cur)
            continue
        if cur is None:
            continue
        mm = re.match(r"\[ACCOUNT\] === (\S+) \|", rest)
        if mm:
            cur["accounts"].setdefault(mm.group(1), "no result")
            continue
        mm = re.match(r"\[RESULT\] (\S+): (.*)$", rest)
        if mm:
            cur["accounts"][mm.group(1)] = mm.group(2)
    use = None
    for b in blocks:
        if b["ts"] <= boot_ts + 5:
            use = b
    return use or {"ts": None, "accounts": {}, "n": None}


# -- the run log --------------------------------------------------------------

def analyze_log(text: str, boot_ts: float, now: float, tcins, accounts, armed: dict,
                cfg_changed: bool = False, acct_changed: bool = False) -> dict:
    """cfg_changed / acct_changed: the config file was modified AFTER this boot, so the
    enabled counts on disk are not what this run booted with -- count mismatches are then
    informational, not failures (a pre-drop edits the config after the last run)."""
    rows = []          # dicts: id, status, detail, line, t
    seen = {}
    last_stamp = boot_ts
    first_stamp = None
    in_thread_start = False
    thread_start_exc = None
    write_auth = {}
    selftest = {}
    first_sight = {}
    vpc = []
    visibility = None
    tracebacks = {"benign": 0, "other": {}}
    pending_tb = False
    stall = None
    exc_in_thread = []
    boot_skip = []
    fx1006 = None
    watchdog_armed = False
    health = None
    proxies_loaded = None
    init_flags = {}
    cart_audit = []
    latch_restored = None
    lines = text.splitlines()
    age = max(0.0, now - boot_ts)

    for no, raw in enumerate(lines, 1):
        # time: the last logger stamp before this physical line (glued lines included)
        for sm in STAMP.finditer(raw):
            ts = stamp_ts(sm)
            if first_stamp is None:
                first_stamp = ts
            last_stamp = ts
        t = last_stamp - boot_ts
        for mid, rx, deadline, req in MARKERS:
            if mid in seen:
                continue
            m = rx.search(raw)
            if m:
                seen[mid] = (m, no, t)
        in_window = t <= BOOT_WINDOW_S
        if "Starting bulletproof event-driven architecture" in raw:
            in_thread_start = True
        if "All threads started successfully" in raw:
            in_thread_start = False
        if raw.startswith("Exception in thread") and in_window:
            exc_in_thread.append((no, raw.strip()[:120]))
            if in_thread_start and thread_start_exc is None:
                thread_start_exc = (no, raw.strip()[:120], False)   # (line, text, resolved)
        if raw.startswith("Traceback"):
            pending_tb = True
            if in_thread_start and thread_start_exc is None:
                thread_start_exc = (no, "Traceback while starting threads", False)
            continue
        if pending_tb and re.match(r"^[A-Za-z_.]*(Error|Exception|Exit)\b", raw):
            pending_tb = False
            benign = bool(BENIGN_TRACE.search(raw))
            if in_window:
                if benign:
                    tracebacks["benign"] += 1
                else:
                    key = raw.strip()[:90]
                    tracebacks["other"][key] = tracebacks["other"].get(key, 0) + 1
            if thread_start_exc is not None and not thread_start_exc[2]:
                if not benign:
                    thread_start_exc = (thread_start_exc[0], raw.strip()[:160], True)
                elif not in_thread_start:
                    thread_start_exc = None          # only benign noise, and the threads came up
        m = re.search(r"\[WARMUP#\d+/([^\]]+)\] (dummy POST status \d+ .{1,3} write-auth alive|WRITE-AUTH DEAD[^\n]{0,60})", raw)
        if m and in_window:
            write_auth[m.group(1)] = ("alive" if "alive" in m.group(2) else "DEAD", no, t)
        m = re.search(r"\[HARVEST/([^\]]+)\] SELFTEST verdict: probes=(\d+) replayed=(\d+) accepted=\[([^\]]*)\] rejected=\[([^\]]*)\]", raw)
        if m and m.group(1) not in selftest:
            selftest[m.group(1)] = (m.group(4), m.group(5), no, t)
        for m in re.finditer(r"\[STOCK STATUS\] tcin=(\d+) #1 old=\(first\) new=([^ ]+)", raw):
            first_sight.setdefault(m.group(1), (m.group(2), no, t))
            if "VPC" in m.group(2):
                vpc.append(m.group(1))
        m = re.search(r"\[TCIN-VISIBILITY\] (\d+) of (\d+) configured TCIN\(s\) are INVISIBLE[^\[]*(\[[^\]]*\])", raw)
        if m and visibility is None:
            visibility = (m.group(1), m.group(2), m.group(3), no, t)
        m = re.search(r"\[STALL_WATCHDOG\] STALL: (.{0,120})", raw)
        if m and stall is None and in_window:
            stall = (m.group(1), no, t)
        if "[STALL_WATCHDOG] armed" in raw:
            watchdog_armed = True
        m = re.search(r"\[BOOT_SKIP\] skipping ([^\n]{0,80})", raw)
        if m:
            boot_skip.append(m.group(1).strip())
        m = re.search(r"\[FX1006\] multi-SKU dispatch flags: (FAIR=\d MISS=\d KEEP=\d YIELD=\d LOCK=\d)", raw)
        if m:
            fx1006 = m.group(1)
        m = re.search(r"\[HEALTH\] (rss=\S+ .{0,160})", raw)
        if m and health is None:
            health = (m.group(1), no, t)
        m = re.search(r"\[PROXY\] Loaded (\d+) proxies", raw)
        if m and proxies_loaded is None:
            proxies_loaded = int(m.group(1))
        m = re.search(r"\[STOCK\] started: .*?tcins=(\d+)", raw)
        if m and "loaded_tcins" not in init_flags:
            init_flags["loaded_tcins"] = int(m.group(1))
        # print lines get a logger stamp glued on ("True2026-10-08 23:49:03,…"): match the word only
        m = re.search(r"\[INIT\]\s+use_real_purchasing: (True|False)", raw)
        if m:
            init_flags["use_real_purchasing"] = m.group(1)
        m = re.search(r"\[INIT\]\s+TEST_MODE: (true|false|True|False)", raw)
        if m:
            init_flags["test_mode"] = m.group(1)
        m = re.search(r"\[BOOT_CART_AUDIT\] (cart empty[^\n]{0,40}|adopt[^\n]{0,100}|delet[^\n]{0,100})", raw)
        if m and in_window:
            cart_audit.append((m.group(1).strip()[:110], no, t))
        m = re.search(r"\[AMBIGUOUS_COMMIT\] restored (\d+) latch", raw)
        if m and in_window and int(m.group(1)) > 0:
            latch_restored = (int(m.group(1)), no, t)

    def add(mid, status, detail, line=None, t=None):
        rows.append({"id": mid, "status": status, "detail": detail, "line": line,
                     "t": None if t is None else round(t, 1)})

    # ordered markers
    for mid, rx, deadline, req in MARKERS:
        if mid in seen:
            m, no, t = seen[mid]
            if mid == "worker_pool":
                n = int(m.group(1))
                want = len(accounts) if accounts else None
                if want is not None and n != want and acct_changed:
                    add(mid, "INFO", f"pool sized {n} account(s); target_accounts.json changed after this boot (now {want} enabled)", no, t)
                elif want is not None and n != want:
                    add(mid, "FAIL", f"pool sized {n} account(s) but config enables {want} ({', '.join(accounts)})", no, t)
                else:
                    add(mid, "OK", f"pool sized {n} account(s)", no, t)
            elif mid == "login_check":
                if m.group(1) == "True":
                    add(mid, "OK", "login check True", no, t)
                else:
                    add(mid, "FAIL", "login check False -- Worker 1 not logged in (exit 87 path)", no, t)
            elif mid == "pool_ready":
                r, n = int(m.group(1)), int(m.group(2))
                if r == n:
                    add(mid, "OK", f"{r}/{n} monitor sessions ready", no, t)
                elif r >= max(12, n - 3):
                    add(mid, "WARN", f"only {r}/{n} monitor sessions ready at boot", no, t)
                else:
                    add(mid, "FAIL", f"only {r}/{n} monitor sessions ready at boot", no, t)
            elif mid == "first_stats":
                sweeps, rate, ok, e403, e429, other, sr, sc = m.groups()
                bad = int(e403) > 0 or int(e429) > 0
                add(mid, "FAIL" if bad else "OK",
                    f"first STATS: sweeps={sweeps} {rate}/s 200={ok} 403={e403} 429={e429} other={other} sessions={sr}r/{sc}c", no, t)
            elif mid == "ground_truth":
                n = int(m.group(2))
                want = len(tcins) if tcins else None
                in_stock = m.group(1).strip()
                k_inv = int(visibility[0]) if visibility else 0     # the [TCIN-VISIBILITY] banner's count
                if want is not None and n != want and cfg_changed:
                    add(mid, "INFO", f"ground truth saw {n} TCINs; product_config.json changed after this boot (now {want} enabled)", no, t)
                elif want is None or n == want:
                    add(mid, "OK", f"ground truth ok: {n} TCINs, in_stock=[{in_stock}]", no, t)
                elif k_inv and n + k_inv == want:
                    add(mid, "OK", f"ground truth ok: {n} visible + {k_inv} invisible (unpublished, named in the visibility banner) = {want} enabled; in_stock=[{in_stock}]", no, t)
                elif not visibility and age < 600:
                    add(mid, "PENDING", f"ground truth sees {n} of {want} enabled TCINs; waiting for the [TCIN-VISIBILITY] banner to name the missing ones (due by +600 s)", no, t)
                else:
                    add(mid, "FAIL", f"ground truth sees {n} TCINs, the visibility banner accounts for {k_inv}, config enables {want} -- the counts do not reconcile", no, t)
            else:
                add(mid, "OK", rx.pattern.replace("\\", "")[:60], no, t)
        else:
            if age < deadline:
                add(mid, "PENDING", f"not seen yet (due by +{deadline} s; run age {age:.0f} s)")
            else:
                add(mid, "FAIL" if req else "WARN", f"MISSING after {age:.0f} s (expected by +{deadline} s)")

    # thread-start exception (the 10-07 signature)
    if thread_start_exc and (thread_start_exc[2] or "all_threads" not in seen):
        add("thread_start_exception", "FAIL",
            f"exception while starting threads: {thread_start_exc[1]} -- purchasing may be dead while the process lives", thread_start_exc[0])
    for no, s in exc_in_thread:
        add("exception_in_thread", "FAIL", s, no)
    if stall:
        add("stall", "FAIL", f"STALL_WATCHDOG: {stall[0]}", stall[1], stall[2])
    for s in boot_skip:
        add("boot_skip", "WARN", f"[BOOT_SKIP] skipping {s} -- that account is out of this fleet", None)

    # write-auth / harvest per account
    for a in (accounts or []):
        if a in write_auth:
            st, no, t = write_auth[a]
            add(f"write_auth:{a}", "OK" if st == "alive" else "WARN",
                f"{a}: write-auth {st}" + ("" if st == "alive" else " (a boot 401 is often the ~20% decoy mix; persistent = token problem)"), no, t)
        elif age >= 300:
            add(f"write_auth:{a}", "WARN", f"{a}: no warmup write-auth line by +{age:.0f} s")
        else:
            add(f"write_auth:{a}", "PENDING", f"{a}: warmup write-auth not seen yet (due by +300 s)")
        if a in selftest:
            acc, rej, no, t = selftest[a]
            codes = [c.strip() for c in acc.split(",") if c.strip()]
            good = bool(codes) and all(c == "424" for c in codes)
            add(f"harvest:{a}", "OK" if good else "WARN",
                f"{a}: harvest selftest accepted=[{acc}] rejected=[{rej}]" + ("" if good else " (accepted should hold only 424s; a 429 counted as accepted on 10-02)"), no, t)
        elif age >= 300:
            add(f"harvest:{a}", "WARN", f"{a}: no harvest SELFTEST verdict by +{age:.0f} s")
        else:
            add(f"harvest:{a}", "PENDING", f"{a}: harvest selftest not seen yet (due by +300 s)")

    # purchasing really on; boot cart audit; restored latches
    if init_flags.get("test_mode") is not None:
        tm = init_flags["test_mode"].lower()
        add("test_mode", "OK" if tm == "false" else "FAIL", f"[INIT] TEST_MODE: {init_flags['test_mode']}" + ("" if tm == "false" else " -- the bot will NOT place real orders"))
    if init_flags.get("loaded_tcins") is not None and tcins:
        lt = init_flags["loaded_tcins"]
        if lt == len(tcins) or cfg_changed:
            add("loaded_tcins", "OK" if lt == len(tcins) else "INFO", f"[STOCK] started with tcins={lt}" + ("" if lt == len(tcins) else f" (config now enables {len(tcins)}; it changed after this boot)"))
        else:
            add("loaded_tcins", "FAIL", f"[STOCK] started with tcins={lt} but the config enables {len(tcins)} -- the bot loaded a different list (the config loads at boot; restart after an edit)")
    if init_flags.get("use_real_purchasing") is not None:
        rp = init_flags["use_real_purchasing"]
        add("real_purchasing", "OK" if rp == "True" else "FAIL", f"[INIT] use_real_purchasing: {rp}" + ("" if rp == "True" else " -- purchases are MOCKED"))
    if cart_audit:
        adopted = [c for c in cart_audit if not c[0].startswith("cart empty")]
        if adopted:
            add("boot_cart_audit", "WARN", f"boot cart audit found a leftover line: " + "; ".join(c[0] for c in adopted[:3]) + " (a leftover cart line is adopted as HELD or deleted; it is not checked against the config)", adopted[0][1], adopted[0][2])
        else:
            add("boot_cart_audit", "OK", f"boot cart audit: {len(cart_audit)} cart(s) empty", cart_audit[0][1], cart_audit[0][2])
    if latch_restored:
        add("ac1_latch_restored", "WARN", f"[AMBIGUOUS_COMMIT] restored {latch_restored[0]} latch(es) from the previous run -- those (account, TCIN) pairs sit out; check order history", latch_restored[1], latch_restored[2])

    # visibility
    invisible_list = []
    if visibility:
        k, n, lst, no, t = visibility
        invisible_list = re.findall(r"\d{6,}", lst)
        add("tcin_visibility", "WARN", f"{k} of {n} configured TCINs INVISIBLE to RedSky: {lst} (unpublished = expected for a pre-order that has not gone live; a wrong TCIN looks the same -- verify the number)", no, t)
    elif "ground_truth" in seen:
        add("tcin_visibility", "OK", "no [TCIN-VISIBILITY] banner (every enabled TCIN visible)")
    if tcins and cfg_changed:
        add("first_sighting", "INFO", f"[STOCK STATUS] first sightings for {len(first_sight)} TCINs (the config changed after this boot; no comparison)")
    elif tcins:
        missing = [t for t in tcins if t not in first_sight and t not in invisible_list]
        if first_sight and not missing:
            add("first_sighting", "OK", f"[STOCK STATUS] first sighting for all {len(tcins) - len(invisible_list)} visible TCINs"
                + (f" ({len(invisible_list)} invisible ones excluded)" if invisible_list else ""))
        elif first_sight and age >= 480:
            add("first_sighting", "WARN", f"{len(missing)} enabled TCIN(s) with no [STOCK STATUS] first sighting: {missing}")
        elif not first_sight and age >= 480:
            add("first_sighting", "WARN", "no [STOCK STATUS] lines (RESILIENT_STATUS_LOG off, or no read yet)")
        else:
            add("first_sighting", "PENDING", f"first sightings so far {len(first_sight)}/{len(tcins)}")
    if vpc:
        add("marketplace_listing", "WARN", f"RTC VPC (a marketplace listing that never fires) on: {sorted(set(vpc))}")

    # flags
    want = armed or {}
    if fx1006 is not None:
        exp = "FAIR=%s MISS=%s KEEP=%s YIELD=%s LOCK=%s" % tuple(
            (want.get(k, "0") or "0")[:1] for k in ("TARGET_MULTI_SKU_FAIR_SHARE", "TARGET_MULTI_SKU_MISS_REARM",
                                                     "TARGET_STOCK_RESET_KEEP_LIVE_FAILED", "TARGET_WAVE_FIRST_YIELD_MISSED",
                                                     "TARGET_SHARED_LOCK_ORDER_FIX"))
        add("fx1006_flags", "OK" if fx1006 == exp or not armed else "WARN",
            f"[FX1006] {fx1006}" + ("" if fx1006 == exp or not armed else f" but the wrapper arms {exp}"))
    if want.get("TARGET_STALL_WATCHDOG", "0") == "1":
        if watchdog_armed:
            add("stall_watchdog", "OK", "[STALL_WATCHDOG] armed")
        elif age >= 300:
            add("stall_watchdog", "WARN", "TARGET_STALL_WATCHDOG=1 in the wrapper but no '[STALL_WATCHDOG] armed' line")
        else:
            add("stall_watchdog", "PENDING", "watchdog line not seen yet")
    if health:
        add("health", "INFO", f"[HEALTH] {health[0][:150]}", health[1], health[2])
    if proxies_loaded is not None:
        add("proxies", "INFO", f"[PROXY] Loaded {proxies_loaded} proxies")

    # tracebacks
    other = tracebacks["other"]
    if other:
        top = sorted(other.items(), key=lambda kv: -kv[1])[:3]
        add("tracebacks", "WARN", f"{sum(other.values())} non-benign traceback(s): " + "; ".join(f"{k} x{v}" for k, v in top)
            + f" (+{tracebacks['benign']} benign asyncio ConnectionReset)")
    else:
        add("tracebacks", "INFO", f"{tracebacks['benign']} benign asyncio traceback(s), 0 other")

    return {"rows": rows, "first_stamp": first_stamp, "age_s": round(age, 1), "lines": len(lines)}


def port_open(port: int = 5001, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def verdict(rows) -> str:
    st = {r["status"] for r in rows}
    if "FAIL" in st:
        return "RED"
    if "PENDING" in st:
        return "PENDING"
    return "GREEN"


def run_once(root: Path, log, now, check_port: bool = True) -> dict:
    now = time.time() if now is None else now
    log = log or newest_run_log(root)
    if log is None or not Path(log).exists():
        return {"verdict": "RED", "log": None, "rows": [{"id": "run_log", "status": "FAIL",
                "detail": "no logs/runs/run_*.log found -- has the bat been started?", "line": None, "t": None}]}
    log = Path(log)
    boot = boot_ts_from_name(log) or os.path.getmtime(log)
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"verdict": "RED", "log": str(log), "rows": [{"id": "run_log", "status": "FAIL", "detail": f"unreadable: {e}", "line": None, "t": None}]}
    tcins = enabled_tcins(root)
    accounts = enabled_accounts(root)

    def _changed_after(p: Path) -> bool:
        try:
            return os.path.getmtime(p) > boot + 1.0
        except OSError:
            return False
    res = analyze_log(text, boot, now, tcins, accounts, armed_flags(root),
                      cfg_changed=_changed_after(root / "config" / "product_config.json"),
                      acct_changed=_changed_after(root / "config" / "target_accounts.json"))
    rows = res["rows"]
    # wrapper
    try:
        w = parse_wrapper((root / "logs" / "bot_restart_wrapper.log").read_text(encoding="utf-8", errors="replace"), boot)
    except OSError:
        w = {"found": False}
    if w.get("found"):
        if w.get("guard_tripped"):
            rows.insert(0, {"id": "wrapper", "status": "FAIL", "detail": "single-instance guard TRIPPED after this launch -- another app.py was running", "line": None, "t": None})
        if w.get("login_code") not in (None, 0):
            rows.insert(0, {"id": "wrapper_login_pass", "status": "FAIL", "detail": f"the wrapper's relogin pass exited code={w['login_code']} (-1073741510 = Ctrl+C / aborted)", "line": None, "t": None})
        else:
            rows.insert(0, {"id": "wrapper_login_pass", "status": "OK" if w.get("login_code") == 0 else "INFO",
                            "detail": f"relogin pass code={w.get('login_code')}, readiness code={w.get('readiness_code')}", "line": None, "t": None})
        if w.get("exit87"):
            rows.insert(0, {"id": "wrapper_exit87", "status": "FAIL", "detail": "wrapper saw exit 87 after this launch (Worker 1 not logged in) -- the relaunch loop is running", "line": None, "t": None})
        elif w.get("exited_code") is not None:
            rows.insert(0, {"id": "wrapper_exit", "status": "FAIL" if w["exited_code"] != 0 else "WARN",
                            "detail": f"the wrapper recorded app.py exited code={w['exited_code']} after this launch (0 = operator stop; -1073741819 = 0xC0000005 native crash; 87 = not logged in; 2 = deadman)",
                            "line": None, "t": None})
    else:
        rows.insert(0, {"id": "wrapper", "status": "INFO", "detail": "no wrapper 'launch #' line for this boot (started outside the bat?)", "line": None, "t": None})
    # relogin pass detail
    try:
        rl = parse_relogin((root / "logs" / "relogin.log").read_text(encoding="utf-8", errors="replace"), boot)
    except OSError:
        rl = {"accounts": {}}
    for a in (accounts or []):
        r = rl.get("accounts", {}).get(a)
        if r is None:
            continue
        ok = ("already logged in" in r) or ("LOGGED IN" in r and "NOT" not in r)
        rows.insert(1, {"id": f"relogin:{a}", "status": "OK" if ok else "FAIL", "detail": f"{a}: {r[:110]}", "line": None, "t": None})
    # dashboard port (a live process answers on 5001)
    if check_port:
        up = port_open()
        age = res["age_s"]
        if up:
            rows.append({"id": "dashboard", "status": "OK", "detail": "dashboard answering on 127.0.0.1:5001", "line": None, "t": None})
        elif age > 120:
            rows.append({"id": "dashboard", "status": "FAIL", "detail": f"nothing answers on 127.0.0.1:5001 at run age {age:.0f} s -- the process is not running", "line": None, "t": None})
        else:
            rows.append({"id": "dashboard", "status": "PENDING", "detail": "dashboard port not up yet", "line": None, "t": None})
    return {"verdict": verdict(rows), "log": str(log), "boot": _local(boot), "age_s": res["age_s"],
            "lines": res["lines"], "rows": rows}


def render(result: dict) -> str:
    out = ["=" * 78, f"BOOT CHECK -- {result.get('log')}  boot {result.get('boot')}  age {result.get('age_s')} s  lines {result.get('lines')}", "=" * 78]
    for r in result["rows"]:
        t = "" if r.get("t") is None else f"+{r['t']:>6.1f}s "
        ln = "" if r.get("line") is None else f"L{r['line']} "
        out.append(f"[{r['status']:<7}] {t}{ln}{r['id']}: {r['detail']}")
    out.append("-" * 78)
    v = result["verdict"]
    nxt = ""
    if v == "PENDING":
        pend = [r for r in result["rows"] if r["status"] == "PENDING"]
        nxt = f"  waiting on: {', '.join(r['id'] for r in pend[:6])}"
    out.append(f"VERDICT: {v}{nxt}")
    if v == "RED":
        out.append("  a FAIL above means the bot cannot buy as it stands: stop the bat (Ctrl+C in its window), fix, restart.")
    if v == "GREEN":
        warns = [r for r in result["rows"] if r["status"] == "WARN"]
        out.append("  every buy-path marker present." + (f" {len(warns)} WARN line(s) above to read once." if warns else ""))
    out.append("=" * 78)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", help="run log to check (default: newest logs/runs/run_*.log)")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--follow", action="store_true", help="re-check every 15 s until GREEN or RED (max --deadline s after boot)")
    ap.add_argument("--deadline", type=int, default=720)
    ap.add_argument("--now", type=float, default=None, help="epoch seconds to evaluate at (tests)")
    ap.add_argument("--no-port", action="store_true", help="skip the dashboard port probe")
    a = ap.parse_args(argv)
    root = Path(a.root)
    log = Path(a.log) if a.log else None
    while True:
        try:
            res = run_once(root, log, a.now, check_port=not a.no_port)
        except Exception as e:   # never leave the operator without a verdict
            res = {"verdict": "RED", "log": str(log), "rows": [{"id": "boot_check", "status": "FAIL", "detail": f"boot_check itself failed: {type(e).__name__}: {e}", "line": None, "t": None}]}
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            print(render(res))
        if not a.follow or res["verdict"] != "PENDING" or (res.get("age_s") or 0) > a.deadline:
            break
        time.sleep(15)
    return {"GREEN": 0, "RED": 1}.get(res["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
