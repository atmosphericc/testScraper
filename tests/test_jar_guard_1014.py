#!/usr/bin/env python3
"""Offline tests: FX-1014-JARGUARD -- never overwrite a MEMBER session jar with a GUEST one (2026-10-14).

On 10-14 relogin_one.py's validate-first pass and the in-bot sentinel's save_session_state wrote
guest cookies (idToken sut=G) over the account jars without reading sut (C-1014-02 / C-1014-04).
TARGET_JAR_GUEST_GUARD=1 keeps a member jar (sut R / M) when the new cookies are a guest's.

Pins:
  - flag off: src.jar_guard.inspect is (False, None) for every input; both savers overwrite as before
  - flag on: member jar + guest cookies -> blocked, the file is byte-identical afterwards
  - flag on: guest jar + guest cookies, missing jar, junk jar -> written, with a GUEST message
  - flag on: member cookies over anything -> written, no message
  - accessToken is the fallback when idToken is absent; non-target.com cookies are ignored
  - harvest_accounts._write_session and SessionManager.save_session_state both honour it

No browser, no network. Run: venv/Scripts/python.exe tests/test_jar_guard_1014.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import jar_guard  # noqa: E402
import harvest_accounts  # noqa: E402
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


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def _cookies(sut, name="idToken", domain=".target.com"):
    return [
        {"name": name, "value": _jwt({"sut": sut, "exp": 2000000000}), "domain": domain, "path": "/"},
        {"name": "login-session", "value": "x", "domain": ".target.com", "path": "/"},
    ]


def _write_jar(path, sut):
    path.write_text(json.dumps({"cookies": _cookies(sut) if sut else []}), encoding="utf-8")


def _set_flag(on):
    if on:
        os.environ["TARGET_JAR_GUEST_GUARD"] = "1"
    else:
        os.environ.pop("TARGET_JAR_GUEST_GUARD", None)


IDENT = {"account_id": "t", "user_agent": "ua", "viewport": None, "timezone": None, "locale": None}


def test_inspect(tmp):
    print("inspect()")
    member, guest, junk = tmp / "m.json", tmp / "g.json", tmp / "j.json"
    _write_jar(member, "R")
    _write_jar(guest, "G")
    junk.write_text("{not json", encoding="utf-8")
    missing = tmp / "missing.json"

    _set_flag(False)
    for p in (member, guest, junk, missing):
        check(f"flag off -> (False, None) for {p.name}", jar_guard.inspect(p, _cookies("G")) == (False, None))

    _set_flag(True)
    b, why = jar_guard.inspect(member, _cookies("G"))
    check("member jar + guest cookies -> blocked", b is True and "kept m.json" in (why or ""), why)
    m2 = tmp / "m2.json"
    m2.write_text(json.dumps({"cookies": _cookies("M")}), encoding="utf-8")
    check("sut=M jar is protected too", jar_guard.inspect(m2, _cookies("G"))[0] is True)
    for p in (guest, junk, missing):
        b, why = jar_guard.inspect(p, _cookies("G"))
        check(f"{p.name} + guest cookies -> written with a GUEST message", b is False and "GUEST" in (why or ""), why)
    for p in (member, guest, missing):
        check(f"member cookies over {p.name} -> (False, None)", jar_guard.inspect(p, _cookies("R")) == (False, None))
    check("accessToken fallback", jar_guard.sut_of(_cookies("G", name="accessToken")) == "G")
    check("non-target.com idToken ignored", jar_guard.sut_of(_cookies("G", domain=".example.com")) is None)
    check("no auth cookie -> (False, None)", jar_guard.inspect(member, [{"name": "x", "value": "y", "domain": ".target.com"}]) == (False, None))
    check("garbage cookies arg never raises", jar_guard.inspect(member, "garbage") == (False, None))


def test_harvest_write(tmp):
    print("harvest_accounts._write_session")
    jar = tmp / "target.json"
    _write_jar(jar, "R")
    before = jar.read_bytes()

    _set_flag(True)
    harvest_accounts._write_session(jar, _cookies("G"), IDENT)
    check("flag on: guest over member -> file unchanged", jar.read_bytes() == before)
    harvest_accounts._write_session(jar, _cookies("R"), IDENT)
    check("flag on: member over member -> written", jar.read_bytes() != before
          and jar_guard.jar_sut(jar) == "R" and "saved_at" in json.loads(jar.read_text(encoding="utf-8")))

    _write_jar(jar, "R")
    _set_flag(False)
    harvest_accounts._write_session(jar, _cookies("G"), IDENT)
    check("flag off: guest over member -> overwritten (previous behaviour)", jar_guard.jar_sut(jar) == "G")


class _Tab:
    url = "about:blank"

    def __init__(self, cookies):
        self._c = [SimpleNamespace(name=c["name"], value=c["value"], domain=c["domain"], path=c["path"],
                                   expires=2000000000.0, http_only=True, secure=True, same_site=None,
                                   session=False) for c in cookies]

    async def send(self, _cmd):
        return self._c


def test_session_manager_save(tmp):
    print("SessionManager.save_session_state")
    jar = tmp / "target-2.json"

    async def run(sut_live):
        sm = SessionManager(session_path=str(jar), account_id="business")
        sm._active_tab = _Tab(_cookies(sut_live))
        return await sm.save_session_state()

    _write_jar(jar, "R")
    before = jar.read_bytes()
    _set_flag(True)
    r = asyncio.run(run("G"))
    check("flag on: guest live session over member jar -> not saved, returns False",
          r is False and jar.read_bytes() == before, r)
    r = asyncio.run(run("R"))
    check("flag on: member live session -> saved", r is True and jar.read_bytes() != before, r)

    _write_jar(jar, "G")
    r = asyncio.run(run("G"))
    check("flag on: guest over guest -> saved (nothing to protect)", r is True and jar_guard.jar_sut(jar) == "G", r)

    _write_jar(jar, "R")
    _set_flag(False)
    r = asyncio.run(run("G"))
    check("flag off: guest over member -> saved (previous behaviour)", r is True and jar_guard.jar_sut(jar) == "G", r)


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_inspect(tmp)
        test_harvest_write(tmp)
        test_session_manager_save(tmp)
    _set_flag(False)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
