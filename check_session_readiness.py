#!/usr/bin/env python3
"""Zero-browser drop-readiness pre-check: decode each account's PERSISTED Target
session (target*.json) and report whether it will start hot.

Complements verify_multi_account_live.py: that one launches the real browsers and
fires a live carts-write probe (the definitive check, but heavyweight + needs a
fresh go). THIS one just reads the saved cookie jars and decodes the JWTs — no
Chrome, no network, no side effects — so you can spot-check readiness anytime.

What it tells you (from persisted state):
  - login-session TTL  → can this account mint a MEMBER token at bot startup?
    (login-session MISSING/expired ⇒ startup must do a credential relogin first;
     "session-typed" (expires=-1, fresh from a real login) is healthy — the boot
     watchdog promotes it to a 30d persistent cookie via CDP)
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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Run cleanly from the .bat (cmd console is cp1252/cp437; the ✅/⚠ glyphs would
# otherwise raise UnicodeEncodeError and exit non-zero). errors='replace' keeps
# the ASCII verdict text intact even where the glyphs can't render.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
NOW = time.time()
AUTH = ("login-session", "refreshToken", "accessToken", "idToken")

# 2026-09-28 pre-drop: READINESS_GUEST_CHECK (default 1; 0 = the pre-09-28 output
# byte for byte). All three jars printed "healthy MEMBER" that night while each held
# only a GUEST idToken (sut=G): a jar with NO accessToken fell through to "will
# re-mint at startup (usually fine)" without looking at the idToken. And since
# 2026-09-25 ~06:00 nothing inside the bot re-mints a member token (rung 1 404,
# rung 2 no token), so a member token's exp is when that account's write-auth ends:
# print it. Also: a naive saved_at (session_manager writes local time) was read as
# UTC -- the documented "+5 h display skew" on 'saved Xh ago'.
_GUEST_CHECK = __import__("os").environ.get("READINESS_GUEST_CHECK", "1").strip() != "0"

# 2026-10-04: READINESS_REMINT_AWARE (default 1; 0 = the 09-28 output byte for byte).
# "nothing in the bot re-mints" was REFUTED on 09-29 (C-0929-01: the running bot re-mints
# each member token at its 4 h expiry, 12/12). And the bat's start pass
# (`relogin_one.py all`, validate-first) refreshes an EXPIRED member token from a live
# login-session before app.py starts: primary 2026-10-04 20:48:19 ("already logged in",
# token iat 20:48:14), business / alt-1 2026-10-02 09:23:39 / 09:23:54, primary / alt-1
# 2026-09-28 16:11-16:12. So an expired token on a live member session needs no hand
# login; only a missing/expired login-session or a guest jar does. The bat re-runs this
# check AFTER that pass, so a pass that failed still shows up red at boot.
_REMINT_AWARE = __import__("os").environ.get("READINESS_REMINT_AWARE", "1").strip() != "0"


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
            if dt.tzinfo is None and not _GUEST_CHECK:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()   # naive -> local time (how session_manager writes it)
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


# 2026-08-25: filled by tcin_visibility_report() so the VERDICT block can print a
# one-line status without re-reading state. Keys: status ("unknown" | "warning" |
# "partial" | "ok"), reason, invisible, unchecked, stale, age, n_enabled.
_VIS: dict = {"status": "unknown", "reason": "not evaluated"}

# Real timing from run_20260824_231920.log: the stats loop and the ground-truth
# loop start together with a 30 s first wait, so the banner (the only thing that
# prints it / writes the state) fires in the SAME SECOND as the first
# '[STOCK STATS] t=30.0s' line -- 30 s after the pool-ready line, ~3-4 min after
# launch (08-24: launch 23:19:20 -> pool ready 23:22:33 -> STATS + banner 23:23:03).
_BANNER_TIMING = ("in the same second as the first [STOCK STATS] t=30.0s line (30 s after "
                  "'[MULTI_SESSION] started -- N/N sessions ready', ~3-4 min after launch; "
                  "08-24: launch 23:19:20 -> pool ready 23:22:33 -> STATS + banner 23:23:03)")

# 2026-08-25 round 3: dispatch_verify sends the FULL enabled list unchunked and
# RedSky caps product_summary_with_fulfillment_v1 at 30/req (client chunk = 28), so
# above this the ground-truth read fails every cycle and visibility is UNKNOWN.
_MAX_VERIFY_TCINS = 30   # RedSky caps the endpoint at 30/req; the ground-truth read is unchunked


def _fmt_local(unix: float | None) -> str:
    """Local wall-clock for a unix stamp, or '?' when missing/unparseable."""
    try:
        return datetime.fromtimestamp(float(unix)).strftime("%Y-%m-%d %H:%M:%S") if unix else "?"
    except Exception:
        return "?"


def _too_many_tcins_reason(n: int) -> str:
    return (f"{n} enabled TCINs > {_MAX_VERIFY_TCINS} -- the ground-truth read is unchunked and "
            f"RedSky caps the endpoint at 30/req, so it will fail every cycle and TCIN visibility "
            f"will be UNKNOWN all run; disable some before launch (the sweep chunks at 28 and is "
            f"unaffected)")


def _fmt_age(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        return f"{seconds/60:.0f}m"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}d"


_CFG_ERR = ""


def _enabled_tcins() -> list[str] | None:
    """Enabled TCINs from config/product_config.json, or None when the config
    cannot be read (never [] -- an empty list would read as a false OK)."""
    global _CFG_ERR
    _CFG_ERR = ""
    try:
        with open(ROOT / "config" / "product_config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        return [str(p.get("tcin")).strip() for p in cfg.get("products", [])
                if isinstance(p, dict) and p.get("tcin") and p.get("enabled", True)]
    except Exception as e:
        _CFG_ERR = f"{type(e).__name__}: {e}"
        return None


def _vis_unknown(reason: str) -> str:
    global _VIS
    _VIS = {"status": "unknown", "reason": reason}
    return "unknown"


def tcin_visibility_report() -> str:
    """2026-08-25: surface TCINs that were configured but ABSENT from RedSky on
    the last bot run (unpublished on Target => invisible to detection; 08-24 went
    0-for with 4 of 13 armed TCINs in that state and nobody saw the warning).
    READ-ONLY: reads state/tcin_visibility.json written by the checker.
    Returns a status string:
      "unknown" -- no state yet / reader unavailable / config unreadable
      "warning" -- at least one enabled TCIN was INVISIBLE in the last run
      "partial" -- nothing invisible, but enabled TCIN(s) were never checked by
                   that run (added since) and/or the state is STALE (>24h)
      "ok"      -- state fresh and every enabled TCIN was configured AND visible
    Never affects the exit code (unpublished TCINs are legitimate).
    """
    global _VIS
    print("\n" + "-" * 78)
    try:
        from src.monitoring.tcin_visibility import load_state, summarize, format_invisible_warning
    except ImportError as e:
        print(f"TCIN visibility: reader unavailable ({e})")
        return _vis_unknown(f"reader unavailable ({e})")
    enabled = _enabled_tcins()
    if enabled is None:
        print(f"TCIN visibility: could not read config/product_config.json ({_CFG_ERR}) "
              f"-- visibility UNKNOWN")
        return _vis_unknown(f"could not read config/product_config.json ({_CFG_ERR})")
    if len(enabled) > _MAX_VERIFY_TCINS:
        reason = _too_many_tcins_reason(len(enabled))
        print(f"TCIN visibility: {reason}")
        return _vis_unknown(reason)
    if not enabled:
        # Before the stale/invisible ladder (round-3 review): "nothing armed" must
        # never be reported as merely PARTIAL/STALE.
        print("TCIN visibility: no enabled TCINs in config/product_config.json -- nothing is armed")
        return _vis_unknown("no enabled TCINs in config/product_config.json")
    try:
        state = load_state(ROOT / "state")
    except Exception as e:
        print(f"TCIN visibility: reader unavailable ({e})")
        return _vis_unknown(f"reader unavailable ({e})")
    if state is None:
        print("TCIN visibility: no state yet -- the checker writes state/tcin_visibility.json on "
              "its first successful ground-truth read;")
        print(f"  read the [TCIN-VISIBILITY] banner {_BANNER_TIMING}")
        return _vis_unknown("no state from a prior bot run yet (see above)")
    try:
        summary = summarize(state, enabled, NOW)
    except Exception as e:
        print(f"TCIN visibility: reader unavailable ({e})")
        return _vis_unknown(f"reader unavailable ({e})")
    stale_s = ", STALE" if summary.stale else ""
    age = _fmt_age(summary.age_s)
    if summary.verified:
        print(f"TCIN visibility (from the last bot run, {age} ago{stale_s}): "
              f"{len(summary.visible)}/{len(summary.configured)} configured TCIN(s) visible to RedSky")
    else:
        # never lead with an all-clear count for a run that could not verify
        print(f"TCIN visibility (from the last bot run, {age} ago{stale_s}): UNVERIFIED -- "
              f"last-known {len(summary.visible)}/{len(summary.configured)} visible, "
              f"NOT a fresh verdict")
    # format_invisible_warning already emits the "NOT monitored (added since)" line
    # for unchecked_enabled -- do not print it a second time here.
    for line in format_invisible_warning(summary):
        print(f"  {line}")
    if summary.stale:
        print(f"  [TCIN-VISIBILITY] STALE: re-check {_BANNER_TIMING}")
    if not summary.verified:
        # Round 3: the checker writes verified=false once 10 consecutive ground-truth
        # reads fail (>30 TCINs, throttled pool, no ready session). The lists above are
        # the last KNOWN state, not a verdict -> UNKNOWN, never OK/PARTIAL.
        reason = (f"the last run could NOT verify visibility ({summary.gt_fail_streak} consecutive "
                  f"failed ground-truth reads since "
                  f"{_fmt_local(summary.verification_failed_since_unix)}; {age} old) "
                  f"-- treat every enabled TCIN as unverified")
        print(f"  {reason}")
        return _vis_unknown(reason)
    invisible = sorted(summary.invisible_enabled)
    visible_set = set(summary.visible)
    # "unchecked" = never monitored by that run, plus the defensive case of an
    # enabled TCIN that was configured but landed in neither list.
    unchecked = sorted(t for t in enabled if t not in visible_set and t not in summary.invisible_enabled)
    if invisible:
        status = "warning"
    elif unchecked or summary.stale:
        status = "partial"
    else:
        status = "ok"
    _VIS = {"status": status, "reason": "", "invisible": invisible, "unchecked": unchecked,
            "stale": summary.stale, "age": age, "n_enabled": len(enabled)}
    return status


def main() -> int:
    print("=" * 78)
    print("DROP-READINESS PRE-CHECK — persisted sessions (READ-ONLY, no browser)")
    print("=" * 78)
    accts = load_accounts()
    all_green = True
    any_file = False
    member_exps: list = []          # (exp unix, acct) -- READINESS_GUEST_CHECK only
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
        # login-session often arrives as a browser-session cookie (expires=-1, no
        # client TTL) straight after a real login — that is NOT dead: the session
        # watchdog promotes it to a 30d persistent cookie at boot
        # (session_manager._fix_session_cookies). Only missing or past-dated = dead.
        ls_cookie = cks.get("login-session")
        ls_session_typed = bool(ls_cookie) and ls <= 0
        at = cks.get("accessToken")
        claims = jwt_claims(at["value"]) if at else {}
        eid = bool(claims.get("eid") or claims.get("sub"))
        sut = claims.get("sut") or claims.get("user_type") or "?"
        is_member = eid and str(sut).upper() in ("R", "M", "MEMBER")
        idt = cks.get("idToken")
        id_sut = str(jwt_claims(idt["value"]).get("sut") or "?") if (_GUEST_CHECK and idt) else "?"
        at_exp = to_unix(claims.get("exp")) if (_GUEST_CHECK and claims) else 0.0

        # Verdict: login-session is what lets startup mint a member token.
        if not ls_cookie:
            verdict = "❌ login-session MISSING — startup needs a credential relogin first"
            all_green = False
        elif 0 < ls < NOW:
            verdict = "❌ login-session EXPIRED — startup needs a credential relogin first"
            all_green = False
        elif not at and _GUEST_CHECK and id_sut.upper() == "G":
            bat = f"hand_login_{acct.replace('-', '')}_force.bat"
            verdict = (f"❌ SIGNED OUT — no accessToken and the idToken is a GUEST's (sut=G). "
                       f"Force a hand login ({bat}) before boot")
            all_green = False
        elif not at:
            verdict = "⚠  no accessToken saved — will re-mint at startup (usually fine)"
        elif not is_member:
            verdict = f"⚠  persisted token was GUEST (sut={sut}) — startup must re-mint MEMBER"
            all_green = False
        elif _GUEST_CHECK and at_exp and at_exp <= NOW and _REMINT_AWARE and id_sut.upper() == "R":
            verdict = (f"✅ member token expired {_fmt_local(at_exp)} but the login-session is live — "
                       f"the bat's start pass (relogin_one.py all) refreshes it before app.py; "
                       f"no hand login needed")
        elif _GUEST_CHECK and at_exp and at_exp <= NOW:
            verdict = (f"❌ MEMBER token EXPIRED {_fmt_local(at_exp)} — since 2026-09-25 nothing in "
                       f"the bot re-mints it; force a hand login before boot")
            all_green = False
        else:
            verdict = "✅ healthy MEMBER session — will start hot"
            if _GUEST_CHECK and at_exp:
                member_exps.append((at_exp, acct))
                verdict += (f"; member token expires {_fmt_local(at_exp)} "
                            f"({(at_exp - NOW) / 3600:.1f}h left)")

        ls_s = "session-typed (promoted to 30d at boot)" if ls_session_typed else ttl(ls)
        print(f"\n{acct:10} saved {saved_s}")
        print(f"           login-session={ls_s}   refreshToken={ttl(rt)}   "
              f"accessToken={'MEMBER' if is_member else 'guest/none'} (sut={sut})"
              + (f"   idToken sut={id_sut}" if _GUEST_CHECK else ""))
        print(f"           {verdict}")

    # 2026-08-25: TCIN visibility from the last run (independent of session health;
    # never changes all_green or the exit code -- unpublished TCINs are legitimate).
    vis_status = tcin_visibility_report()

    print("\n" + "=" * 78)
    if not any_file:
        print("VERDICT: no session files found — accounts will start signed out.")
    elif all_green:
        print("VERDICT: ✅ all accounts hold healthy MEMBER sessions → all start hot at boot.")
        print("  NOTE: this is PERSISTED state. It does NOT prove the live token survives")
        print("  to the drop — the rebuilds churn at runtime. Confirm close to the drop with")
        print("  verify_multi_account_live.py, and see docs/PRE_DROP_RUNBOOK.md.")
        if member_exps and _REMINT_AWARE:
            first_exp, first_acct = min(member_exps)
            print(f"  Member tokens live 4 h ({first_acct}'s is first to expire, at "
                  f"{_fmt_local(first_exp)}); the running bot re-mints each at expiry (C-0929-01).")
            print("  A hand login is needed only for a ❌ line: a missing/expired login-session or")
            print("  a signed-out (guest) jar.")
        elif member_exps:
            first_exp, first_acct = min(member_exps)
            # rung 1 (token_refresh) 404s and rung 2 (/account reload) mints nothing
            print(f"  WRITE-AUTH ENDS: {first_acct}'s member token expires first, at "
                  f"{_fmt_local(first_exp)}. Since 2026-09-25 nothing in the bot re-mints a")
            print("  member token, so each account's add-to-carts 401 from its expiry on (or")
            print("  from TARGET_TOKEN_MIN_TTL_S before it, when the keep-fresh repair deletes it).")
    else:
        print("VERDICT: ⚠ one or more accounts will NOT start hot — see above.")
        if _GUEST_CHECK:
            print("  Recover them PRE-drop with a FORCED hand login (hand_login_<acct>_force.bat):")
            print("  the wrapper's relogin_one.py pass calls a guest 'already logged in' and the")
            print("  scripted re-login has been Shape-blocked at the username step. Then re-run.")
        else:
            print("  Recover them PRE-drop (restart bot / nightly relogin), then re-run.")
    if vis_status == "ok":
        print(f"TCIN VISIBILITY: OK -- all {_VIS['n_enabled']} enabled TCIN(s) were visible to RedSky "
              f"in the last run ({_VIS['age']} old)")
    elif vis_status == "warning":
        print(f"TCIN VISIBILITY: WARNING -- {len(_VIS['invisible'])} enabled TCIN(s) invisible to RedSky "
              f"in the last run: {_VIS['invisible']} (see above)")
    elif vis_status == "partial":
        parts = []
        if _VIS["unchecked"]:
            parts.append(f"{len(_VIS['unchecked'])} enabled TCIN(s) NOT yet checked (added since the "
                         f"last run): {_VIS['unchecked']}")
        if _VIS["stale"]:
            parts.append(f"state is STALE ({_VIS['age']} old)")
        print("TCIN VISIBILITY: PARTIAL -- " + " -- ".join(parts)
              + f" -- confirm the [TCIN-VISIBILITY] banner {_BANNER_TIMING}, "
              f"or re-run this script once the bot is up")
    else:
        print(f"TCIN VISIBILITY: UNKNOWN -- {_VIS.get('reason', 'see above')}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
