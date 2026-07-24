#!/usr/bin/env python3
"""Sign the PERSONAL browsers out of Target before the bot's accounts mint tokens.

WHY THIS EXISTS
---------------
Any normal browsing session logged into target.com on this machine is a SECOND
live session on one of the bot's accounts. Target rotates the member token out
from under the bot's session — that is the "token churn" that leaves every
account holding a GUEST token at fire time (see docs/FAILURES.md). Confirmed
2026-07-13: the personal Chrome `Default` profile held 48 target.com cookies
incl. a live `refreshToken`.

2026-07-14 overnight post-mortem widened the scope: business & alt-1 held DEAD
write-auth at every drop wave and the run's own alarm fired "[AUTH_CRITICAL]
TOKEN CHURN — suspect a second live session." The old guard only cleaned Google
Chrome — but this is a Win11 box whose DEFAULT browser is Microsoft Edge (auto-
signed into the operator's Microsoft account = the `primary` bot account), and
Edge was never touched. So the guard now sweeps EVERY Chromium browser present:
Chrome, Edge, and Brave.

WHAT IT DOES
------------
1. For each installed Chromium browser: closes it (gracefully; force only if it
   won't go). The browser keeps an EXCLUSIVE lock on its cookie DB — it cannot
   be read or edited while running — so closing is the only option.
2. Deletes every cookie whose host is target.com, from every personal profile of
   that browser. Nothing else is touched: other sites, passwords, history, tabs
   all survive.
3. Reopens each browser that HAD A VISIBLE WINDOW, with --restore-last-session,
   so the operator gets their tabs back. A browser that was only alive as
   background processes (Edge "startup boost", Chrome "continue running
   background apps" — no window) is purged and left closed, so the sweep no
   longer pops a blank window for a browser you never had open.

The bot's OWN Chromes (zendriver/nodriver `nodriver-profile*`) live in a
different user-data dir and are never matched by the Default/Profile-N filter.

To SEE which browser/profile/account is signed in without changing anything, run
the read-only companion:  diagnose_token_churn.py  (use --close for a definitive
read while browsers are open).

Run:  venv/Scripts/python.exe chrome_target_signout.py
Env:  CHROME_SIGNOUT_SKIP=1   -> no-op (kill switch)
      TARGET_SIGNOUT_BROWSERS=chrome,edge  -> restrict the sweep (default: all)
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

_LA = Path(os.environ.get("LOCALAPPDATA", ""))
_PF = Path(r"C:\Program Files")
_PF86 = Path(r"C:\Program Files (x86)")

# name -> (user-data dir, process image name, [candidate exe install paths]).
# Every Chromium browser whose personal Target session would churn the bot.
BROWSERS: dict[str, tuple[Path, str, list[Path]]] = {
    "chrome": (
        _LA / "Google" / "Chrome" / "User Data", "chrome.exe",
        [_PF / r"Google\Chrome\Application\chrome.exe",
         _PF86 / r"Google\Chrome\Application\chrome.exe"],
    ),
    "edge": (
        _LA / "Microsoft" / "Edge" / "User Data", "msedge.exe",
        [_PF / r"Microsoft\Edge\Application\msedge.exe",
         _PF86 / r"Microsoft\Edge\Application\msedge.exe"],
    ),
    "brave": (
        _LA / "BraveSoftware" / "Brave-Browser" / "User Data", "brave.exe",
        [_PF / r"BraveSoftware\Brave-Browser\Application\brave.exe",
         _PF86 / r"BraveSoftware\Brave-Browser\Application\brave.exe"],
    ),
}


def running(image: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                         capture_output=True, text=True).stdout
    return image.lower() in out.lower()


def has_visible_window(image: str) -> bool:
    """True if some process for this image owns a visible top-level window.

    Win11 keeps browser processes alive with NO window — Edge "startup boost"
    and Chrome's "continue running background apps when closed". Those show up
    in tasklist (so running() is True) but the user has nothing open. We use
    MainWindowHandle to tell a real window from a background-only process, and
    only reopen the former — otherwise the sweep spawns a blank new-tab window
    for a browser the operator never had open. MUST be called BEFORE closing
    the browser (closing destroys the window this looks for).

    Detection failure falls back to True (reopen): never leave a browser the
    operator had open closed just because the probe broke.
    """
    name = image[:-4] if image.lower().endswith(".exe") else image
    ps = (f"if (Get-Process -Name '{name}' -ErrorAction SilentlyContinue | "
          f"Where-Object {{ $_.MainWindowHandle -ne 0 }}) {{ 'YES' }} else {{ 'NO' }}")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15).stdout
        return "YES" in out.upper()
    except Exception:
        return True


def close_browser(image: str, timeout_s: float = 12.0) -> bool:
    """Graceful close first (tabs saved), force only if it refuses to exit."""
    subprocess.run(["taskkill", "/IM", image],
                   capture_output=True, text=True)              # WM_CLOSE, no /F
    deadline = time.time() + timeout_s
    while running(image) and time.time() < deadline:
        time.sleep(0.5)
    if running(image):
        # Background helpers (--no-startup-window) never take WM_CLOSE.
        subprocess.run(["taskkill", "/F", "/IM", image],
                       capture_output=True, text=True)
        time.sleep(2.0)
    return not running(image)


def reopen_browser(name: str, exes: list[Path]) -> None:
    exe = next((p for p in exes if p.exists()), None)
    if not exe:
        print(f"[SIGNOUT] {name}: exe not found — reopen it yourself")
        return
    subprocess.Popen([str(exe), "--restore-last-session"],
                     creationflags=subprocess.DETACHED_PROCESS)
    print(f"[SIGNOUT] {name}: reopened (--restore-last-session)")


def profiles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile")))


def cookie_db(profile: Path) -> Path | None:
    for candidate in (profile / "Network" / "Cookies", profile / "Cookies"):
        if candidate.exists():
            return candidate
    return None


def purge(name: str, profile: Path) -> int:
    db = cookie_db(profile)
    if db is None:
        return 0
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%target.com%'").fetchall()
        if not rows:
            print(f"[SIGNOUT] {name}/{profile.name}: clean (no target.com cookies)")
            return 0
        auth = sorted(AUTH_COOKIES & {n for (n,) in rows})
        state = f"SIGNED IN (auth: {auth})" if auth else "not signed in"
        cur = con.execute("DELETE FROM cookies WHERE host_key LIKE '%target.com%'")
        con.commit()
        print(f"[SIGNOUT] {name}/{profile.name}: {len(rows)} target.com cookies — "
              f"{state} -> deleted {cur.rowcount}")
        return cur.rowcount
    finally:
        con.close()


def sweep_browser(name: str) -> int:
    """Close (if running), purge every profile, reopen. Returns cookies deleted.
    Raises on hard failure so main() can report exit 1."""
    root, image, exes = BROWSERS[name]
    profs = profiles(root)
    if not profs:
        return 0  # browser not installed / no personal profiles

    was_running = running(image)
    # Decide reopen from a REAL window, not just a live process. Snapshot now,
    # BEFORE close_browser() destroys the window we're probing for. A browser
    # that was only background processes (no window) is purged and left closed
    # instead of reopened as a blank new-tab window.
    had_window = was_running and has_visible_window(image)
    if was_running and not close_browser(image):
        print(f"[SIGNOUT] FAILED to close {name} — cookie DB stays locked. "
              f"Sign out of Target in {name} manually before the drop.")
        raise RuntimeError(f"{name} would not close")

    total = 0
    try:
        for p in profs:
            total += purge(name, p)
    finally:
        if had_window:
            reopen_browser(name, exes)
        elif was_running:
            print(f"[SIGNOUT] {name}: was background-only (no window) — "
                  f"purged, not reopened")
    return total


def main() -> int:
    if os.environ.get("CHROME_SIGNOUT_SKIP") == "1":
        print("[SIGNOUT] skipped (CHROME_SIGNOUT_SKIP=1)")
        return 0

    only = os.environ.get("TARGET_SIGNOUT_BROWSERS", "").strip().lower()
    wanted = [b.strip() for b in only.split(",") if b.strip()] if only else list(BROWSERS)
    unknown = [b for b in wanted if b not in BROWSERS]
    if unknown:
        print(f"[SIGNOUT] ignoring unknown browser(s) in TARGET_SIGNOUT_BROWSERS: {unknown}")
    wanted = [b for b in wanted if b in BROWSERS]

    installed = [b for b in wanted if profiles(BROWSERS[b][0])]
    if not installed:
        print("[SIGNOUT] no personal Chromium profiles found — nothing to do")
        return 0

    total, failed = 0, []
    for name in installed:
        try:
            total += sweep_browser(name)
        except Exception as e:
            print(f"[SIGNOUT] {name}: FAILED: {e!r}")
            failed.append(name)

    if failed:
        print(f"[SIGNOUT] DONE WITH ERRORS — {total} cookie(s) removed; "
              f"could not clean: {', '.join(failed)}. Sign those out manually.")
        return 1
    if total:
        print(f"[SIGNOUT] DONE — removed {total} target.com cookie(s) across "
              f"{', '.join(installed)}. The bot's accounts now hold the only live "
              f"Target session.")
    else:
        print(f"[SIGNOUT] DONE — personal browsers ({', '.join(installed)}) were "
              f"already signed out of Target.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
