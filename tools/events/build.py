#!/usr/bin/env python3
"""Structured event store for the bot's run logs (logs/runs/run_*.log -> SQLite).

    python tools/events/build.py [--db PATH] [--only run_20260930_014647] [--rebuild] [--quiet]

One tested parser, so a post-mortem is a SQL query instead of a fresh regex over a
12-60 MB text log. Read-only over the logs; stdlib only; never touches the bot, a
browser or the network. Incremental: a file is re-parsed only when its size, mtime
or PARSER_VERSION changed (its old rows are deleted first). Query it with
tools/events/q.py.

LOG FORMAT RULES THIS PARSER IS BUILT ON (all seen in real logs)
  * Two writers share one stream. print() lines carry no timestamp; logger lines
    start "YYYY-MM-DD HH:MM:SS,mmm LEVEL name: ". A print() writes its text and its
    newline in two writes, so another thread's logger line often lands between them
    ("...ident=primary2026-09-16 02:22:48,960 INFO ..."). Every physical line is
    therefore split at EVERY embedded logger timestamp before anything is matched:
    the text before the first stamp is print text, each stamp starts a logger
    segment. Two prints can also glue ("...ident=business[PURCHASE] ..."), so every
    field regex is bounded (ident values stop at '[' / whitespace / a glued date,
    and are validated against the run's [WORKER_POOL] account list).
  * Many events are written twice (print + logger copy). ONE form is used per marker:
        [ATC_RESP]        logger form when this file's logger copies carry tab=
                          (TARGET_ATC_RESP_LABEL armed, 09-22 on) -- it has the tab AND
                          a millisecond stamp; otherwise the print form
                          "[INTERCEPTOR:<tab>] [ATC_RESP]" (the only form naming the
                          tab before 09-22). Chosen per file; runs.atc_resp_form says
                          which; the other form's count is kept in `checks`.
        [ATC_RESP_HDRS]   logger form.   [HARVEST/<acct>] REPLAY/bank EMPTY  logger form.
        [STOCK][FLIP], [STOCK STATS]      logger (they have no print copy).
        [RACE], [FAST_LANE], [EXPOSURE], [FS_TICKET], [WON_CART_DIRECT], [STOCK] IN
        STOCK, [API_CYCLE], [PURCHASE] ATC fetch, [FORWARDER] W.., [WORKER_POOL],
        order lines                        print (they have no logger copy).
    A marker found in the other kind of segment is counted in `unparsed` as
    "<MARKER>@wrong_form" so a format change cannot hide.
  * Print-only events have no time of their own: ts_ms is the last logger stamp before
    them (ts_src says so); a shot's ts_ms is its own atc_t0 when the chain line has one.
  * Logger stamps are local wall time; atc_t0/read_ms are epoch ms. The UTC offset is
    measured per file from lines that carry both (flip read_ms, chain atc_t0), rounded
    to 15 min; runs.tz_src says how it was obtained.

SHOTS (one row per main-tab add-to-cart request)
  anchors: "[FAST_LANE] chain done ... atc=<status> ... ident=" (src=chain); a fast-lane
  evaluate timeout / raise with no chain line (src=orphan: no response, gate unknown);
  an ident-tagged "[PURCHASE] ATC fetch" outcome with no pending chain (src=legacy).
  Every main-tab [ATC_RESP] is joined to one anchor of the SAME status:
    ts / ts_loose  global greedy on |logger_ts - (atc_t0 + atc_rt)| <= 2 s (|dt| <= 10 ms
                   = 'ts'), when the chain has atc_t0 and the file uses the logger form
                   (09-22+). It looks both ways: the logger copy of a shot's own
                   [ATC_RESP] can land AFTER its chain line (09-25 L53930, glued to the
                   chain print's tail). Two responses stamped in the same ms with the
                   same key are a tie; FIFO decides and no gate depends on it.
    seq / seq1     otherwise the nearest preceding pending response (seq_after: within
                   50 lines after, when none precedes).
    seq_hint       same-status candidates disagree on the key, and the anchor's own
                   [PURCHASE] ATC fetch body decides (''=empty -> ERR_A2C; a
                   DCO_RATE_LIMITED body -> FAST_SELLING; checks.body_hint_vs_key_429
                   measures that rule wherever both exist).
    ambiguous      it cannot decide: key NULL (gate unknown), and every candidate is
                   tainted so the leftover is not handed to the next anchor as certain.
  A response no anchor claims becomes its own row (src=resp_only, ident NULL). Nothing is
  dropped: shots(chain+legacy+orphan) = anchors, joined + resp_only = main [ATC_RESP]
  lines, and checks.legacy_fire_vs_legacy_shots reconciles legacy shots against the
  legacy path's own "[PURCHASE] Firing ATC fetch" lines.
  race: the open [RACE] whose worker list holds the ident and whose "k/N accounts done"
  has not yet named it (an account is in one race at a time). shot_idx: 1-based per
  (race, ident) in log order.

GATE (never guessed; 'unknown' is counted, not folded into a bucket)
  cart          200/201
  wall1_limited 429 + tgt-cart-error-key ERR_A2C_TCIN_RATE_LIMITED (the limiter)
  admitted_fs   429 + FAST_SELLING / DCO_RATE_LIMITED (past both walls, cart throttle)
  wall2_denied  401 (past the limiter, denied at the SSX hop)
  inventory     424
  other         any other status (503, 431, 400, 0 ...)
  unknown       no status (no response) or a 429 whose key is missing / unrecognised
                (logs before 2026-08-27 have no [ATC_RESP], so their 429s are unknown;
                shots.body_hint keeps the [PURCHASE] body class for those)

ORDERS  "*** ORDER PLACED ***" / "[API_PLACE_ORDER] Order placed" (tools/analysis/funnel.py's
  definition), one row per order_id. Buyer: the thread's "Purchase execution completed ...
  'order_id'" line, then its "Marked thread as completing: <tcin>#W<n>" (W<n> -> ident via
  the [RACE] labels); fallback the next unused race-done line whose newest entry is
  'purchased' (orders.ident_src says which).

WINDOWS  a window opens at a [STOCK][FLIP] new_window=1 line and runs until that TCIN's
  next new_window=1 flip (stock_check_resilient.py:136-144 / :844). Races take the
  window of their TCIN that contains their start (first shot atc_t0); the earliest race
  of a window is flip_opened. Runs with no flip lines get NULL (unknown), not 0.
"""
from __future__ import annotations

import argparse
import calendar
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

PARSER_VERSION = 1

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / 'logs' / 'runs'
DEFAULT_DB = ROOT / 'logs' / 'events' / 'events.sqlite'

# ---------------------------------------------------------------------------------
# Line splitting
# ---------------------------------------------------------------------------------
LOGGER_RE = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) '
                       r'(?:DEBUG|INFO|WARNING|ERROR|CRITICAL) (?:([\w.\-]+): )?')
_NAIVE_CACHE: dict = {}


def naive_ms(stamp: str, ms: str) -> int:
    """'2026-09-30 02:31:23' + '538' -> wall-clock ms as if the wall clock were UTC."""
    base = _NAIVE_CACHE.get(stamp)
    if base is None:
        base = calendar.timegm((int(stamp[0:4]), int(stamp[5:7]), int(stamp[8:10]),
                                int(stamp[11:13]), int(stamp[14:16]), int(stamp[17:19]),
                                0, 0, 0)) * 1000
        _NAIVE_CACHE[stamp] = base
    return base + int(ms)


def split_segments(line: str):
    """[(kind, text, naive_ms|None, stamp_text|None)] -- kind 'print' or 'logger'.
    A physical line is cut at every embedded logger timestamp (glued lines)."""
    ms = list(LOGGER_RE.finditer(line))
    if not ms:
        return [('print', line, None, None)] if line else []
    out = []
    if ms[0].start() > 0:
        out.append(('print', line[:ms[0].start()], None, None))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(line)
        out.append(('logger', line[m.end():end], naive_ms(m.group(1), m.group(2)),
                    m.group(1) + ',' + m.group(2)))
    return out


# ---------------------------------------------------------------------------------
# Field regexes (bounded: every value stops at whitespace or a glued '[')
# ---------------------------------------------------------------------------------
IDENT = r'(?:W\d+/)?(?P<ident>[A-Za-z][A-Za-z0-9_]*?(?:-\d+)?)(?=\d{4}-\d\d-\d\d|[\s\[\],]|$)'
IDENT_RE = re.compile(r'\bident=' + IDENT)
TCIN = r'\d{6,12}(?!\d)'

RACE_START = re.compile(r'\[RACE\] (?P<tcin>' + TCIN + r'): racing (?P<n>\d+) accounts? → \[(?P<workers>[^\]]*)\]')
RACE_DONE = re.compile(r'\[RACE\] (?P<tcin>' + TCIN + r'): (?P<k>\d+)/(?P<n>\d+) accounts? done, '
                       r'units_bought=(?P<units>\d+), breakdown=\{(?P<bd>[^{}]*)\}')
BD_ENTRY = re.compile(r"'W(\d+)/([^']+)': (?:'([^']*)'|(\w+))")
WORKER = re.compile(r"'W(\d+)/([^']+)'")
FIRE = re.compile(r'\[FAST_LANE\] Firing ATC\S* chain \(tcin=(?P<tcin>' + TCIN + r'), qty=(?P<qty>\d+)\)')
CHAIN = re.compile(r'\[FAST_LANE\] chain done in (?P<dur>[\d.]+)s — atc=(?P<atc>[\w-]+) pre=(?P<pre>[\w-]+) '
                   r'po=(?P<po>[\w-]+) skip=(?P<skip>[^\s\[]+)(?P<rest>[^\[]*)')
T0_RE = re.compile(r'\batc_t0=(\d{13}|-)')
RT_RE = re.compile(r'\batc_rt=(\d+|-)')
TO_ABORT = re.compile(r'\[FAST_LANE\] evaluate timed out after \d+s — stage=(?P<stage>\S+) atc=(?P<atc>\S+) '
                      r'ABORTED before any place-order \(skip=(?P<skip>[^)]*)\)(?P<rest>[^\[]*)')
TO_NOTREL = re.compile(r'\[FAST_LANE\] evaluate timed out after \d+s — stage read .*?\(not relabelled\)(?P<rest>[^\[]*)')
TO_QTY = re.compile(r'\[FAST_LANE\] evaluate timed out after \d+s — ATC-stage abort NOT relabelled')
TO_UNKNOWN = re.compile(r'\[FAST_LANE\] evaluate timed out after \d+s — place-order state UNKNOWN')
EV_RAISED = re.compile(r'\[FAST_LANE\] evaluate raised: .*? — place-order state UNKNOWN')
EV_BADTYPE = re.compile(r'\[FAST_LANE\] unexpected result type')

RESP_BODY = (r'\[ATC_RESP\] status=(?P<status>\d+|None) method=(?P<method>\w+) '
             r'tgt-cart-error-key=(?P<key>[^\s\[]*) x-request-id=(?P<xrid>[^\s\[]*) url=cart_items(?P<rest>[^\[]*)')
ATC_RESP_P = re.compile(r'\[INTERCEPTOR:(?P<ptab>[\w-]+)\] ' + RESP_BODY)
ATC_RESP_L = re.compile(RESP_BODY)
ENVOY_RE = re.compile(r'\benvoy_ms=([^\s|]+)')
TAB_RE = re.compile(r'\btab=([\w-]+)')
SELFTEST_RE = re.compile(r'\bselftest=(on|off)')
HDRS = re.compile(r'\[ATC_RESP_HDRS\] tab=(?P<tab>[\w-]+) status=(?P<status>\d+|None) '
                  r'(?:n=(?P<n>\d+)(?P<rest>.*)|unformattable)')

FLIP = re.compile(r'\[STOCK\]\[FLIP\] tcin=(?P<tcin>' + TCIN + r') #(?P<n>\d+) read_ms=(?P<read_ms>\d+) '
                  r'rt_ms=(?P<rt>\S+) last_oos_ms=(?P<oos>\d+) since_oos_ms=(?P<since>\S+) '
                  r'new_window=(?P<nw>[01]) via=(?P<via>\S+) \((?P<net>[^)]*)\) status=(?P<status>\S+)')
FLIP_CAP = re.compile(r'\[STOCK\]\[FLIP\] tcin=(?P<tcin>' + TCIN + r'): (?P<cap>\d+) flips logged this run')
FLIP_ERR = re.compile(r'\[STOCK\]\[FLIP\] tcin=\S*?: (?:could not format|not logged)')
INSTOCK = re.compile(r'\[STOCK\] IN STOCK: (?P<list>\d{6,12}(?:, \d{6,12})*)(?!\d)')
API_CYCLE_IN = re.compile(r'\[(?P<hms>\d\d:\d\d:\d\d)\] \[API_CYCLE\] IN STOCK: \[(?P<list>[^\]]*)\]')
STATS = re.compile(r'\[STOCK STATS\] t=(?P<t>[\d.]+)s sweeps=(?P<sweeps>\d+) \([\d.]+/s\) 200=(?P<s200>\d+) '
                   r'403=(?P<s403>\d+)(?: 429=(?P<s429>\d+))? other=(?P<other>\d+)(?: beh=(?P<beh>\d+))? '
                   r'outstanding=(?P<out>\d+)')
EXPOSURE = re.compile(r'\[EXPOSURE\] ident=(?P<ident>\S+?) tcin=(?P<tcin>\S+?) kind=(?P<kind>\S+?) '
                      r'run_shots=(?P<rs>\d+) run_s=(?P<rsec>\d+) win_age_s=(?P<wa>[\d-]+) '
                      r'chrome_age_s=(?P<ca>[\d-]+) proxied=(?P<prox>yes|no|-) resting=(?P<resting>yes|no)')
FS_TICKET = re.compile(r'\[FS_TICKET\] ident=(?P<ident>\S+?) tcin=(?P<tcin>\S+?) cart_id=(?P<cart>\S+?) '
                       r'n=(?P<n>\d+) cls=(?P<cls>\S+?) gap_s=(?P<gap>\S+?) ms_since_201=(?P<ms201>\S+?) '
                       r'live=(?P<live>\S+?) win_age=(?P<wa>\S+?) layer=(?P<layer>\S+?) mode=(?P<mode>\S+?) '
                       r'status=(?P<status>\S+?) key=(?P<key>\S+?) envoy_ms=(?P<envoy>\S+?) js_ms=(?P<js>[\d-]+)')
WCD_END = re.compile(r'\[WON_CART_DIRECT\] end reason=(?P<reason>\S+?) verdict=(?P<verdict>\S+?) '
                     r'tickets_call=(?P<tc>\S+?) tickets_cart=(?P<tcart>\S+?) live=(?P<live>\S+?) '
                     r'oos=(?P<oos>\S+?) sched_used=(?P<su>\S+?) verified=(?P<ver>\S+?) '
                     r'cvv_put=(?P<cvv>\S+?) fs_seen=(?P<fs>\S+?) dl_left=(?P<dl>-?[\d.]+)s '
                     r'held=(?P<held>yes|no)(?: others_live=.*?)? ident=' + IDENT)
ORDER_FL = re.compile(r'\*\*\* ORDER PLACED \*\*\* HTTP (?P<http>\d+) at t=[\d.]+s — order_id=(?P<oid>[0-9A-Za-z-]{6,40})')
ORDER_API = re.compile(r'\[API_PLACE_ORDER\] Order placed — order_id=(?P<oid>[0-9A-Za-z-]{6,40})')
RPT_DONE = re.compile(r'\[REAL_PURCHASE_THREAD\] \[OK\] Purchase execution completed \(attempt \d+\): \{(?P<d>.*)')
RPT_MARK = re.compile(r'\[REAL_PURCHASE_THREAD\] Marked thread as completing: (?P<tcin>\d{6,12}?)'
                      r'(?:#W(?P<w>\d{1,2}?))?(?=\d{4}-\d\d-\d\d|\D|$)')
ORDER_CTX = re.compile(r'Purchase successful: (?:(?P<tcin>' + TCIN + r')|(?P<name>.+?)) - Order: (?P<oid>[0-9A-Za-z-]{6,40})')
WARM_POST = re.compile(r'\[INTERCEPTOR:warmup\] +(?:\[CHECKOUT_POST\] +)?POST (?P<url>\S+)')
WARM_CAP = re.compile(r'\[INTERCEPTOR:warmup\] (?:Captured \d+ headers \(Shape tokens: (?P<tok>\d+), prev cache had \d+\)'
                      r'|Preserved cache \(\d+ Shape tokens, age=-?[\d.]+s\) — new capture had only (?P<tok2>\d+) Shape tokens)')
CRED = re.compile(r'\[HARVEST/(?P<acct>[\w-]+)\] (?:(?P<replay>REPLAY on main shot: banked set age=(?P<age>\d+)s)'
                  r'|(?P<empty>bank EMPTY at shot time)|(?P<stale>bank STALE at shot time))')
WORKER_POOL = re.compile(r'\[WORKER_POOL\] sized from \S+: \d+ account\(s\) -> \[(?P<list>[^\]]*)\]')
FWD_BIND = re.compile(r'\[FORWARDER\] W\d+/(?P<ident>[\w-]+?) → [\d.]+:\d+ \(exit via account proxy\)')
FWD_WARN = re.compile(r'\[FORWARDER\] \[WARN\] W\d+/(?P<ident>[\w-]+?) proxy unparseable')
FWD_REVERT = re.compile(r'\[FORWARDER\] \[(?:WARN|ERROR)\] (?:could not import ForwarderPool|start_all failed)')
PFETCH_RL = re.compile(r'\[PURCHASE\] ATC fetch: rate-limited \((?P<status>\d{1,3})\) body=(?P<body>.*?) — bailing(?P<rest>.*)')
PFETCH_ST = re.compile(r'\[PURCHASE\] ATC fetch status: (?P<status>\d{1,3})(?P<rest>.*)')
PFETCH_AUX = re.compile(r'\[PURCHASE\] ATC fetch \d{3} auth denied')
PFIRE = re.compile(r'\[PURCHASE\] Firing ATC fetch qty=(?P<qty>\d+)')
PF_T_IDENT = re.compile(r'\(t=[\d.]+s\)(?: ident=' + IDENT + ')?')

# Presence patterns: a hit that its family's full regexes cannot parse is `unparsed`.
PRINT_FAMILIES = [
    ('RACE', r'\[RACE\] '),
    ('FL_FIRE', r'\[FAST_LANE\] Firing ATC'),
    ('FL_CHAIN', r'\[FAST_LANE\] chain done'),
    ('FL_TIMEOUT', r'\[FAST_LANE\] (?:evaluate timed out|evaluate raised|unexpected result type)'),
    ('ATC_RESP_P', r'\[INTERCEPTOR:[\w-]+\] \[ATC_RESP\] '),
    ('INSTOCK', r'\[STOCK\] IN STOCK: '),
    ('API_CYCLE_IN', r'\[\d\d:\d\d:\d\d\] \[API_CYCLE\] IN STOCK: '),
    ('EXPOSURE', r'\[EXPOSURE\] '),
    ('FS_TICKET', r'\[FS_TICKET\] '),
    ('WCD_END', r'\[WON_CART_DIRECT\] end '),
    ('ORDER', r'\*\*\* ORDER PLACED \*\*\*|\[API_PLACE_ORDER\] Order placed'),
    ('ORDER_CTX', r'Purchase successful: '),
    ('RPT_DONE', r'\[REAL_PURCHASE_THREAD\] \[OK\] Purchase execution completed'),
    ('RPT_MARK', r'\[REAL_PURCHASE_THREAD\] Marked thread as completing: '),
    ('WARM_POST', r'\[INTERCEPTOR:warmup\] +(?:\[CHECKOUT_POST\] +)?POST '),
    ('WARM_CAP', r'\[INTERCEPTOR:warmup\] (?:Captured |Preserved cache )'),
    ('WORKER_POOL', r'\[WORKER_POOL\] sized from'),
    ('FWD', r'\[FORWARDER\] (?:W\d+/|\[WARN\] |\[ERROR\] )'),
    ('PFETCH', r'\[PURCHASE\] ATC fetch'),
    ('PFIRE', r'\[PURCHASE\] Firing ATC fetch'),
    # logger-only markers seen in print text = a format change (counted, never parsed)
    ('FLIP@wrong_form', r'\[STOCK\]\[FLIP\] '),
    ('STATS@wrong_form', r'\[STOCK STATS\] '),
]
LOGGER_FAMILIES = [
    ('ATC_RESP_L', r'^\[ATC_RESP\] '),
    ('HDRS', r'^\[ATC_RESP_HDRS\] '),
    ('FLIP', r'\[STOCK\]\[FLIP\] '),
    ('STATS', r'\[STOCK STATS\] '),
    ('CRED', r'\[HARVEST/[\w-]+\] (?:REPLAY on main shot|bank EMPTY at shot time|bank STALE at shot time)'),
    ('RACE@wrong_form', r'\[RACE\] '),
    ('FL_CHAIN@wrong_form', r'\[FAST_LANE\] chain done'),
    ('EXPOSURE@wrong_form', r'\[EXPOSURE\] '),
    ('FS_TICKET@wrong_form', r'\[FS_TICKET\] '),
]


def _dispatch(fams):
    return re.compile('|'.join('(?P<%s>%s)' % (n.replace('@', '__'), p) for n, p in fams))


PRINT_DISPATCH = _dispatch(PRINT_FAMILIES)
LOGGER_DISPATCH = _dispatch(LOGGER_FAMILIES)


# ---------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------
def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num_s(v):
    """'10s' / '-' -> 10 / None"""
    if v is None:
        return None
    return _int(str(v).rstrip('s'))


def _bool(v):
    return {'True': 1, 'False': 0, 'true': 1, 'false': 0, 'yes': 1, 'no': 0}.get(str(v))


def gate_of(status, key):
    """The wall a main-tab add-to-cart stopped at. Never guesses: see module doc."""
    if status is None:
        return 'unknown'
    if status in (200, 201):
        return 'cart'
    if status == 401:
        return 'wall2_denied'
    if status == 424:
        return 'inventory'
    if status == 429:
        if key is None:
            return 'unknown'
        k = key.upper()
        if 'ERR_A2C_TCIN_RATE_LIMITED' in k:
            return 'wall1_limited'
        if 'FAST_SELLING' in k or 'DCO_RATE_LIMITED' in k:
            return 'admitted_fs'
        return 'unknown'
    return 'other'


def body_hint_of(status, body):
    """[PURCHASE] ATC fetch body -> 'empty' | 'dco' | 'fs' | 'body' | None."""
    if body is None:
        return None
    b = body.strip()
    if b in ("''", '""', ''):
        return 'empty'
    if 'DCO_RATE_LIMITED' in b:
        return 'dco'
    if 'FAST_SELLING' in b:
        return 'fs'
    return 'body'


DECISIVE_HINTS = ('empty', 'dco', 'fs')     # body classes that tell ERR_A2C from FAST_SELLING


def hint_consistent(hint, key):
    if hint is None or key is None:
        return True
    k = key.upper()
    fs = 'FAST_SELLING' in k or 'DCO' in k
    if hint == 'empty':
        return not fs
    if hint in ('dco', 'fs'):
        return fs
    return True


# ---------------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------------
class RunParser:
    """Feed physical lines with feed(); finish() returns every table's rows."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0
        self.line = 0
        self.lines = 0
        self.last_naive = None
        self.last_stamp = None
        self.first_stamp = None
        self.seen = Counter()
        self.parsed = Counter()
        self.first_bad = {}
        self.ev = defaultdict(list)
        self.recent_api = deque(maxlen=8)   # API_CYCLE events for IN STOCK timing

    # -- intake -------------------------------------------------------------------
    def feed(self, lineno: int, line: str) -> None:
        self.line = lineno
        self.lines = lineno
        for kind, text, nms, stamp in split_segments(line):
            if kind == 'logger':
                self.last_naive = nms
                self.last_stamp = stamp
                if self.first_stamp is None:
                    self.first_stamp = stamp
                disp = LOGGER_DISPATCH
            else:
                disp = PRINT_DISPATCH
            if '[' not in text and '***' not in text and 'Purchase successful' not in text:
                continue
            for m in disp.finditer(text):
                fam = m.lastgroup.replace('__', '@')
                self.seen[fam] += 1
                if '@' in fam:
                    self._bad(fam)
                    continue
                ok = getattr(self, '_h_' + fam)(text, m.start(), kind == 'logger')
                if ok:
                    self.parsed[fam] += 1
                else:
                    self._bad(fam)

    def _bad(self, fam):
        if fam not in self.first_bad:
            self.first_bad[fam] = self.line

    def _add(self, _ev, **kw):
        self.seq += 1
        kw['seq'] = self.seq
        kw['line'] = self.line
        kw.setdefault('naive', self.last_naive)
        kw.setdefault('stamp', self.last_stamp)
        self.ev[_ev].append(kw)
        return kw

    # -- handlers (return True when parsed) ---------------------------------------
    def _h_RACE(self, t, p, lg):
        m = RACE_START.match(t, p)
        if m:
            workers = [w[1] for w in WORKER.findall(m.group('workers'))]
            self._add('race_start', tcin=m.group('tcin'), width=int(m.group('n')),
                      workers=workers, wlabels=m.group('workers'))
            return True
        m = RACE_DONE.match(t, p)
        if m:
            bd = [(e[1], e[2] if e[2] or not e[3] else e[3]) for e in BD_ENTRY.findall(m.group('bd'))]
            self._add('race_done', tcin=m.group('tcin'), k=int(m.group('k')), n=int(m.group('n')),
                      units=int(m.group('units')), bd=bd)
            return True
        return False

    def _h_FL_FIRE(self, t, p, lg):
        m = FIRE.match(t, p)
        if not m:
            return False
        self._add('fire', tcin=m.group('tcin'), qty=int(m.group('qty')))
        return True

    def _h_FL_CHAIN(self, t, p, lg):
        m = CHAIN.match(t, p)
        if not m:
            return False
        rest = m.group('rest')
        im = IDENT_RE.search(rest)
        t0 = T0_RE.search(rest)
        rt = RT_RE.search(rest)
        self._add('chain', atc=_int(m.group('atc')), atc_raw=m.group('atc'), pre=m.group('pre'),
                  po=m.group('po'), skip=m.group('skip'), dur=_float(m.group('dur')),
                  ident_raw=im.group('ident') if im else None,
                  t0=_int(t0.group(1)) if t0 else None, rt=_int(rt.group(1)) if rt else None)
        return True

    def _h_FL_TIMEOUT(self, t, p, lg):
        m = TO_ABORT.match(t, p)
        if m:
            im = IDENT_RE.search(m.group('rest'))
            self._add('orphan', variant='abort', atc=_int(m.group('atc')),
                      ident_raw=im.group('ident') if im else None)
            return True
        m = TO_NOTREL.match(t, p)
        if m:
            im = IDENT_RE.search(m.group('rest'))
            self._add('notrel', ident_raw=im.group('ident') if im else None)
            return True
        if TO_QTY.match(t, p):
            return True                                  # aux line; its shot anchors on UNKNOWN
        for rx, var in ((TO_UNKNOWN, 'timeout'), (EV_RAISED, 'raised'), (EV_BADTYPE, 'bad_result')):
            if rx.match(t, p):
                self._add('orphan', variant=var, atc=None, ident_raw=None)
                return True
        return False

    def _resp(self, m, form, tab, lg):
        rest = m.group('rest')
        env = ENVOY_RE.search(rest)
        st = SELFTEST_RE.search(rest)
        self._add('resp_' + form, tab=tab, status=_int(m.group('status')), key=m.group('key'),
                  envoy=env.group(1) if env else None, selftest=st.group(1) if st else None,
                  is_logger=lg)

    def _h_ATC_RESP_P(self, t, p, lg):
        m = ATC_RESP_P.match(t, p)
        if not m:
            return False
        self._resp(m, 'p', m.group('ptab'), lg)
        return True

    def _h_ATC_RESP_L(self, t, p, lg):
        m = ATC_RESP_L.match(t, p)
        if not m:
            return False
        tm = TAB_RE.search(m.group('rest'))
        self._resp(m, 'l', tm.group(1) if tm else None, lg)
        return True

    def _h_HDRS(self, t, p, lg):
        m = HDRS.match(t, p)
        if not m:
            return False
        hdr = {}
        for part in (m.group('rest') or '').split(' | '):
            part = part.strip()
            if not part:
                continue
            k, _, v = part.partition('=')
            hdr[k.strip().lower()] = v.strip()
        self._add('hdrs', tab=m.group('tab'), status=_int(m.group('status')), hdr=hdr)
        return True

    def _h_FLIP(self, t, p, lg):
        m = FLIP.match(t, p)
        if m:
            self._add('flip', tcin=m.group('tcin'), n=int(m.group('n')), read_ms=int(m.group('read_ms')),
                      rt=_int(m.group('rt')), oos=_int(m.group('oos')), since=_int(m.group('since')),
                      nw=int(m.group('nw')), via=m.group('via'), status=m.group('status'))
            return True
        m = FLIP_CAP.match(t, p)
        if m:
            self._add('flip_cap', tcin=m.group('tcin'), cap=int(m.group('cap')))
            return True
        if FLIP_ERR.match(t, p):
            self._add('flip_err')
            return True
        return False

    def _h_INSTOCK(self, t, p, lg):
        m = INSTOCK.match(t, p)
        if not m:
            return False
        self._add('instock', tcins=[x.strip() for x in m.group('list').split(',')])
        return True

    def _h_API_CYCLE_IN(self, t, p, lg):
        m = API_CYCLE_IN.match(t, p)
        if not m:
            return False
        tc = re.findall(r'\d{6,12}', m.group('list'))
        self._add('api_cycle', hms=m.group('hms'), tcins=tc)
        return True

    def _h_STATS(self, t, p, lg):
        m = STATS.match(t, p)
        if not m:
            return False
        g = m.groupdict()
        self._add('stats', t_s=_float(g['t']), sweeps=int(g['sweeps']),
                  s200=int(g['s200']), s403=int(g['s403']), s429=_int(g['s429']),
                  other=int(g['other']), beh=_int(g['beh']), out=int(g['out']))
        return True

    def _h_EXPOSURE(self, t, p, lg):
        m = EXPOSURE.match(t, p)
        if not m:
            return False
        self._add('exposure', ident_raw=m.group('ident'), tcin=m.group('tcin'), kind=m.group('kind'),
                  win_age=_int(m.group('wa')), proxied=m.group('prox'))
        return True

    def _h_FS_TICKET(self, t, p, lg):
        m = FS_TICKET.match(t, p)
        if not m:
            return False
        self._add('ticket', **{k: v for k, v in m.groupdict().items()})
        return True

    def _h_WCD_END(self, t, p, lg):
        m = WCD_END.match(t, p)
        if not m:
            return False
        self._add('wcd_end', **{('ident_raw' if k == 'ident' else k): v for k, v in m.groupdict().items()})
        return True

    def _h_ORDER(self, t, p, lg):
        m = ORDER_FL.match(t, p)
        if m:
            self._add('order', oid=m.group('oid'), route='fast_lane')
            return True
        m = ORDER_API.match(t, p)
        if m:
            self._add('order', oid=m.group('oid'), route='api')
            return True
        return False

    def _h_ORDER_CTX(self, t, p, lg):
        m = ORDER_CTX.match(t, p)
        if not m:
            return False
        if m.group('tcin'):                               # the product-name variant carries no TCIN
            self._add('order_ctx', tcin=m.group('tcin'), oid=m.group('oid'))
        return True

    def _h_RPT_DONE(self, t, p, lg):
        m = RPT_DONE.match(t, p)
        if not m:
            return False
        oid = re.search(r"'order_id': '([0-9A-Za-z-]{6,40})'", m.group('d'))
        if oid:                                           # only successful purchases carry one
            tc = re.search(r"'tcin': '(\d{6,12})'", m.group('d'))
            self._add('order_done', oid=oid.group(1), tcin=tc.group(1) if tc else None)
        return True

    def _h_RPT_MARK(self, t, p, lg):
        m = RPT_MARK.match(t, p)
        if not m:
            return False
        self._add('thread_mark', tcin=m.group('tcin'), w=m.group('w'))
        return True

    def _h_WARM_POST(self, t, p, lg):
        m = WARM_POST.match(t, p)
        if not m:
            return False
        self._add('warm_post', url=m.group('url'))
        return True

    def _h_WARM_CAP(self, t, p, lg):
        m = WARM_CAP.match(t, p)
        if not m:
            return False
        tok = m.group('tok') if m.group('tok') is not None else m.group('tok2')
        self._add('warm_cap', tok=int(tok))
        return True

    def _h_CRED(self, t, p, lg):
        m = CRED.match(t, p)
        if not m:
            return False
        if m.group('replay'):
            src, det = 'banked_replay', 'replay'
        else:
            src, det = 'page_signed', ('empty' if m.group('empty') else 'stale')
        self._add('cred', acct=m.group('acct'), src=src, det=det, age=_int(m.group('age')))
        return True

    def _h_WORKER_POOL(self, t, p, lg):
        m = WORKER_POOL.match(t, p)
        if not m:
            return False
        self._add('worker_pool', idents=re.findall(r"'([^']+)'", m.group('list')))
        return True

    def _h_FWD(self, t, p, lg):
        m = FWD_BIND.match(t, p)
        if m:
            self._add('fwd', ident_raw=m.group('ident'), bound=1)
            return True
        m = FWD_WARN.match(t, p)
        if m:
            self._add('fwd', ident_raw=m.group('ident'), bound=0)
            return True
        if FWD_REVERT.match(t, p):
            self._add('fwd_revert')
            return True
        return False

    def _h_PFIRE(self, t, p, lg):
        m = PFIRE.match(t, p)
        if not m:
            return False
        self._add('pfire')
        return True

    def _h_PFETCH(self, t, p, lg):
        if PFETCH_AUX.match(t, p):
            return True                                  # second line of a 401; not a shot
        m = PFETCH_RL.match(t, p)
        body = None
        if m:
            body = m.group('body')
        else:
            m = PFETCH_ST.match(t, p)
            if not m:
                return False
            bm = re.match(r" body=(.*?) \(t=[\d.]+s\)", m.group('rest'))
            body = bm.group(1) if bm else "''"
        im = PF_T_IDENT.search(m.group('rest'))
        self._add('pfetch', status=_int(m.group('status')),
                  ident_raw=(im.group('ident') if im and im.group('ident') else None),
                  hint=body_hint_of(_int(m.group('status')), body))
        return True

    # -- post-processing -----------------------------------------------------------
    def finish(self) -> dict:
        E = self.ev
        checks = {}
        # idents
        known = []
        if E['worker_pool']:
            known = list(E['worker_pool'][0]['idents'])
        glued = Counter()

        def norm(raw):
            if raw is None:
                return None
            r = re.sub(r'^W\d+/', '', raw)
            if not known or r in known:
                return r
            best = max((k for k in known if r.startswith(k)), key=len, default=None)
            if best:
                glued[r] += 1
                return best
            glued['UNKNOWN:' + r] += 1
            return r

        for k in ('chain', 'orphan', 'notrel', 'exposure', 'wcd_end', 'pfetch', 'fwd'):
            for e in E[k]:
                e['ident'] = norm(e.get('ident_raw'))
        for e in E['ticket']:
            e['ident'] = norm(e.get('ident'))
        for e in E['cred']:
            e['ident'] = norm(e['acct'])
        if glued:
            checks['ident_glue_repaired'] = dict(glued)

        # UTC offset: logger wall clock minus epoch, rounded to 15 min
        diffs = [e['naive'] - e['read_ms'] for e in E['flip'] if e['naive'] is not None]
        diffs += [e['naive'] - (e['t0'] + (e['rt'] or 0)) for e in E['chain']
                  if e['t0'] and e['naive'] is not None]
        if diffs:
            diffs.sort()
            med = diffs[len(diffs) // 2]
            off = int(round(med / 900000.0)) * 900000
            tz_src = 'measured(n=%d)' % len(diffs)
        elif self.first_stamp:
            st = self.first_stamp
            nv = naive_ms(st[:19], st[20:23])
            guess = time.mktime(time.strptime(st[:19], '%Y-%m-%d %H:%M:%S'))
            off = nv - int(guess * 1000) - int(st[20:23])
            tz_src = 'local_machine'
        else:
            off, tz_src = None, None
        self.utc_off = off

        def ep(nv):
            return None if (nv is None or off is None) else nv - off

        # [ATC_RESP] form per file
        rp, rl = E['resp_p'], E['resp_l']
        if rl and all(e['tab'] for e in rl):
            form, resps = 'logger', rl
        elif rp:
            form, resps = 'print', rp
        elif rl:
            form, resps = 'logger_untabbed', []
            checks['atc_resp_logger_without_tab'] = len(rl)
        else:
            form, resps = None, []
        if rp or rl:
            cp = Counter(e['tab'] for e in rp)
            cl = Counter(e['tab'] for e in rl)
            checks['atc_resp_print_by_tab'] = dict(cp)
            checks['atc_resp_logger_by_tab'] = dict(cl)
        main_resps = [e for e in resps if e['tab'] == 'main']
        warm_resps = [e for e in resps if e['tab'] == 'warmup']
        other_tabs = Counter(e['tab'] for e in resps if e['tab'] not in ('main', 'warmup'))
        if other_tabs:
            checks['atc_resp_other_tabs'] = dict(other_tabs)
        for e in main_resps:
            e['ts_ok'] = (form == 'logger')
            e['matched'] = False
            e['hdr'] = None

        # [ATC_RESP_HDRS] -> main responses, FIFO by (status, key) in log order
        hq = defaultdict(deque)
        stream = sorted([('r', e) for e in main_resps] +
                        [('h', e) for e in E['hdrs'] if e['tab'] == 'main'], key=lambda x: x[1]['seq'])
        h_unpaired = 0
        for kind, e in stream:
            if kind == 'r':
                hq[(e['status'], e['key'])].append(e)
            else:
                k = (e['status'], e['hdr'].get('tgt-cart-error-key', '-'))
                if hq[k]:
                    r = hq[k].popleft()
                    r['hdr'] = e['hdr']
                    r['hdr_line'] = e['line']
                else:
                    h_unpaired += 1
        if E['hdrs']:
            checks['hdrs_main_unpaired'] = h_unpaired

        # ---- pass 1: races, anchors, credentials, hints -------------------------
        races = []
        open_of = {}                   # ident -> race index (account in one race at a time)
        anchors = []
        last_anchor = {}
        cred_q = defaultdict(list)
        purchased = []
        last_fire_tcin = None
        pending_notrel = None
        wcd_tcin = {}
        last_ticket_tcin = {}
        s = []
        for k in ('race_start', 'race_done', 'fire', 'chain', 'orphan', 'notrel', 'pfetch', 'cred',
                  'wcd_end', 'ticket'):
            s += [(e['seq'], k, e) for e in E[k]]
        for e in main_resps:
            s.append((e['seq'], 'resp', e))
        s.sort(key=lambda x: x[0])
        race_mismatch = 0

        def open_races():
            return sorted({i for i in open_of.values()})

        def mk_anchor(e, src, ident, status):
            ri = open_of.get(ident) if ident else None
            rsrc = 'ident' if ri is not None else None
            if ri is None and not ident:
                o = open_races()
                if len(o) == 1:
                    ri, rsrc = o[0], 'only_open'
            a = dict(seq=e['seq'], line=e['line'], naive=e['naive'], src=src, ident=ident, status=status,
                     t0=e.get('t0'), rt=e.get('rt'), race=ri, race_src=rsrc, pfetch=None, hint=None,
                     cred=None, resp=None, join_q=None, join_dt=None, fire_tcin=last_fire_tcin,
                     variant=e.get('variant'))
            # credential: the ident's latest unconsumed shot-time line since its race began
            if ident and cred_q[ident]:
                lo = races[ri]['seq'] if ri is not None else e['seq'] - 10 ** 9
                cands = [c for c in cred_q[ident] if lo < c['seq'] < e['seq']]
                if cands:
                    c = cands[-1]
                    cred_q[ident].remove(c)
                    a['cred'] = c
            anchors.append(a)
            if ident:
                last_anchor[ident] = a
            return a

        for _, k, e in s:
            if k == 'race_start':
                r = dict(idx=len(races), seq=e['seq'], line=e['line'], naive=e['naive'], tcin=e['tcin'],
                         width=e['width'], workers=e['workers'], active=set(), done_line=None, units=None)
                races.append(r)
                for w in e['workers']:
                    wn = norm(w)
                    open_of[wn] = r['idx']
                    r['active'].add(wn)
            elif k == 'race_done':
                if not e['bd']:
                    continue
                wn, val = norm(e['bd'][-1][0]), e['bd'][-1][1]
                ri = open_of.get(wn)
                if ri is None or races[ri]['tcin'] != e['tcin']:
                    race_mismatch += 1
                    cand = [r for r in races if r['tcin'] == e['tcin'] and wn in r['active']]
                    ri = cand[-1]['idx'] if cand else None
                if ri is not None:
                    races[ri]['active'].discard(wn)
                    if open_of.get(wn) == ri:
                        del open_of[wn]
                    if e['k'] == e['n']:
                        races[ri]['done_line'] = e['line']
                        races[ri]['units'] = e['units']
                if val == 'purchased':
                    purchased.append((e['seq'], e['tcin'], wn))
            elif k == 'fire':
                last_fire_tcin = e['tcin']
            elif k == 'cred':
                cred_q[e['ident']].append(e)
            elif k == 'chain':
                mk_anchor(e, 'chain', e['ident'], e['atc'])
            elif k == 'notrel':
                pending_notrel = e
            elif k == 'orphan':
                ident = e['ident']
                isrc = 'line' if ident else None
                if not ident and pending_notrel and e['line'] - pending_notrel['line'] <= 10:
                    ident, isrc = pending_notrel['ident'], 'notrel'
                pending_notrel = None
                if not ident:
                    o = open_races()
                    lo = min((races[i]['seq'] for i in o), default=None)
                    act = set().union(*[races[i]['active'] for i in o]) if o else set()
                    if lo is not None:
                        c = {idn for idn, lst in cred_q.items() if idn in act
                             and any(lo < x['seq'] < e['seq'] for x in lst)}
                        if len(c) == 1:
                            ident, isrc = c.pop(), 'unclaimed_cred'
                a = mk_anchor(e, 'orphan', ident, e['atc'])
                a['ident_src'] = isrc
            elif k == 'pfetch':
                ident = e['ident']
                a = None
                if ident:
                    b = last_anchor.get(ident)
                    if b and b['pfetch'] is None and b['src'] == 'chain' and b['status'] == e['status'] \
                            and e['line'] - b['line'] <= 400:
                        a = b
                else:
                    for b in reversed(anchors[-12:]):
                        if b['pfetch'] is None and b['src'] == 'chain' and b['status'] == e['status'] \
                                and e['line'] - b['line'] <= 400:
                            a = b
                            break
                if a is not None:
                    a['pfetch'] = e
                    a['hint'] = e['hint']
                else:
                    b = mk_anchor(e, 'legacy', ident, e['status'])
                    b['pfetch'] = e
                    b['hint'] = e['hint']
            elif k == 'resp':
                e['open'] = open_races()
            elif k == 'wcd_end':
                ri = open_of.get(e['ident'])
                wcd_tcin[e['seq']] = races[ri]['tcin'] if ri is not None else last_ticket_tcin.get(e['ident'])
            elif k == 'ticket':
                last_ticket_tcin[e['ident']] = e['tcin']
        checks['race_done_mismatch'] = race_mismatch
        unclaimed_cred = sum(len(v) for v in cred_q.values())
        if E['cred']:
            checks['cred_unclaimed'] = unclaimed_cred

        # ---- pass 2: join main responses to anchors ------------------------------
        # (a) timestamp join, global greedy by |logger_ts - (atc_t0 + atc_rt)|: needs the
        #     chain's atc_t0/atc_rt and the logger-form [ATC_RESP] (09-22 on). Same status,
        #     within 2 s, and never against the anchor's own [PURCHASE] body class.
        MAXGAP, MAXDT, AFTER = 3000, 2000, 300
        mr = sorted(main_resps, key=lambda e: e['seq'])
        by_status = defaultdict(list)
        for r in mr:
            by_status[r['status']].append(r)
        pairs = []
        for a in anchors:
            if a['status'] is None or not a['t0'] or a['rt'] is None:
                continue
            want = a['t0'] + a['rt']
            for r in by_status.get(a['status'], []):
                # the logger copy of a shot's own [ATC_RESP] can land AFTER its chain line
                # (glued to the chain print's tail: 09-25 L53930), so look both ways
                if not r['ts_ok'] or not (-AFTER <= a['line'] - r['line'] <= MAXGAP):
                    continue
                if not hint_consistent(a['hint'], r['key']):
                    continue
                dt = ep(r['naive']) - want
                if abs(dt) <= MAXDT:
                    pairs.append((abs(dt), r['seq'], a['seq'], dt, a, r))
        pairs.sort(key=lambda x: x[:3])      # ties (same ms, same key): FIFO
        for _, _, _, dt, a, r in pairs:
            if a['resp'] is not None or r['matched']:
                continue
            r['matched'] = True
            a['resp'], a['join_dt'] = r, dt
            a['join_q'] = 'ts' if abs(dt) <= 10 else 'ts_loose'
        # (b) sequential join for everything else: the nearest preceding pending response
        #     of the same status; if the candidates disagree on the key, the anchor's
        #     [PURCHASE] body class picks; if it cannot, the key stays NULL (ambiguous).
        pend = []
        ri_ = 0
        for a in sorted(anchors, key=lambda a: a['seq']):
            while ri_ < len(mr) and mr[ri_]['seq'] < a['seq']:
                pend.append(mr[ri_])
                ri_ += 1
            pend = [r for r in pend if not r['matched'] and a['line'] - r['line'] <= MAXGAP]
            if a['status'] is None or a['resp'] is not None:
                continue
            cands = [r for r in pend if r['status'] == a['status']]
            if not cands:
                continue
            keys = {r['key'] for r in cands}
            decisive = a['hint'] in DECISIVE_HINTS
            q = None
            if len(keys) > 1:
                c2 = [r for r in cands if hint_consistent(a['hint'], r['key'])] if decisive else []
                if c2 and len({r['key'] for r in c2}) == 1:
                    cands, q = c2, 'seq_hint'
                else:
                    # cannot tell which response is ours: key stays NULL, and every candidate
                    # is tainted so the leftover is not handed to the next anchor as certain
                    q = 'ambiguous'
                    for r in cands:
                        r['amb'] = True
            best = cands[-1]
            if q is None and best.get('amb'):
                q = 'seq_hint' if (decisive and hint_consistent(a['hint'], best['key'])) else 'ambiguous'
            if q is None:
                q = 'seq1' if len(cands) == 1 else 'seq'
            best['matched'] = True
            a['resp'] = best
            a['join_q'] = q
        # (c) second chance: a response printed after its anchor
        left = [r for r in mr if not r['matched']]
        for a in anchors:
            if a['resp'] is None and a['status'] is not None:
                for r in left:
                    if not r['matched'] and r['status'] == a['status'] and 0 < r['line'] - a['line'] <= 50:
                        r['matched'] = True
                        a['resp'] = r
                        a['join_q'] = 'seq_after'
                        break

        hk = Counter()
        for a in anchors:
            if a['resp'] is not None and a['hint'] is not None and a['status'] == 429:
                hk['agree' if hint_consistent(a['hint'], a['resp']['key']) else 'CONFLICT'] += 1
        if hk:
            checks['body_hint_vs_key_429'] = dict(hk)

        # ---- windows ----------------------------------------------------------------
        flips = sorted(E['flip'], key=lambda f: (f['tcin'], f['read_ms'], f['seq']))
        have_flips = bool(E['flip'])
        cap_seq = {e['tcin']: e['seq'] for e in E['flip_cap']}
        windows = []
        by_t = defaultdict(list)
        for f in flips:
            by_t[f['tcin']].append(f)
        for tc, fl in by_t.items():
            starts = [f for f in fl if f['nw'] == 1]
            for i, st in enumerate(starts):
                end = starts[i + 1]['read_ms'] if i + 1 < len(starts) else None
                inw = [f for f in fl if f['read_ms'] >= st['read_ms'] and (end is None or f['read_ms'] < end)]
                windows.append(dict(tcin=tc, first=st['read_ms'], end=end, flips=inw, line=st['line'],
                                    trunc=1 if (tc in cap_seq and end is None) else 0, reads=[], races=[]))
        windows.sort(key=lambda w: (w['first'], w['tcin']))
        for i, w in enumerate(windows, 1):
            w['id'] = i
            for f in w['flips']:
                f['win'] = i
        wins_of = defaultdict(list)
        for w in windows:
            wins_of[w['tcin']].append(w)

        def win_for(tc, t_ms):
            if t_ms is None:
                return None
            ws = [w for w in wins_of.get(tc, []) if w['first'] <= t_ms and (w['end'] is None or t_ms < w['end'])]
            return ws[-1] if ws else None

        # IN STOCK reads: whole-second time from the following [API_CYCLE] line
        api = sorted(E['api_cycle'], key=lambda e: e['seq'])
        ai = 0
        read_unassigned = 0
        read_rows = []
        for r in sorted(E['instock'], key=lambda e: e['seq']):
            while ai < len(api) and (api[ai]['seq'] < r['seq']):
                ai += 1
            t_ms, src = None, None
            if ai < len(api) and api[ai]['line'] - r['line'] <= 5 and set(r['tcins']) <= set(api[ai]['tcins']):
                t_ms = self._hms_epoch(api[ai]['hms'], r['naive'])
                src = 'api_cycle'
            elif r['naive'] is not None:
                t_ms, src = ep(r['naive']), 'logger_prior'
            for tc in r['tcins']:
                w = None
                if t_ms is not None:
                    cand = [w_ for w_ in wins_of.get(tc, []) if w_['first'] <= t_ms + 999
                            and (w_['end'] is None or t_ms < w_['end'])]
                    w = cand[-1] if cand else None
                if w is not None:
                    w['reads'].append(t_ms)
                elif have_flips:
                    read_unassigned += 1
                read_rows.append((tc, t_ms, src, w['id'] if w else None))
        if have_flips:
            checks['instock_reads_outside_windows'] = read_unassigned

        # ---- shots --------------------------------------------------------------------
        shots = []
        for a in anchors:
            r = a['resp']
            status = a['status'] if a['status'] is not None else (r['status'] if r else None)
            key = r['key'] if r else None
            if a['join_q'] == 'ambiguous':
                key = None
            shots.append(self._shot_row(a, r, status, key, ep, races))
        for r in mr:
            if not r['matched']:
                a = dict(seq=r['seq'], line=r['line'], naive=r['naive'], src='resp_only', ident=None,
                         status=r['status'], t0=None, rt=None,
                         race=r['open'][0] if len(r.get('open', [])) == 1 else None,
                         race_src='only_open' if len(r.get('open', [])) == 1 else None, pfetch=None,
                         hint=None, cred=None, resp=r, join_q=None, join_dt=None, fire_tcin=None, variant=None)
                shots.append(self._shot_row(a, r, r['status'], r['key'], ep, races))
        shots.sort(key=lambda x: x['_seq'])
        idx = Counter()
        for sh in shots:
            if sh['race_seq'] is not None and sh['ident']:
                idx[(sh['race_seq'], sh['ident'])] += 1
                sh['shot_idx'] = idx[(sh['race_seq'], sh['ident'])]
                sh['is_first'] = 1 if sh['shot_idx'] == 1 else 0

        # race start = first shot atc_t0, else the race line's prior logger stamp
        first_t0 = {}
        for sh in shots:
            if sh['race_seq'] is not None and sh['ts_src'] == 'atc_t0':
                rs = sh['race_seq']
                first_t0[rs] = min(first_t0.get(rs, sh['ts_ms']), sh['ts_ms'])
        for r in races:
            rs = r['idx'] + 1
            if rs in first_t0:
                r['start_ms'], r['start_src'] = first_t0[rs], 'first_shot_t0'
            else:
                r['start_ms'], r['start_src'] = ep(r['naive']), 'prior_logger_ts'
            r['win'] = None
            r['flip_opened'] = None
            if have_flips:
                capped = r['tcin'] in cap_seq and r['seq'] > cap_seq[r['tcin']]
                w = win_for(r['tcin'], r['start_ms'])
                if w is not None:
                    r['win'] = w
                    w['races'].append(r)
                elif not capped:
                    r['flip_opened'] = 0
        for w in windows:
            w['races'].sort(key=lambda r: (r['start_ms'], r['seq']))
            for j, r in enumerate(w['races']):
                r['flip_opened'] = 1 if j == 0 else 0
        for sh in shots:
            if sh['race_seq'] is not None:
                r = races[sh['race_seq'] - 1]
                sh['flip_opened_race'] = r['flip_opened']
                if r['win'] is not None:
                    sh['window_id'] = r['win']['id']
                    if sh['ts_src'] == 'atc_t0':
                        sh['window_age_ms'] = sh['ts_ms'] - r['win']['first']
                if sh['tcin'] is None:
                    sh['tcin'], sh['tcin_src'] = r['tcin'], 'race'

        # proxied per ident: [EXPOSURE] proxied= wins, else the boot forwarder lines
        idents = {}
        allid = set(known)
        for e in E['exposure']:
            allid.add(e['ident'])
        boot = {}
        if E['worker_pool']:
            reverted = bool(E['fwd_revert'])
            bound = {e['ident'] for e in E['fwd'] if e['bound']}
            warned = {e['ident'] for e in E['fwd'] if not e['bound']}
            for i in known:
                boot[i] = 0 if (reverted or i in warned or i not in bound) else 1
        for i in sorted(x for x in allid if x):
            ex = Counter(e['proxied'] for e in E['exposure'] if e['ident'] == i)
            if ex['yes'] and not ex['no']:
                pv, src = 1, 'exposure'
            elif ex['no'] and not ex['yes']:
                pv, src = 0, 'exposure'
            elif ex['yes'] and ex['no']:
                pv, src = None, 'exposure_mixed'
            elif i in boot:
                pv, src = boot[i], 'boot_forwarder'
            else:
                pv, src = None, None
            idents[i] = dict(ident=i, proxied=pv, proxied_src=src, boot_proxied=boot.get(i),
                             exp_yes=ex['yes'], exp_no=ex['no'])
            if i in boot and src == 'exposure' and boot[i] != pv:
                checks.setdefault('proxied_boot_vs_exposure_disagree', []).append(i)
        for sh in shots:
            if sh['ident'] in idents:
                sh['proxied'] = idents[sh['ident']]['proxied']

        # ---- decoys (warmup-tab responses, one row each) --------------------------------
        decoys = []
        posts, reqs = deque(), deque()
        wstream = sorted([('p', e) for e in E['warm_post']] + [('c', e) for e in E['warm_cap']] +
                         [('r', e) for e in warm_resps], key=lambda x: x[1]['seq'])
        last_kind = None
        for kind, e in wstream:
            if kind == 'p':
                if 'cart_items' in e['url']:
                    posts.append(e)
                last_kind = None
            elif kind == 'c':
                if posts:
                    posts.popleft()
                    reqs.append((e['tok'], e['seq']))
                last_kind = 'c'
            else:
                tok, q = None, None
                if reqs:
                    tok, _ = reqs.popleft()
                    q = 'adjacent' if last_kind == 'c' and not reqs else 'fifo'
                decoys.append(dict(line=e['line'], ts_ms=ep(e['naive']), ts=e['stamp'],
                                   status=e['status'], key=e['key'],
                                   shape_tokens=tok, selftest=e['selftest'], envoy_ms=_int(e['envoy']),
                                   pair_q=q, ts_src='logger' if e['is_logger'] else 'logger_prior'))
                last_kind = 'r'

        # ---- tickets / loop ends / stats / orders ------------------------------------
        tickets = []
        for e in E['ticket']:
            tickets.append(dict(line=e['line'], ts_ms=ep(e['naive']), ident=e['ident'], tcin=e['tcin'],
                                cart_id=e['cart'], n=_int(e['n']), cls=e['cls'], gap_s=_float(e['gap']),
                                ms_since_201=_int(e['ms201']), live=_bool(e['live']),
                                win_age_s=_num_s(e['wa']), layer=e['layer'], mode=e['mode'],
                                status=_int(e['status']), key=e['key'], envoy_ms=_int(e['envoy']),
                                js_ms=_int(e['js'])))
        loop_ends = []
        for e in E['wcd_end']:
            loop_ends.append(dict(line=e['line'], ts_ms=ep(e['naive']), ident=e['ident'],
                                  tcin=wcd_tcin.get(e['seq']), reason=e['reason'], verdict=e['verdict'],
                                  tickets_call=_int(e['tc']), tickets_cart=_int(e['tcart']),
                                  live=_bool(e['live']), oos=_int(e['oos']), sched_used=_int(e['su']),
                                  verified=_bool(e['ver']), cvv_put=e['cvv'], fs_seen=e['fs'],
                                  dl_left_s=_float(e['dl']), held=_bool(e['held'])))
        stats = [dict(line=e['line'], ts_ms=ep(e['naive']), ts=e['stamp'], t_s=e['t_s'], sweeps=e['sweeps'],
                      s200=e['s200'], s403=e['s403'], s429=e['s429'], other=e['other'], beh=e['beh'],
                      outstanding=e['out']) for e in E['stats']]
        # orders: the buying thread prints "Purchase execution completed ... 'order_id'" and
        # then "Marked thread as completing: <tcin>#W<n>"; W<n> -> ident from the race labels.
        # Fallback: the first unused race-done line whose newest entry is 'purchased'.
        wmap = {}
        for e in E['race_start']:
            for num, name in WORKER.findall(e['wlabels']):
                wmap[num] = norm(name)
        ctx_tcin = {e['oid']: e['tcin'] for e in E['order_ctx']}
        done_of = {}
        for e in E['order_done']:
            done_of.setdefault(e['oid'], e)
        marks = sorted(E['thread_mark'], key=lambda e: e['seq'])
        orders, seen_oid, used_p = [], set(), set()
        for e in sorted(E['order'], key=lambda e: e['seq']):
            if e['oid'] in seen_oid:
                checks['order_duplicate_lines'] = checks.get('order_duplicate_lines', 0) + 1
                continue
            seen_oid.add(e['oid'])
            od = done_of.get(e['oid'])
            tc = ctx_tcin.get(e['oid']) or (od['tcin'] if od else None)
            who, wsrc = None, None
            if od is not None:
                mk = next((m_ for m_ in marks if m_['seq'] > od['seq'] and m_['line'] - od['line'] <= 5
                           and m_['tcin'] == od['tcin']), None)
                if mk is not None and mk['w'] in wmap:
                    who, wsrc = wmap[mk['w']], 'thread_mark'
            if who is None:
                for j, (sq, ptc, idn) in enumerate(purchased):
                    if j not in used_p and sq > e['seq'] and (tc is None or ptc == tc):
                        used_p.add(j)
                        who, wsrc = idn, 'race_done'
                        tc = tc or ptc
                        break
            orders.append(dict(line=e['line'], ts_ms=ep(e['naive']), ts=e['stamp'], ident=who,
                               ident_src=wsrc, tcin=tc, order_id=e['oid'], route=e['route']))

        # ---- rows -----------------------------------------------------------------------
        race_rows = []
        n_by_race = Counter(sh['race_seq'] for sh in shots if sh['race_seq'] is not None)
        for r in races:
            race_rows.append(dict(race_seq=r['idx'] + 1, line=r['line'], tcin=r['tcin'], width=r['width'],
                                  workers=','.join(norm(w) or '' for w in r['workers']),
                                  start_ms=r['start_ms'], start_src=r['start_src'],
                                  flip_opened=r['flip_opened'], window_id=r['win']['id'] if r['win'] else None,
                                  lag_ms=(r['start_ms'] - r['win']['first']) if (r['win'] and r['start_ms'] is not None) else None,
                                  n_shots=n_by_race.get(r['idx'] + 1, 0), done_line=r['done_line'],
                                  units_bought=r['units']))
        flip_rows = [dict(line=f['line'], tcin=f['tcin'], flip_no=f['n'], read_ms=f['read_ms'],
                          since_oos_ms=f['since'], new_window=f['nw'], via=f['via'], rt_ms=f['rt'],
                          last_oos_ms=f['oos'], status=f['status'], window_id=f.get('win'))
                     for f in sorted(E['flip'], key=lambda f: f['seq'])]
        shots_in_win = Counter(sh['window_id'] for sh in shots if sh.get('window_id'))
        win_rows = []
        for w in windows:
            last = max([w['first']] + [f['read_ms'] for f in w['flips']] + w['reads'])
            win_rows.append(dict(window_id=w['id'], tcin=w['tcin'], first_read_ms=w['first'],
                                 last_read_ms=last, end_ms=w['end'], reads=len(w['reads']),
                                 flips=len(w['flips']), races=len(w['races']),
                                 shots=shots_in_win.get(w['id'], 0), trunc=w['trunc'], line=w['line']))
        if E['flip_err']:
            checks['flip_unformatted_lines'] = len(E['flip_err'])

        jq = Counter(sh['join_q'] or ('-' if sh['src'] != 'resp_only' else 'resp_only') for sh in shots)
        checks['join_quality'] = dict(jq)
        checks['shot_src'] = dict(Counter(sh['src'] for sh in shots))
        checks['chain_without_ident'] = sum(1 for e in E['chain'] if not e['ident'])
        checks['main_resp'] = len(main_resps)
        checks['fire_lines'] = len(E['fire'])
        # reconciliation: legacy-path shots vs the legacy path's own fire lines
        if E['pfire'] or any(sh['src'] == 'legacy' for sh in shots):
            checks['legacy_fire_vs_legacy_shots'] = '%d/%d' % (
                len(E['pfire']), sum(1 for sh in shots if sh['src'] == 'legacy'))

        unparsed = []
        for fam in sorted(set(self.seen) | set(self.parsed)):
            unparsed.append(dict(marker=fam, seen=self.seen[fam], parsed=self.parsed[fam],
                                 n=self.seen[fam] - self.parsed[fam], first_line=self.first_bad.get(fam)))
        run = dict(first_ts=self.first_stamp, last_ts=self.last_stamp, lines=self.lines,
                   utc_offset_min=(off // 60000) if off is not None else None, tz_src=tz_src,
                   atc_resp_form=form, flip_log=1 if have_flips else 0)
        for sh in shots:
            sh.pop('_seq', None)
        return dict(run=run, shots=shots, races=race_rows, flips=flip_rows, windows=win_rows,
                    tickets=tickets, loop_ends=loop_ends, decoys=decoys, monitor_stats=stats,
                    orders=orders, idents=list(idents.values()), unparsed=unparsed,
                    checks=[dict(name=k, value=repr(v)) for k, v in sorted(checks.items())])

    def _hms_epoch(self, hms, ref_naive):
        """[HH:MM:SS] (local) -> epoch ms, dated from the nearest logger stamp."""
        if ref_naive is None or self.utc_off is None:
            return None
        day = ref_naive - ref_naive % 86400000
        h, m, s_ = (int(x) for x in hms.split(':'))
        nv = day + (h * 3600 + m * 60 + s_) * 1000
        if nv < ref_naive - 43200000:
            nv += 86400000
        elif nv > ref_naive + 43200000:
            nv -= 86400000
        return nv - self.utc_off

    def _shot_row(self, a, r, status, key, ep, races):
        hdr = (r or {}).get('hdr') if r else None
        ri = a['race']
        env = None
        if r and r.get('envoy') not in (None, '-'):
            env = _int(r['envoy'])
        elif hdr and hdr.get('x-envoy-upstream-service-time'):
            env = _int(hdr.get('x-envoy-upstream-service-time'))
        if a['t0']:
            ts_ms, ts_src = a['t0'], 'atc_t0'
        else:
            ts_ms, ts_src = ep(a['naive']), 'logger_prior'
        cred = a.get('cred')
        return dict(
            _seq=a['seq'], line=a['line'], ts_ms=ts_ms, ts_src=ts_src, ident=a['ident'],
            tcin=races[ri]['tcin'] if ri is not None else a.get('fire_tcin'),
            tcin_src='race' if ri is not None else ('last_fire' if a.get('fire_tcin') else None),
            race_seq=(ri + 1) if ri is not None else None, race_src=a.get('race_src'),
            shot_idx=None, is_first=None, flip_opened_race=None, window_id=None, window_age_ms=None,
            status=status, err_key=key, gate=gate_of(status, key),
            has_ssx_hop=(1 if 'x-ssx-hop' in hdr else 0) if hdr is not None else None,
            has_restarts=(1 if 'fastly-restarts' in hdr else 0) if hdr is not None else None,
            retry_after=hdr.get('retry-after') if hdr else None,
            content_length=_int(hdr.get('content-length')) if hdr else None,
            envoy_ms=env, atc_rt_ms=a.get('rt'), proxied=None,
            cred_source=cred['src'] if cred else None, cred_detail=cred['det'] if cred else None,
            bank_age_s=cred['age'] if cred else None,
            src=a['src'], variant=a.get('variant'), ident_src=a.get('ident_src'),
            join_q=a['join_q'], join_dt_ms=a['join_dt'], resp_line=r['line'] if r else None,
            hdrs_line=(r or {}).get('hdr_line') if r else None, body_hint=a.get('hint'))


def parse_lines(run_id, lines):
    """Parse an iterable of physical lines (str, no newline). Returns finish() dict."""
    p = RunParser(run_id)
    for i, ln in enumerate(lines, 1):
        p.feed(i, ln)
    return p.finish()


def parse_file(path: Path):
    p = RunParser(path.stem)
    with open(path, 'rb') as fh:
        for i, raw in enumerate(fh, 1):
            p.feed(i, raw.rstrip(b'\r\n').decode('utf-8', 'replace'))
    return p.finish()


# ---------------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------------
SCHEMA = {
    'runs': ['run_id TEXT PRIMARY KEY', 'file TEXT', 'first_ts TEXT', 'last_ts TEXT', 'lines INTEGER',
             'parser_version INTEGER', 'utc_offset_min INTEGER', 'tz_src TEXT', 'atc_resp_form TEXT',
             'flip_log INTEGER'],
    'ingested': ['file TEXT PRIMARY KEY', 'size INTEGER', 'mtime REAL', 'parser_version INTEGER',
                 'built_at TEXT'],
    'shots': ['run_id TEXT', 'line INTEGER', 'ts_ms INTEGER', 'ts_src TEXT', 'ident TEXT', 'tcin TEXT',
              'tcin_src TEXT', 'race_seq INTEGER', 'race_src TEXT', 'shot_idx INTEGER', 'is_first INTEGER',
              'flip_opened_race INTEGER', 'window_id INTEGER', 'window_age_ms INTEGER', 'status INTEGER',
              'err_key TEXT', 'gate TEXT', 'has_ssx_hop INTEGER', 'has_restarts INTEGER', 'retry_after TEXT',
              'content_length INTEGER', 'envoy_ms INTEGER', 'atc_rt_ms INTEGER', 'proxied INTEGER',
              'cred_source TEXT', 'cred_detail TEXT', 'bank_age_s INTEGER', 'src TEXT', 'variant TEXT',
              'ident_src TEXT', 'join_q TEXT', 'join_dt_ms INTEGER', 'resp_line INTEGER', 'hdrs_line INTEGER',
              'body_hint TEXT'],
    'races': ['run_id TEXT', 'race_seq INTEGER', 'line INTEGER', 'tcin TEXT', 'width INTEGER', 'workers TEXT',
              'start_ms INTEGER', 'start_src TEXT', 'flip_opened INTEGER', 'window_id INTEGER',
              'lag_ms INTEGER', 'n_shots INTEGER', 'done_line INTEGER', 'units_bought INTEGER'],
    'flips': ['run_id TEXT', 'line INTEGER', 'tcin TEXT', 'flip_no INTEGER', 'read_ms INTEGER',
              'since_oos_ms INTEGER', 'new_window INTEGER', 'via TEXT', 'rt_ms INTEGER', 'last_oos_ms INTEGER',
              'status TEXT', 'window_id INTEGER'],
    'windows': ['run_id TEXT', 'window_id INTEGER', 'tcin TEXT', 'first_read_ms INTEGER',
                'last_read_ms INTEGER', 'end_ms INTEGER', 'reads INTEGER', 'flips INTEGER', 'races INTEGER',
                'shots INTEGER', 'trunc INTEGER', 'line INTEGER'],
    'tickets': ['run_id TEXT', 'line INTEGER', 'ts_ms INTEGER', 'ident TEXT', 'tcin TEXT', 'cart_id TEXT',
                'n INTEGER', 'cls TEXT', 'gap_s REAL', 'ms_since_201 INTEGER', 'live INTEGER',
                'win_age_s INTEGER', 'layer TEXT', 'mode TEXT', 'status INTEGER', 'key TEXT',
                'envoy_ms INTEGER', 'js_ms INTEGER'],
    'loop_ends': ['run_id TEXT', 'line INTEGER', 'ts_ms INTEGER', 'ident TEXT', 'tcin TEXT', 'reason TEXT',
                  'verdict TEXT', 'tickets_call INTEGER', 'tickets_cart INTEGER', 'live INTEGER', 'oos INTEGER',
                  'sched_used INTEGER', 'verified INTEGER', 'cvv_put TEXT', 'fs_seen TEXT', 'dl_left_s REAL',
                  'held INTEGER'],
    'decoys': ['run_id TEXT', 'line INTEGER', 'ts_ms INTEGER', 'ts TEXT', 'ts_src TEXT', 'status INTEGER',
               'key TEXT', 'shape_tokens INTEGER', 'selftest TEXT', 'envoy_ms INTEGER', 'pair_q TEXT'],
    'monitor_stats': ['run_id TEXT', 'line INTEGER', 'ts_ms INTEGER', 'ts TEXT', 't_s REAL', 'sweeps INTEGER',
                      's200 INTEGER', 's403 INTEGER', 's429 INTEGER', 'other INTEGER', 'beh INTEGER',
                      'outstanding INTEGER'],
    'orders': ['run_id TEXT', 'line INTEGER', 'ts_ms INTEGER', 'ts TEXT', 'ident TEXT', 'tcin TEXT',
               'order_id TEXT', 'route TEXT', 'ident_src TEXT'],
    'idents': ['run_id TEXT', 'ident TEXT', 'proxied INTEGER', 'proxied_src TEXT', 'boot_proxied INTEGER',
               'exp_yes INTEGER', 'exp_no INTEGER'],
    'unparsed': ['run_id TEXT', 'marker TEXT', 'n INTEGER', 'seen INTEGER', 'parsed INTEGER',
                 'first_line INTEGER'],
    'checks': ['run_id TEXT', 'name TEXT', 'value TEXT'],
}
RUN_TABLES = [t for t in SCHEMA if t not in ('runs', 'ingested')]
INDEXES = ['CREATE INDEX IF NOT EXISTS ix_shots_run ON shots(run_id, race_seq, ident)',
           'CREATE INDEX IF NOT EXISTS ix_races_run ON races(run_id, race_seq)',
           'CREATE INDEX IF NOT EXISTS ix_decoys_run ON decoys(run_id)',
           'CREATE INDEX IF NOT EXISTS ix_stats_run ON monitor_stats(run_id, line)']


def connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    gi = db.parent / '.gitignore'
    if db.parent.name == 'events' and not gi.exists():
        # derived from gitignored logs/runs/; never commit the store
        gi.write_text('*\n', encoding='utf-8')
    con = sqlite3.connect(str(db))
    for t, cols in SCHEMA.items():
        con.execute('CREATE TABLE IF NOT EXISTS %s (%s)' % (t, ', '.join(cols)))
        have = {r[1] for r in con.execute('PRAGMA table_info(%s)' % t)}
        for c in cols:
            name = c.split()[0]
            if name not in have and 'PRIMARY KEY' not in c:
                con.execute('ALTER TABLE %s ADD COLUMN %s' % (t, c))
    for ix in INDEXES:
        con.execute(ix)
    return con


def store(con: sqlite3.Connection, path: Path, res: dict, size: int, mtime: float) -> None:
    run_id = path.stem
    for t in RUN_TABLES:
        con.execute('DELETE FROM %s WHERE run_id=?' % t, (run_id,))
    con.execute('DELETE FROM runs WHERE run_id=?', (run_id,))
    r = res['run']
    con.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)',
                (run_id, str(path.name), r['first_ts'], r['last_ts'], r['lines'], PARSER_VERSION,
                 r['utc_offset_min'], r['tz_src'], r['atc_resp_form'], r['flip_log']))
    for t in RUN_TABLES:
        rows = res.get(t) or []
        if not rows:
            continue
        cols = [c.split()[0] for c in SCHEMA[t] if c.split()[0] != 'run_id']
        con.executemany('INSERT INTO %s (run_id, %s) VALUES (?%s)' % (t, ', '.join(cols), ',?' * len(cols)),
                        [(run_id,) + tuple(row.get(c) for c in cols) for row in rows])
    con.execute('INSERT OR REPLACE INTO ingested VALUES (?,?,?,?,?)',
                (path.name, size, mtime, PARSER_VERSION, time.strftime('%Y-%m-%d %H:%M:%S')))


def summarize(run_id, res) -> str:
    sh = res['shots']
    g = Counter(s['gate'] for s in sh)
    bad = [u for u in res['unparsed'] if u['n']]
    parts = ['%s: lines=%d shots=%d %s races=%d flips=%d windows=%d decoys=%d tickets=%d loop_ends=%d '
             'stats=%d orders=%d' % (run_id, res['run']['lines'], len(sh), dict(sorted(g.items())),
                                     len(res['races']), len(res['flips']), len(res['windows']),
                                     len(res['decoys']), len(res['tickets']), len(res['loop_ends']),
                                     len(res['monitor_stats']), len(res['orders']))]
    parts.append('    unparsed remainder: ' + (', '.join('%s %d/%d (first line %s)' % (
        u['marker'], u['n'], u['seen'], u['first_line']) for u in bad) if bad else 'none'))
    return '\n'.join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', default=str(DEFAULT_DB))
    ap.add_argument('--only', action='append', help='run id or file stem (repeatable)')
    ap.add_argument('--rebuild', action='store_true', help='drop every table and re-ingest')
    ap.add_argument('--logs', default=str(RUNS_DIR), help='directory of run_*.log files')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    db = Path(a.db)
    if a.rebuild and db.exists():
        con = sqlite3.connect(str(db))
        for t in SCHEMA:
            con.execute('DROP TABLE IF EXISTS %s' % t)
        con.commit()
        con.close()
    con = connect(db)
    files = sorted(Path(a.logs).glob('run_*.log'))
    if a.only:
        want = {w.replace('.log', '') for w in a.only}
        files = [f for f in files if f.stem in want]
        missing = want - {f.stem for f in files}
        if missing:
            print('not found: %s' % ', '.join(sorted(missing)))
    done = skipped = 0
    tot = Counter()
    t0 = time.time()
    for f in files:
        st = f.stat()
        row = con.execute('SELECT size, mtime, parser_version FROM ingested WHERE file=?', (f.name,)).fetchone()
        if row and row[0] == st.st_size and abs((row[1] or 0) - st.st_mtime) < 1e-6 and row[2] == PARSER_VERSION:
            skipped += 1
            continue
        t1 = time.time()
        res = parse_file(f)
        store(con, f, res, st.st_size, st.st_mtime)
        con.commit()
        done += 1
        for u in res['unparsed']:
            tot[u['marker']] += u['n']
        if not a.quiet:
            print(summarize(f.stem, res) + '   [%.1fs]' % (time.time() - t1), flush=True)
    print('== %d file(s) parsed, %d unchanged, %.0fs, db=%s' % (done, skipped, time.time() - t0, db))
    if done:
        print('== unparsed remainder over the parsed files: %s' % (
            ', '.join('%s=%d' % kv for kv in sorted(tot.items()) if kv[1]) or 'none'))
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
