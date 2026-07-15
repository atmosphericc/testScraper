#!/usr/bin/env python3
"""READ-ONLY churn-source probe: find every LOCAL browser session logged into
Target, on any Chromium browser (Chrome + Edge + Brave), across every profile.

WHY THIS EXISTS
---------------
2026-07-14 overnight post-mortem: business & alt-1 held DEAD write-auth at every
drop wave (ATC 401 _ERR_AUTH_DENIED), and the run's own alarm fired
"[AUTH_CRITICAL] TOKEN CHURN — fresh member tokens keep getting invalidated
within minutes. Suspect a second live session on this account." A second live
Target session anywhere makes Target rotate the member token out from under the
bot, so the account starts every drop window cold.

`chrome_target_signout.py` heals this at boot — but it ONLY scans Google Chrome.
Microsoft Edge (the Win11 default, auto-signed into the operator's Microsoft
account) is never touched. This probe reports EVERY local Chromium session so we
can see exactly which browser/profile/account is the leak — or rule out the
machine entirely (→ the second session is off-machine: phone / Target app /
another PC / an account-level flag).

Nothing is modified. Cookie DBs are snapshot-copied to a temp dir and opened
read-only, so it is safe to run while the browsers are open (a running browser
may hide very recent writes; close it for a perfectly current read).

Run:  venv/Scripts/python.exe diagnose_token_churn.py
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

# Auth cookies that prove a signed-in (not guest) Target session.
AUTH_COOKIES = {"accessToken", "idToken", "refreshToken", "login-session"}
# JWT cookies worth decoding to name the account behind the session.
JWT_COOKIES = ("idToken", "accessToken")

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))

# (label, user-data-dir, main exe name) — every Chromium browser on this box.
BROWSERS = [
    ("Chrome", LOCALAPPDATA / "Google" / "Chrome" / "User Data", "chrome.exe"),
    ("Edge",   LOCALAPPDATA / "Microsoft" / "Edge" / "User Data", "msedge.exe"),
    ("Brave",  LOCALAPPDATA / "BraveSoftware" / "Brave-Browser" / "User Data", "brave.exe"),
]

# account_id -> username, from the bot's own account file (for match-by-email).
def load_bot_accounts() -> dict[str, str]:
    try:
        d = json.load(open(Path(__file__).parent / "config" / "target_accounts.json"))
        return {a.get("username", "").lower(): a.get("account_id", "?")
                for a in d.get("accounts", []) if a.get("username")}
    except Exception:
        return {}


def is_running(exe: str) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                             capture_output=True, text=True).stdout.lower()
        return exe.lower() in out
    except Exception:
        return False


def close_browser(exe: str, timeout_s: float = 12.0) -> bool:
    """Graceful close (tabs saved via restore), force only if it refuses."""
    subprocess.run(["taskkill", "/IM", exe], capture_output=True, text=True)
    deadline = time.time() + timeout_s
    while is_running(exe) and time.time() < deadline:
        time.sleep(0.5)
    if is_running(exe):
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, text=True)
        time.sleep(2.0)
    return not is_running(exe)


def reopen_browser(exe: str) -> None:
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        for sub in (r"Google\Chrome\Application\chrome.exe",
                    r"Microsoft\Edge\Application\msedge.exe",
                    r"BraveSoftware\Brave-Browser\Application\brave.exe"):
            exe_path = base / sub
            if exe_path.name.lower() == exe.lower() and exe_path.exists():
                subprocess.Popen([str(exe_path), "--restore-last-session"],
                                 creationflags=0x00000008)  # DETACHED_PROCESS
                return


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


def raw_snapshot(src: Path, dst: Path) -> bool:
    """Copy a file even while Chromium holds it open, by opening with the same
    full share mode (READ|WRITE|DELETE) the browser used. Plain shutil.copy2 is
    denied on the live Cookies DB; this is how cookie tools snapshot it."""
    GENERIC_READ = 0x80000000
    FILE_SHARE_ALL = 0x07          # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(src), GENERIC_READ, FILE_SHARE_ALL, None,
                        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if h == wintypes.HANDLE(-1).value or not h:
        return False
    try:
        buf = ctypes.create_string_buffer(1 << 20)
        read = wintypes.DWORD(0)
        with open(dst, "wb") as f:
            while True:
                if not k32.ReadFile(h, buf, len(buf), ctypes.byref(read), None) or read.value == 0:
                    break
                f.write(buf.raw[:read.value])
        return dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False
    finally:
        k32.CloseHandle(h)


def snapshot(src: Path, dst: Path) -> bool:
    if raw_snapshot(src, dst):
        return True
    try:
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def webkit_to_dt(ts: int) -> datetime | None:
    """Chromium timestamps are microseconds since 1601-01-01 UTC."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts / 1_000_000 - 11644473600, tz=timezone.utc)
    except Exception:
        return None


# ---- cookie-value decryption (best effort; identifies the account) ----------

def dpapi_unprotect(blob: bytes) -> bytes | None:
    """CryptUnprotectData via ctypes — no pywin32 dependency."""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def aes_key(user_data_root: Path) -> bytes | None:
    """The AES-GCM cookie key from Local State (v10/v11 scheme). Returns None if
    the browser uses newer app-bound (v20) encryption we can't unwrap here."""
    try:
        ls = json.load(open(user_data_root / "Local State", encoding="utf-8"))
        enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])
        if enc[:5] != b"DPAPI":
            return None
        return dpapi_unprotect(enc[5:])
    except Exception:
        return None


def decrypt_value(enc: bytes, key: bytes | None) -> str | None:
    """Decrypt a Chromium cookie value. Handles v10/v11 AES-256-GCM; v20
    (app-bound) is not unwrappable from here → returns None."""
    if not enc:
        return None
    try:
        prefix = enc[:3]
        if prefix in (b"v10", b"v11") and key:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce, ct = enc[3:15], enc[15:]
            return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8", "replace")
        if prefix == b"v20":
            return None  # app-bound; can't decrypt out-of-process
        # Legacy DPAPI-wrapped value (no version prefix).
        out = dpapi_unprotect(enc)
        return out.decode("utf-8", "replace") if out else None
    except Exception:
        return None


def jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def identify_account(claims: dict, bot: dict[str, str]) -> str | None:
    """Match a decoded token to a bot account by any email-ish claim."""
    for k in ("email", "eid", "username", "user_name", "sub", "preferred_username", "loginId"):
        v = str(claims.get(k, "")).lower()
        if v in bot:
            return f"{bot[v]} ({v})"
        if "@" in v:
            return f"UNKNOWN account ({v})"
    return None


def scan_profile(label: str, root: Path, profile: Path, key: bytes | None,
                 bot: dict[str, str]) -> dict | None:
    db = cookie_db(profile)
    if db is None:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="churnprobe_"))
    try:
        # Snapshot the DB (+ WAL/SHM) so a running browser's lock can't block us.
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                snapshot(src, tmp / (db.name + suffix))
        copied = tmp / db.name
        if not copied.exists():
            return {"label": label, "profile": profile.name, "error": "cookie DB locked — close the browser and re-run"}
        con = sqlite3.connect(f"file:{copied}?mode=ro&immutable=1", uri=True)
        try:
            rows = con.execute(
                "SELECT name, host_key, expires_utc, last_access_utc, encrypted_value "
                "FROM cookies WHERE host_key LIKE '%target.com%'").fetchall()
        finally:
            con.close()
        if not rows:
            return {"label": label, "profile": profile.name, "signed_in": False, "count": 0}

        names = {r[0] for r in rows}
        auth_present = sorted(AUTH_COOKIES & names)
        last_access = max((r[3] for r in rows), default=0)
        account = None
        if auth_present:
            for name, _host, _exp, _la, enc in rows:
                if name in JWT_COOKIES and account is None:
                    val = decrypt_value(enc, key)
                    if val:
                        account = identify_account(jwt_claims(val), bot)
        return {
            "label": label, "profile": profile.name,
            "signed_in": bool(auth_present), "count": len(rows),
            "auth": auth_present, "account": account,
            "last_access": webkit_to_dt(last_access),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    do_close = "--close" in sys.argv[1:]
    bot = load_bot_accounts()
    print("=" * 74)
    print("TOKEN-CHURN SOURCE PROBE — local Chromium Target sessions (READ-ONLY)")
    print("=" * 74)
    print(f"Bot accounts: {', '.join(f'{v}={k}' for k, v in bot.items()) or '(none loaded)'}")
    if do_close:
        print("Mode: --close (will gracefully close browsers for a definitive read,")
        print("      then reopen them with --restore-last-session)")
    print()

    # In --close mode, shut the browsers so their exclusive cookie-DB lock lifts.
    closed: list[str] = []
    if do_close:
        for _label, _root, exe in BROWSERS:
            if _root.exists() and is_running(exe):
                if close_browser(exe):
                    closed.append(exe)
                    print(f"[probe] closed {exe} for reading")
                else:
                    print(f"[probe] could NOT close {exe} — its profiles stay locked")
        print()

    findings: list[dict] = []
    locked: list[str] = []
    for label, root, exe in BROWSERS:
        if not root.exists():
            continue
        running = is_running(exe)
        key = aes_key(root)
        keynote = "" if key else "  (cookie key app-bound/unreadable — account id best-effort)"
        print(f"── {label}{'  [RUNNING]' if running else ''}{keynote}")
        profs = profiles(root)
        if not profs:
            print("   no profiles found")
            continue
        for prof in profs:
            r = scan_profile(label, root, prof, key, bot)
            if r is None:
                continue
            if r.get("error"):
                print(f"   {prof.name}: ⚠ {r['error']}")
                locked.append(f"{label}/{prof.name}")
                continue
            if not r["signed_in"]:
                extra = f"{r['count']} non-auth target.com cookies" if r["count"] else "clean"
                print(f"   {prof.name}: {extra}")
                continue
            la = r["last_access"]
            la_s = la.astimezone().strftime("%Y-%m-%d %H:%M") if la else "?"
            acct = r["account"] or "account unknown (encrypted)"
            print(f"   {prof.name}: ⚠ SIGNED IN TO TARGET → {acct}")
            print(f"             auth cookies: {r['auth']}   last active: {la_s}")
            findings.append({**r, "browser": label})

    # Reopen anything we closed, so the operator gets their tabs back.
    if closed:
        print()
        for exe in closed:
            reopen_browser(exe)
            print(f"[probe] reopened {exe}")

    print("\n" + "=" * 74)
    live = [f for f in findings if f["signed_in"]]
    if live:
        print(f"VERDICT: {len(live)} local Target session(s) competing with the bot:")
        for f in live:
            print(f"  • {f['browser']}/{f['profile']} → {f.get('account') or 'unknown account'}")
        print("\n  These rotate the member token out from under the bot at drop time.")
        print("  FIX: sign out there (or let the boot guard clean it — Edge/Brave are")
        print("  now covered by chrome_target_signout.py). Re-run to confirm clean.")
        if locked:
            print(f"\n  NOTE: {len(locked)} profile(s) were locked and unread: {', '.join(locked)}")
    elif locked:
        print("VERDICT: INCONCLUSIVE — the profiles that matter were LOCKED (browser open):")
        for p in locked:
            print(f"  • {p}  (unread)")
        print("\n  Modern Edge/Chrome lock the cookie DB exclusively while running, so")
        print("  a signed-in session there is INVISIBLE to a live scan. Do ONE of:")
        print("    • re-run definitively:  venv/Scripts/python.exe diagnose_token_churn.py --close")
        print("    • or just rely on the boot guard, which closes+cleans them anyway.")
    else:
        print("VERDICT: No local browser holds a Target session (all profiles read clean).")
        print("  → The churn source is OFF this machine. For each churning account")
        print("    (business=mfpshopllc@gmail.com, alt-1=anurajsinha17@gmail.com) check:")
        print("      • the Target app / target.com on your PHONE (most common)")
        print("      • target.com signed in on another PC / laptop")
        print("      • if none: a Target-side account flag (not fixable client-side).")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
