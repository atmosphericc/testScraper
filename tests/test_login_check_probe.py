#!/usr/bin/env python3
"""No-browser unit for app._probe_target_login (2026-08-25 login-gate fix).

The boot login gate false-negatived because sm._active_tab is often a warmup
/cart tab (no 'Hi,' greeting) and /cart IS target.com, so the old guard skipped
the homepage nav and the 4s find() timed out -> exit 87 -> crash-loop that
degraded tokens to GUEST. The fix ALWAYS navigates to the homepage first and
uses a longer, tunable timeout. This pins that behaviour with a fake tab.

Run: venv/Scripts/python.exe tests/test_login_check_probe.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import _probe_target_login  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} -- {detail}")


class FakeTab:
    """Records .get() targets; .find() returns/raises per the script."""
    def __init__(self, find_result=None, find_exc=None, get_exc=None):
        self.gets = []
        self._find_result = find_result
        self._find_exc = find_exc
        self._get_exc = get_exc
        self.find_calls = []

    async def get(self, url):
        self.gets.append(url)
        if self._get_exc is not None:
            raise self._get_exc

    async def find(self, text, best_match=False, timeout=0):
        self.find_calls.append({"text": text, "timeout": timeout})
        if self._find_exc is not None:
            raise self._find_exc
        return self._find_result


# 1. Logged in: find returns an element -> True, and the homepage was navigated
#    to FIRST (the core of the fix), regardless of where the tab started.
t = FakeTab(find_result=object())
ok = asyncio.run(_probe_target_login(t, timeout_s=10.0))
check("logged_in_returns_true", ok is True, str(ok))
check("navigates_homepage_first",
      t.gets == ["https://www.target.com"], str(t.gets))
check("probes_after_nav", t.find_calls and t.find_calls[0]["text"] == "Hi,", str(t.find_calls))
check("passes_timeout_through", t.find_calls and t.find_calls[0]["timeout"] == 10.0,
      str(t.find_calls))

# 2. Genuinely logged out: nodriver find() raises TimeoutError on no-match ->
#    False (the logout protection must survive the fix).
t = FakeTab(find_exc=asyncio.TimeoutError())
ok = asyncio.run(_probe_target_login(t, timeout_s=3.0))
check("logged_out_timeout_returns_false", ok is False, str(ok))
check("logged_out_still_navigated", t.gets == ["https://www.target.com"], str(t.gets))

# 3. find() returns a falsy element (no raise) -> False.
t = FakeTab(find_result=None)
check("falsy_find_returns_false", asyncio.run(_probe_target_login(t)) is False)

# 4. Homepage nav fails -> still probes the current page (nav errors are
#    non-fatal), and a present greeting still yields True.
t = FakeTab(find_result=object(), get_exc=RuntimeError("nav boom"))
ok = asyncio.run(_probe_target_login(t))
check("nav_failure_nonfatal_still_true", ok is True, str(ok))
check("nav_failure_still_attempted_get", t.gets == ["https://www.target.com"], str(t.gets))

# 5. A /cart-style start still gets navigated to the homepage (the exact
#    2026-08-25 regression): the helper does not trust the current URL.
t = FakeTab(find_result=object())
asyncio.run(_probe_target_login(t))
check("cart_tab_gets_renavigated", t.gets == ["https://www.target.com"], str(t.gets))

# 6. Default timeout is 10s when not overridden.
t = FakeTab(find_result=object())
asyncio.run(_probe_target_login(t))
check("default_timeout_10s", t.find_calls[0]["timeout"] == 10.0, str(t.find_calls))

print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
sys.exit(1 if FAIL else 0)
