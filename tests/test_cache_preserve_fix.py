#!/usr/bin/env python3
"""
Standalone test for the Shape-headers cache-preserve fix in
src/session/purchase_executor.py:442-456.

What we're proving:
  Before the fix, this rule locked the cache forever once a 7-token capture
  was seen, because every subsequent 6-token capture was rejected:
      skip_cache_update = (prev > 0 and new < prev)

  After the fix, only true zero-token poisoning is blocked, and only while
  the cache is fresh (<60s). 6-token captures correctly refresh a 7-token
  cache:
      cache_age_now = ...
      skip_cache_update = (prev > 0 and new == 0 and cache_age_now < 60)

We test BOTH rules against a synthetic 3-hour idle scenario (cache boots at
7 tokens, only warmup 6-token captures arrive after that) and assert:
  - OLD rule: cache stuck at boot timestamp forever (the production bug)
  - NEW rule: cache rotates on every warmup capture (the fix)

This test does NOT make any network calls, does NOT launch Chrome, does NOT
touch any production state. It exercises only the predicate logic.
"""
import time


# ── Mock the cache state the executor maintains ──────────────────────────────

class MockCache:
    def __init__(self, boot_headers):
        self._cached_cart_headers = dict(boot_headers)
        self._cached_cart_headers_ts = time.time()


def _shape_token_count(hdrs):
    """Same predicate the executor uses (line 436-439)."""
    return len([h for h in hdrs
                if h.lower().startswith('x-')
                and h.lower() != 'x-application-name'])


# ── The two rules under test ─────────────────────────────────────────────────

def old_skip_rule(cache, new_headers, now):
    """Pre-fix rule from purchase_executor.py:446 (the bug)."""
    prev = _shape_token_count(cache._cached_cart_headers)
    new = _shape_token_count(list(new_headers.keys()))
    return (prev > 0 and new < prev)


def new_skip_rule(cache, new_headers, now):
    """Post-fix rule from purchase_executor.py:451-456."""
    prev = _shape_token_count(cache._cached_cart_headers)
    new = _shape_token_count(list(new_headers.keys()))
    cache_age_now = (now - cache._cached_cart_headers_ts) if cache._cached_cart_headers_ts else 999.0
    return (prev > 0 and new == 0 and cache_age_now < 60.0)


def apply_capture(cache, new_headers, skip_rule, now):
    """Mirror the executor's update logic at line 457-459."""
    if not skip_rule(cache, new_headers, now):
        cache._cached_cart_headers = dict(new_headers)
        cache._cached_cart_headers_ts = now
        return 'updated'
    return 'preserved'


# ── Realistic header sets ────────────────────────────────────────────────────

# Boot-time main-tab POST capture (7 Shape tokens, like a real ATC)
BOOT_HEADERS_7 = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Cookie': '...',
    'Origin': 'https://www.target.com',
    'Referer': 'https://www.target.com/p/-/A-50270379',
    'User-Agent': 'Mozilla/5.0 ...',
    'X-GyJwza5Z-a': 'tok_a',
    'X-GyJwza5Z-a0': 'tok_a0',
    'X-GyJwza5Z-b': 'tok_b',
    'X-GyJwza5Z-c': 'tok_c',
    'X-GyJwza5Z-d': 'tok_d',
    'X-GyJwza5Z-f': 'tok_f',
    'X-GyJwza5Z-z': 'tok_z',
    'sec-ch-ua': '"Chromium";v="131"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
}

# Warmup-tab POST capture (6 Shape tokens — one fewer than main-tab)
WARMUP_HEADERS_6 = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Cookie': '...',
    'Origin': 'https://www.target.com',
    'Referer': 'https://www.target.com/cart',
    'User-Agent': 'Mozilla/5.0 ...',
    'X-GyJwza5Z-a': 'rotated_a',
    'X-GyJwza5Z-a0': 'rotated_a0',
    'X-GyJwza5Z-b': 'rotated_b',
    'X-GyJwza5Z-c': 'rotated_c',
    'X-GyJwza5Z-d': 'rotated_d',
    'X-GyJwza5Z-z': 'rotated_z',
    'sec-ch-ua': '"Chromium";v="131"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
}

# Page-driven natural fetch (0 Shape tokens — true poisoning case)
NATURAL_FETCH_0 = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Cookie': '...',
    'Origin': 'https://www.target.com',
    'User-Agent': 'Mozilla/5.0 ...',
    'x-application-name': 'web',  # the only X-header but excluded from count
}


# ── Tests ────────────────────────────────────────────────────────────────────

def test_token_counts():
    print("=== Test: token counts match production semantics ===")
    assert _shape_token_count(list(BOOT_HEADERS_7.keys())) == 7, \
        f"BOOT should have 7, got {_shape_token_count(list(BOOT_HEADERS_7.keys()))}"
    assert _shape_token_count(list(WARMUP_HEADERS_6.keys())) == 6
    assert _shape_token_count(list(NATURAL_FETCH_0.keys())) == 0
    print("  PASS — boot=7, warmup=6, natural=0")


def test_old_rule_locks_cache_forever():
    """Reproduce the production bug: 3-hour idle locks cache at boot."""
    print()
    print("=== Test: OLD rule locks cache for 3+ hours of idle ===")
    cache = MockCache(BOOT_HEADERS_7)
    boot_ts = cache._cached_cart_headers_ts
    print(f"  t=0:    boot capture (7 tokens) — ts={boot_ts:.0f}")

    # Simulate 3 hours of idle warmup cycles (every 24s)
    n_cycles = (3 * 3600) // 24
    updates = 0
    preserves = 0
    sim_time = boot_ts
    for i in range(n_cycles):
        sim_time += 24
        result = apply_capture(cache, WARMUP_HEADERS_6, old_skip_rule, sim_time)
        if result == 'updated':
            updates += 1
        else:
            preserves += 1

    final_age = sim_time - cache._cached_cart_headers_ts
    print(f"  After {n_cycles} warmup cycles ({n_cycles * 24}s elapsed):")
    print(f"    updates: {updates}")
    print(f"    preserves: {preserves}")
    print(f"    final cache age: {final_age:.0f}s ({final_age/3600:.1f}h)")

    assert updates == 0, f"OLD rule should never update cache; got {updates}"
    assert preserves == n_cycles
    assert final_age > 3 * 3600 - 60  # ~3h stale
    print(f"  PASS — cache locked at boot timestamp, {final_age:.0f}s stale "
          f"(reproduces production bug)")


def test_new_rule_rotates_cache():
    """Verify the fix: warmup captures refresh the cache."""
    print()
    print("=== Test: NEW rule rotates cache on every warmup capture ===")
    cache = MockCache(BOOT_HEADERS_7)
    boot_ts = cache._cached_cart_headers_ts

    n_cycles = (3 * 3600) // 24
    updates = 0
    preserves = 0
    sim_time = boot_ts
    for i in range(n_cycles):
        sim_time += 24
        result = apply_capture(cache, WARMUP_HEADERS_6, new_skip_rule, sim_time)
        if result == 'updated':
            updates += 1
        else:
            preserves += 1

    final_age = sim_time - cache._cached_cart_headers_ts
    print(f"  After {n_cycles} warmup cycles ({n_cycles * 24}s elapsed):")
    print(f"    updates: {updates}")
    print(f"    preserves: {preserves}")
    print(f"    final cache age: {final_age:.0f}s")

    assert updates == n_cycles, f"NEW rule should update every cycle; got {updates}"
    assert preserves == 0
    assert final_age <= 24, f"final cache should be ~24s old; got {final_age:.0f}s"
    print(f"  PASS — cache stayed fresh ({final_age:.0f}s old at end)")


def test_new_rule_still_blocks_zero_token_poisoning():
    """The original anti-poisoning intent must still hold for 0-token fetches."""
    print()
    print("=== Test: NEW rule still blocks 0-token natural fetches ===")
    cache = MockCache(WARMUP_HEADERS_6)
    boot_ts = cache._cached_cart_headers_ts

    # Within fresh window (<60s): poisoning attempt should be blocked
    sim_time = boot_ts + 10
    result = apply_capture(cache, NATURAL_FETCH_0, new_skip_rule, sim_time)
    assert result == 'preserved', \
        f"0-token fetch within fresh window should be blocked; got {result}"
    print(f"  t=10s:  0-token natural fetch → {result} (blocked, cache still fresh)")
    assert _shape_token_count(list(cache._cached_cart_headers.keys())) == 6

    # Past fresh window (>=60s): even 0-token wins (defense-in-depth — cache was
    # going to be useless anyway, and a 0-token fetch lets the warmup loop know
    # to re-trigger). This is intentional in the new rule.
    sim_time = boot_ts + 90
    result = apply_capture(cache, NATURAL_FETCH_0, new_skip_rule, sim_time)
    assert result == 'updated', \
        f"After 90s, even 0-token should win; got {result}"
    print(f"  t=90s:  0-token natural fetch → {result} (accepted past 60s freshness)")


def test_new_rule_handles_main_tab_after_warmup():
    """Verify a 7-token main-tab POST after a 6-token warmup state still works."""
    print()
    print("=== Test: NEW rule lets main-tab 7-token capture overwrite warmup 6-token ===")
    cache = MockCache(WARMUP_HEADERS_6)
    boot_ts = cache._cached_cart_headers_ts

    # Simulate purchase: warmup (6) just landed, then main-tab POST (7) fires
    sim_time = boot_ts + 1
    result = apply_capture(cache, BOOT_HEADERS_7, new_skip_rule, sim_time)
    assert result == 'updated'
    assert _shape_token_count(list(cache._cached_cart_headers.keys())) == 7
    print(f"  6→7 transition allowed (real purchase path remains healthy)")


def test_new_rule_handles_warmup_after_main_tab():
    """The exact production case: 7-token boot, then 6-token warmup at t=24s."""
    print()
    print("=== Test: NEW rule allows 7→6 transition (the production bug case) ===")
    cache = MockCache(BOOT_HEADERS_7)
    boot_ts = cache._cached_cart_headers_ts

    sim_time = boot_ts + 24
    result = apply_capture(cache, WARMUP_HEADERS_6, new_skip_rule, sim_time)
    assert result == 'updated', \
        f"7→6 must be allowed (this is the bug); got {result}"
    assert _shape_token_count(list(cache._cached_cart_headers.keys())) == 6
    new_age = sim_time - cache._cached_cart_headers_ts
    print(f"  t=24s: 6-token warmup overwrote 7-token boot → cache age={new_age:.0f}s")
    print(f"  PASS — exact failure mode from screenshots is now fixed")


def test_summary():
    print()
    print("=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)
    print()
    print("Production bug (OLD rule):")
    print("  After 3 hours of idle, cache stuck at boot — 100% of warmup")
    print("  captures rejected. ATC fetches would use dead Shape tokens (403).")
    print()
    print("Fix (NEW rule):")
    print("  Cache rotates on every warmup cycle (~24s). 0-token poisoning still")
    print("  blocked while fresh, but stale cache always loses to non-zero.")


if __name__ == '__main__':
    test_token_counts()
    test_old_rule_locks_cache_forever()
    test_new_rule_rotates_cache()
    test_new_rule_still_blocks_zero_token_poisoning()
    test_new_rule_handles_main_tab_after_warmup()
    test_new_rule_handles_warmup_after_main_tab()
    test_summary()
