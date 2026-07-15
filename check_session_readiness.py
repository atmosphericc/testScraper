#!/usr/bin/env python3
"""Zero-browser drop-readiness pre-check: decode each account's PERSISTED Target
session (target*.json) and report whether it will start hot.

Complements verify_multi_account_live.py: that one launches the real browsers and
fires a live carts-write probe (the definitive check, but heavyweight + needs a
fresh go). THIS one just reads the saved cookie jars and decodes the JWTs — no
Chrome, no network, no side effects — so you can spot-check readiness anytime.

What it tells you (from persisted state):
  - login-session TTL  → can this account mint a MEMBER token at bot startup?
    (login-session dead ⇒ startup must do a credential relogin first)
  - refreshToken TTL   → longer-lived session anchor
  - accessToken type   → was the account MEMBER (sut=R + eid) or GUEST when saved?

What it CANNOT tell you: whether the LIVE token will survive to the drop moment.
That is the 2026-07-14 churn — only a live carts WRITE proves it, and only at the
time it matters. Run verify_multi_account_live.py (or watch the bot boot) for that.

Run:  venv/Scripts/python.exe check_session_readiness.py
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOW = time.time()
AUTH = ("login-session", "refreshToken", "accessToken", "idToken")


def load_accounts() -> list[tuple[str, str]]:
    try:
        cfg = json.load(open(ROOT / "config" / "target_accounts.json"))
        return [(a["account_id"], a.get("session_path", "")) for a in cfg.get("accounts", [])
                if a.get("enabled", True) and a.get("session_path")]
    except Exception:
        return [("primary", "target.json"), ("business", "target-2.json"), ("alt-1", "target-3.json")]


def to_unix(x) -> float:
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    try:
        return float(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0


def jwt_claims(v: str) -> dict:
    try:
        p = v.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def ttl(exp: float) -> str:
    if not exp:
        return "—"
    d = exp - NOW
    if d < 0:
        return f"EXPIRED {abs(d)/3600:.0f}h ago"
    return f"{d/3600:.1f}h" if d < 86400 else f"{d/86400:.1f}d"


def main() -> int:
    print("=" * 78)
    print("DROP-READINESS PRE-CHECK — persisted sessions (READ-ONLY, no browser)")
    print("=" * 78)
    accts = load_accounts()
    all_green = True
    any_file = False
    for acct, fn in accts:
        path = ROOT / fn
        if not path.exists():
            print(f"\n{acct:10} ❌ session file {fn} MISSING — account will start signed out")
            all_green = False
            continue
        any_file = True
        try:
            data = json.load(open(path))
        except Exception as e:
            print(f"\n{acct:10} ❌ {fn} unreadable: {e}")
            all_green = False
            continue
        cks = {c["name"]: c for c in data.get("cookies", []) if c.get("name")}
        saved = to_unix(data.get("saved_at"))
        saved_s = f"{(NOW-saved)/3600:.0f}h ago" if saved else "?"

        def cexp(n: str) -> float:
            c = cks.get(n)
            return to_unix(c.get("expires")) if c else 0.0

        ls, rt = cexp("login-session"), cexp("refreshToken")
        at = cks.get("accessToken")
        claims = jwt_claims(at["value"]) if at else {}
        eid = bool(claims.get("eid") or claims.get("sub"))
        sut = claims.get("sut") or claims.get("user_type") or "?"
        is_member = eid and str(sut).upper() in ("R", "M", "MEMBER")

        # Verdict: login-session is what lets startup mint a member token.
        if ls <= 0:
            verdict = "❌ login-session DEAD — startup needs a credential relogin first"
            all_green = False
        elif not at:
            verdict = "⚠  no accessToken saved — will re-mint at startup (usually fine)"
        elif not is_member:
            verdict = f"⚠  persisted token was GUEST (sut={sut}) — startup must re-mint MEMBER"
            all_green = False
        else:
            verdict = "✅ healthy MEMBER session — will start hot"

        print(f"\n{acct:10} saved {saved_s}")
        print(f"           login-session={ttl(ls)}   refreshToken={ttl(rt)}   "
              f"accessToken={'MEMBER' if is_member else 'guest/none'} (sut={sut})")
        print(f"           {verdict}")

    print("\n" + "=" * 78)
    if not any_file:
        print("VERDICT: no session files found — accounts will start signed out.")
    elif all_green:
        print("VERDICT: ✅ all accounts hold healthy MEMBER sessions → all start hot at boot.")
        print("  NOTE: this is PERSISTED state. It does NOT prove the live token survives")
        print("  to the drop — the rebuilds churn at runtime. Confirm close to the drop with")
        print("  verify_multi_account_live.py, and see docs/PRE_DROP_RUNBOOK.md.")
    else:
        print("VERDICT: ⚠ one or more accounts will NOT start hot — see above.")
        print("  Recover them PRE-drop (restart bot / nightly relogin), then re-run.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
