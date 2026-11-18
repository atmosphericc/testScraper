#!/usr/bin/env python3
"""Loud operator alert when the harvest hits a Press & Hold wall DURING a live drop.

WHY THIS EXISTS
---------------
The one bypass of Target's HUMAN/PerimeterX "Press & Hold" wall that is PROVEN to
work (2026-11-18 01:07, CURRENT_STATE) is the operator manually pressing-and-holding
the challenge on the two account Chrome windows: within seconds the harvest mints a
real-click `a0=yes` sensor and the bank goes LIVE (SELFTEST accepted=[424,424]).
It is a ~5-10 min timer, so the clear that wins a drop is the one done seconds BEFORE
stock hits. The problem was never the clearing -- it was NOTICING in time without
staring at a screen. This turns that into a loud, on-cue alert.

It does NOT solve the wall automatically. It makes the proven human-clear effortless:
when a wall is up AND stock is dropping, it shouts, speaks, beeps and raises the bot
Chrome windows so the operator does the 2 clicks on cue.

WHAT IT KEYS ON (signals already in the live run log -- nothing new logged)
---------------------------------------------------------------------------
  WALL present  : `MISS-PROBE ... pxfilled=yes` (hits=px_container,px_container_filled)
                  -- the C-1116-02 discriminator; fired 44/44 on screenshot-confirmed
                  walls on 2026-11-16 and 1742x overnight 2026-11-18.
  BANK live/clear: `CAPTURED tokens=N a0=yes`  OR  `banked replay is LIVE`
                  -- a real-click sensor was minted (the human-clear worked).
  DROP live     : a non-empty `in_stock=[<digit>...]`, `[WAVE`, `CARTED`,
                  `place_order`, `ORDER_PLACED` -- a window is actually open.

SAFETY
------
Read-only on the run log. Touches NO bot code, NO network, NO money. The OS alert is
a best-effort PowerShell call (voice + beep + raise windows); --dry-run / --self-test
never fire it, so the detector can be validated against a captured log offline.

USAGE
-----
  # Drop-night watch (RECOMMENDED): only shout when a wall is up AND stock is dropping
  python tools/ops/wall_alert.py --on-stock

  # Any sustained wall (noisier; good for a pre-drop warm check)
  python tools/ops/wall_alert.py

  # Restrict to a clock band (local time), e.g. the 00-06 drop band
  python tools/ops/wall_alert.py --on-stock --band 0-6

  # Validate the detector against a captured log -- prints the state timeline, no alerts
  python tools/ops/wall_alert.py --self-test logs/runs/run_20261118_002152.log

Exit codes: 0 normal stop (Ctrl+C or --once fired), 2 no run log found.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_NAME = re.compile(r"run_(\d{8})_(\d{6})\.log$")

# log line timestamp: "2026-11-18 05:02:50,609 ..."
TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
MISS_PROBE_RE = re.compile(r"MISS-PROBE\b")
PXFILLED_RE = re.compile(r"pxfilled=(yes|no)")
A0YES_RE = re.compile(r"CAPTURED tokens=\d+ a0=yes")
BANK_LIVE_RE = re.compile(r"banked replay is LIVE")
# non-empty in_stock, or any explicit drop/cart/order marker
DROP_RE = re.compile(r"in_stock=\[[0-9]|\[WAVE|CARTED|added to cart|place_order|ORDER_PLACED|order_placed")


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


def parse_ts(line: str):
    m = TS_RE.search(line)
    if not m:
        return None
    y, mo, d, hh, mm, ss, ms = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mm, ss, ms * 1000).timestamp()
    except ValueError:
        return None


class Event:
    __slots__ = ("t", "kind")

    def __init__(self, t: float, kind: str):
        self.t = t
        self.kind = kind  # 'wall' | 'clear' | 'drop'


def events_from_lines(lines):
    """Yield Events with a monotonically carried timestamp (continuation lines reuse
    the previous line's timestamp, since the bot echoes each message twice)."""
    last_ts = None
    for line in lines:
        ts = parse_ts(line)
        if ts is not None:
            last_ts = ts
        t = ts if ts is not None else last_ts
        if t is None:
            continue
        if MISS_PROBE_RE.search(line):
            m = PXFILLED_RE.search(line)
            if m:
                yield Event(t, "wall" if m.group(1) == "yes" else "nowall")
        if A0YES_RE.search(line) or BANK_LIVE_RE.search(line):
            yield Event(t, "clear")
        if DROP_RE.search(line):
            yield Event(t, "drop")


def classify(events, now_t, recover_s, window_min):
    """State over the window [now_t - recover_s, now_t]."""
    recent = [e for e in events if now_t - recover_s <= e.t <= now_t]
    walls = sum(1 for e in recent if e.kind == "wall")
    nowalls = sum(1 for e in recent if e.kind == "nowall")
    bank_live = any(e.kind == "clear" for e in recent)
    drop_live = any(e.kind == "drop" for e in recent)
    # wall is active if we have >= window_min fresh pxfilled=yes probes, they dominate
    # the recent probes, and no fresh real-click clear has landed.
    wall_active = (walls >= window_min) and (walls >= nowalls) and not bank_live
    return {
        "wall_active": wall_active,
        "bank_live": bank_live,
        "drop_live": drop_live,
        "walls": walls,
        "nowalls": nowalls,
    }


def tail_bytes(path: Path, nbytes: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
                f.readline()  # drop partial first line
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def fire_os_alert(msg: str, speak: str, raise_windows: bool):
    """Best-effort Windows alert: spoken phrase + beeps + raise bot Chrome windows.
    Never raises; a missing shell or non-Windows host just prints."""
    if sys.platform != "win32":
        print(f"\a[wall-alert] (non-Windows; would alert) {msg}", flush=True)
        return
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "for($i=0;$i-lt3;$i++){[console]::beep(1100,350);[console]::beep(760,350)};"
        f"try{{(New-Object -ComObject SAPI.SpVoice).Speak('{speak}')}}catch{{}};"
    )
    if raise_windows:
        ps += (
            "try{"
            "Add-Type @'\nusing System;using System.Runtime.InteropServices;"
            "public class W{[DllImport(\"user32.dll\")]public static extern bool SetForegroundWindow(IntPtr h);"
            "[DllImport(\"user32.dll\")]public static extern bool ShowWindow(IntPtr h,int n);}\n'@;"
            "Get-Process chrome -ErrorAction SilentlyContinue|Where-Object{$_.MainWindowHandle -ne 0}|"
            "ForEach-Object{[W]::ShowWindow($_.MainWindowHandle,9);[W]::SetForegroundWindow($_.MainWindowHandle)}"
            "}catch{}"
        )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       timeout=20, capture_output=True)
    except Exception:
        pass
    print(f"\a[wall-alert] {msg}", flush=True)


def banner(msg: str) -> str:
    bar = "=" * 72
    return f"\n{bar}\n  !! {msg}\n{bar}"


def in_band(ts: float, band) -> bool:
    if band is None:
        return True
    lo, hi = band
    h = datetime.fromtimestamp(ts).hour
    return lo <= h < hi if lo <= hi else (h >= lo or h < hi)


def self_test(path: Path, recover_s, window_min, on_stock):
    """Replay a captured log and print every state transition. No OS alerts."""
    text = path.read_text(encoding="utf-8", errors="replace")
    events = list(events_from_lines(text.splitlines()))
    if not events:
        print(f"[self-test] no events parsed from {path}")
        return
    print(f"[self-test] {path.name}: {len(events)} events "
          f"({sum(e.kind=='wall' for e in events)} wall, "
          f"{sum(e.kind=='clear' for e in events)} clear, "
          f"{sum(e.kind=='drop' for e in events)} drop)")
    # step through at each event time, report transitions of the alert condition
    prev_alert = None
    first_alert_t = None
    transitions = 0
    for e in events:
        st = classify(events, e.t, recover_s, window_min)
        alert = st["wall_active"] and (st["drop_live"] if on_stock else True)
        if alert != prev_alert:
            ts = datetime.fromtimestamp(e.t).strftime("%H:%M:%S")
            label = "ALERT" if alert else "clear"
            print(f"  {ts}  -> {label:5s}  wall={st['wall_active']!s:5s} "
                  f"drop={st['drop_live']!s:5s} bank_live={st['bank_live']!s:5s} "
                  f"(walls={st['walls']} nowalls={st['nowalls']})")
            if alert and first_alert_t is None:
                first_alert_t = e.t
            prev_alert = alert
            transitions += 1
    mode = "on-stock (wall AND drop)" if on_stock else "any sustained wall"
    print(f"[self-test] mode={mode}  alert-condition transitions={transitions}  "
          f"first-alert={'none' if first_alert_t is None else datetime.fromtimestamp(first_alert_t).strftime('%H:%M:%S')}")


def main():
    ap = argparse.ArgumentParser(description="Loud alert on a Press & Hold wall during a drop.")
    ap.add_argument("--log", help="run log (default: newest logs/runs/run_*.log)")
    ap.add_argument("--on-stock", action="store_true",
                    help="only alert when a wall is up AND stock is dropping (recommended)")
    ap.add_argument("--band", help="restrict alerts to a local-time hour band, e.g. 0-6")
    ap.add_argument("--window-min", type=int, default=3,
                    help="min fresh pxfilled=yes probes in the window to call it a wall (default 3)")
    ap.add_argument("--recover-s", type=float, default=120.0,
                    help="seconds of history that define 'recent' (default 120)")
    ap.add_argument("--re-alert-s", type=float, default=120.0,
                    help="re-alert interval while still walled (default 120)")
    ap.add_argument("--poll-s", type=float, default=5.0, help="poll interval (default 5)")
    ap.add_argument("--once", action="store_true", help="exit after the first alert")
    ap.add_argument("--no-raise", action="store_true", help="do not raise Chrome windows")
    ap.add_argument("--dry-run", action="store_true", help="detect + print, never fire the OS alert")
    ap.add_argument("--self-test", metavar="LOG", help="replay a captured log, print the state timeline, exit")
    args = ap.parse_args()

    band = None
    if args.band:
        try:
            lo, hi = (int(x) for x in args.band.split("-", 1))
            band = (lo, hi)
        except ValueError:
            ap.error("--band must look like 0-6")

    if args.self_test:
        self_test(Path(args.self_test), args.recover_s, args.window_min, args.on_stock)
        return 0

    log = Path(args.log) if args.log else newest_run_log(ROOT)
    if log is None or not log.exists():
        print("[wall-alert] no run log found -- is the bot up?", file=sys.stderr)
        return 2

    mode = "on-stock (wall AND drop)" if args.on_stock else "any sustained wall"
    print(f"[wall-alert] watching {log.name}  mode={mode}  band={args.band or 'any'}  "
          f"poll={args.poll_s}s re-alert={args.re_alert_s}s  (Ctrl+C to stop)", flush=True)

    last_alert_t = 0.0
    last_state = None
    try:
        while True:
            text = tail_bytes(log, 1_000_000)
            events = list(events_from_lines(text.splitlines()))
            now_t = time.time()
            # use the newest event time as "now" so a stale log (bot stopped) decays out
            ref_t = max((e.t for e in events), default=now_t)
            st = classify(events, ref_t, args.recover_s, args.window_min)
            alert = st["wall_active"] and (st["drop_live"] if args.on_stock else True) and in_band(ref_t, band)

            if st != last_state:
                stamp = datetime.now().strftime("%H:%M:%S")
                print(f"  {stamp}  wall={st['wall_active']!s:5s} drop={st['drop_live']!s:5s} "
                      f"bank_live={st['bank_live']!s:5s} (walls={st['walls']} nowalls={st['nowalls']})",
                      flush=True)
                last_state = dict(st)

            if alert and (now_t - last_alert_t) >= args.re_alert_s:
                msg = ("PRESS & HOLD WALL + STOCK DROPPING -- clear the 2 Chrome windows NOW"
                       if args.on_stock else "PRESS & HOLD WALL up on harvest -- bank is cold")
                speak = ("Press and hold. Stock is dropping. Clear the windows now."
                         if args.on_stock else "Press and hold wall is up.")
                print(banner(msg), flush=True)
                if not args.dry_run:
                    fire_os_alert(msg, speak, raise_windows=not args.no_raise)
                last_alert_t = now_t
                if args.once:
                    return 0

            time.sleep(args.poll_s)
    except KeyboardInterrupt:
        print("\n[wall-alert] stopped.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
