#!/usr/bin/env python3
"""Smoke test: level re-arm map builder (StockMonitorThread._build_level_rearm_map).

2026-08-02 (07-31 post-drop audit): resilient mode publishes 'stock_updated'
only on OOS→in-stock transitions, so a wave that ends 'failed' while the item
stays in stock never re-races (95274164/95274160 each sat live ~22 min with one
race + 17-18 min of silence). The new level re-arm loop re-publishes
failed-but-still-stocked TCINs; this test pins its filter semantics:

  - included ONLY when: in_stock AND freshly swept AND purchase state 'failed'
  - 'purchased' NEVER re-armed (repeat buys still require a real flip)
  - 'attempting'/'queued'/'ready'/unknown states never re-armed
  - stale snapshots (dead checker) never re-armed
  - output uses the _adapter stock_data shape, str-keyed

No browser, no network. Run: python tests/test_level_rearm_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import StockMonitorThread  # noqa: E402

build = StockMonitorThread._build_level_rearm_map

NOW = time.time()


class _S:
    """Stand-in for stock_check_resilient.TcinStatus."""
    def __init__(self, tcin, in_stock=True, last=None, title=None, avail='IN_STOCK'):
        self.tcin = tcin
        self.in_stock = in_stock
        self.last_checked_at = NOW - 1 if last is None else last
        self.title = title
        self.availability_status = avail


class _Broken:
    """Attribute access raises — must be skipped, not fatal."""
    @property
    def in_stock(self):
        raise RuntimeError("torn read")


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def test_failed_fresh_in_stock_included():
    m = build({'111': _S('111', title='ETB')}, {'111': {'status': 'failed'}}, NOW)
    check("failed_fresh_in_stock_included", set(m) == {'111'})
    d = m.get('111', {})
    check("adapter_shape", d.get('in_stock') is True and d.get('title') == 'ETB'
          and 'last_checked' in d and d.get('status_detail') == 'IN_STOCK')


def test_title_fallback():
    m = build({'111': _S('111', title=None)}, {'111': {'status': 'failed'}}, NOW)
    check("title_fallback", m.get('111', {}).get('title') == 'Product 111')


def test_non_failed_states_excluded():
    states = {
        '1': {'status': 'purchased'},
        '2': {'status': 'attempting'},
        '3': {'status': 'queued'},
        '4': {'status': 'ready'},
    }
    snap = {t: _S(t) for t in states}
    snap['5'] = _S('5')  # no state entry at all
    m = build(snap, states, NOW)
    check("non_failed_states_excluded", m == {})


def test_oos_excluded():
    m = build({'111': _S('111', in_stock=False)}, {'111': {'status': 'failed'}}, NOW)
    check("oos_excluded", m == {})


def test_stale_snapshot_excluded():
    snap = {
        '111': _S('111', last=NOW - 91),   # stale — checker likely dead
        '222': _S('222', last=0),          # never checked
        '333': _S('333', last=NOW - 5),    # fresh
    }
    states = {t: {'status': 'failed'} for t in snap}
    m = build(snap, states, NOW)
    check("stale_snapshot_excluded", set(m) == {'333'})


def test_broken_entry_skipped():
    snap = {'bad': _Broken(), '111': _S('111')}
    m = build(snap, {'111': {'status': 'failed'}, 'bad': {'status': 'failed'}}, NOW)
    check("broken_entry_skipped", set(m) == {'111'})


def test_int_tcin_key_normalized():
    m = build({95274164: _S(95274164)}, {'95274164': {'status': 'failed'}}, NOW)
    check("int_tcin_key_normalized", set(m) == {'95274164'})


if __name__ == '__main__':
    test_failed_fresh_in_stock_included()
    test_title_fallback()
    test_non_failed_states_excluded()
    test_oos_excluded()
    test_stale_snapshot_excluded()
    test_broken_entry_skipped()
    test_int_tcin_key_normalized()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
