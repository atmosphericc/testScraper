#!/usr/bin/env python
"""Staleness tripwire, printed at the start of every Claude Code session.

Read-only. Never raises, never blocks a session: any failure prints one line and
exits 0. It answers a single question before any work starts -- "how much of what
I am about to rely on has already expired?"

Botting is adversarial and non-stationary. Target changes without telling us, and
this project once ran six weeks on a belief that had already been falsified. The
whole point of this script is to make that gap visible on sight instead of on
inspection.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, ".claude", "state", "CURRENT_STATE.md")
WRAPPER = os.path.join(ROOT, "run_bot_with_nightly_restart.bat")
CHANGES = os.path.join(ROOT, "docs", "TARGET_CHANGES.md")

# Adversary facts have a ~2-week half-life (see CURRENT_STATE.md decay classes).
WARN_DAYS = 14
LOUD_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(when: datetime | None) -> float | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (_now() - when).total_seconds() / 86400.0


def _state_asof() -> tuple[datetime | None, str | None]:
    """Parse the '**As of: YYYY-MM-DD ... HEAD `abc123`' line."""
    try:
        with open(STATE, encoding="utf-8") as fh:
            head = fh.read(4000)
    except OSError:
        return None, None
    m = re.search(r"As of:\s*(\d{4})-(\d{2})-(\d{2})", head)
    when = None
    if m:
        when = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    h = re.search(r"HEAD\s*`([0-9a-f]{6,40})`", head)
    return when, (h.group(1) if h else None)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _mtime(path: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return None


def _latest_run_log() -> tuple[str | None, datetime | None]:
    runs = os.path.join(ROOT, "logs", "runs")
    newest, newest_t = None, None
    try:
        for name in os.listdir(runs):
            p = os.path.join(runs, name)
            if not os.path.isfile(p):
                continue
            t = _mtime(p)
            if t and (newest_t is None or t > newest_t):
                newest, newest_t = name, t
    except OSError:
        pass
    return newest, newest_t


def main() -> int:
    lines: list[str] = []
    warn = False

    asof, state_head = _state_asof()
    age = _age_days(asof)
    if asof is None:
        lines.append("  state file: NOT FOUND or unparseable -- .claude/state/CURRENT_STATE.md")
        warn = True
    else:
        tag = "OK"
        if age is not None and age >= LOUD_DAYS:
            tag, warn = "EXPIRED", True
        elif age is not None and age >= WARN_DAYS:
            tag, warn = "STALE", True
        days = max(0, (_now().date() - asof.date()).days)
        lines.append(
            "  state file: %s (%d days old) -- %s" % (asof.strftime("%Y-%m-%d"), days, tag)
        )

    head = _git("rev-parse", "--short", "HEAD")
    if head and state_head and not head.startswith(state_head[: len(head)]) and not state_head.startswith(head):
        lines.append(
            "  HEAD moved since the state file was written: %s -> %s" % (state_head, head)
        )
        warn = True

    # Compare at DAY granularity: a same-day edit is not staleness, it is this
    # session's own work, and crying wolf on it trains the reader to ignore the box.
    wt = _mtime(WRAPPER)
    if wt and asof and wt.date() > asof.date():
        lines.append(
            "  run_bot_with_nightly_restart.bat changed AFTER the state file "
            "(%s) -- armed flags in it may be wrong" % wt.strftime("%Y-%m-%d")
        )
        warn = True

    name, t = _latest_run_log()
    if t:
        lines.append("  last run log: %s (%.0f days ago)" % (t.strftime("%Y-%m-%d"), _age_days(t) or 0))

    ct = _mtime(CHANGES)
    if ct:
        lines.append("  adversary changelog last touched: %s" % ct.strftime("%Y-%m-%d"))

    print("")
    print("=" * 72)
    print(" BOT STATE CHECK -- Target is a moving opponent; verify before relying")
    print("=" * 72)
    for ln in lines:
        print(ln)
    if warn:
        print("")
        print("  >> Facts about ADVERSARY BEHAVIOUR have a ~2-week half-life.")
        print("  >> Re-derive anything load-bearing before acting on it.")
        print("  >> Read .claude/state/CURRENT_STATE.md; it lists what is unresolved.")
    print("=" * 72)
    print("")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never block a session
        print("[state_check] skipped: %s" % exc)
        sys.exit(0)
