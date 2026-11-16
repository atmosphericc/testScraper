#!/usr/bin/env python3
"""FX-1114-PXRECONFIRM offline test. No browser, no network, no account files.

Final design: park ONLY on a persistent STRONG marker on the PX re-read; container-only
(flash OR stuck placeholder) never parks; a wall whose strong markers render by the re-read
parks; inconclusive re-read -> fail-open; disabled -> park immediately.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.session import shape_harvest
from src.session import px_challenge as pxc
from src.session.purchase_executor import PurchaseExecutor

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f'  ok   {name}')
    else:
        FAIL += 1; print(f'  FAIL {name}')

PDP = 'https://www.target.com/p/-/A-21516452'
FLASH  = {'px_container': True, 'px_iframe': False, 'press_hold': False, 'denied': False, 'title': 'Product not found', 'url': PDP}
REAL   = {'px_container': True, 'px_iframe': False, 'press_hold': True, 'denied': False, 'title': '', 'url': 'https://www.target.com/blocked'}
IFRAME = {'px_container': True, 'px_iframe': True, 'press_hold': False, 'denied': False, 'title': '', 'url': PDP}
CLEAR  = {'px_container': False, 'px_iframe': False, 'press_hold': False, 'denied': False, 'title': '', 'url': PDP}
# 2026-11-16 (C-1116-01): the real 11-16 Press & Hold -- container present AND populated, but the
# challenge renders in a cross-origin overlay so press_hold/denied/px_iframe all stay False and the
# top-doc title is still the product. ONLY px_container_filled separates it from the FLASH placeholder.
FILLED = {'px_container': True, 'px_container_filled': True, 'px_iframe': False, 'press_hold': False, 'denied': False, 'title': 'Tide Pods HE Compatible', 'url': PDP}
ON = {'px_reconfirm': True, 'px_reconfirm_delay_s': 0.0}
ON_FILLED = {'px_reconfirm': True, 'px_reconfirm_delay_s': 0.0, 'px_filled_strong': True}

def test_config_flags():
    c = shape_harvest.config({})
    check('px_reconfirm default OFF', c['px_reconfirm'] is False)
    check('delay default 2.5', abs(c['px_reconfirm_delay_s'] - 2.5) < 1e-9)
    check('px_reconfirm =1 ON', shape_harvest.config({'TARGET_HARVEST_PX_RECONFIRM': '1'})['px_reconfirm'] is True)
    check('delay floored at 0.5 (0 -> 0.5)', abs(shape_harvest.config({'TARGET_HARVEST_PX_RECONFIRM_DELAY_S': '0'})['px_reconfirm_delay_s'] - 0.5) < 1e-9)
    check('delay clamped <=10', shape_harvest.config({'TARGET_HARVEST_PX_RECONFIRM_DELAY_S': '999'})['px_reconfirm_delay_s'] <= 10.0)
    check('px_filled_strong default OFF', c['px_filled_strong'] is False)
    check('px_filled_strong =1 ON', shape_harvest.config({'TARGET_PX_FILLED_STRONG': '1'})['px_filled_strong'] is True)

def test_px_verdict_is_strong():
    check('strong: REAL True', pxc.px_verdict_is_strong(REAL) is True)
    check('strong: IFRAME True', pxc.px_verdict_is_strong(IFRAME) is True)
    check('strong: FLASH (container-only) False', pxc.px_verdict_is_strong(FLASH) is False)
    check('strong: CLEAR False', pxc.px_verdict_is_strong(CLEAR) is False)
    check('strong: non-dict False', pxc.px_verdict_is_strong('x') is False)
    # C-1116-01: filled-container discriminator -- flag-gated, and never downgrades a verdict
    check('strong: FILLED default(OFF) False', pxc.px_verdict_is_strong(FILLED) is False)
    check('strong: FILLED filled_counts=True True', pxc.px_verdict_is_strong(FILLED, filled_counts=True) is True)
    check('strong: FLASH filled_counts=True still False (no 11-06 regression)', pxc.px_verdict_is_strong(FLASH, filled_counts=True) is False)
    check('strong: REAL filled_counts=True still True', pxc.px_verdict_is_strong(REAL, filled_counts=True) is True)
    check('strong: CLEAR filled_counts=True False', pxc.px_verdict_is_strong(CLEAR, filled_counts=True) is False)

class FakeTab:
    def __init__(self, snaps, raise_on=None):
        self._snaps = list(snaps); self.evals = 0; self._raise_on = raise_on
    async def evaluate(self, js, **kw):
        self.evals += 1
        if self._raise_on is not None and self.evals == self._raise_on:
            raise TimeoutError('simulated re-read timeout')
        return self._snaps[min(self.evals - 1, len(self._snaps) - 1)]

def _make_exec(cfg):
    ex = object.__new__(PurchaseExecutor)
    ex._harvest_cfg = cfg; ex._harvest_tcin = '21516452'; ex._harvest_tab_nav_ts = 100.0
    ex._harvest_probe_nav_ts = None; ex._parked = []; ex._logs = []
    ex._harvest_log = lambda msg: ex._logs.append(str(msg))
    async def _no_shot(tab, info, px): return None
    ex._harvest_miss_screenshot = _no_shot
    ex._harvest_px_park = lambda desc: ex._parked.append(desc)
    return ex

def _run(snaps, cfg=ON, raise_on=None):
    ex = _make_exec(cfg); tab = FakeTab(snaps, raise_on=raise_on)
    out = asyncio.run(ex._harvest_miss_probe(tab))
    return ex, tab, out

def test_container_flash_rotates():
    ex, tab, out = _run([FLASH, CLEAR])
    check('container flash -> NOT parked', ex._parked == [])
    check('container flash -> 2 evals', tab.evals == 2)

def test_container_stuck_placeholder_rotates():
    ex, tab, out = _run([FLASH, FLASH])            # persists past the delay (cycle-6: no drain)
    check('container stuck -> NOT parked', ex._parked == [])
    check('container stuck -> returns False', out is False)

def test_container_becomes_strong_parks():
    ex, tab, out = _run([FLASH, REAL])             # a real wall whose strong markers render late
    check('container->strong -> parked', len(ex._parked) == 1)
    check('container->strong -> returns True', out is True)

def test_strong_persists_parks():
    ex, tab, out = _run([REAL, REAL])
    check('strong persists -> parked', len(ex._parked) == 1)

def test_iframe_persists_parks():
    ex, tab, out = _run([IFRAME, IFRAME])
    check('iframe persists -> parked', len(ex._parked) == 1)

def test_strong_flash_rotates():
    ex, tab, out = _run([REAL, CLEAR])
    check('strong flash -> NOT parked', ex._parked == [])

def test_strong_first_read_reread_error_parks():
    # Finding-2: a confirmed STRONG first read is NOT discarded on a re-read timeout -- fall back.
    ex, tab, out = _run([REAL], raise_on=2)
    check('strong first + re-read ERROR -> PARKED (fallback)', len(ex._parked) == 1)
    check('strong first + re-read ERROR -> returns True', out is True)
    check('strong first + re-read ERROR -> 2 evals attempted', tab.evals == 2)
    check('strong first + re-read ERROR -> logged parking', any('keeping the' in m for m in ex._logs))

def test_container_first_read_reread_error_fail_open():
    ex, tab, out = _run([FLASH], raise_on=2)
    check('container first + re-read ERROR -> NOT parked (fail-open)', ex._parked == [])
    check('container first + re-read ERROR -> logged fail-open', any('fail-open' in m for m in ex._logs))

def test_strong_first_read_nondict_reread_parks():
    # A non-dict re-read is inconclusive (like an error): a confirmed STRONG first read must
    # NOT be discarded -- fall back to it and park (matches the error-path contract).
    ex, tab, out = _run([REAL, 'not-a-dict'])
    check('strong first + non-dict re-read -> PARKED (fallback)', len(ex._parked) == 1)
    check('strong first + non-dict re-read -> returns True', out is True)
    check('strong first + non-dict re-read -> logged parking', any('keeping the' in m for m in ex._logs))
    check('strong first + non-dict re-read -> logged inconclusive', any('inconclusive' in m for m in ex._logs))

def test_container_first_read_nondict_reread_fail_open():
    ex, tab, out = _run([FLASH, 'not-a-dict'])
    check('container first + non-dict re-read -> NOT parked (fail-open)', ex._parked == [])
    check('container first + non-dict re-read -> logged fail-open', any('fail-open' in m for m in ex._logs))

def test_disabled_parks_immediately():
    ex, tab, out = _run([FLASH], cfg={'px_reconfirm': False, 'px_reconfirm_delay_s': 0.0})
    check('disabled -> parked immediately', len(ex._parked) == 1)
    check('disabled -> 1 eval', tab.evals == 1)

def test_filled_container_parks_only_when_flag_on():
    # C-1116-01 end-to-end through _harvest_miss_probe: a POPULATED container re-read parks ONLY
    # when px_filled_strong is set; with the flag off it stays fail-open (container-only), so the
    # staged rollout cannot re-introduce the 11-06 drain by default.
    ex, tab, out = _run([FILLED, FILLED], cfg=ON_FILLED)
    check('filled + flag ON -> parked', len(ex._parked) == 1)
    check('filled + flag ON -> returns True', out is True)
    ex2, tab2, out2 = _run([FILLED, FILLED], cfg=ON)
    check('filled + flag OFF -> NOT parked (fail-open)', ex2._parked == [])
    check('filled + flag OFF -> returns False', out2 is False)

def main():
    for t in (test_config_flags, test_px_verdict_is_strong, test_container_flash_rotates,
              test_container_stuck_placeholder_rotates, test_container_becomes_strong_parks,
              test_strong_persists_parks, test_iframe_persists_parks, test_strong_flash_rotates,
              test_strong_first_read_reread_error_parks, test_container_first_read_reread_error_fail_open,
              test_strong_first_read_nondict_reread_parks, test_container_first_read_nondict_reread_fail_open,
              test_disabled_parks_immediately, test_filled_container_parks_only_when_flag_on):
        t()
    print(f'\n{PASS} passed, {FAIL} failed')
    sys.exit(1 if FAIL else 0)

if __name__ == '__main__':
    main()
