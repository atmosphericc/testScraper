#!/usr/bin/env python3
"""Lane system live validation (2026-10-25).

Money-free: guest Chrome, control TCIN 81926151 (never buyable, 424 when
Shape passes, 401 when Shape denies).  No account touched.

Tests the full pipeline:
  1. ShapeHarvester on a reserve BD IP -> token bank fills
  2. LaneShooter fires ATC via curl_cffi using those tokens -> PASS/DENY
  3. Reports: harvester rate, token quality, shooter results

Usage:
  python tools/experiments/lane_validation.py                    # first reserve IP
  python tools/experiments/lane_validation.py --ip reserve:1     # second reserve
  python tools/experiments/lane_validation.py --ip resi:0        # first residential
  python tools/experiments/lane_validation.py --duration 60      # run for 60s
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.lanes.shape_harvester import ShapeHarvester, ShapeTokenBank
from src.lanes.lane_shooter import LaneShooter, _build_atc_body, _impersonate_for

CONTROL_TCIN = "81926151"


def resolve_proxy(spec: str) -> str:
    """Resolve 'reserve:0', 'resi:1', etc to a proxy URL."""
    cfg_path = ROOT / 'config' / 'proxyIps.json'
    with open(cfg_path) as f:
        data = json.load(f)

    m = re.match(r'(reserve|resi|sweep):(\d+)', spec)
    if m:
        pool_key = {'reserve': 'reserve_proxies', 'resi': 'residential_proxies',
                    'sweep': 'proxies'}[m.group(1)]
        idx = int(m.group(2))
        pool = data.get(pool_key, [])
        if idx < len(pool):
            return pool[idx]
        raise ValueError(f"{spec}: index {idx} out of range (pool has {len(pool)})")

    if spec.startswith('http'):
        return spec
    raise ValueError(f"unrecognized proxy spec: {spec}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ip', default='reserve:0',
                    help='Proxy spec: reserve:0, resi:1, etc')
    ap.add_argument('--duration', type=int, default=90,
                    help='Total test duration in seconds')
    ap.add_argument('--harvest-only', action='store_true',
                    help='Only test the harvester, skip shooter')
    a = ap.parse_args(argv)

    proxy_url = resolve_proxy(a.ip)
    ip_label = a.ip.replace(':', '_')
    print(f"[VALIDATE] proxy={a.ip} duration={a.duration}s")
    print(f"[VALIDATE] control TCIN={CONTROL_TCIN} (never buyable)")
    print()

    # Phase 1: Harvester
    print("=" * 60)
    print("PHASE 1: Shape Harvester")
    print("=" * 60)

    harvester = ShapeHarvester(
        proxy_url=proxy_url,
        ip_label=ip_label,
        token_ttl_s=300.0,
        harvest_interval_s=4.0,
        chrome_max_age_s=780.0,
    )

    harvester.start()

    # Wait for first tokens
    print("[VALIDATE] Waiting for harvester to produce tokens...")
    t0 = time.time()
    wait_timeout = min(a.duration, 120)
    while (time.time() - t0) < wait_timeout:
        avail = harvester.bank.available
        if avail >= 3:
            print(f"[VALIDATE] Harvester has {avail} tokens after "
                  f"{time.time() - t0:.1f}s")
            break
        time.sleep(2)
    else:
        print(f"[VALIDATE] Harvester produced {harvester.bank.available} tokens "
              f"in {wait_timeout}s")
        if harvester.bank.available == 0:
            print("[VALIDATE] FAIL: no tokens produced")
            harvester.stop()
            return 1

    print()
    h_status = harvester.status()
    print(f"  Harvested: {h_status['harvests']}")
    print(f"  Failures:  {h_status['failures']}")
    print(f"  Bank:      {h_status['bank']['available']} available")
    print(f"  Chrome:    {'UP' if h_status['chrome_alive'] else 'DOWN'} "
          f"(age {h_status['chrome_age_s']:.0f}s)")
    print()

    if a.harvest_only:
        # Let it run for the duration, report stats
        remaining = a.duration - (time.time() - t0)
        if remaining > 0:
            print(f"[VALIDATE] Letting harvester run for {remaining:.0f}s more...")
            time.sleep(remaining)
        h_status = harvester.status()
        print(f"\n  Final: harvested={h_status['harvests']} "
              f"failures={h_status['failures']} "
              f"bank={h_status['bank']['available']}")
        harvester.stop()
        return 0

    # Phase 2: Shooter
    print("=" * 60)
    print("PHASE 2: curl_cffi Shooter")
    print("=" * 60)

    # Create a shooter with a dummy session (guest, no account cookies needed
    # for the control TCIN — it returns 424/401 regardless of auth)
    # We'll create a temporary guest session file
    import tempfile
    guest_session = tempfile.mktemp(suffix='.json')
    # Write minimal guest cookies (the control TCIN doesn't need auth)
    Path(guest_session).write_text(json.dumps({
        'cookies': [
            {'name': 'visitorId', 'value': 'test', 'domain': '.target.com'},
        ],
        'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }))

    shooter = LaneShooter(
        bank=harvester.bank,
        proxy_url=proxy_url,
        session_path=guest_session,
        account_id='guest_test',
        ip_label=ip_label,
    )

    # Fire a few shots
    results = {'pass': 0, 'shape_denied': 0, 'limited': 0, 'error': 0, 'no_token': 0}
    n_shots = min(10, harvester.bank.available)
    print(f"[VALIDATE] Firing {n_shots} shots at control TCIN...")
    print()

    for i in range(n_shots):
        result = shooter.fire(CONTROL_TCIN, 2)
        status = result['atc_status']
        hint = result.get('body_hint', '')[:60]
        token_age = result.get('token_age_s', 0)
        rt = result.get('rt_ms', 0)

        if result.get('error') == 'no_shape_token':
            verdict = 'NO_TOKEN'
            results['no_token'] += 1
        elif status == 401 and not result.get('hop'):
            verdict = 'SHAPE_DENIED'
            results['shape_denied'] += 1
        elif status == 429:
            verdict = 'EDGE_LIMITED'
            results['limited'] += 1
        elif status in (424, 200, 201):
            verdict = 'PASS'
            results['pass'] += 1
        elif status == 0:
            verdict = 'ERROR'
            results['error'] += 1
        else:
            verdict = f'OTHER_{status}'
            results['error'] += 1

        print(f"  shot {i+1:>2}: status={status} verdict={verdict} "
              f"rt={rt}ms token_age={token_age:.1f}s "
              f"hint={hint!r}")

        if i < n_shots - 1:
            time.sleep(3.5)

    # Cleanup
    try:
        os.unlink(guest_session)
    except Exception:
        pass
    harvester.stop()

    # Report
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    total = sum(results.values())
    print(f"  Total shots:   {total}")
    print(f"  PASS (424):    {results['pass']} — Shape accepted, IP-bound token works")
    print(f"  SHAPE_DENIED:  {results['shape_denied']} — Shape rejected (token/IP mismatch?)")
    print(f"  EDGE_LIMITED:  {results['limited']} — edge 429 (limiter lottery)")
    print(f"  NO_TOKEN:      {results['no_token']} — bank empty")
    print(f"  ERROR:         {results['error']}")
    print()

    if results['pass'] > 0:
        print("  ✓ VALIDATION PASSED — Shape tokens from this IP work with curl_cffi")
        return 0
    elif results['limited'] > 0:
        print("  ~ INCONCLUSIVE — edge limiter blocked all shots (Shape not tested)")
        return 0
    elif results['shape_denied'] == total:
        print("  ✗ VALIDATION FAILED — all shots Shape-denied")
        return 1
    else:
        print("  ? MIXED — review the shot log above")
        return 1


if __name__ == '__main__':
    sys.exit(main())
