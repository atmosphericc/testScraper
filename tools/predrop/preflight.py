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
ENV_NAME = re.compile(r"['\"]((?:TARGET|RESILIENT|STOCK|USE_RESILIENT|CHROME_|READINESS|RELOGIN|DROP_READINESS|SINGLE_INSTANCE|WRAPPER|VALIDATE|HARVEST|PX_|SHAPE_|BD_)[A-Z0-9_]*)['\"]")
MONITOR_ENV_PREFIX = ("RESILIENT_", "STOCK_", "TARGET_SWEEPS_PER_SEC", "USE_RESILIENT_STACK", "CHROME_STAGGER_TOTAL_S")
MAX_ONE_CHUNK = 28


class Report:
    def __init__(self):
        self.rows = []

    def add(self, area, cid, status, detail, fix=""):
        self.rows.append({"id": f"{area}.{cid}", "area": area, "status": status, "detail": detail, "fix": fix})

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


def check_host(R: Report):
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

    def windows_update():
        rows = _ps_json("Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\WindowsUpdate\\UX\\Settings' -ErrorAction SilentlyContinue | Select-Object PauseUpdatesExpiryTime,ActiveHoursStart,ActiveHoursEnd")
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

    R.guarded(A, "cfg", cfg)
    if run_readiness:
        R.guarded(A, "readiness", readiness)
    R.guarded(A, "latches", latches)


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
        if n > 25:
            R.add(A, "gt_read_unproven", "WARN", f"{n} TCINs: the apps-channel ground-truth read is unchunked and has only been proven with <= 25 TCINs (25 once, 2026-10-08 22:41); 26-28 is untested",
                  "after boot, confirm `[GROUND-TRUTH] pool cache-bust ok` lines keep coming; 10 failures in a row = `[TCIN-VISIBILITY] UNKNOWN` (the sweep itself still detects)")

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

def check_drop(R: Report, drop_ts, wu_paused_ok: bool):
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
    if 2 <= hour < 11 and not wu_paused_ok:
        R.add(A, "wu_overlap", "WARN", "the drop falls in 02:00-11:00, when Windows Update may restart the host (active hours end 02:00)", "pause Windows Update for the week")
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
    for area in ("drop", "accounts", "config", "repo", "process", "host", "monitor", "last_run"):
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


def run(root: Path, drop: str = "", tcins=None, host=True, readiness=True, git=True) -> dict:
    R = Report()
    drop_ts = _parse_drop(drop) if drop else None
    if drop and drop_ts is None:
        R.add("drop", "parse", "WARN", f"could not parse --drop {drop!r} (use \"YYYY-MM-DD HH:MM\" local)")
    last_boot_ts = check_last_run(R, root)
    check_repo(R, root, git, last_boot_ts)
    pinfo = {"bot_running": False}
    if host and WIN:
        pinfo = check_process(R, root)
        check_host(R)
    elif host:
        R.add("host", "skipped", "UNKNOWN", "host checks need Windows PowerShell")
    check_accounts(R, root, readiness, drop_ts)
    check_config(R, root, tcins)
    check_monitor(R, root, last_boot_ts)
    wu_ok = any(r["id"] == "host.wu_pause" and r["status"] == "OK" for r in R.rows)
    mode = check_drop(R, drop_ts, wu_ok)
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
