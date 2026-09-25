#!/usr/bin/env python3
"""Offline tests: TARGET_TOKEN_MINT_LOG -- log Target's answer to the member-token mint (2026-09-25).

The token repair (SessionManager.refresh_access_token) minted 80/80 times through
09-25 03:54, then 0/62 from 06:01 on all three accounts (docs/CLAIMS.md C-0925-04).
Why is NOT ESTABLISHED because Target's answer was never logged: rung 1's script
returned `true` whatever happened, and rung 2 logged only the jar afterwards.
TARGET_TOKEN_MINT_LOG=1 logs rung 1's HTTP status (or error name) and, on a failed
rung 2, where the auth-gated /account load ended up. LOG ONLY.

Pins:
  - flag off: the rung-1 script is the pre-09-25 literal byte for byte, no new line
  - flag on, mint fails: `mint rung1 token_refresh -> status=...` and
    `mint rung2 after /account reload final_url=... accessToken=none ttl=...`
  - flag on vs off: the same return value, the same cookie deletes and the same
    /account load; the only extra call is one read-only `location.href`
  - flag on, rung 1 mints: the rung-1 line, no delete, no /account load
  - a raising location.href read still logs (final_url=?(...)) and never raises

No browser, no network. Run: venv/Scripts/python.exe tests/test_token_mint_log.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time as _real_time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.session.session_manager as smm  # noqa: E402
from src.session.session_manager import SessionManager  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {str(detail)[:300]}")


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class _FastTime:
    """Stand-in for the module's `time`: every time() call advances 0.5 s, so the
    2 s and 8 s jar polls end after a handful of iterations instead of 10 s."""
    def __init__(self):
        self._t = _real_time.time()

    def time(self):
        self._t += 0.5
        return self._t

    def __getattr__(self, name):
        return getattr(_real_time, name)


REFRESH_URL = 'https://gsp.target.com/gsp/token_refresh?client_id=ecom-web-1.0.0'
# The pre-09-25 rung-1 script, rendered (session_manager.py at HEAD 7e58eac0).
QUIET_JS = ("(async () => {\n"
            f"                        try {{ await fetch({json.dumps(REFRESH_URL)},\n"
            "                            {method:'POST', credentials:'include'}); } catch(_e) {}\n"
            "                        return true;\n"
            "                    })()")


class _Tab:
    def __init__(self, href="https://www.target.com/login?client_id=ecom-web-1.0.0", href_raises=False):
        self.evals, self.sends, self.gets = [], [], []
        self.href, self.href_raises = href, href_raises

    async def evaluate(self, js, await_promise=False, **kw):
        self.evals.append(js)
        if js == 'location.href':
            if self.href_raises:
                raise RuntimeError("tab gone")
            return self.href
        if 'r.status' in js:
            return 'status=401 type=basic redirected=false'
        return True

    async def send(self, cmd):
        # A zendriver CDP command is a generator whose first yield is the wire
        # request {method, params}; record that (repr() would include an address).
        try:
            self.sends.append(json.dumps(next(cmd), sort_keys=True))
        except Exception as e:
            self.sends.append(f"unreadable:{type(e).__name__}")
        return None

    async def get(self, url):
        self.gets.append(url)
        return None


def _run(flag_on, mint_after_rung1=False, href_raises=False):
    sm = SessionManager(session_path="t.json", account_id="primary")
    cap = _Cap()
    sm.logger = logging.getLogger(f"tml.{flag_on}.{mint_after_rung1}.{href_raises}")
    sm.logger.handlers[:] = [cap]
    sm.logger.setLevel(logging.INFO)
    sm.logger.propagate = False
    saves = []

    async def _status(tab=None):
        if mint_after_rung1:
            return {'present': True, 'member': True, 'eid': 'x', 'ttl_s': 14000.0, 'iat': None}
        return {'present': False, 'member': False, 'eid': None, 'ttl_s': -1.0, 'iat': None}

    async def _save():
        saves.append(1)

    sm.get_access_token_status = _status
    sm.save_session_state = _save
    tab = _Tab(href_raises=href_raises)
    old_time, old_env = smm.time, os.environ.get("TARGET_TOKEN_MINT_LOG")
    smm.time = _FastTime()
    os.environ.pop("TARGET_TOKEN_REFRESH_URL", None)
    if flag_on:
        os.environ["TARGET_TOKEN_MINT_LOG"] = "1"
    else:
        os.environ.pop("TARGET_TOKEN_MINT_LOG", None)
    try:
        ok = asyncio.run(sm.refresh_access_token(tab))
    finally:
        smm.time = old_time
        if old_env is None:
            os.environ.pop("TARGET_TOKEN_MINT_LOG", None)
        else:
            os.environ["TARGET_TOKEN_MINT_LOG"] = old_env
    return ok, tab, cap.lines, saves


def test_flag_off_is_the_old_script():
    ok, tab, lines, saves = _run(False)
    check("off_returns_false", ok is False, ok)
    check("off_rung1_script_byte_identical", tab.evals and tab.evals[0] == QUIET_JS, repr(tab.evals[:1])[:400])
    check("off_no_mint_lines", not any("mint rung" in l for l in lines), lines)
    check("off_could_not_mint_still_logged", any("could NOT mint a member token" in l for l in lines), lines)
    check("off_no_href_read", 'location.href' not in tab.evals, tab.evals)


def test_flag_on_failed_mint_logs_both_rungs():
    ok, tab, lines, saves = _run(True)
    r1 = [l for l in lines if "mint rung1 token_refresh ->" in l]
    r2 = [l for l in lines if "mint rung2 after /account reload" in l]
    check("on_returns_false", ok is False, ok)
    check("on_rung1_status_logged", len(r1) == 1 and "status=401 type=basic redirected=false" in r1[0]
          and "[TOKEN] primary:" in r1[0], r1)
    check("on_rung2_url_and_class_logged", len(r2) == 1
          and "final_url=https://www.target.com/login?client_id=ecom-web-1.0.0" in r2[0]
          and "accessToken=none" in r2[0] and "ttl=-1s" in r2[0], r2)
    check("on_could_not_mint_still_logged", any("could NOT mint a member token" in l for l in lines), lines)


def test_on_vs_off_same_requests():
    ok0, t0, _, s0 = _run(False)
    ok1, t1, _, s1 = _run(True)
    check("same_return", ok0 == ok1, (ok0, ok1))
    check("same_cookie_deletes", t0.sends == t1.sends and len(t0.sends) == 3, (t0.sends, t1.sends))
    check("same_account_load", t0.gets == t1.gets == ['https://www.target.com/account'], (t0.gets, t1.gets))
    check("same_saves", s0 == s1, (s0, s1))
    extra = [j for j in t1.evals if j not in t0.evals]
    check("only_extra_call_is_status_script_and_href", sorted(set(extra)) == sorted(
        {j for j in t1.evals if 'r.status' in j} | {'location.href'}), extra)


def test_flag_on_rung1_mints():
    ok, tab, lines, saves = _run(True, mint_after_rung1=True)
    check("r1ok_returns_true", ok is True, ok)
    check("r1ok_rung1_line", any("mint rung1 token_refresh -> status=401" in l for l in lines), lines)
    check("r1ok_no_delete_no_reload", tab.sends == [] and tab.gets == [], (tab.sends, tab.gets))
    check("r1ok_no_rung2_line", not any("mint rung2" in l for l in lines), lines)


def test_href_read_raising():
    ok, tab, lines, saves = _run(True, href_raises=True)
    r2 = [l for l in lines if "mint rung2 after /account reload" in l]
    check("href_raise_still_logs", len(r2) == 1 and "final_url=?(RuntimeError)" in r2[0], r2)
    check("href_raise_returns_false", ok is False, ok)


def test_gate_and_wrapper():
    src = (ROOT / "src" / "session" / "session_manager.py").read_text(encoding="utf-8")
    check("gate_exact", "_mint_log = os.environ.get('TARGET_TOKEN_MINT_LOG', '0').strip() == '1'" in src)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("ascii", "replace").split("\r\n")
    check("bat_armed_once", bat.count("set TARGET_TOKEN_MINT_LOG=1") == 1,
          [l for l in bat if "TARGET_TOKEN_MINT_LOG" in l])


if __name__ == "__main__":
    test_flag_off_is_the_old_script()
    test_flag_on_failed_mint_logs_both_rungs()
    test_on_vs_off_same_requests()
    test_flag_on_rung1_mints()
    test_href_read_raising()
    test_gate_and_wrapper()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
