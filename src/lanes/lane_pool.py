"""LanePool — builds and manages independent firing lanes across multiple IPs.

The pool's job:
  1. Read config: accounts, ISP proxies, residential proxies, reserve proxies
  2. Build one ShapeHarvester per unique exit IP
  3. Build one Lane per (account x IP) combination, each with its own shooter
  4. Fan stock-flip events to all lanes simultaneously
  5. Collect per-lane and per-harvester diagnostics

The pool does NOT impose a fleet-wide cadence. Each lane decides when
to fire based on its own response history (LaneCadence).

IP allocation strategy:
  - The sweep pool (config.proxies) stays with the stock monitor by default
  - Reserve proxies (config.reserve_proxies) are available for lanes
  - Residential proxies (config.residential_proxies) are available for lanes
  - TARGET_LANE_IPS overrides: comma-separated list of proxy URLs or
    indices like 'reserve:0', 'resi:1', 'sweep:3'

Usage (from app.py when TARGET_LANE_MODE=1):

    pool = LanePool.from_config(worker_map={'primary': w1, ...})
    pool.start_harvesters()       # starts all harvester Chromes
    pool.on_stock_flip(tcin, 2)   # fans to all lanes simultaneously
    pool.stop()                   # clean shutdown
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .lane import Lane, LaneCadence
from .shape_harvester import ShapeHarvester
from .lane_shooter import LaneShooter
from .lane_checkout import LaneCheckout

_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_CFG = _ROOT / 'config' / 'target_accounts.json'
PROXY_CFG = _ROOT / 'config' / 'proxyIps.json'


def _load_proxy_config() -> Dict[str, List[str]]:
    """Load all proxy pools from config/proxyIps.json."""
    try:
        with open(PROXY_CFG) as f:
            data = json.load(f)
        return {
            'sweep': data.get('proxies', []),
            'reserve': data.get('reserve_proxies', []),
            'residential': data.get('residential_proxies', []),
        }
    except Exception:
        return {'sweep': [], 'reserve': [], 'residential': []}


def _ip_label(proxy_url: str) -> str:
    """Extract a human-readable IP label from a proxy URL."""
    if not proxy_url:
        return 'home'
    m = re.search(r'-ip-(\d+\.\d+\.\d+\.\d+)', proxy_url)
    if m:
        return m.group(1)
    m = re.search(r'session-([a-f0-9]+)', proxy_url)
    if m:
        return f'resi_{m.group(1)[:8]}'
    return 'proxy'


def _resolve_lane_ips(env_val: str, proxy_pools: Dict[str, List[str]]) -> List[str]:
    """Resolve TARGET_LANE_IPS env value to a list of proxy URLs.

    Accepts:
      - 'reserve:0,reserve:1' — indices into the reserve pool
      - 'resi:0,resi:1,resi:2,resi:3' — indices into the residential pool
      - 'sweep:8,sweep:9' — indices into the sweep pool
      - 'all_reserve' — all reserve proxies
      - 'all_resi' — all residential proxies
      - Full URLs (passthrough)
    """
    if not env_val.strip():
        return []

    result = []
    for token in env_val.split(','):
        token = token.strip()
        if not token:
            continue

        if token == 'all_reserve':
            result.extend(proxy_pools.get('reserve', []))
            continue
        if token == 'all_resi':
            result.extend(proxy_pools.get('residential', []))
            continue
        if token == 'all_sweep':
            result.extend(proxy_pools.get('sweep', []))
            continue

        m = re.match(r'(reserve|resi|sweep):(\d+)', token)
        if m:
            pool_key = {'reserve': 'reserve', 'resi': 'residential', 'sweep': 'sweep'}[m.group(1)]
            idx = int(m.group(2))
            pool = proxy_pools.get(pool_key, [])
            if idx < len(pool):
                result.append(pool[idx])
            else:
                print(f"[LANE_POOL] WARNING: {token} index {idx} out of range "
                      f"(pool has {len(pool)})")
            continue

        # Assume it's a full URL
        if token.startswith('http'):
            result.append(token)
        else:
            print(f"[LANE_POOL] WARNING: unrecognized IP spec '{token}'")

    return result


class LanePool:
    """Manages N independent firing lanes across multiple exit IPs."""

    def __init__(
        self,
        lanes: List[Lane],
        harvesters: Optional[Dict[str, ShapeHarvester]] = None,
        checkouts: Optional[Dict[str, LaneCheckout]] = None,
        claim_registry: Optional[Any] = None,
    ):
        self.lanes = lanes
        self.harvesters = harvesters or {}
        self.checkouts = checkouts or {}
        # Cross-path double-fire guard (PurchaseClaimRegistry), shared with the
        # main purchase manager. None => no coordination (fine in isolated
        # tests / fire-only mode). app.py injects a live one under MODE=1.
        self.claim_registry = claim_registry
        self._lock = threading.Lock()

    @classmethod
    def from_config(
        cls,
        worker_map: Optional[Dict[str, Any]] = None,
        claim_registry: Optional[Any] = None,
    ) -> 'LanePool':
        """Build lanes from config + env.

        Parameters
        ----------
        worker_map : dict, optional
            Maps account_id -> Worker object. Required for checkout handoff.
            If None, lanes run in fire-only mode (no checkout).
        """
        if not ACCOUNTS_CFG.exists():
            print("[LANE_POOL] No accounts config — empty pool")
            return cls([])

        with open(ACCOUNTS_CFG) as f:
            acct_cfg = json.load(f)

        proxy_pools = _load_proxy_config()
        accounts = [a for a in acct_cfg.get('accounts', []) if a.get('enabled', True)]

        if not accounts:
            print("[LANE_POOL] No enabled accounts — empty pool")
            return cls([])

        # Resolve which IPs to use for lanes
        lane_ips_env = os.environ.get('TARGET_LANE_IPS', '').strip()
        if lane_ips_env:
            lane_ips = _resolve_lane_ips(lane_ips_env, proxy_pools)
        else:
            # Default: all reserve + all residential
            lane_ips = (proxy_pools.get('reserve', [])
                        + proxy_pools.get('residential', []))

        if not lane_ips:
            print("[LANE_POOL] No lane IPs configured — "
                  "set TARGET_LANE_IPS or add reserve/residential proxies")
            return cls([])

        # Read cadence config from env
        fast_min = float(os.environ.get('TARGET_LANE_FAST_MIN_S', '3.0'))
        fast_max = float(os.environ.get('TARGET_LANE_FAST_MAX_S', '4.5'))
        cool_401 = float(os.environ.get('TARGET_LANE_COOL_401_S', '25.0'))
        cool_streak = int(os.environ.get('TARGET_LANE_COOL_401_STREAK', '3'))
        token_ttl = float(os.environ.get('TARGET_LANE_TOKEN_TTL_S', '300.0'))
        harvest_interval = float(os.environ.get('TARGET_LANE_HARVEST_INTERVAL_S', '4.0'))
        chrome_max_age = float(os.environ.get('TARGET_LANE_CHROME_MAX_AGE_S', '780.0'))
        cookie_refresh = float(os.environ.get('TARGET_LANE_COOKIE_REFRESH_S', '60.0'))

        # Deduplicate IPs and build one harvester per unique IP
        seen_ips: Dict[str, str] = {}  # ip_label -> proxy_url
        for url in lane_ips:
            label = _ip_label(url)
            if label not in seen_ips:
                seen_ips[label] = url

        harvesters: Dict[str, ShapeHarvester] = {}
        for label, url in seen_ips.items():
            harvesters[label] = ShapeHarvester(
                proxy_url=url,
                ip_label=label,
                token_ttl_s=token_ttl,
                harvest_interval_s=harvest_interval,
                chrome_max_age_s=chrome_max_age,
            )

        # Build one LaneCheckout per account
        checkouts: Dict[str, LaneCheckout] = {}
        for acct in accounts:
            aid = acct['account_id']
            cvv = acct.get('cvv', '')
            checkouts[aid] = LaneCheckout(account_id=aid, cvv=cvv)

        # Build lanes: one per (account x IP)
        lanes: List[Lane] = []
        for acct in accounts:
            aid = acct['account_id']
            session_path = acct.get('session_path', '')

            for label, url in seen_ips.items():
                harvester = harvesters[label]
                shooter = LaneShooter(
                    bank=harvester.bank,
                    proxy_url=url,
                    session_path=session_path,
                    account_id=aid,
                    ip_label=label,
                    cookie_refresh_s=cookie_refresh,
                )

                cadence = LaneCadence(
                    fast_retry_min_s=fast_min,
                    fast_retry_max_s=fast_max,
                    cool_401_s=cool_401,
                    cool_401_streak=cool_streak,
                )

                lane = Lane(
                    lane_id=f"{aid}@{label}",
                    account_id=aid,
                    proxy_url=url,
                    ip_label=label,
                    worker=worker_map.get(aid) if worker_map else None,
                    shooter=shooter,
                    checkout=checkouts.get(aid),
                    cadence=cadence,
                )
                lanes.append(lane)

        print(f"[LANE_POOL] Built {len(lanes)} lanes "
              f"({len(accounts)} accounts x {len(seen_ips)} IPs), "
              f"{len(harvesters)} harvesters")
        for label in sorted(seen_ips):
            print(f"  IP {label}: "
                  f"{', '.join(a['account_id'] for a in accounts)} "
                  f"(harvester interval={harvest_interval}s)")

        return cls(lanes, harvesters, checkouts, claim_registry=claim_registry)

    def start_harvesters(self) -> None:
        """Start all Shape harvesters (each in its own thread)."""
        for label, h in self.harvesters.items():
            h.start()
        print(f"[LANE_POOL] Started {len(self.harvesters)} harvesters")

    def stop_harvesters(self) -> None:
        """Stop all harvesters."""
        for h in self.harvesters.values():
            h.stop()

    def stop(self) -> None:
        """Full shutdown: stop all harvesters, end any active races."""
        self.stop_harvesters()
        for lane in self.lanes:
            if lane.racing:
                lane.end_race()
        print("[LANE_POOL] Stopped")

    def on_stock_flip(
        self,
        tcin: str,
        quantity: int = 2,
        fire_fn: Optional[Callable] = None,
    ) -> List[threading.Thread]:
        """Fan a stock-flip event to every lane simultaneously.

        If fire_fn is None, each lane uses its built-in shooter.
        """
        threads: List[threading.Thread] = []
        reg = self.claim_registry

        for lane in self.lanes:
            if lane.racing:
                print(f"[LANE_POOL] {lane.label()} already racing "
                      f"{lane._active_tcin} — skipping {tcin}")
                continue

            # Check if the lane's harvester has tokens
            if lane.ip_label in self.harvesters:
                bank = self.harvesters[lane.ip_label].bank
                if bank.available == 0:
                    print(f"[LANE_POOL] {lane.label()} no Shape tokens — skipping")
                    continue

            # Cross-path double-fire guard: claim this account x TCIN before
            # firing. If another lane of the SAME account, or the main purchase
            # path, already holds it, yield — one place-order per account x TCIN
            # across both paths (prevents a double order / double charge on one
            # member account + saved card). Released in the runner's finally.
            if reg is not None and not reg.try_claim(lane.account_id, tcin, lane.lane_id):
                print(f"[LANE_POOL] {lane.label()} yielding {tcin} — "
                      f"account x TCIN already claimed "
                      f"(owner={reg.owner(lane.account_id, tcin)})")
                continue

            lane.start_race(tcin)

            def _lane_runner(_lane=lane, _tcin=tcin, _qty=quantity, _fn=fire_fn, _reg=reg):
                try:
                    print(f"[LANE] {_lane.label()} entering race for {_tcin}")
                    if _fn is not None:
                        _fn(_lane, _tcin, _qty)
                    elif _lane.shooter is not None:
                        from .lane_executor import lane_race_loop
                        lane_race_loop(
                            _lane, _tcin, _qty,
                            fire_fn=_shooter_fire_fn,
                        )
                    else:
                        print(f"[LANE] {_lane.label()} DRY RUN — "
                              f"would fire ATC for {_tcin} qty={_qty}")
                except Exception as e:
                    print(f"[LANE] {_lane.label()} error: {e}")
                finally:
                    _lane.end_race()
                    if _reg is not None:
                        _reg.release(_lane.account_id, _tcin, _lane.lane_id)

            t = threading.Thread(
                target=_lane_runner, daemon=True,
                name=f"lane-{lane.lane_id}-{tcin}")
            t.start()
            threads.append(t)

        print(f"[LANE_POOL] Dispatched {len(threads)} lanes for {tcin}")
        return threads

    def harvester_status(self) -> List[Dict[str, Any]]:
        return [h.status() for h in self.harvesters.values()]

    def status(self) -> List[Dict[str, Any]]:
        """Per-lane diagnostic summary."""
        return [lane.summary() for lane in self.lanes]

    def print_status(self) -> None:
        """Print harvester + lane status tables."""
        print("\n── Harvesters ──")
        for h in self.harvesters.values():
            s = h.status()
            print(f"  {s['ip']:<20} harvested={s['harvests']:<5} "
                  f"bank={s['bank']['available']:<4} "
                  f"chrome={'UP' if s['chrome_alive'] else 'DOWN'} "
                  f"age={s['chrome_age_s']:.0f}s")

        print("\n── Lanes ──")
        for s in self.status():
            edge_pct = f"{s['edge_pass_rate']:.1f}%"
            print(f"  {s['lane']:<28} shots={s['shots']:<5} "
                  f"edge_pass={s['edge_pass']:<4} ({edge_pct:>5}) "
                  f"orders={s['orders']} "
                  f"no_token={s['no_token']}")

    def __len__(self) -> int:
        return len(self.lanes)

    def __iter__(self):
        return iter(self.lanes)


def _shooter_fire_fn(lane: Lane, tcin: str, quantity: int) -> Dict[str, Any]:
    """Default fire_fn: uses the lane's built-in shooter.

    On a 201 (cart success), hands off to the account's Chrome for checkout.
    """
    if lane.shooter is None:
        return {'atc_status': 0, 'body_hint': 'no_shooter', 'success': False}

    result = lane.shooter.fire(tcin, quantity)

    # On cart success, hand to checkout
    if result.get('success') and lane.checkout and lane.worker:
        cart_id = result.get('cart_id', '')
        print(f"[LANE] {lane.label()} ATC SUCCESS (status={result['atc_status']}) "
              f"— handing to checkout")
        try:
            checkout_result = lane.checkout.checkout(
                lane.worker, tcin, quantity, cart_id)
            result['checkout'] = checkout_result
            po_status = checkout_result.get('po', {}).get('status', 0)
            if po_status in (200, 201):
                result['order_placed'] = True
        except Exception as e:
            print(f"[LANE] {lane.label()} checkout handoff failed: {e}")
            result['checkout_error'] = str(e)

    return result
