"""Offline tests for src/lanes/ — the independent-lane ATC firing module.

Tests the lane, pool, executor, shooter, checkout and harvester bank.
No network, no Chrome, no real proxies.
"""

import os
import sys
import time
import threading
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.lanes.lane import Lane, LaneCadence
from src.lanes.lane_pool import LanePool, _resolve_lane_ips, _ip_label
from src.lanes.lane_executor import lane_race_loop
from src.lanes.shape_harvester import (
    ShapeTokenBank, ShapeToken, order_headers,
    safe_chrome_max_age, _is_resi_proxy, _proxy_has_credentials,
    relaunch_budget_exceeded,
    RESI_CHROME_MAX_AGE_CEILING_S, MAX_REACTIVE_RELAUNCH,
)
from src.lanes.lane_shooter import LaneShooter, _build_atc_body, _read_account_cookies


# ── LaneCadence tests ────────────────────────────────────────────────────

def test_cadence_fast_retry_after_edge_429():
    """Edge 429 → fast retry (3-4.5s), not wave-first 55-70s."""
    c = LaneCadence(fast_retry_min_s=3.0, fast_retry_max_s=4.5)
    c.record_result(429, '')  # edge 429
    delay = c.next_fire_delay()
    assert 3.0 <= delay <= 4.5, f"Expected 3.0-4.5s, got {delay}"
    assert c.consecutive_edge_429 == 1


def test_cadence_cool_after_shape_401_streak():
    """3 consecutive Shape 401s → cool-off."""
    c = LaneCadence(cool_401_s=25.0, cool_401_streak=3)
    for _ in range(3):
        c.record_result(401)
    delay = c.next_fire_delay()
    assert delay >= 25.0, f"Expected >=25s after 3x401, got {delay}"


def test_cadence_reset_on_success():
    """A successful ATC resets all streaks."""
    c = LaneCadence()
    c.record_result(429, '')
    c.record_result(429, '')
    assert c.consecutive_edge_429 == 2
    c.record_result(201)
    assert c.consecutive_edge_429 == 0
    assert c.consecutive_shape_401 == 0
    assert c.total_orders == 1


def test_cadence_401_resets_edge_streak():
    """A 401 means we passed the edge — reset the edge streak."""
    c = LaneCadence()
    c.record_result(429, '')
    c.record_result(429, '')
    assert c.consecutive_edge_429 == 2
    c.record_result(401)
    assert c.consecutive_edge_429 == 0
    assert c.total_edge_pass == 1


# ── Lane tests ────────────────────────────────────────────────────────────

def test_lane_label():
    """Lane label includes the IP indicator."""
    home = Lane(lane_id='primary@home', account_id='primary', proxy_url='', ip_label='home')
    assert 'home' in home.label()

    proxy = Lane(lane_id='alt-1@31.105.228.245', account_id='alt-1',
                 proxy_url='http://brd-customer-x-ip-31.105.228.245:pass@host:33335',
                 ip_label='31.105.228.245')
    assert '31.105.228.245' in proxy.label()


def test_lane_race_lifecycle():
    """Lane race start/end state management."""
    lane = Lane(lane_id='test@home', account_id='test')
    assert not lane.racing
    lane.start_race('12345')
    assert lane.racing
    assert lane._active_tcin == '12345'
    lane.end_race()
    assert not lane.racing


# ── LanePool tests ────────────────────────────────────────────────────────

def test_lane_pool_from_config_empty():
    """Pool with no config file returns empty."""
    pool = LanePool([])
    assert len(pool) == 0


def test_lane_pool_fan_out():
    """on_stock_flip fans to all lanes in parallel."""
    results = []

    def mock_fire(lane, tcin, qty):
        results.append(lane.lane_id)
        time.sleep(0.05)

    lanes = [
        Lane(lane_id='a@home', account_id='a'),
        Lane(lane_id='b@home', account_id='b'),
        Lane(lane_id='c@home', account_id='c'),
    ]
    pool = LanePool(lanes)
    threads = pool.on_stock_flip('99999', quantity=2, fire_fn=mock_fire)

    for t in threads:
        t.join(timeout=5)

    assert sorted(results) == ['a@home', 'b@home', 'c@home'], f"Expected all 3, got {results}"
    assert not any(l.racing for l in lanes)


def test_lane_pool_skip_already_racing():
    """A lane already racing is skipped on the next flip."""
    fired = []

    def mock_fire(lane, tcin, qty):
        fired.append(lane.lane_id)
        time.sleep(0.2)

    lanes = [Lane(lane_id='a@home', account_id='a'),
             Lane(lane_id='b@home', account_id='b')]
    pool = LanePool(lanes)

    # Start a race on lane a manually
    lanes[0].start_race('11111')

    threads = pool.on_stock_flip('22222', quantity=2, fire_fn=mock_fire)
    for t in threads:
        t.join(timeout=5)

    assert 'a@home' not in fired, "Lane a should have been skipped (already racing)"
    assert 'b@home' in fired
    lanes[0].end_race()


# ── lane_race_loop tests ─────────────────────────────────────────────────

def test_lane_race_loop_budget():
    """Race loop respects the budget."""
    lane = Lane(lane_id='test@home', account_id='test',
                cadence=LaneCadence(fast_retry_min_s=0.05, fast_retry_max_s=0.1))
    lane.start_race('99999')

    call_count = [0]

    def mock_fire(l, tcin, qty):
        call_count[0] += 1
        return {'atc_status': 429, 'body_hint': '', 'success': False}

    from src.lanes import lane_executor
    old_budget = lane_executor.RACE_BUDGET_S
    lane_executor.RACE_BUDGET_S = 0.5

    result = lane_race_loop(lane, '99999', 2, fire_fn=mock_fire)

    lane_executor.RACE_BUDGET_S = old_budget

    assert result['shots'] >= 2, f"Should fire multiple shots, got {result['shots']}"
    assert result['elapsed_s'] <= 2.0, f"Should respect budget"


def test_lane_race_loop_stops_on_success():
    """Race loop stops immediately on a successful ATC."""
    lane = Lane(lane_id='test@home', account_id='test',
                cadence=LaneCadence(fast_retry_min_s=0.01, fast_retry_max_s=0.02))
    lane.start_race('99999')

    call_count = [0]

    def mock_fire(l, tcin, qty):
        call_count[0] += 1
        if call_count[0] == 3:
            return {'atc_status': 201, 'body_hint': '', 'success': True}
        return {'atc_status': 429, 'body_hint': '', 'success': False}

    from src.lanes import lane_executor
    old_budget = lane_executor.RACE_BUDGET_S
    lane_executor.RACE_BUDGET_S = 10.0

    result = lane_race_loop(lane, '99999', 2, fire_fn=mock_fire)

    lane_executor.RACE_BUDGET_S = old_budget

    assert result['shots'] == 3, f"Should stop after success on shot 3, got {result['shots']}"
    assert lane.cadence.total_orders == 1


def test_lane_race_loop_stops_on_oos():
    """Race loop stops when stock_live_fn returns False."""
    lane = Lane(lane_id='test@home', account_id='test',
                cadence=LaneCadence(fast_retry_min_s=0.01, fast_retry_max_s=0.02))
    lane.start_race('99999')

    call_count = [0]

    def mock_fire(l, tcin, qty):
        call_count[0] += 1
        return {'atc_status': 429, 'body_hint': '', 'success': False}

    def stock_live(tcin):
        return call_count[0] < 3

    from src.lanes import lane_executor
    old_budget = lane_executor.RACE_BUDGET_S
    lane_executor.RACE_BUDGET_S = 10.0

    result = lane_race_loop(lane, '99999', 2, fire_fn=mock_fire,
                            stock_live_fn=stock_live)

    lane_executor.RACE_BUDGET_S = old_budget

    assert result['shots'] <= 4, f"Should stop when OOS, got {result['shots']}"


# ── Lane summary tests ────────────────────────────────────────────────────

def test_lane_summary():
    """Lane summary reports correct stats."""
    lane = Lane(lane_id='test@home', account_id='test')
    lane.cadence.record_result(429, '')
    lane.cadence.record_result(429, '')
    lane.cadence.record_result(401)
    lane.cadence.record_result(201)

    s = lane.summary()
    assert s['shots'] == 4
    assert s['edge_pass'] == 2  # 401 + 201 both passed edge
    assert s['shape_pass'] == 1  # only 201 passed shape
    assert s['orders'] == 1


# ── ShapeTokenBank tests ─────────────────────────────────────────────────

def test_token_bank_deposit_withdraw():
    """Deposit tokens, withdraw FIFO."""
    bank = ShapeTokenBank(ttl_s=60.0, max_size=10)
    assert bank.available == 0

    bank.deposit({'x-test': 'val1'}, 'body1', 'Chrome/136')
    bank.deposit({'x-test': 'val2'}, 'body2', 'Chrome/136')
    assert bank.available == 2
    assert bank.total_produced == 2

    t1 = bank.withdraw()
    assert t1 is not None
    assert t1.headers.get('x-test') == 'val1'
    assert bank.total_consumed == 1
    assert bank.available == 1

    t2 = bank.withdraw()
    assert t2 is not None
    assert t2.headers.get('x-test') == 'val2'

    t3 = bank.withdraw()
    assert t3 is None, "Bank should be empty"


def test_token_bank_newest_first():
    """newest_first=True draws the freshest token first (LIFO), matching the
    production bank's pop_fresh. Default stays FIFO (asserted above)."""
    bank = ShapeTokenBank(ttl_s=60.0, max_size=10, newest_first=True)
    bank.deposit({'x-test': 'val1'}, 'body1', 'Chrome/136')
    bank.deposit({'x-test': 'val2'}, 'body2', 'Chrome/136')
    t1 = bank.withdraw()
    assert t1 is not None and t1.headers.get('x-test') == 'val2', "freshest first"
    t2 = bank.withdraw()
    assert t2 is not None and t2.headers.get('x-test') == 'val1'
    assert bank.withdraw() is None


def test_token_bank_expiry():
    """Expired tokens are skipped on withdraw."""
    bank = ShapeTokenBank(ttl_s=0.01, max_size=10)
    bank.deposit({'x-test': 'old'}, 'body', 'Chrome/136')
    time.sleep(0.05)
    t = bank.withdraw()
    assert t is None, "Token should have expired"
    assert bank.total_expired == 1


def test_token_bank_stats():
    """Bank stats are accurate."""
    bank = ShapeTokenBank(ttl_s=60.0)
    bank.deposit({}, '', '')
    bank.deposit({}, '', '')
    bank.withdraw()
    s = bank.stats()
    assert s['produced'] == 2
    assert s['consumed'] == 1
    assert s['available'] == 1


# ── order_headers tests ──────────────────────────────────────────────────

def test_order_headers_shape_tokens_after_accept():
    """Shape x-* headers are placed after accept in Chrome's wire order."""
    raw = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'x-custom-shape': 'sensor_data',
        'x-application-name': 'web',
        'User-Agent': 'Chrome/136',
    }
    ordered = order_headers(raw)
    keys = list(ordered.keys())
    # x-custom-shape should appear after accept
    assert 'accept' in keys
    accept_idx = keys.index('accept')
    assert 'x-custom-shape' in keys
    shape_idx = keys.index('x-custom-shape')
    assert shape_idx > accept_idx


# ── LaneShooter tests (no network) ───────────────────────────────────────

def test_shooter_no_token():
    """Shooter returns error when bank is empty."""
    bank = ShapeTokenBank(ttl_s=60.0)
    shooter = LaneShooter(
        bank=bank, proxy_url='', session_path='nonexistent.json',
        account_id='test', ip_label='test')
    result = shooter.fire('12345', 2)
    assert result['atc_status'] == 0
    assert result['error'] == 'no_shape_token'
    assert shooter.total_no_token == 1


def test_shooter_no_cookies():
    """Shooter returns error when session file is missing."""
    bank = ShapeTokenBank(ttl_s=60.0)
    bank.deposit({'x-test': 'val'}, 'body', 'Chrome/136')
    shooter = LaneShooter(
        bank=bank, proxy_url='', session_path='nonexistent.json',
        account_id='test', ip_label='test')
    result = shooter.fire('12345', 2)
    assert result['atc_status'] == 0
    assert result['error'] == 'no_account_cookies'
    assert shooter.total_no_cookies == 1


def test_build_atc_body():
    """ATC body has correct structure."""
    body = json.loads(_build_atc_body('12345', 2))
    assert body['cart_item']['tcin'] == '12345'
    assert body['cart_item']['quantity'] == 2
    assert body['cart_item']['item_channel_id'] == '10'
    assert body['cart_type'] == 'REGULAR'
    assert body['shopping_context'] == 'DIGITAL'


# ── IP resolution tests ──────────────────────────────────────────────────

def test_ip_label_bd():
    url = 'http://brd-customer-x-ip-31.105.228.245:pass@brd.superproxy.io:33335'
    assert _ip_label(url) == '31.105.228.245'


def test_ip_label_resi():
    url = 'http://user:pass_session-a2f4f975_lifetime-168h@geo.iproyal.com:12321'
    assert _ip_label(url) == 'resi_a2f4f975'


def test_ip_label_home():
    assert _ip_label('') == 'home'


def test_resolve_lane_ips():
    pools = {
        'sweep': ['http://sweep-0', 'http://sweep-1', 'http://sweep-2'],
        'reserve': ['http://reserve-0', 'http://reserve-1'],
        'residential': ['http://resi-0', 'http://resi-1', 'http://resi-2'],
    }

    # Named indices
    result = _resolve_lane_ips('reserve:0,resi:1', pools)
    assert result == ['http://reserve-0', 'http://resi-1']

    # all_reserve
    result = _resolve_lane_ips('all_reserve', pools)
    assert result == ['http://reserve-0', 'http://reserve-1']

    # all_resi
    result = _resolve_lane_ips('all_resi', pools)
    assert result == ['http://resi-0', 'http://resi-1', 'http://resi-2']

    # Out of range
    result = _resolve_lane_ips('reserve:99', pools)
    assert result == []

    # Empty
    result = _resolve_lane_ips('', pools)
    assert result == []


# ── Harvester freeze / PX hardening (2026-10-26) ────────────────────────────
# URLs below are host-only on purpose (no auth): the helpers substring-match the
# host, so no credentials are needed to exercise them.

def test_is_resi_proxy():
    """IPRoyal residential URLs are resi; BD ISP and home are not."""
    assert _is_resi_proxy('http://geo.iproyal.com:12321') is True
    assert _is_resi_proxy('http://brd.superproxy.io:33335') is False
    assert _is_resi_proxy('') is False
    assert _is_resi_proxy(None) is False


def test_safe_chrome_max_age_resi_clamped():
    """A resi harvester never exceeds the freeze-onset ceiling, even if a higher
    value is configured (C-1023-12)."""
    resi = 'http://geo.iproyal.com:12321'
    # Configured 780 > ceiling 720 → clamped to the ceiling.
    assert safe_chrome_max_age(780, resi) == RESI_CHROME_MAX_AGE_CEILING_S
    # Configured already under the ceiling → kept.
    assert safe_chrome_max_age(600, resi) == 600
    # Absurd env value → still clamped.
    assert safe_chrome_max_age(99999, resi) == RESI_CHROME_MAX_AGE_CEILING_S


def test_safe_chrome_max_age_bd_unchanged():
    """BD ISP and home keep their configured max-age (they do not freeze)."""
    bd = 'http://brd.superproxy.io:33335'
    assert safe_chrome_max_age(780, bd) == 780
    assert safe_chrome_max_age(2400, bd) == 2400
    assert safe_chrome_max_age(780, '') == 780          # home line
    # Garbage falls back to the 780 default, not a crash.
    assert safe_chrome_max_age('oops', bd) == 780


def test_proxy_has_credentials():
    """Credentialed proxies (user:pass@host) must be fronted by the forwarder;
    host-only or loopback URLs are used directly. Tokens here are placeholders."""
    assert _proxy_has_credentials('http://x:y@h:1') is True          # has creds
    assert _proxy_has_credentials('http://geo.iproyal.com:12321') is False
    assert _proxy_has_credentials('http://127.0.0.1:8080') is False  # local fwd
    assert _proxy_has_credentials('') is False
    assert _proxy_has_credentials(None) is False


def test_relaunch_budget_exceeded():
    """Reactive relaunches are capped as a RATE over a trailing hour (FS-W-D)."""
    now = 10_000.0
    # Exactly MAX within the window is allowed; one more trips it.
    times = [now - i for i in range(MAX_REACTIVE_RELAUNCH)]   # MAX entries, all recent
    assert relaunch_budget_exceeded(times, now) is False
    times.append(now)                                        # MAX+1
    assert relaunch_budget_exceeded(times, now) is True
    # Stale entries (older than the window) are pruned in place and don't count.
    old = [now - 7200 for _ in range(MAX_REACTIVE_RELAUNCH + 5)]
    assert relaunch_budget_exceeded(old, now) is False
    assert old == []                                         # pruned in place


# ── Runner ────────────────────────────────────────────────────────────────

_TESTS = [
    test_cadence_fast_retry_after_edge_429,
    test_cadence_cool_after_shape_401_streak,
    test_cadence_reset_on_success,
    test_cadence_401_resets_edge_streak,
    test_lane_label,
    test_lane_race_lifecycle,
    test_lane_pool_from_config_empty,
    test_lane_pool_fan_out,
    test_lane_pool_skip_already_racing,
    test_lane_race_loop_budget,
    test_lane_race_loop_stops_on_success,
    test_lane_race_loop_stops_on_oos,
    test_lane_summary,
    test_token_bank_deposit_withdraw,
    test_token_bank_newest_first,
    test_token_bank_expiry,
    test_token_bank_stats,
    test_order_headers_shape_tokens_after_accept,
    test_shooter_no_token,
    test_shooter_no_cookies,
    test_build_atc_body,
    test_ip_label_bd,
    test_ip_label_resi,
    test_ip_label_home,
    test_resolve_lane_ips,
    test_is_resi_proxy,
    test_safe_chrome_max_age_resi_clamped,
    test_safe_chrome_max_age_bd_unchanged,
    test_proxy_has_credentials,
    test_relaunch_budget_exceeded,
]


if __name__ == '__main__':
    passed = 0
    failed = 0
    for fn in _TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)
