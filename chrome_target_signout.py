#!/usr/bin/env python3
"""Sign the PERSONAL Chrome out of Target before the bot's accounts mint tokens.

WHY THIS EXISTS
---------------
The primary bot account is the operator's own Microsoft/Target login, so any
normal browsing session logged into target.com in the personal Chrome is a
SECOND live session on the same account. Target rotates the member token out
from under the bot's session — that is the 2026-07-10 "token churn" that left
every account holding a GUEST token at fire time (see docs/FAILURES.md). It was
confirmed on 2026-07-13: the personal Chrome `Default` profile held 48
target.com cookies including a live `refreshToken`.

The operator was told "just sign out before each drop" three sessions running
and it kept coming back, so the bot now heals it itself at startup.

WHAT IT DOES
------------
1. Closes Chrome (gracefully; force only if it will not go). Chrome keeps an
   EXCLUSIVE lock on its cookie DB — plain copy, .NET share-mode read and even
   elevated `robocopy /B` all fail — so the DB simply cannot be read or edited
   while Chrome runs. Closing it is the only option.
2. Deletes every cookie whose host is target.com, from every personal profile.
   Nothing else is touched: other sites, passwords, history, tabs all survive.
3. Reopens Chrome with --restore-last-session if it was running, so the
   operator gets their tabs back.

The bot's OWN Chromes (nodriver-profile*) live in a different user-data dir and
are never touched by this.

Run:  venv/Scripts/python.exe chrome_target_signout.py
Env:  CHROME_SIGNOUT_SKIP=1   -> no-op (kill switch)
Exit: 0 on success/no-op, 1 on failure (the wrapper does NOT abort on failure —
      a stale personal session degrades the drop, it does not break the bot).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

AUTH_COOKIES = {"accessToken", "idToken", "refreshToken", "login-session"}

CHROME_EXES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def chrome_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                         capture_output=True, text=True).stdout
    return "chrome.exe" in out.lower()


def close_chrome(timeout_s: float = 12.0) -> bool:
    """Graceful close first (tabs saved), force only if it refuses to exit."""
    subprocess.run(["taskkill", "/IM", "chrome.exe"],
                   capture_output=True, text=True)          # WM_CLOSE, no /F
    deadline = time.time() + timeout_s
    while chrome_running() and time.time() < deadline:
        time.sleep(0.5)
    if chrome_running():
        # Background helpers (--no-startup-window) never take WM_CLOSE.
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                       capture_output=True, text=True)
        time.sleep(2.0)
    return not chrome_running()


def reopen_chrome() -> None:
    exe = next((p for p in CHROME_EXES if p.exists()), None)
    if not exe:
        print("[CHROME-SIGNOUT] chrome.exe not found — reopen it yourself")
        return
    subprocess.Popen([str(exe), "--restore-last-session"],
                     creationflags=subprocess.DETACHED_PROCESS)
    print("[CHROME-SIGNOUT] Chrome reopened (--restore-last-session)")


def profiles() -> list[Path]:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile")))


def cookie_db(profile: Path) -> Path | None:
    for candidate in (profile / "Network" / "Cookies", profile / "Cookies"):
        if candidate.exists():
            return candidate
    return None


def purge(profile: Path) -> int:
    db = cookie_db(profile)
    if db is None:
        return 0
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%target.com%'").fetchall()
        if not rows:
            print(f"[CHROME-SIGNOUT] {profile.name}: clean (no target.com cookies)")
            return 0
        auth = sorted(AUTH_COOKIES & {n for (n,) in rows})
        state = f"SIGNED IN (auth: {auth})" if auth else "not signed in"
        cur = con.execute("DELETE FROM cookies WHERE host_key LIKE '%target.com%'")
        con.commit()
        print(f"[CHROME-SIGNOUT] {profile.name}: {len(rows)} target.com cookies — "
              f"{state} -> deleted {cur.rowcount}")
        return cur.rowcount
    finally:
        con.close()


def main() -> int:
    if os.environ.get("CHROME_SIGNOUT_SKIP") == "1":
        print("[CHROME-SIGNOUT] skipped (CHROME_SIGNOUT_SKIP=1)")
        return 0

    profs = profiles()
    if not profs:
        print("[CHROME-SIGNOUT] no personal Chrome profiles found — nothing to do")
        return 0

    was_running = chrome_running()
    if was_running and not close_chrome():
        print("[CHROME-SIGNOUT] FAILED to close Chrome — cookie DB stays locked. "
              "Sign out of Target in Chrome manually before the drop.")
        return 1

    total = 0
    try:
        for p in profs:
            total += purge(p)
    except Exception as e:
        print(f"[CHROME-SIGNOUT] FAILED: {e!r}")
        if was_running:
            reopen_chrome()
        return 1

    if was_running:
        reopen_chrome()

    if total:
        print(f"[CHROME-SIGNOUT] DONE — removed {total} target.com cookie(s). "
              f"The bot's accounts now hold the only live Target session.")
    else:
        print("[CHROME-SIGNOUT] DONE — personal Chrome was already signed out of Target.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
