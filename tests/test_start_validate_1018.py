#!/usr/bin/env python3
"""Offline test: relogin_one._on_account_page_loggedin reason log + flag-gated retry (FX-1018-STARTVALIDATE).

An exception during the /account check used to return False like a login redirect, so the
start pass signed out a healthy account; nothing logged which case happened. Pins:
  - retries=0 (default): an exception -> False + "validation error" line, no retry
  - retries=1: an exception then /account -> True + "validation retry 1/1" line
  - retries exhausted -> False
  - a login URL -> False at once, never retried, no sleep beyond the 3 s page wait
  - non-int env -> 0; a failing log() never changes the result; no query string logged

No browser, no network: importing relogin_one only pulls helper modules (checked at import);
asyncio.sleep is replaced so the test takes no wall time. Run: python tests/test_start_validate_1018.py
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import relogin_one as R  # noqa: E402  (import-time side effects: none, only helper imports)

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if (detail and not cond) else ""), flush=True)


class FakeTab:
    def __init__(self, plan):
        self.plan = list(plan)  # each: Exception instance, or a url string
        self.url = ""
        self.gets = 0

    async def get(self, u):
        self.gets += 1
        step = self.plan.pop(0)
        if isinstance(step, Exception):
            raise step
        self.url = step


LOGS, SLEEPS = [], []


async def fake_sleep(s):
    SLEEPS.append(s)


R.asyncio.sleep = fake_sleep  # module-level asyncio object is shared; restored in run()
_real_log = R.log


def fake_log(step, msg):
    LOGS.append(f"[{step}] {msg}")


R.log = fake_log


def run(plan, retries, log_raises=False):
    LOGS.clear()
    SLEEPS.clear()
    if retries is None:
        os.environ.pop("TARGET_START_VALIDATE_RETRIES", None)
    else:
        os.environ["TARGET_START_VALIDATE_RETRIES"] = str(retries)
    if log_raises:
        def boom(step, msg):
            raise OSError("disk full")
        R.log = boom
    try:
        tab = FakeTab(plan)
        real = asyncio.sleep
        try:
            asyncio.sleep = fake_sleep
            res = asyncio.run(R._on_account_page_loggedin(tab))
        finally:
            asyncio.sleep = real
        return res, tab
    finally:
        R.log = fake_log


OK = "https://www.target.com/account"
LOGIN = "https://www.target.com/login?client_id=abc&secret=zzz"

# default (env unset) and explicit 0: one attempt, exception -> False with the reason line
for env in (None, "0"):
    res, tab = run([TimeoutError("nav timeout")], env)
    check(f"default_{env}_exc_false", res is False and tab.gets == 1)
    check(f"default_{env}_exc_logged", any("validation error TimeoutError" in l and "treated as not logged in" in l for l in LOGS), str(LOGS))
    check(f"default_{env}_no_retry_line", not any("retry" in l for l in LOGS))

# retries=1: raise once, then lands on /account
res, tab = run([ConnectionError("ws closed"), OK], 1)
check("retry1_true", res is True and tab.gets == 2, f"{res} {tab.gets}")
check("retry1_logged", any("validation retry 1/1 after ConnectionError" in l for l in LOGS), str(LOGS))
check("retry1_slept_2s", 2 in SLEEPS, str(SLEEPS))

# retries=2, all raise -> False after 3 attempts
res, tab = run([OSError("a"), OSError("b"), OSError("c")], 2)
check("exhausted_false", res is False and tab.gets == 3, f"{res} {tab.gets}")
check("exhausted_retry_lines", any("retry 1/2" in l for l in LOGS) and any("retry 2/2" in l for l in LOGS), str(LOGS))

# retries=1, two exceptions queued -> only one retry
res, tab = run([OSError("a"), OSError("b")], 1)
check("retry_cap", res is False and tab.gets == 2)

# login redirect: False at once, no retry even with retries=3, no 2 s pause
res, tab = run([LOGIN, OK], 3)
check("login_false_no_retry", res is False and tab.gets == 1 and 2 not in SLEEPS, f"{res} {tab.gets} {SLEEPS}")
check("login_logged_reason", any("validation: not logged in" in l for l in LOGS), str(LOGS))
check("login_no_query_string_logged", not any("secret" in l or "client_id" in l or "?" in l.split("path=")[-1] for l in LOGS), str(LOGS))
check("login_not_error_line", not any("validation error" in l for l in LOGS))

# non-account url without login word -> False (url check), still no retry
res, tab = run(["https://www.target.com/"], 3)
check("home_false_no_retry", res is False and tab.gets == 1)

# plain success logs nothing
res, tab = run([OK], 2)
check("success_true_quiet", res is True and not LOGS and tab.gets == 1)

# garbage env -> 0
for env in ("abc", "", "-3", "1.5"):
    res, tab = run([TimeoutError("x"), OK], env)
    check(f"garbage_env_{env!r}_is_zero", res is False and tab.gets == 1, f"{res} {tab.gets}")

# a failing logger never changes the result
res, tab = run([TimeoutError("x")], 0, log_raises=True)
check("log_fail_exc_still_false", res is False)
res, tab = run([TimeoutError("x"), OK], 1, log_raises=True)
check("log_fail_retry_still_true", res is True)
res, tab = run([LOGIN], 0, log_raises=True)
check("log_fail_login_still_false", res is False)

# repr truncated to 160 chars
res, tab = run([RuntimeError("Z" * 500)], 0)
line = next((l for l in LOGS if "validation error" in l), "")
check("repr_truncated", 0 < line.count("Z") <= 160, str(len(line)))

print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
sys.exit(1 if FAILED else 0)
