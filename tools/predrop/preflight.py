#!/usr/bin/env python3
"""Pre-drop preflight -- every zero-browser readiness check in one READ-ONLY pass.

    python tools/predrop/preflight.py [--drop "2026-10-09 02:00"] [--tcins 1010892076,...]
                                      [--json] [--no-host] [--no-readiness] [--no-git]

Never launches the bot, a browser, a login or a test; never writes anything; never prints a
credential (config/proxyIps.json is counted, not shown; CVVs are compared, not shown).
Exit code 0 always -- the operator decides; the verdict line says GO / GO-WITH-WARNINGS /
NO-GO. Every check is guarded: a check that cannot run prints UNKNOWN and the pass goes on.

What it covers, and the night each check was learned the hard way:
  repo      HEAD, uncommitted code, CRLF / duplicate `set` in the wrapper, flags armed but
            read by nothing, flags changed since the last live boot (UNPROVEN LIVE),
            app.py setdefaults (absent from the .bat is not off)                 09-21, 10-05
  process   a bot already running (age, the 24 h rule), ORPHAN bot Chromes holding a profile
            lock (0xC0000005 on the next relaunch), remote-session tooling           09-11, 10-02
  host      display / sleep never on AC, screensaver off, Windows Update paused, pending
            reboot, GPU / power events in the System log, memory, disk, autostart  10-02, 10-06
  accounts  check_session_readiness.py (hand login ONLY on a red line), login-session TTL
            projected to the drop, payment prerequisites present, CVV equal to the code's
            default, boot-skip list, CVV-challenge latch, ambiguous-commit latch 09-28, 10-04/05
  config    enabled TCINs <= 28 (one RedSky chunk), requested TCINs present, duplicates, qty
            pins, last-run visibility, purchase-state latches                   09-21, 08-24
  monitor   proxy pool size, parked exits carried in proxy_state.json, the apps_raw channel,
            chunking at the enabled count, the exact validate_proxies.py command with the
            wrapper's env (a bare run tests the wrong channel)                        09-22
  last_run  the newest run's boot (tools/predrop/boot_check.py), end, STATS, stalls, health,
            money markers                                                               10-07
  drop      hours to the drop, mode (FULL / SHORT / LAST-CALL), Windows Update overlap
            2026-10-18: with --drop, host.wu_pause_vs_drop (the pause must end >= drop + 12 h) and host.wu_active_hours (the
            LIVE active hours vs the drop SPAN, drop .. drop + WU_DROP_SPAN_H h; Windows auto-adjusts them); on every run
            (no --drop needed) repo.state_bat_cites (a `bat:NNNN` cite in the live sections of CURRENT_STATE.md that no
            longer lands on a `set` line of the wrapper, or on the flag the text names)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
try:
    import boot_check as _bc           # same directory, read-only
except Exception:                      # pragma: no cover
    _bc = None

WIN = os.name == "nt"
ENV_NAME = re.compile(r"['\"]((?:TARGET|ENSURE_|RESILIENT|STOCK|USE_RESILIENT|CHROME_|READINESS|RELOGIN|DROP_READINESS|SINGLE_INSTANCE|WRAPPER|VALIDATE|HARVEST|PX_|SHAPE_|BD_)[A-Z0-9_]*)['\"]")
MONITOR_ENV_PREFIX = ("RESILIENT_", "STOCK_", "TARGET_SWEEPS_PER_SEC", "USE_RESILIENT_STACK", "CHROME_STAGGER_TOTAL_S")
MAX_ONE_CHUNK = 28


class Report:
    def __init__(self):
        self.rows = []

    def add(self, area, cid, status, detail, fix="", items=None):
        row = {"id": f"{area}.{cid}", "area": area, "status": status, "detail": detail, "fix": fix}
        if items is not None:
            row["items"] = items          # the full machine-readable list (--json); the detail text is for people
        self.rows.append(row)

    def guarded(self, area, cid, fn):
        try:
            fn()
        except Exception as e:
            self.add(area, cid, "UNKNOWN", f"check failed: {type(e).__name__}: {str(e)[:160]}")


def _run(cmd, timeout=30, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _ps(script: str, timeout=30):
    if not WIN:
        raise RuntimeError("not Windows")
    rc, out, err = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)
    return out.strip()


def _ps_json(script: str, timeout=30):
    out = _ps(script + " | ConvertTo-Json -Compress -Depth 3", timeout=timeout)
    if not out:
        return []
    v = json.loads(out)
    return v if isinstance(v, list) else [v]


def _read(path: Path, limit=None) -> str:
    b = path.read_bytes()
    if limit:
        b = b[:limit]
    return b.decode("utf-8", "replace")


def _bat_sets(root: Path):
    """[(name, value, line_no)] for every `set NAME=VALUE` in the wrapper, in order."""
    out = []
    for i, raw in enumerate(_read(root / "run_bot_with_nightly_restart.bat").splitlines(), 1):
        m = re.match(r"^\s*set\s+([A-Za-z0-9_]+)=(.*?)\s*$", raw)
        if m:
            out.append((m.group(1), m.group(2), i))
    return out


def _armed(root: Path) -> dict:
    d = {}
    for n, v, _ in _bat_sets(root):
        d[n] = v
    return d


def _products(root: Path):
    cfg = json.loads(_read(root / "config" / "product_config.json"))
    return cfg.get("products", [])


def _accounts(root: Path):
    cfg = json.loads(_read(root / "config" / "target_accounts.json"))
    return cfg.get("accounts", [])


def _hours(s: float) -> str:
    return f"{s / 3600:.1f} h"


def _parse_drop(s: str):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            now = datetime.now()
            if fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
                if dt < now:
                    dt = dt.replace(day=now.day + 1) if now.day < 28 else datetime.fromtimestamp(dt.timestamp() + 86400)
            elif fmt == "%m/%d %H:%M":
                dt = dt.replace(year=now.year)
            return dt.timestamp()
        except ValueError:
            continue
    return None


# 2026-10-18 readiness audit (FS-A3): the Windows Update pause is compared with the DROP, not with now.
WU_AFTER_DROP_S = 12 * 3600       # the pause must outlast the drop by this much (a restart after the window still loses the session)


def _parse_iso_ts(s):
    """ISO-8601 -> epoch seconds, or None (the registry's PauseUpdatesExpiryTime is e.g. 2026-10-22T05:01:59Z)."""
    s = str(s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00")))
        return dt.timestamp()
    except Exception:
        return None


def _hour_or_none(v):
    try:
        h = int(str(v).strip())
    except Exception:
        return None
    return h if 0 <= h <= 23 else None


def _in_active_hours(hour, start, end):
    """True = the hour is inside the Windows Update ACTIVE hours (no automatic restart then); False = inside the
    allowed-restart interval; None = unknown. start > end wraps midnight (19-13 means 19:00 .. 13:00 next day)."""
    if start is None or end is None or start == end:
        return None
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


WU_DROP_SPAN_H = 4                # the drop SPAN tested against the active hours: drop .. drop + 4 h (the 10-16 drop ran 02:25-03:39; the band ends ~05:30)


def _drop_span_vs_active(drop_ts, start, end, span_h=WU_DROP_SPAN_H):
    """Pure. Where does the drop SPAN (drop_ts .. drop_ts + span_h h, sampled every 15 min) sit against the Windows Update active
    hours? -> None (hours unknown) | {"inside": True} (the whole span is active = no automatic restart) |
    {"inside": False, "first_outside": epoch} (the span runs into the allowed-restart interval). Drops run past their first hour,
    so the drop hour alone says nothing: 9-3 holds 02:30 but not 03:00."""
    if drop_ts is None or start is None or end is None or start == end:
        return None
    t, stop = drop_ts, drop_ts + span_h * 3600
    while t <= stop:
        if not _in_active_hours(datetime.fromtimestamp(t).hour, start, end):
            return {"inside": False, "first_outside": t}
        t += 900
    return {"inside": True}


def wu_assess(reg, drop_ts=None, now=None):
    """Pure: the Windows Update facts from the UX\\Settings registry row (a dict; {} when the key is unreadable)."""
    now = time.time() if now is None else now
    reg = reg or {}
    exp_ts = _parse_iso_ts(reg.get("PauseUpdatesExpiryTime"))
    pinned = {k: reg.get(k) for k in ("SmartActiveHoursState", "UserChoiceActiveHoursStart", "UserChoiceActiveHoursEnd")
              if str(reg.get(k) if reg.get(k) is not None else "").strip()}
    return {"have_reg": bool(reg), "exp_ts": exp_ts, "paused": exp_ts is not None and exp_ts > now,
            "ah_start": _hour_or_none(reg.get("ActiveHoursStart")), "ah_end": _hour_or_none(reg.get("ActiveHoursEnd")),
            "ah_values_present": pinned,
            "covers_drop": (drop_ts is not None and exp_ts is not None and exp_ts > now and exp_ts >= drop_ts + WU_AFTER_DROP_S)}


def wu_rows(R, info, drop_ts):
    """The --drop-aware Windows Update rows. Adds NOTHING without a drop moment (default output unchanged)."""
    A = "host"
    if drop_ts is None:
        return
    if not info.get("have_reg"):
        R.add(A, "wu_registry", "WARN", "HKLM:\\SOFTWARE\\Microsoft\\WindowsUpdate\\UX\\Settings is not readable (key or values missing): the pause expiry and the active hours are UNKNOWN",
              "check Settings -> Windows Update by hand: the pause must end after the drop + 12 h")
    exp_ts = info.get("exp_ts")
    need = drop_ts + WU_AFTER_DROP_S
    if info.get("paused"):
        d_h = (exp_ts - drop_ts) / 3600.0
        if exp_ts < need:
            R.add(A, "wu_pause_vs_drop", "WARN",
                  f"the Windows Update pause ends {datetime.fromtimestamp(exp_ts):%Y-%m-%d %H:%M} = {abs(d_h):.1f} h {'BEFORE' if d_h < 0 else 'after'} the drop "
                  f"({datetime.fromtimestamp(drop_ts):%Y-%m-%d %H:%M}); it must outlast the drop by 12 h (>= {datetime.fromtimestamp(need):%Y-%m-%d %H:%M}) or an update can install and restart the host in or right after the window",
                  "extend the pause before the drop (Settings -> Windows Update -> Pause updates, or tools/ops/wu_drop_guard.ps1 elevated -Days N); the pause is the only durable control")
        else:
            R.add(A, "wu_pause_vs_drop", "OK",
                  f"the Windows Update pause ends {datetime.fromtimestamp(exp_ts):%Y-%m-%d %H:%M} = {d_h:.1f} h after the drop (>= 12 h)")
    s, e = info.get("ah_start"), info.get("ah_end")
    if s is None or e is None:
        R.add(A, "wu_active_hours", "WARN", "the live Windows Update active hours are not readable (ActiveHoursStart/End missing)",
              "check Settings -> Windows Update -> Advanced -> Active hours by hand")
        return
    ah = f"{s:02d}:00-{e:02d}:00"
    span = _drop_span_vs_active(drop_ts, s, e)
    vals = info.get("ah_values_present") or {}
    if vals:
        auto = "; " + ", ".join(f"{k}={v}" for k, v in vals.items()) + " present in UX\\Settings (meaning not verified)"
    else:
        auto = ("; no SmartActiveHoursState / UserChoiceActiveHours value in UX\\Settings pins them, so Windows may auto-adjust them "
                "(it rewrote 19-13 to 9-3 on 2026-10-16 01:22:59)")
    win = f"{datetime.fromtimestamp(drop_ts):%H:%M}-{datetime.fromtimestamp(drop_ts + WU_DROP_SPAN_H * 3600):%H:%M}"
    covered = bool(info.get("covers_drop"))
    if span is None:
        where = f"the drop window {win} is of unknown position against the active hours"
    elif span["inside"]:
        where = f"the drop window {win} lies inside the active hours (no automatic restart)"
    else:
        where = (f"the drop window {win} runs past {datetime.fromtimestamp(span['first_outside']):%H:%M}, OUTSIDE the active hours "
                 f"= inside the allowed-restart interval")
    # the status follows position + pause cover; the pause shortfall itself is the host.wu_pause_vs_drop row
    if covered:
        R.add(A, "wu_active_hours", "OK", f"live active hours {ah}; {where}; the pause covers the drop + 12 h, so no automatic restart is expected{auto}")
    elif span is not None and span["inside"]:
        R.add(A, "wu_active_hours", "OK", f"live active hours {ah}; {where}{auto}")
    else:
        R.add(A, "wu_active_hours", "WARN",
              f"live active hours {ah}; {where} and no pause covers the drop + 12 h{auto}",
              "extend the Windows Update pause past the drop + 12 h; re-pinning the active hours alone is not durable")


# F7 (2026-10-18): `bat:NNNN` line-number cites in CURRENT_STATE.md go stale on every wrapper edit.
BAT_CITE = re.compile(r"\bbat:(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)")
STATE_LIVE_DAYS = 14               # a dated `## ` section older than this (vs the file's As-of) is history, not a live section
_FLAG_WORD = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,}\b")


def _heading_date(h, year):
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", h)
    try:
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        m = re.search(r"(?<![\d-])(\d\d)-(\d\d)(?![\d-])", h)
        if m and year:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
    except ValueError:
        pass
    return None


_LOOSE_SET = re.compile(r'^\s*set\s+(?:/[aApP]\s+)?"?([A-Za-z0-9_]+)=(.*?)"?\s*$', re.I)


def _loose_bat_sets(bat_text):
    """[(name, value, line_no)] for `set NAME=V`, `set "NAME=V"` and `set /a NAME=V` lines. Used by the F7 cite check ONLY:
    _bat_sets (which _armed reads) stays strict."""
    out = []
    for i, raw in enumerate((bat_text or "").splitlines(), 1):
        m = _LOOSE_SET.match(raw)
        if m:
            out.append((m.group(1), m.group(2), i))
    return out


def stale_bat_cites(state_text, bat_sets, bat_nlines, asof=None, live_days=STATE_LIVE_DAYS, bat_text=None):
    """Pure. Which `bat:NNNN[-MMMM][,PPP]` cites in the LIVE sections of CURRENT_STATE.md do not hold up against the wrapper?
    Live = the preamble, every undated `## ` section, `## STATE CARD`, and each dated section within `live_days` of `asof`
    (the file's As-of date); older dated sections are history and are only counted. `bat_text` (the wrapper's text), when given,
    adds the quoted `set "NAME=V"` and `set /a` lines to the strict `bat_sets`.
      stale       a single cite N whose line N is not a `set` line; a range A-B past the file's end or holding no `set` line
      moved       the cite resolves, but the wrapper flags NAMED in its own segment (before it back to the previous cite, at most
                  140 chars, and after it up to the next cite or the line end) all sit OUTSIDE the cited lines: the numbers shifted
      unverified  a RANGE that names no wrapper flag in its segment: a dense wrapper holds a `set` line in almost any range, so
                  "holds a set line" proves nothing; it is counted, never reported as resolved"""
    sets = list(bat_sets)
    if bat_text:
        have = {i for _n, _v, i in sets}
        sets += [t for t in _loose_bat_sets(bat_text) if t[2] not in have]
    set_at = {}
    name_lines = {}
    for n, _v, i in sets:
        set_at[i] = n
        name_lines.setdefault(n, []).append(i)
    live_cites = hist_cites = 0
    stale, moved, unverified = [], [], []
    live = True
    for ln_no, line in enumerate(state_text.splitlines(), 1):
        if line.startswith("## "):
            d = _heading_date(line, asof.year if asof else None)
            live = (d is None or asof is None or line.startswith("## STATE CARD") or (asof - d).days <= live_days)
        ms = list(BAT_CITE.finditer(line))
        for k, m in enumerate(ms):
            if not live:
                hist_cites += 1
                continue
            live_cites += 1
            before_from = max(ms[k - 1].end() if k else 0, m.start() - 140)
            after_to = ms[k + 1].start() if k + 1 < len(ms) else len(line)
            seg = line[before_from:m.start()] + " " + line[m.end():after_to]
            names = []
            for w in _FLAG_WORD.findall(seg):
                if w in name_lines and w not in names:
                    names.append(w)
            ok_all, has_range, cited = True, False, []
            for part in m.group(1).split(","):
                a, _, b = part.partition("-")
                lo, hi = int(a), int(b or a)
                cited.append((lo, hi))
                has_range = has_range or bool(b)
                hit = [i for i in range(lo, min(hi, bat_nlines) + 1) if i in set_at]
                if (not b and lo not in set_at) or (b and (hi > bat_nlines or not hit)):
                    ok_all = False
                    why = (f"beyond the wrapper's {bat_nlines} lines" if hi > bat_nlines else "no `set` line there")
                    stale.append({"state_line": ln_no, "cite": "bat:" + part, "why": why})
            if not ok_all:
                continue
            if names:
                if not any(lo <= i <= hi for n in names for i in name_lines[n] for lo, hi in cited):
                    moved.append({"state_line": ln_no, "cite": m.group(0),
                                  "now_at": {n: name_lines[n][:2] for n in names[:3]}})
            elif has_range:
                unverified.append({"state_line": ln_no, "cite": m.group(0)})
    return {"live_cites": live_cites, "history_cites": hist_cites, "stale": stale, "moved": moved, "unverified": unverified}


# ── repo ───────────────────────────────────────────────────────────────────────

def check_repo(R: Report, root: Path, git: bool, last_boot_ts):
    A = "repo"

    def head():
        rc, out, _ = _run(["git", "log", "-1", "--format=%h|%cd|%s", "--date=iso"], cwd=str(root))
        rc2, br, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(root))
        h, d, s = (out.strip().split("|", 2) + ["", ""])[:3]
        R.add(A, "head", "INFO", f"HEAD {h} ({d}) on {br.strip()}: {s[:70]}")

    def dirty():
        rc, out, _ = _run(["git", "status", "--porcelain"], cwd=str(root))
        code = []
        for ln in out.splitlines():
            st, path = ln[:2], ln[3:].strip()
            if st.strip() in ("??", "!!") or path.startswith("logs/") or path.startswith("config/product_config_backup") \
                    or path.startswith("config/target_accounts.json.bak") or path.endswith(".png"):
                continue
            code.append(path)
        if code:
            R.add(A, "dirty", "WARN", f"{len(code)} tracked file(s) modified and uncommitted: {code[:8]}",
                  "commit (or revert) before the drop -- a reboot keeps the files but loses the reasoning; the state file must describe THIS tree")
        else:
            R.add(A, "dirty", "OK", "no uncommitted code (only logs / backups differ)")

    def state_asof():
        st = root / ".claude" / "state" / "CURRENT_STATE.md"
        head_txt = _read(st, 4000)
        m = re.search(r"As of:\s*(\d{4})-(\d{2})-(\d{2})", head_txt)
        asof = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date() if m else None
        bat_d = datetime.fromtimestamp((root / "run_bot_with_nightly_restart.bat").stat().st_mtime).date()
        cfg_d = datetime.fromtimestamp((root / "config" / "product_config.json").stat().st_mtime).date()
        if asof is None:
            R.add(A, "state_asof", "WARN", "CURRENT_STATE.md has no parseable 'As of' date")
        elif bat_d > asof or cfg_d > asof:
            R.add(A, "state_asof", "WARN", f"CURRENT_STATE as-of {asof}; wrapper changed {bat_d}, config {cfg_d} -- its armed-flag facts may be stale",
                  "re-derive the armed flags from the wrapper itself (this pass does) and update the state file")
        else:
            R.add(A, "state_asof", "OK", f"CURRENT_STATE as-of {asof} is newer than the wrapper ({bat_d}) and the config ({cfg_d})")

    def state_bat_cites():
        st = root / ".claude" / "state" / "CURRENT_STATE.md"
        txt = _read(st)
        m = re.search(r"As of:\s*(\d{4})-(\d{2})-(\d{2})", txt[:4000])
        asof = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date() if m else None
        bat_nlines = len(_read(root / "run_bot_with_nightly_restart.bat").splitlines())
        bat_txt = _read(root / "run_bot_with_nightly_restart.bat")
        res = stale_bat_cites(txt, _bat_sets(root), bat_nlines, asof, bat_text=bat_txt)
        base = (f"{res['live_cites']} `bat:NNNN` cite(s) in the live sections of CURRENT_STATE.md "
                f"({res['history_cites']} more in dated history sections not checked)")
        items = ([dict(x, kind="stale") for x in res["stale"]] + [dict(x, kind="moved") for x in res["moved"]]
                 + [dict(x, kind="unverified") for x in res["unverified"]])
        if res["stale"] or res["moved"]:
            parts = [f"L{x['state_line']} {x['cite']} ({x['why']})" for x in res["stale"]]
            parts += [f"L{x['state_line']} {x['cite']} (resolves to another flag; the named flag is now at {x['now_at']})" for x in res["moved"]]
            unv = (f"; {len(res['unverified'])} range cite(s) name no flag and cannot be verified" if res["unverified"] else "")
            R.add(A, "state_bat_cites", "WARN",
                  f"{base}: {len(res['stale'])} do not resolve to a `set` line, {len(res['moved'])} point away from the flag the text names{unv}: " + "; ".join(parts),
                  "cite the flag NAME (grep the wrapper for it), not a line number; re-derive the armed flags from the wrapper itself", items=items)
        elif res["unverified"]:
            R.add(A, "state_bat_cites", "INFO",
                  f"{base}: none stale or moved, but {len(res['unverified'])} range cite(s) name no wrapper flag, so they cannot be verified: "
                  + "; ".join(f"L{x['state_line']} {x['cite']}" for x in res["unverified"]),
                  "cite the flag NAME next to a line range", items=items)
        else:
            R.add(A, "state_bat_cites", "OK", f"{base}: all resolve to a `set` line (and to the flag the text names)", items=items)

    def bat_sanity():
        b = (root / "run_bot_with_nightly_restart.bat").read_bytes()
        lines = b.split(b"\n")
        lf_only = sum(1 for ln in lines[:-1] if not ln.endswith(b"\r"))
        non_ascii = sum(1 for ch in b if ch > 127)
        if lf_only:
            R.add(A, "bat_eol", "FAIL", f"{lf_only} line(s) of the wrapper end in LF only -- batch files MUST be CRLF", "convert to CRLF before running it")
        else:
            R.add(A, "bat_eol", "OK", f"wrapper is CRLF throughout ({len(lines)} lines)")
        if non_ascii:
            R.add(A, "bat_ascii", "WARN", f"{non_ascii} non-ASCII byte(s) in the wrapper (cmd.exe reads it as the OEM code page)", "keep new lines ASCII")
        names = {}
        for n, v, i in _bat_sets(root):
            names.setdefault(n, []).append(i)
        dupes = {n: ls for n, ls in names.items() if len(ls) > 1}
        if dupes:
            R.add(A, "bat_dupes", "WARN", f"{len(dupes)} variable(s) set more than once (the LAST wins): " + ", ".join(f"{n}@{ls}" for n, ls in list(dupes.items())[:6]),
                  "assign each variable exactly once and last")
        else:
            R.add(A, "bat_dupes", "OK", f"{len(names)} variables, each set once")

    def flags_dead():
        armed = _armed(root)
        referenced = set()
        bat_txt = _read(root / "run_bot_with_nightly_restart.bat")
        for m in re.finditer(r"[%!]([A-Za-z0-9_]+)[%!]", bat_txt):
            referenced.add(m.group(1))
        skip = {"venv", ".venv", "node_modules", "__pycache__", ".git", "logs", "dist", ".pytest_cache", "state"}
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in skip]
            rel = Path(dp).relative_to(root).parts
            if rel and rel[0] not in ("src", "tools", "dashboard"):
                dns[:] = []
                if len(rel) > 0 and rel[0] != "":
                    pass
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                try:
                    txt = _read(Path(dp) / fn)
                except OSError:
                    continue
                for m in ENV_NAME.finditer(txt):
                    referenced.add(m.group(1))
        dead = sorted(n for n in armed if n not in referenced and n.upper() == n)
        if dead:
            R.add(A, "flags_dead", "WARN", f"{len(dead)} flag(s) set in the wrapper that no code reads: {dead[:10]}",
                  "a dead flag is a belief with no effect -- find the real name or delete the line")
        else:
            R.add(A, "flags_dead", "OK", f"every one of the {len(armed)} wrapper variables is read somewhere")

    def setdefaults():
        txt = _read(root / "app.py", 20000)
        found = re.findall(r"os\.environ\.setdefault\(\s*['\"]([A-Z0-9_]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]", txt)
        armed = _armed(root)
        silent = [f"{k}={v}" for k, v in found if k not in armed]
        R.add(A, "app_setdefaults", "INFO", f"app.py setdefaults at import: {', '.join(f'{k}={v}' for k, v in found)}"
              + (f" -- NOT in the wrapper (so absent != off): {silent}" if silent else ""))

    def since_boot():
        if last_boot_ts is None:
            R.add(A, "since_last_boot", "INFO", "no previous run log -- every armed flag is UNPROVEN LIVE")
            return
        iso = datetime.fromtimestamp(last_boot_ts).strftime("%Y-%m-%d %H:%M:%S")
        rc, c, _ = _run(["git", "rev-list", "-1", f"--before={iso}", "HEAD"], cwd=str(root))
        c = c.strip()
        if rc != 0 or not c:
            R.add(A, "since_last_boot", "UNKNOWN", "could not find the commit at the last boot")
            return
        rc, old, _ = _run(["git", "show", f"{c}:run_bot_with_nightly_restart.bat"], cwd=str(root))
        old_sets = {}
        for raw in old.splitlines():
            m = re.match(r"^\s*set\s+([A-Za-z0-9_]+)=(.*?)\s*$", raw)
            if m:
                old_sets[m.group(1)] = m.group(2)
        new_sets = _armed(root)
        changed = [f"{k}: {old_sets.get(k, '(unset)')} -> {v}" for k, v in new_sets.items() if old_sets.get(k) != v]
        removed = [k for k in old_sets if k not in new_sets]
        rc, lg, _ = _run(["git", "log", "--oneline", f"{c}..HEAD"], cwd=str(root))
        commits = [ln for ln in lg.splitlines() if ln.strip()]
        rc, dirty, _ = _run(["git", "status", "--porcelain", "--", "run_bot_with_nightly_restart.bat", "app.py", "src", "tools"], cwd=str(root))
        dirty_n = len([ln for ln in dirty.splitlines() if ln.strip() and not ln.startswith("??")])
        if changed or removed or commits or dirty_n:
            R.add(A, "since_last_boot", "WARN" if (changed or removed or dirty_n) else "INFO",
                  f"since the last live boot ({iso}, commit {c[:8]}): {len(commits)} commit(s), {len(changed)} flag value(s) changed, "
                  f"{len(removed)} removed, {dirty_n} code/wrapper file(s) dirty. Changed: {changed[:8]}{' removed: ' + str(removed) if removed else ''}",
                  "every changed flag is UNPROVEN LIVE: name its kill switch and the log marker that proves it exercised; watch the first boot with tools/predrop/boot_check.py --follow")
        else:
            R.add(A, "since_last_boot", "OK", f"wrapper flags and code unchanged since the last live boot ({iso}, {c[:8]}) -- this exact tree has booted and run")

    if git:
        R.guarded(A, "head", head)
        R.guarded(A, "dirty", dirty)
    R.guarded(A, "state_asof", state_asof)
    R.guarded(A, "state_bat_cites", state_bat_cites)
    R.guarded(A, "bat_sanity", bat_sanity)
    R.guarded(A, "flags_dead", flags_dead)
    R.guarded(A, "app_setdefaults", setdefaults)
    if git:
        R.guarded(A, "since_last_boot", since_boot)


# ── processes / host ──────────────────────────────────────────────────────────

def check_process(R: Report, root: Path):
    A = "process"
    info = {"bot_running": False}

    def procs():
        rows = _ps_json("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(chrome|python|pythonw|parsecd|rdpclip|TeamViewer|AnyDesk|msrdc|mstsc)\\.exe$' } "
                        "| Select-Object ProcessId,Name,CommandLine,@{n='c';e={$_.CreationDate.ToString('s')}}")
        bot = [p for p in rows if (p.get("Name") or "").lower().startswith("python") and re.search(r"\bapp\.py\b", p.get("CommandLine") or "")]
        other_py = [p for p in rows if (p.get("Name") or "").lower().startswith("python") and re.search(r"relogin_one|validate_proxies|verify_multi_account|test_resilient", p.get("CommandLine") or "")]
        proof = [p for p in rows if (p.get("Name") or "").lower() == "chrome.exe"
                 and re.search(r"_proxy_validation_tmp", p.get("CommandLine") or "", re.I)]
        if proof:
            R.add(A, "proxy_proof_running", "INFO", f"validate_proxies.py is running now ({len(proof)} Chrome processes on its throwaway state dir) -- wait for it before starting the bat")
        bot_profile = [p for p in rows if (p.get("Name") or "").lower() == "chrome.exe"
                       and re.search(r"nodriver-profile|session_profiles|smoke_profiles", p.get("CommandLine") or "", re.I)]
        remote = [p for p in rows if re.match(r"(?i)^(parsecd|rdpclip|TeamViewer|AnyDesk|msrdc|mstsc)\.exe$", p.get("Name") or "")]
        if bot:
            info["bot_running"] = True
            c = bot[0].get("c") or "?"
            try:
                age = time.time() - datetime.strptime(c, "%Y-%m-%dT%H:%M:%S").timestamp()
            except Exception:
                age = 0
            st = "WARN" if age > 20 * 3600 else "INFO"
            R.add(A, "bot", st, f"app.py is RUNNING since {c} ({_hours(age)}, {len(bot)} python process(es) = one bot: launcher + interpreter)",
                  "restart the bat before the window if it will be older than ~24 h at the drop (login-session rot 32-46 h; RSS grows ~0.4 GB/h); config edits need a restart")
        else:
            R.add(A, "bot", "INFO", "no app.py running")
        if other_py:
            R.add(A, "other_python", "WARN", f"other bot tooling running: {[p.get('CommandLine', '')[:60] for p in other_py]}", "wait for it to finish before starting the bat")
        if bot_profile and not bot:
            R.add(A, "orphan_chrome", "FAIL", f"{len(bot_profile)} Chrome process(es) hold a BOT profile with no bot running (pids {[p.get('ProcessId') for p in bot_profile][:6]})",
                  "close them (Task Manager / Stop-Process) -- a held --user-data-dir lock crashes the next relogin pass or boot with 0xC0000005 (09-11)")
        elif bot_profile:
            R.add(A, "orphan_chrome", "INFO", f"{len(bot_profile)} bot-profile Chrome processes (the running bot's)")
        else:
            R.add(A, "orphan_chrome", "OK", "no orphaned bot Chrome holds a profile")
        if remote:
            R.add(A, "remote", "WARN", f"remote-session tooling running: {sorted({p.get('Name') for p in remote})}",
                  "no Parsec / RDP / keyboard / mouse on the host during the window: 20/27 GPU-crash wakes carried a remote-wake code")
        else:
            R.add(A, "remote", "OK", "no remote-session tooling running")

    def port():
        try:
            import socket
            with socket.create_connection(("127.0.0.1", 5001), timeout=1.0):
                R.add(A, "port5001", "INFO", "something answers on 127.0.0.1:5001 (the dashboard)")
        except OSError:
            R.add(A, "port5001", "INFO", "port 5001 free")

    R.guarded(A, "procs", procs)
    R.guarded(A, "port5001", port)
    return info


def check_host(R: Report, drop_ts=None, root=None):
    A = "host"

    def power():
        out = _ps("powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE")
        m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
        ac = int(m.group(1), 16) if m else None
        if ac == 0:
            R.add(A, "display", "OK", "display timeout on AC = Never")
        else:
            R.add(A, "display", "FAIL", f"display timeout on AC = {ac} s (a display WAKE started 27/27 GPU-crash episodes)",
                  "powercfg /change monitor-timeout-ac 0  (restore: 15)")
        out = _ps("powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE")
        m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
        ac = int(m.group(1), 16) if m else None
        if ac == 0:
            R.add(A, "sleep", "OK", "sleep on AC = Never")
        else:
            R.add(A, "sleep", "FAIL", f"sleep on AC = {ac} s", "powercfg /change standby-timeout-ac 0")
        out = _ps("(Get-ItemProperty 'HKCU:\\Control Panel\\Desktop' -ErrorAction SilentlyContinue).ScreenSaveActive")
        if out.strip() in ("0", ""):
            R.add(A, "screensaver", "OK", "screensaver off")
        else:
            R.add(A, "screensaver", "WARN", f"ScreenSaveActive={out.strip()}", "turn the screensaver off (it is a display state change)")

    def profile_signals_env():
        # 2026-10-19 (O1): the scheduled tasks (tools/ops/ensure_armed.py) read the USER environment, the wrapper reads the bat;
        # ENSURE_ARMED_PROFILE_SIGNALS must carry the same value in both. Read-only (Get-ItemProperty only); every failure is a WARN.
        name = "ENSURE_ARMED_PROFILE_SIGNALS"
        fix = (f"set both to the same value: `setx {name} <v>` (user env, read by the scheduled tasks) and `set {name}=<v>` in the bat; "
               f"to disarm: `=0` in the bat AND `reg delete HKCU\\Environment /v {name} /f`")
        try:
            bat_val = _armed(root or ROOT).get(name, "").strip().strip('"')
            raw = _ps(f"(Get-ItemProperty 'HKCU:\\Environment' -ErrorAction SilentlyContinue).{name}")
            user_val = (raw or "").strip()
        except Exception as e:
            R.add(A, "profile_signals_env", "WARN", f"{name}: could not compare the user environment with the bat ({type(e).__name__}: {str(e)[:120]})", fix)
            return
        shown = lambda v: repr(v) if v else "(unset)"
        if bat_val == user_val:
            R.add(A, "profile_signals_env", "OK", f"{name} user environment {shown(user_val)} == bat {shown(bat_val)} (the scheduled tasks and the wrapper agree)")
        else:
            why = "missing from the user environment" if not user_val else "different from the bat"
            R.add(A, "profile_signals_env", "WARN",
                  f"{name}: user environment {shown(user_val)} is {why} (bat {shown(bat_val)}); the scheduled tasks read the USER environment, the wrapper reads the bat; both must match", fix)

    def windows_update():
        rows = _ps_json("Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\WindowsUpdate\\UX\\Settings' -ErrorAction SilentlyContinue | Select-Object PauseUpdatesExpiryTime,ActiveHoursStart,ActiveHoursEnd,SmartActiveHoursState,UserChoiceActiveHoursStart,UserChoiceActiveHoursEnd")
        r = rows[0] if rows else {}
        exp = (r.get("PauseUpdatesExpiryTime") or "").strip()
        ah = f"{r.get('ActiveHoursStart')}-{r.get('ActiveHoursEnd')}"
        au = _ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU' -ErrorAction SilentlyContinue).AUOptions").strip()
        pending = _ps("Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired'").strip().lower() == "true"
        paused = False
        if exp:
            try:
                paused = datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp() > time.time()
            except Exception:
                paused = False
        if pending:
            R.add(A, "wu_reboot", "FAIL", "Windows Update has a REBOOT PENDING -- it can restart the host inside the window",
                  "reboot now (before the window), then re-run this pass")
        if paused:
            R.add(A, "wu_pause", "OK", f"Windows Update paused until {exp} (active hours {ah})")
        else:
            R.add(A, "wu_pause", "WARN", f"Windows Update NOT paused (AUOptions={au or '?'}: scheduled install ~03:00; active hours {ah} -> a restart is possible outside them; a forced restart hit 06-24 01:29)",
                  "Settings -> Windows Update -> Pause updates (1 week). The Startup link does auto-restart the bat after a reboot, but a reboot mid-window still loses the edge")
        # 2026-10-18 FS-A3: compare the pause with the DROP (+12 h) and read the live active hours (rows only with --drop)
        info = wu_assess(r, drop_ts)
        R.wu_info = info
        wu_rows(R, info, drop_ts)

    def events():
        rows = _ps_json("Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddHours(-72)} -ErrorAction SilentlyContinue "
                        "| Where-Object { ($_.ProviderName -match 'nvlddmkm|Kernel-Power|BugCheck' -and $_.Id -in 13,14,41,566,1001) -or ($_.ProviderName -eq 'EventLog' -and $_.Id -in 6008) } "
                        "| Select-Object @{n='t';e={$_.TimeCreated.ToString('s')}},Id,ProviderName", timeout=60)
        bad = [r for r in rows if r.get("Id") in (13, 14, 41, 1001, 6008)]
        wakes = [r for r in rows if r.get("Id") == 566]
        if bad:
            R.add(A, "events", "WARN", f"{len(bad)} GPU / power / crash event(s) in the last 72 h: " + "; ".join(f"{r.get('t')} {r.get('ProviderName')} {r.get('Id')}" for r in bad[:5])
                  + (f" (+{len(wakes)} display wakes)" if wakes else ""),
                  "the 10-02 crash class: no keyboard / mouse / remote session on the host during the window; BIOS / XMP / driver checklist is still open")
        else:
            R.add(A, "events", "OK", f"0 GPU / power / bugcheck events in the last 72 h ({len(wakes)} display wake(s))")

    def resources():
        rows = _ps_json("$o=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{up=((Get-Date)-$o.LastBootUpTime).TotalHours; free=$o.FreePhysicalMemory; disk=(Get-PSDrive C).Free}")
        r = rows[0] if rows else {}
        up = float(r.get("up") or 0)
        free_gb = float(r.get("free") or 0) / 1048576.0
        disk_gb = float(r.get("disk") or 0) / 1073741824.0
        st = "OK"
        fix = ""
        if disk_gb < 20 or free_gb < 8:
            st, fix = "WARN", "free memory / disk before the window (the run log alone grows ~1 MB/h; 18 Chromes need RAM)"
        R.add(A, "resources", st, f"host up {up:.1f} h, free RAM {free_gb:.1f} GB, disk C free {disk_gb:.0f} GB", fix)
        gpu = _ps("(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1).DriverVersion").strip()
        if gpu:
            R.add(A, "gpu_driver", "INFO", f"NVIDIA driver {gpu} (617.14 = 32.0.16.1714: 5/5 episodes since it ended in bugcheck 0x116)")

    def autostart():
        out = _ps("Test-Path \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\TargetBot.lnk\"").strip().lower()
        if out == "true":
            R.add(A, "autostart", "INFO", "Startup folder has TargetBot.lnk: a reboot or logon auto-starts the bat (that is what fired at 19:33 after today's reboot)")
        else:
            R.add(A, "autostart", "INFO", "no TargetBot.lnk in the Startup folder (a reboot leaves the bot down)")

    R.guarded(A, "power", power)
    R.guarded(A, "profile_signals_env", profile_signals_env)
    R.guarded(A, "windows_update", windows_update)
    R.guarded(A, "events", events)
    R.guarded(A, "resources", resources)
    R.guarded(A, "autostart", autostart)


# ── accounts ──────────────────────────────────────────────────────────────────

def parse_readiness(out: str) -> dict:
    """Per-account lines of check_session_readiness.py -> {acct: {mark, login_session_days, member_exp, line}}."""
    res = {"accounts": {}, "verdict": "", "boot_skip": [], "visibility": ""}
    cur = None
    for ln in out.splitlines():
        m = re.match(r"^(\S+)\s+saved .*$", ln)
        if m and not ln.startswith("VERDICT"):
            cur = m.group(1)
            res["accounts"][cur] = {"mark": "?", "login_session_days": None, "member_exp": "", "line": ""}
            continue
        if cur and "login-session=" in ln:
            mm = re.search(r"login-session=([\d.]+)d", ln)
            if mm:
                res["accounts"][cur]["login_session_days"] = float(mm.group(1))
            elif re.search(r"login-session=(MISSING|EXPIRED|—|-)", ln):
                res["accounts"][cur]["login_session_days"] = 0.0
            continue
        if cur and re.match(r"^\s+(✅|❌|⚠)", ln):
            mark = re.match(r"^\s+(✅|❌|⚠)", ln).group(1)
            res["accounts"][cur]["mark"] = {"✅": "ok", "❌": "red", "⚠": "warn"}[mark]
            res["accounts"][cur]["line"] = ln.strip()[:160]
            mm = re.search(r"member token expires (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
            if mm:
                res["accounts"][cur]["member_exp"] = mm.group(1)
            continue
        if ln.startswith("VERDICT:"):
            res["verdict"] = ln.strip()[:160]
        if ln.startswith("BOOT SKIP:"):
            res["boot_skip"].append(ln.strip()[:160])
        if ln.startswith("TCIN VISIBILITY:"):
            res["visibility"] = ln.strip()[:200]
    return res


def check_accounts(R: Report, root: Path, run_readiness: bool, drop_ts):
    A = "accounts"
    accts = []

    def cfg():
        nonlocal accts
        accts = [a for a in _accounts(root) if a.get("enabled", True)]
        disabled = [a.get("account_id") for a in _accounts(root) if not a.get("enabled", True)]
        R.add(A, "enabled", "INFO" if len(accts) >= 3 else "WARN", f"{len(accts)} enabled account(s): {[a.get('account_id') for a in accts]}"
              + (f"; disabled: {disabled}" if disabled else ""), "" if len(accts) >= 3 else "fewer than 3 accounts = fewer first shots per flip")
        default_cvv = None
        try:
            m = re.search(r"^CARD_CVV\s*=\s*['\"]([^'\"]*)['\"]", _read(root / "src" / "session" / "purchase_executor.py", 4000), re.M)
            default_cvv = m.group(1) if m else None
        except Exception:
            pass
        for a in accts:
            aid = a.get("account_id", "?")
            miss = []
            for k in ("username", "password", "session_path", "profile_dir"):
                if not a.get(k):
                    miss.append(k)
            if not (root / str(a.get("session_path", ""))).exists():
                miss.append("session file on disk")
            if not (root / str(a.get("profile_dir", ""))).exists():
                miss.append("profile_dir on disk")
            cvv = str(a.get("cvv") or "").strip()
            if not cvv:
                miss.append("cvv (a named account never falls back to the code default: it would send NO cvv, and a CVV challenge then fails)")
            if miss:
                R.add(A, f"prereq:{aid}", "FAIL", f"{aid}: missing {miss}", "fix config/target_accounts.json (never commit it)")
            elif default_cvv and cvv == default_cvv:
                R.add(A, f"prereq:{aid}", "WARN", f"{aid}: cvv equals the code's hardcoded default (purchase_executor.py CARD_CVV)",
                      "confirm this is the card's real code; it is only sent when Target challenges the CVV (a reactive PUT after a place-order 400, or CVV-first when latched), and a wrong value fails that hop")
            else:
                R.add(A, f"prereq:{aid}", "OK", f"{aid}: credentials, session file, profile and cvv present")

    def readiness():
        rc, out, err = _run([sys.executable, str(root / "check_session_readiness.py")], timeout=90, cwd=str(root))
        p = parse_readiness(out + "\n" + err)
        if not p["accounts"]:
            R.add(A, "readiness", "UNKNOWN", f"check_session_readiness.py printed no account lines (rc={rc}): {out[-200:]}")
            return
        hours_left = None if drop_ts is None else (drop_ts - time.time()) / 3600.0
        for aid, d in p["accounts"].items():
            st = {"ok": "OK", "warn": "WARN", "red": "FAIL"}.get(d["mark"], "UNKNOWN")
            fix = ""
            if st == "FAIL":
                fix = f"hand_login_{aid.replace('-', '')}_force.bat, then re-run this pass (the ONLY case that needs a hand login: a dead login-session or a guest jar)"
            ls = d["login_session_days"]
            proj = ""
            if ls is not None and hours_left is not None:
                if ls * 24 < hours_left + 2:
                    st = "FAIL" if st != "FAIL" else st
                    proj = f"; login-session {ls} d would EXPIRE before the drop (+{hours_left:.1f} h)"
                    fix = fix or f"hand_login_{aid.replace('-', '')}_force.bat before the window"
                else:
                    proj = f"; login-session {ls} d covers the drop (+{hours_left:.1f} h)"
            R.add(A, f"readiness:{aid}", st, f"{aid}: {d['line']}{proj}", fix)
        R.add(A, "readiness_verdict", "INFO", p["verdict"] or "(no VERDICT line)")
        for b in p["boot_skip"]:
            R.add(A, "boot_skip", "WARN", b, "a real login (hand_login_<acct>_force.bat) clears the skip; else it expires after TARGET_BOOT_SKIP_TTL_H")
        if p["visibility"]:
            st = "OK" if p["visibility"].startswith("TCIN VISIBILITY: OK") else ("WARN" if "WARNING" in p["visibility"] else "INFO")
            R.add(A, "visibility_last_run", st, p["visibility"])

    def latches():
        skip = root / "state" / "boot_skip_accounts.json"
        if skip.exists():
            try:
                d = json.loads(_read(skip)).get("accounts") or {}
            except Exception:
                d = {"?": {}}
            ttl = 12.0
            try:
                ttl = min(48.0, max(1.0, float(_armed(root).get("TARGET_BOOT_SKIP_TTL_H", "12"))))
            except Exception:
                pass
            live = {a: e for a, e in d.items() if ttl * 3600 - (time.time() - float((e or {}).get("ts", 0) or 0)) > 0}
            if live:
                R.add(A, "boot_skip_file", "WARN", f"state/boot_skip_accounts.json would leave OUT of the next boot: {list(live)}",
                      "a real login clears it; or delete the file if the account is known good")
            else:
                R.add(A, "boot_skip_file", "INFO", "boot-skip file present but every entry expired")
        else:
            R.add(A, "boot_skip_file", "OK", "no boot-skip file (full fleet boots)")
        flags = sorted((root / "state").glob("cvv_challenge_*.flag"))
        if flags:
            parts = []
            for f in flags:
                age_d = (time.time() - f.stat().st_mtime) / 86400.0
                parts.append(f"{f.name} ({age_d:.0f} d old)")
            R.add(A, "cvv_latch", "WARN", f"CVV-challenge latch file(s): {parts} -- that account's executor starts with _cvv_required=True: the fast lane, won-cart and strike paths keep the API place-order but send a CVV PUT first (one extra in-chain hop); only the legacy Phase-4b path goes DOM-first",
                  "leave it unless the card no longer prompts for CVV (then delete the flag file, or TARGET_CVV_REQUIRED=0); after the run, grep MISSING_CREDIT_CARD_CVV to learn whether the challenge is still real")
        else:
            R.add(A, "cvv_latch", "OK", "no CVV-challenge latch file")
        ac = root / "state" / "ambiguous_commit_latch.json"
        if ac.exists():
            try:
                d = json.loads(_read(ac))
                n = len(d) if isinstance(d, (list, dict)) else "?"
            except Exception:
                n = "unreadable"
            R.add(A, "ac1_latch", "WARN", f"state/ambiguous_commit_latch.json present ({n} entries): latched (identity, TCIN) pairs are not re-raced until the TTL expires",
                  "check order history for the latched orders; leave the file unless each latched pair is accounted for")
        else:
            R.add(A, "ac1_latch", "OK", "no ambiguous-commit latch file")

    def profile_guest():
        # C-1018-20 (2026-10-19): TARGET_JAR_GUEST_GUARD keeps a jar FILE at member while the live
        # Chrome PROFILE is guest; relogin_one.py's validate-first pass labels that "GUEST (sut=G)".
        # WARN only: lists accounts whose newest relogin.log [RESULT] line says so while the jar reads member.
        _ops = str(HERE.parent / "ops")
        sys.path.insert(0, _ops)
        try:
            import ensure_armed as _ea
        finally:
            try:
                sys.path.remove(_ops)
            except ValueError:
                pass
        now = time.time()
        hits, items = [], []
        for a in accts or [x for x in _accounts(root) if x.get("enabled", True)]:
            aid = a.get("account_id", "?")
            r = _ea.newest_relogin_result(aid, root)
            if not r or _ea.GUEST_LABEL not in r[1]:
                continue
            nl, note = _ea.jar_state(root / str(a.get("session_path", "")), now)
            if nl:
                continue          # the jar itself reads guest/missing: readiness already flags it
            age = None if r[0] is None else (now - r[0]) / 3600.0
            age_s = "age unknown" if age is None else f"{age:.1f} h ago"
            hits.append(f"{aid} ({age_s})")
            items.append({"account": aid, "age_h": None if age is None else round(age, 2), "jar": note})
        if hits:
            R.add(A, "profile_guest", "WARN",
                  f"newest relogin.log [RESULT] says GUEST (sut=G) while the jar file reads member: {', '.join(hits)} -- the live Chrome profile is signed out; a jar-only check (ensure_armed, readiness) cannot see it",
                  "a scripted forced login (ENSURE_ARMED_PROFILE_SIGNALS=1 lets ensure_armed do it) or hand login; confirm the profile with relogin_one.py's validate-first output", items=items)
        else:
            R.add(A, "profile_guest", "OK", "no account whose newest relogin.log [RESULT] says GUEST (sut=G) while its jar reads member")

    R.guarded(A, "cfg", cfg)
    if run_readiness:
        R.guarded(A, "readiness", readiness)
    R.guarded(A, "latches", latches)
    R.guarded(A, "profile_guest", profile_guest)


# ── config ────────────────────────────────────────────────────────────────────

def check_config(R: Report, root: Path, requested):
    A = "config"

    def tcins():
        prods = _products(root)
        enabled = [p for p in prods if p.get("enabled", True)]
        ids = [str(p.get("tcin")) for p in enabled]
        n = len(ids)
        dup = sorted({t for t in ids if ids.count(t) > 1})
        if dup:
            R.add(A, "duplicates", "FAIL", f"duplicate enabled TCINs: {dup}", "remove the duplicate entry")
        if n <= MAX_ONE_CHUNK:
            R.add(A, "count", "OK", f"{n} enabled of {len(prods)} configured -- one RedSky chunk (<= {MAX_ONE_CHUNK}); same-response flips are all visible to the first dispatch")
        elif n <= 30:
            R.add(A, "count", "WARN", f"{n} enabled -- 2 RedSky chunks: per-TCIN refresh halves and same-sweep flips split across chunks",
                  f"keep enabled TCINs <= {MAX_ONE_CHUNK}")
        else:
            R.add(A, "count", "FAIL", f"{n} enabled > 30 -- the unchunked ground-truth read fails every cycle and visibility is UNKNOWN all night",
                  f"disable down to <= {MAX_ONE_CHUNK}")
        noqty = [str(p.get("tcin")) for p in enabled if not p.get("qty")]
        big = [f"{p.get('tcin')}:{p.get('qty')}" for p in enabled if (p.get("qty") or 0) > 2]
        if noqty:
            R.add(A, "qty", "WARN", f"{len(noqty)} enabled TCIN(s) without a qty pin: {noqty[:8]} (no pin = the qty-2 policy decides)", "set \"qty\": 2 like the others")
        elif big:
            R.add(A, "qty", "WARN", f"qty > 2 on: {big}", "Target rejects >2 on hot SKUs (MAX_PURCHASE_LIMIT) and it doubles the money at risk")
        else:
            R.add(A, "qty", "OK", f"every enabled TCIN pinned (qty={sorted({p.get('qty') for p in enabled})})")
        unnamed = [str(p.get("tcin")) for p in enabled if re.match(r"^(Product \d+|)$", str(p.get("name", "")).strip())]
        if unnamed:
            R.add(A, "unresolved_names", "INFO", f"enabled TCIN(s) with a placeholder name (unpublished or never resolved): {unnamed}")
        armed = _armed(root)
        q = armed.get("TARGET_QTY_PER_TCIN")
        if q == "1":
            R.add(A, "qty_flag", "OK", "TARGET_QTY_PER_TCIN=1 -- the per-entry qty pins are live")
        else:
            R.add(A, "qty_flag", "WARN", f"TARGET_QTY_PER_TCIN={q!r} -- the per-entry qty keys are IGNORED", "set TARGET_QTY_PER_TCIN=1 if the pins are wanted")
        for k, why in (("TARGET_FORCE_QTY_1", "forces qty 1 everywhere"), ("TARGET_PDP_QTY_LOOKUP", "re-opens the DOM add-to-cart that sends no qty")):
            if armed.get(k, "0") not in ("", "0", "false", "False"):
                R.add(A, f"flag:{k}", "WARN", f"{k}={armed.get(k)} -- {why}")
        if requested:
            want = [t.strip() for t in requested if t.strip()]
            present = {str(p.get("tcin")): p for p in prods}
            absent = [t for t in want if t not in present]
            disabled = [t for t in want if t in present and not present[t].get("enabled", True)]
            extra = [t for t in ids if t not in want]
            if absent:
                R.add(A, "requested_absent", "FAIL", f"requested TCIN(s) NOT in product_config.json: {absent}",
                      "add them (name, enabled true, qty 2) after backing the config up -- 09-21: one of ten was missing")
            if disabled:
                R.add(A, "requested_disabled", "FAIL", f"requested TCIN(s) present but DISABLED: {disabled}", "set \"enabled\": true")
            if not absent and not disabled:
                R.add(A, "requested", "OK", f"all {len(want)} requested TCINs present and enabled")
            if extra:
                R.add(A, "enabled_not_requested", "INFO", f"{len(extra)} enabled TCIN(s) not in tonight's request: {extra} (the config's list is the operator's deliberate target list; never remove unasked)")

    def visibility():
        f = root / "state" / "tcin_visibility.json"
        if not f.exists():
            R.add(A, "visibility", "INFO", "no state/tcin_visibility.json yet")
            return
        d = json.loads(_read(f))
        age_h = (time.time() - float(d.get("updated_at_unix") or 0)) / 3600.0
        enabled = {str(p.get("tcin")) for p in _products(root) if p.get("enabled", True)}
        invisible = [t for t in d.get("invisible", []) if t in enabled]
        unchecked = sorted(enabled - set(d.get("configured", [])))
        st = "OK"
        fix = ""
        parts = [f"last run's visibility {age_h:.0f} h old, verified={d.get('verified')}"]
        if invisible:
            st, fix = "WARN", "an invisible TCIN cannot be detected: unpublished (PDP 404) or a wrong number -- verify at the source"
            parts.append(f"INVISIBLE enabled: {invisible}")
        if unchecked:
            st = "WARN" if st == "OK" else st
            parts.append(f"enabled but never checked by a run: {unchecked} (reads PARTIAL until the bot boots on it; confirm the boot banner)")
        if age_h > 48:
            parts.append("STALE")
        R.add(A, "visibility", st, "; ".join(parts), fix)

    def backup():
        today = datetime.now().strftime("%Y-%m-%d")
        cands = sorted((root / "config").glob("product_config_backup_pre_*_drop.json"))
        last = cands[-1].name if cands else "none"
        R.add(A, "backup", "INFO", f"latest config backup: {last}; before editing tonight copy to config/product_config_backup_pre_{today}_drop.json")

    def purchase_states():
        f = root / "logs" / "purchase_states.json"
        if not f.exists():
            R.add(A, "purchase_states", "INFO", "no logs/purchase_states.json")
            return
        d = json.loads(_read(f))
        enabled = {str(p.get("tcin")) for p in _products(root) if p.get("enabled", True)}
        odd = {t: (v or {}).get("status") for t, v in d.items() if t in enabled and (v or {}).get("status") not in ("ready", "failed", None)}
        if odd:
            R.add(A, "purchase_states", "WARN", f"enabled TCIN(s) with a non-ready persisted purchase state: {odd} (the boot's STARTUP_CLEANUP resets stale ones; read its line in the boot log)")
        else:
            R.add(A, "purchase_states", "OK", f"persisted purchase states for the enabled TCINs are ready/failed only ({len(d)} entries)")

    R.guarded(A, "tcins", tcins)
    R.guarded(A, "visibility", visibility)
    R.guarded(A, "backup", backup)
    R.guarded(A, "purchase_states", purchase_states)


# ── monitor / proxies ─────────────────────────────────────────────────────────

def validate_cmd(root: Path) -> str:
    armed = _armed(root)
    env = [f"{k}={v}" for k, v in armed.items() if k.startswith(MONITOR_ENV_PREFIX)]
    if "CHROME_STAGGER_TOTAL_S" not in armed:
        env.append("CHROME_STAGGER_TOTAL_S=30")
    # VALIDATE_POOL=proxies: the bot reads only the `proxies` key (reserve_proxies is never
    # loaded), so "20/20 HEALTHY" on pool=all counted 2 exits the bot will not use.
    # STOCK_DIAG_PROBES=1 also exercises the unchunked ground-truth read at the current TCIN
    # count (STOCK_CANARY=0 from the wrapper keeps the home IP out of it). The script's own
    # verdict reads only total_success / total_403: judge 206s and timeouts from its
    # [STOCK][EXITS] / [STOCK][OTHER] lines, not from the HEALTHY column.
    env += ["STOCK_DIAG_PROBES=1", "VALIDATE_POOL=proxies", "VALIDATE_DURATION_S=180"]
    return " ".join(env) + " ./venv/Scripts/python.exe -u validate_proxies.py"


def check_monitor(R: Report, root: Path, last_boot_ts):
    A = "monitor"

    def pool():
        cfg = json.loads(_read(root / "config" / "proxyIps.json"))
        n = len(cfg.get("proxies", []))
        res = len(cfg.get("reserve_proxies", []))
        warn = len(cfg.get("warn_proxies", []))
        st = "OK" if n >= 15 else "WARN"
        R.add(A, "pool_config", st, f"proxyIps.json: {n} active exits, {res} reserve, {warn} warn (values never printed)",
              "" if st == "OK" else "fewer than 15 exits = slower per-TCIN refresh and less storm resilience")
        ps = root / "state" / "proxy_state.json"
        if ps.exists():
            d = json.loads(_read(ps))
            from collections import Counter
            c = Counter((v or {}).get("status") for v in d.values())
            now = time.time()
            parked = [ip for ip, v in d.items() if float((v or {}).get("parked_until") or 0) > now]
            burned = [ip for ip, v in d.items() if (v or {}).get("status") in ("burned", "parked") and ip not in parked]
            msg = f"proxy_state.json: {dict(c)} over {len(d)} entries"
            if parked:
                R.add(A, "proxy_state", "WARN", msg + f"; PARKED into the future at boot: {parked}", "a parked exit sits out until parked_until; if the park is stale, the pool boots short")
            elif burned:
                R.add(A, "proxy_state", "WARN", msg + f"; non-active entries: {burned[:6]}")
            else:
                R.add(A, "proxy_state", "OK", msg + "; no exit parked into the future")
        else:
            R.add(A, "proxy_state", "INFO", "no state/proxy_state.json (fresh pool state at boot)")

    def channel():
        armed = _armed(root)
        ch = armed.get("RESILIENT_REDSKY_CHANNEL", "(unset -> web)")
        if ch == "apps_raw":
            R.add(A, "channel", "OK", "RESILIENT_REDSKY_CHANNEL=apps_raw (the in-page web channel is walled by HUMAN/PX on the BD prefixes)")
        else:
            R.add(A, "channel", "WARN", f"RESILIENT_REDSKY_CHANNEL={ch}", "production reads apps_raw")
        n = len([p for p in _products(root) if p.get("enabled", True)])
        chunks = max(1, math.ceil(n / MAX_ONE_CHUNK))
        rps = float(armed.get("TARGET_SWEEPS_PER_SEC", armed.get("RESILIENT_TARGET_RPS", "3.0")) or 3.0)
        R.add(A, "chunking", "OK" if chunks == 1 else "WARN", f"{n} TCINs -> {chunks} chunk(s) of <= {MAX_ONE_CHUNK}; per-TCIN refresh ~{rps / chunks:.2f}/s at {rps} sweeps/s",
              "" if chunks == 1 else f"keep enabled TCINs <= {MAX_ONE_CHUNK}")
        if n <= 25:
            R.add(A, "gt_read_unproven", "OK", f"{n} TCINs: 25 per request is the routine apps-channel shape since 2026-10-09 (14 runs, 876,224 sweeps, 0 HTTP 400); the ground-truth read is the same request")
        else:
            R.add(A, "gt_read_unproven", "WARN",
                  f"{n} TCINs: 26-28 per apps-channel request is untested (25 is proven); the sweep and the ground-truth read are ONE request, so a rejected request size blinds BOTH the sweep and the ground-truth read"
                  + ("" if n <= MAX_ONE_CHUNK else f" (> {MAX_ONE_CHUNK}: the client chunks the sweep, the ground-truth read stays unchunked)"),
                  "run the one-shot size proof before relying on it: three read-only RedSky GETs at 26 / 27 / 28 TCINs through the production path (operator or main session); until then keep enabled TCINs <= 25")

    def last_boot():
        if last_boot_ts is None or _bc is None:
            R.add(A, "last_proof", "WARN", "no previous run log: the pool has never been proven on this host state", validate_cmd(root))
            return
        age_h = (time.time() - last_boot_ts) / 3600.0
        if age_h > 48:
            R.add(A, "last_proof", "WARN", f"the last production-path boot was {age_h:.0f} h ago", "re-prove the pool: " + validate_cmd(root))
        else:
            R.add(A, "last_proof", "OK", f"the last production-path boot was {age_h:.0f} h ago (its first STATS is in last_run.boot below)")
        R.add(A, "validate_cmd", "INFO", "proof command (Git Bash, runs the production path on a throwaway state dir, ~4 min, launches 20 Chromes -- never while the bot runs): " + validate_cmd(root))

    R.guarded(A, "pool", pool)
    R.guarded(A, "channel", channel)
    R.guarded(A, "last_boot", last_boot)


# ── last run ──────────────────────────────────────────────────────────────────

def check_last_run(R: Report, root: Path):
    A = "last_run"
    boot_ts = None
    log = _bc.newest_run_log(root) if _bc else None
    if log is None:
        R.add(A, "log", "INFO", "no run log yet")
        return None
    size_mb = log.stat().st_size / 1048576.0
    boot_ts = _bc.boot_ts_from_name(log)

    def boot():
        res = _bc.run_once(root, log, None, check_port=False)
        fails = [r for r in res["rows"] if r["status"] == "FAIL" and not r["id"].startswith(("wrapper_exit", "dashboard"))]
        warns = [r for r in res["rows"] if r["status"] == "WARN"]
        if fails:
            R.add(A, "boot", "FAIL", f"{log.name}: the last boot on this host was NOT able to buy: " + "; ".join(f"{r['id']}: {r['detail'][:90]}" for r in fails[:3]),
                  "python tools/predrop/boot_check.py --log " + str(log).replace("\\", "/") + " -- fix before the next boot")
        else:
            R.add(A, "boot", "OK", f"{log.name} ({size_mb:.1f} MB): boot GREEN (all buy-path markers)" + (f"; {len(warns)} warn(s)" if warns else ""))

    def tail():
        txt = _read(log)
        stats = re.findall(r"\[STOCK STATS\] t=(\S+) sweeps=(\d+) \([\d.]+/s\) 200=(\d+) 403=(\d+) 429=(\d+) other=(\d+)", txt)
        if stats:
            t, sw, ok, e403, e429, oth = stats[-1]
            st = "WARN" if int(e403) or int(e429) else "OK"
            R.add(A, "stats", st, f"last STATS t={t}: sweeps={sw} 200={ok} 403={e403} 429={e429} other={oth} ({int(oth) / max(1, int(sw)) * 100:.2f}% other)",
                  "" if st == "OK" else "a 403/429 on the monitor = pool trouble: validate_proxies with the wrapper env")
        stalls = txt.count("[STALL_WATCHDOG] STALL:")
        if stalls:
            R.add(A, "stalls", "WARN", f"{stalls} STALL_WATCHDOG stall(s) in the last run", "read the stack dump after the first STALL line (q.py diag)")
        hs = re.findall(r"\[HEALTH\] (rss=\S+ .{0,120})", txt)
        if hs:
            R.add(A, "health", "INFO", f"last [HEALTH] {hs[-1][:120]} ({len(hs)} samples)")
        money = {k: len(re.findall(k, txt)) for k in (r"ORDER PLACED|Checkout complete", r"\[AMBIGUOUS_COMMIT\]", r"\[DOUBLE-BUY GUARD\]", r"\[STOCK\] IN STOCK", r"\[RACE\]")}
        amb = money[r"\[AMBIGUOUS_COMMIT\]"]
        R.add(A, "money", "WARN" if amb else "INFO", f"orders/complete={money[r'ORDER PLACED|Checkout complete']} ambiguous={amb} double-buy-guard={money[r'\[DOUBLE-BUY GUARD\]']} in-stock reads={money[r'\[STOCK\] IN STOCK']} races={money[r'\[RACE\]']}",
              "an AMBIGUOUS_COMMIT = check Target order history now" if amb else "")
        w = _bc.parse_wrapper(_read(root / "logs" / "bot_restart_wrapper.log"), boot_ts) if boot_ts else {"found": False}
        if w.get("found"):
            if w.get("exited_code") is None:
                R.add(A, "end", "INFO", f"wrapper recorded no exit for this run (window closed / host shutdown while running); log last written {datetime.fromtimestamp(log.stat().st_mtime):%Y-%m-%d %H:%M}")
            else:
                R.add(A, "end", "INFO", f"wrapper: app.py exited code={w['exited_code']} ({'operator stop' if w['exited_code'] == 0 else 'NOT a clean stop'}); {len(w.get('after', []))} wrapper line(s) after launch")

    R.guarded(A, "boot", boot)
    R.guarded(A, "tail", tail)
    return boot_ts


# ── drop timing ───────────────────────────────────────────────────────────────

def check_drop(R: Report, drop_ts, wu_paused_ok: bool, wu=None):
    A = "drop"
    if drop_ts is None:
        R.add(A, "time", "INFO", "no --drop given: mode FULL assumed (give --drop \"YYYY-MM-DD HH:MM\" local for the projections)")
        return "FULL"
    hours = (drop_ts - time.time()) / 3600.0
    mode = "FULL" if hours > 3 else ("SHORT" if hours > 1 else "LAST-CALL")
    st = "INFO" if hours > 1 else "WARN"
    R.add(A, "time", st, f"drop at {datetime.fromtimestamp(drop_ts):%Y-%m-%d %H:%M} local = in {hours:.1f} h -> mode {mode} "
          f"(FULL > 3 h: audits + verified changes allowed; SHORT 1-3 h: config + proof + checklist only, no code; LAST-CALL < 1 h: checklist only)",
          "" if hours > 1 else "nothing new gets armed inside the last hour")
    hour = datetime.fromtimestamp(drop_ts).hour
    s_, e_ = (wu or {}).get("ah_start"), (wu or {}).get("ah_end")
    span = _drop_span_vs_active(drop_ts, s_, e_)
    win = f"{datetime.fromtimestamp(drop_ts):%H:%M}-{datetime.fromtimestamp(drop_ts + WU_DROP_SPAN_H * 3600):%H:%M}"
    if not wu_paused_ok and (span["inside"] is False if span is not None else 2 <= hour < 11):
        live = (f"runs past {datetime.fromtimestamp(span['first_outside']):%H:%M}, outside the live active hours {s_:02d}:00-{e_:02d}:00" if span is not None
                else "in 02:00-11:00 (the live active hours were not read)")
        R.add(A, "wu_overlap", "WARN", f"the drop window {win} (drop hour {hour:02d}:00 + {WU_DROP_SPAN_H} h) {live}, when Windows Update may restart the host", "pause Windows Update for the week")
    start = datetime.fromtimestamp(drop_ts - 20 * 60)
    R.add(A, "start_by", "INFO", f"start the bat by {start:%H:%M} (T-20 min): relogin pass ~45 s, boot GREEN at ~+4.5 min, then tools/predrop/boot_check.py --follow")
    return mode


# ── verdict / output ──────────────────────────────────────────────────────────

ORDER = {"FAIL": 0, "WARN": 1, "UNKNOWN": 2, "OK": 3, "INFO": 4}


def verdict(rows) -> str:
    st = {r["status"] for r in rows}
    if "FAIL" in st:
        return "NO-GO"
    if "WARN" in st or "UNKNOWN" in st:
        return "GO-WITH-WARNINGS"
    return "GO"


def render(res: dict) -> str:
    out = ["=" * 94, f"PRE-DROP PREFLIGHT  {res['generated']}  mode {res['mode']}  (read-only, zero-browser)", "=" * 94]
    by_area = {}
    for r in res["rows"]:
        by_area.setdefault(r["area"], []).append(r)
    for area in ("next-drop", "drop", "accounts", "config", "repo", "process", "host", "monitor", "last_run"):
        rows = by_area.get(area)
        if not rows:
            continue
        out.append(f"-- {area} " + "-" * (90 - len(area)))
        for r in sorted(rows, key=lambda r: ORDER.get(r["status"], 9)):
            out.append(f"[{r['status']:<7}] {r['id']}: {r['detail']}")
            if r.get("fix") and r["status"] in ("FAIL", "WARN"):
                out.append(f"           -> {r['fix']}")
    out.append("=" * 94)
    acts = res["operator_actions"]
    out.append(f"VERDICT: {res['verdict']}   ({res['counts']})")
    if acts:
        out.append("OPERATOR ACTIONS (FAIL first):")
        for i, a in enumerate(acts, 1):
            out.append(f"  {i}. [{a['status']}] {a['id']}: {a['fix'] or a['detail']}")
    else:
        out.append("no operator action required by this pass")
    out.append("=" * 94)
    return "\n".join(out)


def check_next_drop_actions(R: Report, root: Path):
    """Surface docs/NEXT_DROP_CHECKLIST.md so the operator sees the pending drop actions when they run /pre-drop
    (added 2026-10-23: the limiter-lottery findings + the follower launches are easy to forget). WARN while the
    file says STATUS: PENDING, OK once it says DONE, nothing if the file is absent."""
    A = "next-drop"

    def actions():
        f = root / "docs" / "NEXT_DROP_CHECKLIST.md"
        if not f.exists():
            return
        txt = _read(f)
        m = re.search(r"STATUS:\s*(\w+)", txt)
        status = (m.group(1).upper() if m else "PENDING")
        acts, grab = [], False
        for ln in txt.splitlines():
            if ln.strip().startswith("## ACTIONS"):
                grab = True
                continue
            if grab and ln.startswith("## "):
                break
            if grab and ln.strip():
                acts.append(ln.rstrip())
        st = "OK" if status == "DONE" else "WARN"
        R.add(A, "next_drop_checklist", st,
              f"docs/NEXT_DROP_CHECKLIST.md is STATUS={status}: {len(acts)} action line(s) for the next drop -- OPEN that file",
              fix=("at ~01:35 CT launch the 4 guest-follower lines (bd0 reserve:0, bd1 reserve:1, resiC resi:2, "
                   "resiD resi:3, each --proactive-relaunch-s 720); fast-cadence alt-1 A/B is already armed; do NOT "
                   "create accounts; after the drop read q.py walls+admits --run <id>, then set STATUS: DONE"),
              items=acts[:24])

    R.guarded(A, "next_drop_checklist", actions)


def run(root: Path, drop: str = "", tcins=None, host=True, readiness=True, git=True) -> dict:
    R = Report()
    drop_ts = _parse_drop(drop) if drop else None
    if drop and drop_ts is None:
        R.add("drop", "parse", "WARN", f"could not parse --drop {drop!r} (use \"YYYY-MM-DD HH:MM\" local)")
    last_boot_ts = check_last_run(R, root)
    check_repo(R, root, git, last_boot_ts)
    check_next_drop_actions(R, root)
    pinfo = {"bot_running": False}
    if host and WIN:
        pinfo = check_process(R, root)
        check_host(R, drop_ts, root)
    elif host:
        R.add("host", "skipped", "UNKNOWN", "host checks need Windows PowerShell")
    check_accounts(R, root, readiness, drop_ts)
    check_config(R, root, tcins)
    check_monitor(R, root, last_boot_ts)
    wu_ok = any(r["id"] == "host.wu_pause" and r["status"] == "OK" for r in R.rows) \
        and not any(r["id"] == "host.wu_pause_vs_drop" and r["status"] == "WARN" for r in R.rows)
    mode = check_drop(R, drop_ts, wu_ok, getattr(R, "wu_info", None))
    rows = R.rows
    acts = [r for r in rows if r["status"] in ("FAIL", "WARN")]
    acts.sort(key=lambda r: ORDER.get(r["status"], 9))
    from collections import Counter
    counts = dict(Counter(r["status"] for r in rows))
    try:
        vcmd = validate_cmd(root)
    except Exception as e:
        vcmd = f"(could not build: {type(e).__name__})"
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mode": mode, "drop_ts": drop_ts,
            "verdict": verdict(rows), "counts": counts, "rows": rows, "operator_actions": acts,
            "validate_cmd": vcmd, "bot_running": pinfo.get("bot_running", False)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drop", default="", help='drop moment, local time: "2026-10-09 02:00"')
    ap.add_argument("--tcins", default="", help="comma-separated TCINs the operator wants tonight")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-host", action="store_true", help="skip PowerShell host / process checks")
    ap.add_argument("--no-readiness", action="store_true", help="do not run check_session_readiness.py")
    ap.add_argument("--no-git", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = run(Path(a.root), a.drop, [t for t in a.tcins.split(",") if t.strip()], host=not a.no_host,
                  readiness=not a.no_readiness, git=not a.no_git)
    except Exception as e:   # never leave the operator without output
        res = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mode": "?", "verdict": "NO-GO", "counts": {"FAIL": 1},
               "rows": [{"id": "preflight", "area": "repo", "status": "FAIL", "detail": f"preflight itself failed: {type(e).__name__}: {e}", "fix": ""}],
               "operator_actions": [], "validate_cmd": ""}
        res["operator_actions"] = res["rows"]
    print(json.dumps(res, indent=1, default=str) if a.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
