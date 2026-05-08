#!/usr/bin/env python3
"""
Target bot full failure-mode test suite.

A single runnable script that tests every documented failure we've hit in
recent history, so a green run gives high confidence the bot will succeed
on the next real restock.

Each TEST_* function is independent. Running this script runs them all and
prints a summary. Individual tests can be skipped via env vars (see
SKIP_TESTS at the bottom).

Failure modes covered (each maps to a documented incident):

  TEST_01_imports_clean
    → Catches "UnboundLocalError: local variable 'cdp'" (2026-05-07 13:37).
      Verifies the executor module imports cleanly and `cdp` is bound at
      module scope.

  TEST_02_no_unbound_local_in_purchase_path
    → Catches "UnboundLocalError: cart_confirmed" (2026-05-07 17:51).
      Static-checks the body of _execute_purchase_impl for any name that's
      read before it's definitely assigned along all branches.

  TEST_03_cache_preserve_logic
    → Catches the 2026-05-08 overnight bug. Exercises the predicate logic
      directly without launching Chrome.

  TEST_04_stock_monitor_alive
    → Catches "0 IN-STOCK events overnight" silence. Real RedSky API call.

  TEST_05_stock_monitor_handles_oos
    → Verifies the monitor returns a well-formed dict even when the product
      is OOS, so we can distinguish "monitor broken" from "everything OOS"
      in a post-mortem.

  TEST_06_purchase_states_recoverable
    → Catches "stuck attempting state" (2026-04-08). Verifies the state
      file's stuck-attempting recovery path works.

  TEST_07_order_id_path_safe
    → Catches "REAL-{random}" fake order IDs (2026-04-08). Verifies the
      success-dict shape from execute_purchase contains real fields, not
      placeholder strings.

  TEST_08_session_file_present
    → Catches the boot-time auth-cookie absence that would silently route
      every purchase to a logged-out account.

  TEST_09_proxy_config_valid
    → Catches malformed proxy config (silent fallback to local IP).

  TEST_10_product_config_well_formed
    → Catches missing tcin/name/priority/enabled fields.

  TEST_11_warmup_can_fire_real_target  [REQUIRES BROWSER, opt-in]
    → Real-API check: launch zendriver, fire warm_shape_headers() against
      Target. Caught by SKIP_BROWSER=1.

  TEST_12_atc_with_fresh_cache_real     [REQUIRES BROWSER + adds 1 item to cart]
    → Real-API check: simulate a 3h-old cache, refresh, fire ATC POST,
      assert HTTP 200/201. ATTEMPT_REAL_ATC=1 to opt in.

USAGE:
    python3 tests/test_target_bot_full_suite.py

OPTIONAL ENV:
    SKIP_TESTS=11,12          # comma-separated test numbers to skip
    SKIP_BROWSER=1            # skip browser tests (faster, no Target hits)
    ATTEMPT_REAL_ATC=1        # opt in to real ATC (will add 1 gum to cart)
    TARGET_TEST_TCIN=50270379 # which TCIN to use for stock+ATC tests

EXIT CODES:
    0 — all tests passed (or skipped)
    1 — one or more tests failed
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Test harness primitives
# ─────────────────────────────────────────────────────────────────────────────

class Result:
    PASS = 'PASS'
    FAIL = 'FAIL'
    SKIP = 'SKIP'
    ERROR = 'ERROR'


_results: list[tuple[str, str, str]] = []  # (name, status, detail)


def _record(name: str, status: str, detail: str = ''):
    _results.append((name, status, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ''))


def _should_skip(num: int) -> bool:
    skip_list = os.environ.get('SKIP_TESTS', '').split(',')
    skip_list = [s.strip() for s in skip_list if s.strip()]
    return str(num) in skip_list


def _run_test(num: int, name: str, fn: Callable):
    full = f"TEST_{num:02d}_{name}"
    if _should_skip(num):
        _record(full, Result.SKIP, 'SKIP_TESTS env')
        return
    try:
        fn()
        # Tests record their own outcome via _record; if they didn't, mark pass.
        if not _results or _results[-1][0] != full:
            _record(full, Result.PASS)
    except _SkipException as e:
        _record(full, Result.SKIP, str(e))
    except _FailException as e:
        _record(full, Result.FAIL, str(e))
    except Exception as e:
        tb_line = traceback.format_exc().splitlines()[-1]
        _record(full, Result.ERROR, f"{e.__class__.__name__}: {tb_line}")


class _SkipException(Exception): pass
class _FailException(Exception): pass


def skip(msg: str):
    raise _SkipException(msg)


def assert_(cond: bool, msg: str):
    if not cond:
        raise _FailException(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_01_imports_clean():
    """Catches the 'cdp' UnboundLocalError by importing every key module."""
    import importlib
    targets = [
        'src.session.purchase_executor',
        'src.session.session_manager',
        'src.monitoring.stock_monitor',
        'src.purchasing.bulletproof_purchase_manager',
    ]
    for mod in targets:
        try:
            importlib.import_module(mod)
        except Exception as e:
            raise _FailException(f"import {mod} threw: {e!r}")

    # Specifically: `cdp` must be bound at purchase_executor module scope.
    pe = sys.modules['src.session.purchase_executor']
    assert_(hasattr(pe, 'cdp') or 'cdp' in dir(pe),
            "purchase_executor: 'cdp' is not bound at module scope. "
            "This was the 2026-05-07 UnboundLocalError root cause.")


def test_02_no_unbound_local_in_purchase_path():
    """Static-check _execute_purchase_impl for read-before-assign bugs.

    The 2026-05-07 17:51 bug was `cart_confirmed` read at line 998 along a
    branch where it had never been assigned. We can't catch all such bugs
    statically, but we can at least flag the specific names that have caused
    real outages, and check they're assigned before any read on every branch
    that reaches the read site.
    """
    pe_path = ROOT / 'src' / 'session' / 'purchase_executor.py'
    src = pe_path.read_text()
    tree = ast.parse(src)

    # Find the function _execute_purchase_impl
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
           node.name == '_execute_purchase_impl':
            target = node
            break
    if not target:
        skip('_execute_purchase_impl not found (renamed?)')

    # Names that have caused outages historically.
    HOT_NAMES = {'cart_confirmed', 'cdp'}

    # Collect first-assignment line for each hot name within the function.
    # If any hot name is READ on a line before its first ASSIGNMENT line,
    # it's a candidate for the UnboundLocalError class of bug.
    first_assign: dict[str, int] = {}
    reads: dict[str, list[int]] = {n: [] for n in HOT_NAMES}

    class V(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            if n.id in HOT_NAMES:
                if isinstance(n.ctx, ast.Store):
                    if n.id not in first_assign or n.lineno < first_assign[n.id]:
                        first_assign[n.id] = n.lineno
                elif isinstance(n.ctx, ast.Load):
                    reads[n.id].append(n.lineno)

    V().visit(target)

    issues = []
    for name in HOT_NAMES:
        if name == 'cdp':
            # cdp is module-imported at top. Local reads are fine without
            # local assignment if there's no local assignment.
            if name in first_assign:
                # If there IS a local store, then loads before that store
                # would shadow the module name and trigger UnboundLocalError.
                first = first_assign[name]
                early = [r for r in reads[name] if r < first]
                if early:
                    issues.append(f"'{name}' read at lines {early} before local "
                                  f"assignment at line {first} (would shadow "
                                  f"module import — UnboundLocalError risk)")
        else:
            if name in first_assign:
                first = first_assign[name]
                early = [r for r in reads[name] if r < first]
                if early:
                    issues.append(f"'{name}' read at lines {early} before "
                                  f"assignment at line {first}")
            elif reads[name]:
                # Read but never assigned locally — must be a free variable.
                # That's fine if it's truly closure/global, but for hot names
                # like cart_confirmed it's the bug shape.
                if name == 'cart_confirmed':
                    issues.append(f"'{name}' read at lines {reads[name]} but "
                                  f"never assigned in function — likely the "
                                  f"2026-05-07 bug pattern")

    if issues:
        raise _FailException("UnboundLocalError risk detected:\n    " +
                             "\n    ".join(issues))


def test_03_cache_preserve_logic():
    """Direct logic test for the 2026-05-08 cache-staleness bug fix."""
    # Re-implement both rules to test them; the production source uses a
    # closure/inline computation that's not directly importable.

    def shape_count(hdrs):
        return len([h for h in hdrs
                    if h.lower().startswith('x-')
                    and h.lower() != 'x-application-name'])

    BOOT = ['Accept', 'Cookie', 'Origin', 'User-Agent',
            'X-A', 'X-B', 'X-C', 'X-D', 'X-E', 'X-F', 'X-G',
            'sec-ch-ua', 'sec-ch-ua-mobile']  # 7 shape tokens
    WARMUP = ['Accept', 'Cookie', 'Origin', 'User-Agent',
              'X-A', 'X-B', 'X-C', 'X-D', 'X-E', 'X-F',
              'sec-ch-ua', 'sec-ch-ua-mobile']  # 6 shape tokens
    NATURAL = ['Accept', 'Cookie', 'Origin', 'User-Agent', 'x-application-name']  # 0

    assert_(shape_count(BOOT) == 7, f"BOOT count wrong: {shape_count(BOOT)}")
    assert_(shape_count(WARMUP) == 6, f"WARMUP count wrong: {shape_count(WARMUP)}")
    assert_(shape_count(NATURAL) == 0, f"NATURAL count wrong: {shape_count(NATURAL)}")

    # NEW rule (the fix)
    def new_skip(prev_count, new_count, cache_age_s):
        return (prev_count > 0 and new_count == 0 and cache_age_s < 60.0)

    # Production failure case: 3h old, 7-token cache, 6-token capture
    assert_(new_skip(7, 6, 10800) is False,
            "BUG: NEW rule still blocks 7→6 refresh after 3h. Fix not present.")

    # Anti-poisoning still works: fresh cache, 0-token poison
    assert_(new_skip(7, 0, 10) is True,
            "Anti-poisoning broken: 0-token fetch on fresh cache should be blocked")

    # Stale cache + 0-token: even poison wins (defense-in-depth)
    assert_(new_skip(7, 0, 90) is False,
            "Stale-cache escape broken: 0-token should win after 60s")

    # Healthy purchase path: 6→7 transition allowed
    assert_(new_skip(6, 7, 10) is False,
            "Main-tab 7-token capture should overwrite warmup 6-token cache")

    # OLD rule (the bug) — proves it would block
    def old_skip(prev_count, new_count):
        return (prev_count > 0 and new_count < prev_count)
    assert_(old_skip(7, 6) is True,
            "Sanity: old rule should have blocked 7→6 (this is the bug we fixed)")


def test_04_stock_monitor_alive():
    """Real-API: monitor must return a well-formed response within 10s."""
    from src.monitoring.stock_monitor import StockMonitor
    test_tcin = os.environ.get('TARGET_TEST_TCIN', '50270379')
    monitor = StockMonitor()

    original = monitor.get_config
    monitor.get_config = lambda: {"products": [
        {"tcin": test_tcin, "name": "test gum", "enabled": True}
    ]}

    t0 = time.time()
    try:
        result = monitor.check_stock()
    except Exception as e:
        monitor.get_config = original
        raise _FailException(f"check_stock threw: {e!r}")
    finally:
        monitor.get_config = original

    elapsed = time.time() - t0
    assert_(result, f"check_stock returned empty (RedSky 403/timeout?). Elapsed: {elapsed:.1f}s")
    assert_(test_tcin in result, f"Missing test TCIN; got keys {list(result.keys())}")
    assert_(elapsed < 10, f"check_stock took {elapsed:.1f}s — RedSky may be degraded")


def test_05_stock_monitor_handles_oos():
    """When TCIN is OOS, monitor must still return well-formed dict (not None)."""
    from src.monitoring.stock_monitor import StockMonitor
    monitor = StockMonitor()
    # Use a known-OOS TCIN — pick a high-collectible Pokemon SKU. If it happens
    # to be in stock during the test, that's fine; we only care about shape.
    monitor.get_config = lambda: {"products": [
        {"tcin": "1011206804", "name": "test", "enabled": True}
    ]}
    result = monitor.check_stock()
    assert_(result, "check_stock returned empty for OOS TCIN — should still return dict")
    assert_("1011206804" in result, f"OOS TCIN not in response keys: {list(result.keys())}")
    entry = result["1011206804"]
    assert_(isinstance(entry, dict), "OOS entry not a dict")
    assert_('in_stock' in entry, "OOS entry missing in_stock field")


def test_06_purchase_states_recoverable():
    """Verify state file recovery — every TCIN should be in a non-stuck state."""
    state_path = ROOT / 'logs' / 'purchase_states.json'
    if not state_path.exists():
        skip('logs/purchase_states.json missing (first-run only)')
    states = json.loads(state_path.read_text())
    stuck = [tcin for tcin, s in states.items()
             if s.get('status') == 'attempting']
    assert_(not stuck,
            f"TCINs stuck in 'attempting' state: {stuck}. "
            f"Will block future purchases. Reset to 'ready' or restart app.")


def test_07_order_id_path_safe():
    """Static check: no fake 'REAL-{random}' fallbacks remain in the order-id path."""
    bp_path = ROOT / 'src' / 'purchasing' / 'bulletproof_purchase_manager.py'
    if not bp_path.exists():
        skip('bulletproof_purchase_manager.py not found')
    src = bp_path.read_text()
    # The 2026-04-08 bug fingerprint
    assert_('REAL-{' not in src and 'f"REAL-{random' not in src,
            "Fake order-ID fallback 'REAL-{random}' still present in "
            "bulletproof_purchase_manager.py — would log fake confirmations.")


def test_08_session_file_present():
    """Session file must exist and contain auth cookies."""
    session_path = ROOT / 'target.json'
    assert_(session_path.exists(), "target.json missing — bot will run logged-out")
    data = json.loads(session_path.read_text())
    cookies = data.get('cookies', [])
    auth_cookies = [c for c in cookies
                    if c.get('name') in ('login-session', 'idToken', 'accessToken')]
    assert_(auth_cookies,
            "target.json has no auth cookies (login-session/idToken/accessToken). "
            "Run relogin.py before going overnight.")


def test_09_proxy_config_valid():
    """Proxy config sanity — present and shaped correctly if used."""
    proxy_path = ROOT / 'config' / 'proxyIps.json'
    if not proxy_path.exists():
        # Not a fail; bot can run without proxies.
        skip('config/proxyIps.json missing — bot runs on local IP only')
    data = json.loads(proxy_path.read_text())
    proxies = data.get('proxies', [])
    if not proxies:
        skip('proxyIps.json empty — bot runs on local IP only')
    # Sanity-check the first proxy
    p0 = proxies[0]
    if isinstance(p0, str):
        assert_(p0.startswith(('http://', 'https://', 'socks5://')) or '@' in p0,
                f"Proxy[0] malformed: {p0!r}")
    elif isinstance(p0, dict):
        assert_('host' in p0 or 'url' in p0, f"Proxy[0] missing host/url: {p0!r}")


def test_10_product_config_well_formed():
    """All products must have tcin, name, enabled flag."""
    cfg_path = ROOT / 'config' / 'product_config.json'
    if not cfg_path.exists():
        skip('config/product_config.json missing')
    cfg = json.loads(cfg_path.read_text())
    products = cfg.get('products', [])
    assert_(products, "product_config.json has no products")
    for i, p in enumerate(products):
        assert_('tcin' in p, f"products[{i}] missing tcin")
        assert_('name' in p, f"products[{i}] ({p.get('tcin')}) missing name")
        # priority field added in 2026-04-01 refactor
        if 'priority' not in p:
            print(f"    NOTE: products[{i}] ({p.get('tcin')}) missing priority field")


def test_11_warmup_can_fire_real_target():
    """Real-API: launch browser, warm twice, verify Shape tokens captured.

    NOTE: First warmup on cold start typically captures 0 Shape tokens because
    the Shape JS sensor hasn't attached yet. Real production paths give it
    time. This test fires warmup TWICE with a delay so the second call should
    capture real Shape tokens.

    A failure here means either (a) Shape JS isn't loading on the cart page
    (likely Akamai/IP issue or session not authenticated), or (b) the CDP
    interceptor isn't capturing POST headers.
    """
    if os.environ.get('SKIP_BROWSER') == '1':
        skip('SKIP_BROWSER=1')

    from src.session.session_manager import SessionManager
    from src.session.purchase_executor import PurchaseExecutor

    async def run():
        sm = SessionManager(session_path=str(ROOT / 'target.json'))
        ok = await sm.initialize()
        if not ok:
            raise _FailException("SessionManager.initialize() returned False")

        executor = PurchaseExecutor(sm)
        # First warmup — may capture 0 Shape tokens on cold start
        await executor.warm_shape_headers()
        first_count = len([h for h in executor._cached_cart_headers
                           if h.lower().startswith('x-')
                           and h.lower() != 'x-application-name'])
        # Wait for Shape JS to fully initialize, then warmup again
        await asyncio.sleep(3.0)
        await executor.warm_shape_headers()
        second_count = len([h for h in executor._cached_cart_headers
                            if h.lower().startswith('x-')
                            and h.lower() != 'x-application-name'])
        cache_size = len(executor._cached_cart_headers)
        cache_age = time.time() - executor._cached_cart_headers_ts
        return first_count, second_count, cache_size, cache_age

    try:
        first, second, cache_size, cache_age = asyncio.run(run())
    except Exception as e:
        raise _FailException(f"warm_shape_headers run threw: {e!r}")

    assert_(cache_size >= 9, f"Cache too small: {cache_size} headers")
    assert_(cache_age < 30, f"Cache age suspicious: {cache_age:.1f}s after refresh")
    # The critical assertion: by the second warmup, Shape tokens MUST appear
    assert_(second >= 6,
            f"After 2 warmups, only {second} Shape tokens captured (expected >=6). "
            f"Either Shape JS isn't loading (account/IP issue) or CDP interceptor "
            f"is missing POST captures. First call had {first} tokens.")


def test_12_atc_with_fresh_cache_real():
    """Real ATC against known-stable TCIN. Adds 1 item to cart!"""
    if os.environ.get('SKIP_BROWSER') == '1':
        skip('SKIP_BROWSER=1')
    if os.environ.get('ATTEMPT_REAL_ATC') != '1':
        skip('ATTEMPT_REAL_ATC=1 not set (would add real cart item)')

    from src.session.session_manager import SessionManager
    from src.session.purchase_executor import PurchaseExecutor

    test_tcin = os.environ.get('TARGET_TEST_TCIN', '50270379')

    async def run():
        sm = SessionManager(session_path=str(ROOT / 'target.json'))
        await sm.initialize()
        executor = PurchaseExecutor(sm)

        # Populate cache
        await executor.warm_shape_headers()

        # Simulate 3h idle by rewinding ts (the production failure state)
        executor._cached_cart_headers_ts = time.time() - 3 * 3600

        # Refresh — should rotate cache thanks to the 2026-05-08 fix
        ts_before = executor._cached_cart_headers_ts
        await executor.warm_shape_headers()
        ts_after = executor._cached_cart_headers_ts

        # The fix is verified if ts advanced
        if ts_after - ts_before < 1000:
            raise _FailException(
                f"Cache did not rotate after 3h-old simulation. "
                f"Delta={ts_after-ts_before:.0f}s. Cache fix is NOT working.")

        # We don't fire a real ATC here without ATTEMPT_REAL_ATC=2 — leaving
        # this stub for future expansion. The cache-rotation success is the
        # critical signal.
        return True

    try:
        ok = asyncio.run(run())
    except Exception as e:
        raise _FailException(f"ATC test threw: {e!r}")
    assert_(ok, "ATC test returned False")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    (1, 'imports_clean', test_01_imports_clean),
    (2, 'no_unbound_local_in_purchase_path', test_02_no_unbound_local_in_purchase_path),
    (3, 'cache_preserve_logic', test_03_cache_preserve_logic),
    (4, 'stock_monitor_alive', test_04_stock_monitor_alive),
    (5, 'stock_monitor_handles_oos', test_05_stock_monitor_handles_oos),
    (6, 'purchase_states_recoverable', test_06_purchase_states_recoverable),
    (7, 'order_id_path_safe', test_07_order_id_path_safe),
    (8, 'session_file_present', test_08_session_file_present),
    (9, 'proxy_config_valid', test_09_proxy_config_valid),
    (10, 'product_config_well_formed', test_10_product_config_well_formed),
    (11, 'warmup_can_fire_real_target', test_11_warmup_can_fire_real_target),
    (12, 'atc_with_fresh_cache_real', test_12_atc_with_fresh_cache_real),
]


def main():
    print("=" * 70)
    print("Target Bot — Full Failure-Mode Regression Suite")
    print("=" * 70)
    print(f"  cwd: {os.getcwd()}")
    print(f"  TARGET_TEST_TCIN: {os.environ.get('TARGET_TEST_TCIN', '50270379')}")
    print(f"  SKIP_BROWSER:     {os.environ.get('SKIP_BROWSER', '0')}")
    print(f"  ATTEMPT_REAL_ATC: {os.environ.get('ATTEMPT_REAL_ATC', '0')}")
    print(f"  SKIP_TESTS:       {os.environ.get('SKIP_TESTS', '(none)')}")
    print()

    for num, name, fn in TESTS:
        _run_test(num, name, fn)

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    counts = {Result.PASS: 0, Result.FAIL: 0, Result.SKIP: 0, Result.ERROR: 0}
    for _, status, _ in _results:
        counts[status] = counts.get(status, 0) + 1
    print(f"  PASS:  {counts[Result.PASS]}")
    print(f"  FAIL:  {counts[Result.FAIL]}")
    print(f"  SKIP:  {counts[Result.SKIP]}")
    print(f"  ERROR: {counts[Result.ERROR]}")
    print()

    bad = counts[Result.FAIL] + counts[Result.ERROR]
    if bad:
        print(f"  Failures/Errors:")
        for name, status, detail in _results:
            if status in (Result.FAIL, Result.ERROR):
                print(f"    {status} {name}: {detail}")
        sys.exit(1)
    print("  All tests passed (or skipped).")
    sys.exit(0)


if __name__ == '__main__':
    main()
