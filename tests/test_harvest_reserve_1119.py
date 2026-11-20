#!/usr/bin/env python3
"""FX-1119-RESERVE offline test. No browser, no network, no account files.

The reserve is a second PDP document, loaded in CLEAN time and never reloaded, that mints
banked Shape sets while the primary harvest is PX-parked / backed off / buttonless. This pins:

  * flag OFF  -> config inert, tick never acts, stuck-reason unchanged (exact prior behaviour);
  * refresh runs ONLY in clean time (primary captured recently, not held, no purchase live),
    respects the retry gap for a dead reserve and the refresh age for a live one;
  * wall-mode mints only when the primary is held AND the reserve is usable AND the bank wants;
  * the reserve click path never navigates: a PX overlay / missing button / two no-POST clicks
    mark it dead instead;
  * a reserve capture is banked with meta src=reserve, is dropped when over the byte cap, and
    never resets the PRIMARY's miss / PX streaks;
  * the bank gate is not "stuck" while the reserve can mint;
  * the orphan closer never closes the reserve tab.

    python tests/test_harvest_reserve_1119.py
"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.session import shape_harvest as sh
from src.session.purchase_executor import PurchaseExecutor

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f'  ok   {name}')
    else:
        FAIL += 1; print(f'  FAIL {name}')


# ---------------------------------------------------------------- config ----
def test_config():
    c = sh.config({})
    check('reserve default OFF', c['reserve'] is False)
    check('refresh default 1800', abs(c['reserve_refresh_s'] - 1800.0) < 1e-9)
    check('clean default 600', abs(c['reserve_clean_s'] - 600.0) < 1e-9)
    check('retry default 600', abs(c['reserve_retry_s'] - 600.0) < 1e-9)
    check('cap default 15500', c['reserve_max_hdr_bytes'] == 15500)
    on = sh.config({'TARGET_HARVEST_RESERVE': '1', 'TARGET_HARVEST_RESERVE_REFRESH_S': '60',
                    'TARGET_HARVEST_RESERVE_MAX_HDR_BYTES': '0'})
    check('reserve =1 ON', on['reserve'] is True)
    check('refresh floored at 300', abs(on['reserve_refresh_s'] - 300.0) < 1e-9)
    check('cap 0 = no cap', on['reserve_max_hdr_bytes'] == 0)
    check('refresh clamped <= 14400', sh.config({'TARGET_HARVEST_RESERVE_REFRESH_S': '99999'})['reserve_refresh_s'] <= 14400.0)
    # every pre-existing key still present and unchanged by the new ones
    for k in ('px_filled_strong', 'px_reconfirm', 'fresh_page', 'bank', 'ttl_s'):
        check(f'pre-existing key {k} intact', k in c)


# ------------------------------------------------------- pure decisions ----
def test_refresh_due():
    f = sh.reserve_refresh_due
    now = 10_000.0
    # clean time, no document yet -> due
    due, why = f(now, 0.0, False, 0.0, now - 30, '', False, 1800, 600, 600)
    check('no doc + clean -> due', due is True and 'no reserve' in why)
    # held primary -> never (a load is what the wall catches)
    check('held -> not due', f(now, 0.0, False, 0.0, now - 30, 'PX park', False, 1800, 600, 600)[0] is False)
    # purchase live -> never
    check('live -> not due', f(now, 0.0, False, 0.0, now - 30, '', True, 1800, 600, 600)[0] is False)
    # primary never captured / captured too long ago -> not clean
    check('no primary capture -> not due', f(now, 0.0, False, 0.0, 0.0, '', False, 1800, 600, 600)[0] is False)
    check('stale primary capture -> not due', f(now, 0.0, False, 0.0, now - 601, '', False, 1800, 600, 600)[0] is False)
    # live document younger than refresh -> fresh
    check('fresh doc -> not due', f(now, now - 100, False, now - 100, now - 30, '', False, 1800, 600, 600)[0] is False)
    check('old doc -> due', f(now, now - 1801, False, now - 1801, now - 30, '', False, 1800, 600, 600)[0] is True)
    # dead document: retry gap then due
    check('dead + inside retry gap -> not due', f(now, now - 100, True, now - 100, now - 30, '', False, 1800, 600, 600)[0] is False)
    check('dead + past retry gap -> due', f(now, now - 700, True, now - 700, now - 30, '', False, 1800, 600, 600)[0] is True)
    # a failed open (no doc yet) also respects the retry gap
    check('no doc + recent failed try -> not due', f(now, 0.0, True, now - 100, now - 30, '', False, 1800, 600, 600)[0] is False)


def test_mint_wanted_and_set_ok():
    check('mint: held+alive+wants', sh.reserve_mint_wanted('PX park', True, True) is True)
    check('mint: not held -> no', sh.reserve_mint_wanted('', True, True) is False)
    check('mint: dead -> no', sh.reserve_mint_wanted('PX park', False, True) is False)
    check('mint: bank full -> no', sh.reserve_mint_wanted('PX park', True, False) is False)
    check('set_ok under cap', sh.reserve_set_ok(14000, 15500) is True)
    check('set_ok over cap', sh.reserve_set_ok(15501, 15500) is False)
    check('set_ok cap 0 = no cap', sh.reserve_set_ok(99999, 0) is True)
    check('set_ok garbage -> True', sh.reserve_set_ok('x', 'y') is True)


# -------------------------------------------------------- executor wiring ----
class FakeTarget:
    def __init__(self, tid, url=''):
        self.target_id = tid; self.url = url

class FakeTab:
    """evaluate() answers from a dict keyed by a JS marker; send() records CDP commands."""
    def __init__(self, tid='T-res', px=None, button=None, raise_eval=False):
        self.target = FakeTarget(tid, 'https://www.target.com/p/-/A-50225561')
        self.px = px if px is not None else {'px_container': False, 'px_container_filled': False, 'px_iframe': False,
                                              'press_hold': False, 'denied': False, 'url': self.target.url, 'title': 't'}
        self.button = button if button is not None else {'found': True, 'disabled': False, 'x': 100, 'y': 200, 'w': 80,
                                                          'h': 30, 'via': 'sel', 'text': 'Add to cart', 'ready': 'complete'}
        self.sends = []; self.gets = []; self.raise_eval = raise_eval
    async def evaluate(self, js, **kw):
        if self.raise_eval:
            raise TimeoutError('simulated')
        if 'px-captcha' in js and 'press_hold' in js and 'buttons' not in js:
            return dict(self.px)
        if 'addToCartButton' in js:
            return dict(self.button)
        if 'visibilityState' in js:
            return {'vis': 'visible', 'focus': True, 'raf_ms': 12}
        return None
    async def send(self, cmd):
        self.sends.append(cmd)
    async def get(self, url):
        self.gets.append(url)
    async def activate(self):
        pass

class FakeSM:
    def __init__(self):
        self.browser = object(); self.account_id = 'alt-1'; self._dead_session_parked_until = 0.0
    def is_purchase_in_progress(self):
        return False

def _make_exec(cfg_over=None, flag_on=True):
    ex = object.__new__(PurchaseExecutor)
    env = {'TARGET_HARVEST_RESERVE': '1' if flag_on else '0', 'TARGET_HARVEST_TCINS': '21516452,50225561,53274278',
           'TARGET_SHAPE_HARVEST': '1'}
    ex._harvest_cfg = sh.config(env)
    if cfg_over:
        ex._harvest_cfg.update(cfg_over)
    ex._shape_bank = sh.ShapeBank(6, 300.0)
    ex.session_manager = FakeSM()
    ex._logs = []
    ex._harvest_log = lambda msg: ex._logs.append(str(msg))
    ex._harvest_tab = None; ex._harvest_tcin = '21516452'; ex._harvest_tcin_idx = 0
    ex._harvest_tab_nav_ts = 0.0; ex._harvest_clicks_since_nav = 0
    ex._harvest_capture_evt = asyncio.Event()
    ex._harvest_stats = {'captured': 0, 'no_tokens': 0}
    ex._harvest_miss_streak = 5; ex._harvest_px_streak = 3; ex._harvest_px_park_nav_ts = 123.0
    ex._harvest_px_parked_until = 0.0; ex._harvest_backoff_until = 0.0; ex._harvest_disabled_reason = ''
    ex._harvest_first_capture_logged = True
    ex._harvest_landed_suspect = False
    ex._harvest_vis_state = ''; ex._harvest_vis_skips = 0
    ex._harvest_reserve_tab = None; ex._harvest_reserve_tcin = ''; ex._harvest_reserve_nav_ts = 0.0
    ex._harvest_reserve_dead_reason = ''; ex._harvest_reserve_last_try_ts = 0.0
    ex._harvest_reserve_last_click_ts = 0.0; ex._harvest_reserve_last_xy = None
    ex._harvest_reserve_clicks = 0; ex._harvest_reserve_nocap = 0; ex._harvest_reserve_wall_logged = ''
    ex._harvest_reserve_fail_streak = 0; ex._harvest_browser_ref = ex.session_manager.browser
    ex._harvest_reserve_browser_ref = ex.session_manager.browser
    ex._harvest_reserve_stats = {'opens': 0, 'captured': 0, 'oversized': 0, 'dead': 0, 'clicks': 0}
    ex._harvest_last_capture_ts = 0.0
    ex._woncart_quiet = lambda: False
    ex._harvest_quiet_now = lambda where: False
    return ex

SHAPE_HDRS = {'Cookie': 'a=b', 'Content-Type': 'application/json',
              'X-abcdefg-a': 'A' * 7000, 'X-abcdefg-b': 'bb', 'X-abcdefg-c': 'cc', 'X-abcdefg-d': 'dd'}

class FakeReq:
    def __init__(self, headers, method='POST'):
        self.headers = headers; self.method = method; self.post_data = '{"cart_item":{"tcin":"50225561"}}'
        self.has_post_data = True
class FakeEvent:
    def __init__(self, headers):
        self.request = FakeReq(headers); self.request_id = 'r1'


def test_capture_routes_reserve():
    ex = _make_exec()
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_tcin = '50225561'
    ex._harvest_reserve_nav_ts = time.time() - 50; ex._harvest_reserve_clicks = 7
    asyncio.run(ex._harvest_capture_and_block(res, FakeEvent(dict(SHAPE_HDRS)), 'https://carts.target.com/web_checkouts/v1/cart_items'))
    check('reserve capture banked', ex._shape_bank.count() == 1)
    ent = ex._shape_bank.pop_fresh()
    check('reserve meta src=reserve', ent['meta'].get('src') == 'reserve' and ent['meta'].get('tcin') == '50225561')
    check('reserve stats captured=1', ex._harvest_reserve_stats['captured'] == 1)
    check('CAPTURED line carries src=reserve + reserve clicks', any('CAPTURED' in l and 'src=reserve' in l and 'clicks_since_nav=7' in l for l in ex._logs))
    check('primary miss streak NOT reset by a reserve capture', ex._harvest_miss_streak == 5 and ex._harvest_px_streak == 3)
    check('primary clean-time stamp NOT set by a reserve capture', ex._harvest_last_capture_ts == 0.0)
    check('request was failed (blocked), never continued', any('fail' in type(s).__name__.lower() or 'FailRequest' in repr(s) for s in res.sends) or len(res.sends) == 1)

def test_capture_primary_sets_clean_stamp():
    ex = _make_exec()
    prim = FakeTab('T-prim'); ex._harvest_tab = prim
    ex._harvest_reserve_tab = FakeTab('T-res')
    asyncio.run(ex._harvest_capture_and_block(prim, FakeEvent(dict(SHAPE_HDRS)), 'https://carts.target.com/web_checkouts/v1/cart_items'))
    check('primary capture banked without src', ex._shape_bank.pop_fresh()['meta'].get('src') is None)
    check('primary capture resets streaks', ex._harvest_miss_streak == 0 and ex._harvest_px_streak == 0)
    check('primary capture stamps clean time', ex._harvest_last_capture_ts > 0)
    check('CAPTURED line has no src=reserve', not any('src=reserve' in l for l in ex._logs))

def test_capture_oversized_dropped():
    ex = _make_exec({'reserve_max_hdr_bytes': 5000})
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_tcin = '50225561'
    asyncio.run(ex._harvest_capture_and_block(res, FakeEvent(dict(SHAPE_HDRS)), 'https://carts.target.com/web_checkouts/v1/cart_items'))
    check('oversized reserve set NOT banked', ex._shape_bank.count() == 0)
    check('oversized counted + logged', ex._harvest_reserve_stats['oversized'] == 1 and any('oversized' in l for l in ex._logs))
    # the same set from the PRIMARY is banked (cap applies to reserve sets only)
    ex2 = _make_exec({'reserve_max_hdr_bytes': 5000}); prim = FakeTab('T-prim'); ex2._harvest_tab = prim
    asyncio.run(ex2._harvest_capture_and_block(prim, FakeEvent(dict(SHAPE_HDRS)), 'https://carts.target.com/web_checkouts/v1/cart_items'))
    check('cap does not apply to primary sets', ex2._shape_bank.count() == 1)


def _patch_click(ex, capture=True, banked=True):
    """Stand-in for human_click: `capture` fires the cart_items POST event; `banked` means the
    interceptor banked it (what _harvest_capture_and_block does on a token-bearing, in-cap set)."""
    async def _hc(tab, x, y, start=None, stats=None, move_abort_ms=None):
        if capture:
            ex._harvest_capture_evt.set()
            if banked:
                ex._harvest_reserve_stats['captured'] += 1
        return (x, y)
    return _hc

def test_reserve_once_mints():
    ex = _make_exec()
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_tcin = '50225561'
    ex._harvest_reserve_nav_ts = time.time() - 1000
    orig = sh.human_click; sh.human_click = _patch_click(ex)
    try:
        ok = asyncio.run(ex._harvest_reserve_once())
    finally:
        sh.human_click = orig
    check('reserve once -> True on a banked capture', ok is True)
    check('reserve clicks counted', ex._harvest_reserve_clicks == 1 and ex._harvest_reserve_stats['clicks'] == 1)
    check('reserve never navigated', res.gets == [])
    check('reserve still alive', ex._harvest_reserve_alive() is True)
    check('fail streak reset on a banked set', ex._harvest_reserve_fail_streak == 0)
    # a captured-but-NOT-banked set (oversized / token-less) is a failed attempt
    ex._harvest_capture_evt = asyncio.Event()
    sh.human_click = _patch_click(ex, capture=True, banked=False)
    try:
        ok2 = asyncio.run(ex._harvest_reserve_once())
    finally:
        sh.human_click = orig
    check('captured-but-not-banked -> False + fail streak 1', ok2 is False and ex._harvest_reserve_fail_streak == 1)


def test_reserve_fail_streak_kills():
    ex = _make_exec()
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    async def _fail(tab): return False
    ex._harvest_reserve_attempt = _fail
    outs = [asyncio.run(ex._harvest_reserve_once()) for _ in range(3)]
    check('3 failed attempts: still alive', outs == [False] * 3 and ex._harvest_reserve_alive() is True)
    asyncio.run(ex._harvest_reserve_once())
    check('4th failed attempt: dead', ex._harvest_reserve_alive() is False
          and 'consecutive attempts without a banked set' in ex._harvest_reserve_dead_reason)
    check('fail-max env honoured (min 2)', PurchaseExecutor._harvest_reserve_fail_max() >= 2)
    # an attempt that raises counts too, never escapes
    ex2 = _make_exec(); ex2._harvest_reserve_tab = FakeTab('T-res'); ex2._harvest_reserve_nav_ts = time.time() - 10
    async def _boom(tab): raise RuntimeError('boom')
    ex2._harvest_reserve_attempt = _boom
    check('raising attempt -> False, counted', asyncio.run(ex2._harvest_reserve_once()) is False and ex2._harvest_reserve_fail_streak == 1)


def test_reserve_quiet_check_is_right_before_click():
    ex = _make_exec()
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    order = []
    async def _vis(tab): order.append('vis'); return 'ok'
    ex._harvest_ensure_visible = _vis
    ex._harvest_quiet_now = lambda where: (order.append('quiet'), True)[1]
    clicked = []
    orig = sh.human_click
    async def _hc(*a, **k): clicked.append(1); return (0, 0)
    sh.human_click = _hc
    try:
        ok = asyncio.run(ex._harvest_reserve_once())
    finally:
        sh.human_click = orig
    check('quiet began after the vis guard -> no click', ok is False and clicked == [])
    check('quiet re-check runs AFTER ensure_visible', order == ['vis', 'quiet'])

def test_reserve_once_px_overlay_marks_dead():
    ex = _make_exec({'px_filled_strong': True})
    res = FakeTab('T-res', px={'px_container': True, 'px_container_filled': True, 'px_iframe': False, 'press_hold': False,
                               'denied': False, 'url': 'https://www.target.com/p/-/A-50225561', 'title': 'Tide'})
    ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    clicked = []
    orig = sh.human_click
    async def _hc(*a, **k): clicked.append(1); return (0, 0)
    sh.human_click = _hc
    try:
        ok = asyncio.run(ex._harvest_reserve_once())
    finally:
        sh.human_click = orig
    check('PX overlay -> False', ok is False)
    check('PX overlay -> dead, no click, no nav', ex._harvest_reserve_dead_reason.startswith('PX overlay') and clicked == [] and res.gets == [])
    check('dead counted once', ex._harvest_reserve_stats['dead'] == 1)
    # bare placeholder (flash) with filled_strong OFF is NOT a wall -> the click proceeds
    ex2 = _make_exec({'px_filled_strong': False})
    res2 = FakeTab('T-res', px={'px_container': True, 'px_container_filled': False, 'px_iframe': False, 'press_hold': False,
                                'denied': False, 'url': 'https://www.target.com/p/-/A-50225561', 'title': 'Tide'})
    ex2._harvest_reserve_tab = res2; ex2._harvest_reserve_nav_ts = time.time() - 10
    sh.human_click = _patch_click(ex2)
    try:
        ok2 = asyncio.run(ex2._harvest_reserve_once())
    finally:
        sh.human_click = orig
    check('bare placeholder is not a wall for the reserve either', ok2 is True)

def test_reserve_once_button_absent_marks_dead():
    ex = _make_exec()
    res = FakeTab('T-res', button={'found': False, 'disabled': True, 'ready': 'complete', 'oos': False})
    ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    ok = asyncio.run(ex._harvest_reserve_once())
    check('button absent -> False + dead', ok is False and 'absent' in ex._harvest_reserve_dead_reason)
    check('button absent -> no nav', res.gets == [])

def test_reserve_once_two_nocap_marks_dead():
    ex = _make_exec()
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    orig = sh.human_click; sh.human_click = _patch_click(ex, capture=False)
    # shorten the 4 s capture wait by pre-expiring: patch asyncio.wait_for on the event only via a tiny timeout
    import src.session.purchase_executor as pe
    real_wait_for = pe.asyncio.wait_for
    async def _fast_wait_for(aw, timeout=None):
        if timeout == 4.0:
            timeout = 0.01
        return await real_wait_for(aw, timeout=timeout)
    pe.asyncio.wait_for = _fast_wait_for
    try:
        r1 = asyncio.run(ex._harvest_reserve_once())
        alive_after_1 = ex._harvest_reserve_alive()
        ex._harvest_capture_evt = asyncio.Event()      # a fresh Event per event loop
        r2 = asyncio.run(ex._harvest_reserve_once())
    finally:
        sh.human_click = orig; pe.asyncio.wait_for = real_wait_for
    check('1st no-POST click: not dead yet', r1 is False and alive_after_1 is True)
    check('2nd no-POST click: dead', r2 is False and 'no cart_items POST' in ex._harvest_reserve_dead_reason)
    check('no nav on no-POST', res.gets == [])

def test_reserve_once_flag_off_inert():
    ex = _make_exec(flag_on=False)
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    check('flag off -> reserve never alive', ex._harvest_reserve_alive() is False)
    ok = asyncio.run(ex._harvest_reserve_once())
    check('flag off -> once is a no-op', ok is False and res.gets == [] and ex._logs == [])
    handled = asyncio.run(ex._harvest_reserve_tick(False))
    check('flag off -> tick returns False, logs nothing', handled is False and ex._logs == [])


def test_stuck_reason():
    ex = _make_exec()
    ex._harvest_px_parked_until = time.time() + 300
    check('held + no reserve -> stuck "PX park"', ex.harvest_stuck_reason() == 'PX park')
    ex._harvest_reserve_tab = FakeTab('T-res'); ex._harvest_reserve_nav_ts = time.time() - 10
    check('held + reserve alive -> NOT stuck', ex.harvest_stuck_reason() == '')
    ex._harvest_reserve_dead_reason = 'PX overlay'
    check('held + reserve dead -> stuck again', ex.harvest_stuck_reason() == 'PX park')
    ex._harvest_px_parked_until = 0.0; ex._harvest_reserve_dead_reason = ''
    ex._harvest_miss_streak = 3
    check('buttonless primary + reserve alive -> NOT stuck', ex.harvest_stuck_reason() == '')
    ex._harvest_disabled_reason = 'x'
    check('disabled still wins over a live reserve', ex.harvest_stuck_reason().startswith('disabled'))
    off = _make_exec(flag_on=False); off._harvest_px_parked_until = time.time() + 300
    off._harvest_reserve_tab = FakeTab('T-res'); off._harvest_reserve_nav_ts = time.time() - 10
    check('flag off -> stuck reason unchanged (PX park)', off.harvest_stuck_reason() == 'PX park')


def test_tick_refresh_then_wallmode():
    ex = _make_exec({'reserve_refresh_s': 300.0, 'reserve_clean_s': 600.0, 'reserve_retry_s': 300.0})
    ex._harvest_miss_streak = 0            # the fixture's streak=5 would itself count as "blocked"
    calls = []
    async def _refresh():
        calls.append('refresh'); ex._harvest_reserve_tab = FakeTab('T-res'); ex._harvest_reserve_nav_ts = time.time()
        ex._harvest_reserve_browser_ref = ex.session_manager.browser; return True
    async def _once():
        calls.append('once'); return True
    ex._harvest_reserve_refresh = _refresh; ex._harvest_reserve_once = _once
    import src.session.purchase_executor as pe
    real_sleep = pe.asyncio.sleep
    async def _nosleep(s): return None
    pe.asyncio.sleep = _nosleep
    try:
        # not clean (primary never captured) -> nothing
        check('tick: not clean -> no refresh', asyncio.run(ex._harvest_reserve_tick(False)) is False and calls == [])
        # clean -> refresh
        ex._harvest_last_capture_ts = time.time()
        check('tick: clean + no doc -> refresh', asyncio.run(ex._harvest_reserve_tick(False)) is True and calls == ['refresh'])
        # primary free, doc fresh -> nothing (no minting while the primary is free)
        check('tick: primary free -> no mint', asyncio.run(ex._harvest_reserve_tick(False)) is False and calls == ['refresh'])
        # primary parked + bank empty -> wall-mode click (no refresh while held)
        ex._harvest_px_parked_until = time.time() + 300
        check('tick: held -> wall-mode once', asyncio.run(ex._harvest_reserve_tick(False)) is True and calls == ['refresh', 'once'])
        check('tick: wall-mode announced once', sum('wall-mode' in l for l in ex._logs) == 1)
        # min gap between reserve clicks
        check('tick: min gap -> no second click', asyncio.run(ex._harvest_reserve_tick(False)) is False and calls == ['refresh', 'once'])
        ex._harvest_reserve_last_click_ts = 0.0
        asyncio.run(ex._harvest_reserve_tick(False))
        check('tick: announcement not repeated for the same hold', sum('wall-mode' in l for l in ex._logs) == 1 and calls.count('once') == 2)
        # bank full -> no mint
        for _ in range(6):
            ex._shape_bank.push(dict(SHAPE_HDRS))
        ex._harvest_reserve_last_click_ts = 0.0
        check('tick: bank full -> no mint', asyncio.run(ex._harvest_reserve_tick(False)) is False and calls.count('once') == 2)
        # buttonless primary (no park) also mints from the reserve -- but does NOT pre-empt the
        # primary: the tick returns False so the loop falls through to the primary path.
        ex._harvest_px_parked_until = 0.0; ex._shape_bank.clear(); ex._harvest_miss_streak = 2
        ex._harvest_reserve_last_click_ts = 0.0; ex._harvest_reserve_wall_logged = ''
        check('tick: buttonless primary -> reserve click WITHOUT pre-emption',
              asyncio.run(ex._harvest_reserve_tick(False)) is False and calls.count('once') == 3)
        check('tick: buttonless primary blocks refresh too', calls.count('refresh') == 1)
        # a real park DOES pre-empt (the primary could do nothing while parked anyway)
        ex._harvest_px_parked_until = time.time() + 300; ex._harvest_reserve_last_click_ts = 0.0
        check('tick: real park -> pre-empts', asyncio.run(ex._harvest_reserve_tick(False)) is True and calls.count('once') == 4)
        # dead reserve while held -> no click, no refresh
        ex._harvest_reserve_dead_reason = 'PX overlay'; ex._harvest_reserve_last_click_ts = 0.0
        check('tick: dead reserve + held -> nothing', asyncio.run(ex._harvest_reserve_tick(False)) is False and calls.count('once') == 4 and calls.count('refresh') == 1)
        # Chrome relaunch while held: the reserve handle is dropped, nothing waits on it. The
        # comparison is against the RESERVE's own Chrome, not the primary's (possibly stale) ref.
        ex._harvest_reserve_dead_reason = ''; ex._harvest_browser_ref = object()   # primary ref stale: NOT enough
        ex._harvest_reserve_last_click_ts = 0.0
        _r = asyncio.run(ex._harvest_reserve_tick(False))
        check('tick: stale PRIMARY ref alone does not drop the reserve (it still mints under the park)',
              _r is True and ex._harvest_reserve_tab is not None and calls.count('once') == 5)
        ex.session_manager.browser = object()                                      # the live Chrome really changed
        check('tick: browser changed -> reserve dropped', asyncio.run(ex._harvest_reserve_tick(False)) is False
              and ex._harvest_reserve_tab is None and ex._harvest_reserve_nav_ts == 0.0 and ex._harvest_reserve_alive() is False)
        check('tick: dropped reserve -> stuck reason is the park again', ex.harvest_stuck_reason() == 'PX park')
        check('tick: drop resets the fail streak', ex._harvest_reserve_fail_streak == 0)
    finally:
        pe.asyncio.sleep = real_sleep


def test_loop_decides_primary_before_reserve():
    """Re-verifier D2: the primary's want/need must be computed BEFORE the reserve tick so a
    reserve set that fills the bank cannot skip the primary's turn in streak-only mode."""
    import inspect
    src = inspect.getsource(PurchaseExecutor._harvest_loop)
    i_want = src.find("want = need > 0 or self._shape_bank.refill_wanted()")
    i_tick = src.find("await self._harvest_reserve_tick(live)")
    i_once = src.find("ok = await self._harvest_once()")
    check('loop: want computed before the reserve tick', 0 < i_want < i_tick)
    check('loop: reserve tick before the primary click', i_tick < i_once)


def test_reserve_stand_down_not_a_failure():
    ex = _make_exec()
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    ex._harvest_quiet_now = lambda where: True
    ok = asyncio.run(ex._harvest_reserve_once())
    check('quiet stand-down -> False, streak unchanged', ok is False and ex._harvest_reserve_fail_streak == 0)
    # a dead-session-parked account ('skip' from the visibility guard) is not a strike either
    ex2 = _make_exec(); ex2._harvest_reserve_tab = FakeTab('T-res'); ex2._harvest_reserve_nav_ts = time.time() - 10
    async def _skip(tab): return 'skip'
    ex2._harvest_ensure_visible = _skip
    check('vis skip -> not a strike', asyncio.run(ex2._harvest_reserve_once()) is False and ex2._harvest_reserve_fail_streak == 0)
    async def _hidden(tab): return 'hidden'
    ex2._harvest_ensure_visible = _hidden
    check('vis hidden -> a strike', asyncio.run(ex2._harvest_reserve_once()) is False and ex2._harvest_reserve_fail_streak == 1)


def test_alive_requires_live_browser():
    ex = _make_exec()
    ex._harvest_reserve_tab = FakeTab('T-res'); ex._harvest_reserve_nav_ts = time.time() - 10
    check('alive on its own Chrome', ex._harvest_reserve_alive() is True)
    ex.session_manager.browser = object()
    check('NOT alive once the live Chrome changed (no tick needed)', ex._harvest_reserve_alive() is False)
    ex._harvest_px_parked_until = time.time() + 300
    check('stale reserve does not hold the gate', ex.harvest_stuck_reason() == 'PX park')


def test_refresh_resets_fail_streak_and_closes_dead_tab():
    class Conn:
        def __init__(self): self.closed = []
        async def send(self, cmd):
            try: self.closed.append(str(cmd.gi_frame.f_locals.get('target_id')))
            except Exception: self.closed.append('?')
    class B:
        def __init__(self): self.connection = Conn(); self.targets = []
        async def get(self, url, new_tab=False): return FakeTab('T-new')
    ex = _make_exec(); b = B(); ex.session_manager.browser = b; ex._harvest_last_capture_ts = time.time()
    async def _setup_ok(tab, persistent=False, label=''):
        from zendriver import cdp as _cdp
        tab.handlers = {_cdp.fetch.RequestPaused: [object()]}
    ex._setup_cdp_fetch_interceptor = _setup_ok
    # D1: a revived reserve starts with a clean streak
    ex._harvest_reserve_fail_streak = 3
    ok = asyncio.run(ex._harvest_reserve_refresh())
    check('refresh -> usable + streak reset + browser ref set', ok is True and ex._harvest_reserve_fail_streak == 0
          and ex._harvest_reserve_browser_ref is b)
    check('refresh selected the shipping cell once', any('fulfillment cell' in l for l in ex._logs))
    # D8: an EXISTING tab that cannot navigate is closed and forgotten, so the next refresh opens anew
    tab = ex._harvest_reserve_tab
    async def _bad_get(url): raise RuntimeError('nav boom')
    tab.get = _bad_get
    ex._harvest_reserve_nav_ts = time.time() - 99999
    ok2 = asyncio.run(ex._harvest_reserve_refresh())
    check('existing tab load failure -> closed + handle None', ok2 is False and ex._harvest_reserve_tab is None
          and b.connection.closed == ['T-new'])
    check('load failure respects the retry gap', ex._harvest_reserve_last_try_ts > 0)
    # D3: close_tab closes on the reserve's own Chrome then drops
    ex2 = _make_exec(); b2 = B(); ex2._harvest_reserve_tab = FakeTab('T-res2'); ex2._harvest_reserve_browser_ref = b2
    ex2._harvest_reserve_nav_ts = time.time(); ex2._harvest_reserve_fail_streak = 2
    asyncio.run(ex2._harvest_reserve_close_tab('test'))
    check('close_tab -> close_target on its Chrome + handle dropped + streak reset',
          b2.connection.closed == ['T-res2'] and ex2._harvest_reserve_tab is None and ex2._harvest_reserve_fail_streak == 0)


def test_oversized_not_counted_in_primary_stats():
    ex = _make_exec({'reserve_max_hdr_bytes': 5000})
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_tcin = '50225561'
    asyncio.run(ex._harvest_capture_and_block(res, FakeEvent(dict(SHAPE_HDRS)), 'https://carts.target.com/web_checkouts/v1/cart_items'))
    check('oversized reserve set leaves the primary stats untouched', ex._harvest_stats == {'captured': 0, 'no_tokens': 0})


def test_suspect_clear_falls_back_to_reserve():
    ex = _make_exec()
    ex._harvest_tab = None; ex._held_cart = None
    res = FakeTab('T-res'); ex._harvest_reserve_tab = res; ex._harvest_reserve_nav_ts = time.time() - 10
    cleared = []
    async def _clear(tab): cleared.append(tab); return True
    ex._clear_cart = _clear
    ex._harvest_landed_suspect = True
    asyncio.run(ex._harvest_clear_suspect_cart())
    check('clear ran on the reserve tab when the primary is None', cleared == [res])
    check('flag cleared after the clear', ex._harvest_landed_suspect is False)
    check('reserve marked dead after being used to clear', ex._harvest_reserve_alive() is False and 'suspect-cart' in ex._harvest_reserve_dead_reason)
    # no tab at all: the flag is KEPT so a later pass retries
    ex2 = _make_exec(); ex2._harvest_tab = None; ex2._harvest_landed_suspect = True
    asyncio.run(ex2._harvest_clear_suspect_cart())
    check('no tab -> flag kept for a retry', ex2._harvest_landed_suspect is True)


def test_refresh_refuses_uninstalled_interceptor():
    ex = _make_exec()
    ex._harvest_last_capture_ts = time.time()
    closed = []
    class Conn:
        async def send(self, cmd):
            try: closed.append(str(cmd.gi_frame.f_locals.get('target_id')))
            except Exception: closed.append('?')
    class B:
        def __init__(self): self.connection = Conn(); self.targets = []
        async def get(self, url, new_tab=False): return FakeTab('T-new')
    ex.session_manager.browser = B()
    async def _setup(tab, persistent=False, label=''): return None      # installs NOTHING
    ex._setup_cdp_fetch_interceptor = _setup
    ok = asyncio.run(ex._harvest_reserve_refresh())
    check('no handler -> not used, closed, dead', ok is False and ex._harvest_reserve_tab is None
          and closed == ['T-new'] and 'interceptor' in ex._harvest_reserve_dead_reason)
    check('dead counted in stats', ex._harvest_reserve_stats['dead'] == 1)
    # with a handler installed the document is verified and becomes usable
    ex2 = _make_exec(); ex2.session_manager.browser = B()
    async def _setup_ok(tab, persistent=False, label=''):
        from zendriver import cdp as _cdp
        tab.handlers = {_cdp.fetch.RequestPaused: [object()]}
    ex2._setup_cdp_fetch_interceptor = _setup_ok
    ok2 = asyncio.run(ex2._harvest_reserve_refresh())
    check('handler installed + button found -> usable', ok2 is True and ex2._harvest_reserve_alive() is True
          and ex2._harvest_reserve_tcin != ex2._harvest_tcin)


def test_orphan_closer_keeps_reserve():
    ex = _make_exec()
    class T:
        def __init__(self, tid, url): self.type_ = 'page'; self.target = FakeTarget(tid, url)
    class Conn:
        def __init__(self): self.closed = []
        async def send(self, cmd):
            # cdp.target.close_target(...) is a not-yet-started generator; its target_id is a local
            try:
                self.closed.append(str(cmd.gi_frame.f_locals.get('target_id')))
            except Exception:
                self.closed.append(repr(cmd))
    class B:
        def __init__(self):
            self.targets = [T('T-res', 'https://www.target.com/p/tide/-/A-50225561'),
                            T('T-orphan', 'https://www.target.com/p/tide/-/A-50225561'),
                            T('T-prim', 'https://www.target.com/p/tide/-/A-50225561')]
            self.connection = Conn()
    b = B()
    ex._harvest_reserve_tab = FakeTab('T-res')
    n = asyncio.run(ex._harvest_close_orphans(b, '/A-50225561', 'T-prim'))
    check('orphan closer closes exactly the orphan', n == 1 and len(b.connection.closed) == 1 and 'T-orphan' in b.connection.closed[0])
    check('orphan closer keeps the reserve and the primary', not any('T-res' in c or 'T-prim' in c for c in b.connection.closed))


def main():
    for t in (test_config, test_refresh_due, test_mint_wanted_and_set_ok, test_capture_routes_reserve,
              test_capture_primary_sets_clean_stamp, test_capture_oversized_dropped, test_reserve_once_mints,
              test_reserve_once_px_overlay_marks_dead, test_reserve_once_button_absent_marks_dead,
              test_reserve_once_two_nocap_marks_dead, test_reserve_once_flag_off_inert, test_stuck_reason,
              test_tick_refresh_then_wallmode, test_orphan_closer_keeps_reserve, test_reserve_fail_streak_kills,
              test_reserve_quiet_check_is_right_before_click, test_suspect_clear_falls_back_to_reserve,
              test_refresh_refuses_uninstalled_interceptor, test_loop_decides_primary_before_reserve,
              test_reserve_stand_down_not_a_failure, test_refresh_resets_fail_streak_and_closes_dead_tab,
              test_oversized_not_counted_in_primary_stats, test_alive_requires_live_browser):
        print(f'== {t.__name__}')
        t()
    print(f'\n{PASS}/{PASS + FAIL} passed')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
