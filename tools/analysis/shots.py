#!/usr/bin/env python3
"""Shared, read-only parser for the bot's run logs (logs/runs/run_*.log).

Every analysis script in tools/analysis/ builds on this so that a number quoted
in docs/ or memory can be re-derived by anyone with one command. Nothing here
touches the bot, the network or a browser.

Log conventions (2026-07 .. 2026-09 logs):
- Only logger lines carry timestamps: "2026-09-16 02:15:31,282 INFO ...". A
  plain print line's time is taken from the nearest preceding timestamped line.
- One fast-lane add-to-cart shot:
    "[FAST_LANE] Firing ATC→pre_checkout→place-order chain (tcin=N, qty=N)"
    "[FAST_LANE] chain done in X.XXs — atc=NNN pre=NNN po=NNN skip=... [ident=NAME]"
  The ident= tag exists only from the 2026-08-25 logs on; earlier logs have no
  identity tag (up to three account threads interleave), so ident is '?'.
- ATC outcome classes:
    edge429  : "[PURCHASE] ATC fetch: rate-limited (429) body=''"  (empty body =
               ERR_A2C_TCIN_RATE_LIMITED, the per-TCIN edge limiter)
    dco429   : same line with DCO_RATE_LIMITED in the body (the cart service's
               "Request throttled due to high demand item" throttle — the edge
               limiter ADMITTED the request)
    201      : "atc=201" on the chain-done line
    401      : "atc=401" (_ERR_AUTH_DENIED)
    other    : any other atc status (431, 503, 424 ...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, List, Optional

TS = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d):(\d\d):(\d\d),(\d{3})")
FIRE = re.compile(r"\[FAST_LANE\] Firing ATC.*?\(tcin=(\d+)")
DONE = re.compile(r"\[FAST_LANE\] chain done in ([\d.]+)s — atc=(\d+) pre=(\d+) po=(\d+)")
START = re.compile(r"\[PURCHASE\] Starting purchase for (\d+)")
OUTCOME = re.compile(r"ATC fetch status: (\d+)")
IDENT = re.compile(r"ident=(primary|business|alt-1|[A-Za-z]+)")   # stops before a glued "2026-..." logger line


@dataclass
class Shot:
    t: float            # seconds since the log's first day 00:00 (day rollover added)
    ident: str          # primary | business | alt-1 | '?'
    tcin: str
    atc: str            # raw atc status from the chain line
    cls: str            # edge429 | dco429 | 201 | 401 | other
    chain_s: float
    line: int           # 1-based line number of the chain-done line


def iter_lines(path: str) -> Iterator[str]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield line


def parse_shots(path: str) -> List[Shot]:
    """All main-tab add-to-cart shots in one log.

    Fast-lane shots come from the `[FAST_LANE] chain done` line; the 429 class
    is resolved from the `[PURCHASE] ATC fetch: rate-limited (429) body=...`
    line of the SAME identity (threads interleave, so matching is by ident, not
    by adjacency). Legacy-path shots (fired while a FAST_SELLING checkout
    cooldown keeps the fast lane off; they log no chain-done line) are picked up
    from an identity-tagged `[PURCHASE] ATC fetch` outcome line that has no
    pending fast-lane shot for that identity; their `chain_s` is 0.0. A legacy
    201 carries no ident tag in the log and is NOT counted (none seen so far).
    Cross-check for any log: len(shots) should equal the number of
    `[INTERCEPTOR:main] [ATC_RESP]` lines (2026-09-18 verifier: 138 = 138)."""
    shots: List[Shot] = []
    ts: Optional[float] = None
    day0: Optional[str] = None
    last_tcin: Optional[str] = None
    pending = {}                      # ident -> Shot awaiting its 429 detail line
    for n, line in enumerate(iter_lines(path), 1):
        m = TS.match(line)
        if m:
            if day0 is None:
                day0 = m.group(1)
            t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) + int(m.group(5)) / 1000
            if m.group(1) != day0:
                t += 86400
            ts = t
        f = FIRE.search(line)
        if f:
            last_tcin = f.group(1)
        sp = START.search(line)
        if sp:
            last_tcin = sp.group(1)
        d = DONE.search(line)
        if d:
            atc = d.group(2)
            im = IDENT.search(line)
            idn = im.group(1) if im else "?"
            cls = {"201": "201", "401": "401"}.get(atc, "429" if atc == "429" else "other")
            shot = Shot(ts or 0.0, idn, last_tcin or "?", atc, cls, float(d.group(1)), n)
            shots.append(shot)
            if cls != "201":             # a 201's detail line carries no ident tag
                pending[idn] = shot       # every other fast-lane shot consumes its own detail line
            else:
                pending.pop(idn, None)
            continue
        if "[PURCHASE] ATC fetch" in line and "auth denied" not in line:
            im = IDENT.search(line)
            idn = im.group(1) if im else "?"
            is429 = "rate-limited (429)" in line
            st = OUTCOME.search(line)
            if is429:
                cls = "dco429" if "DCO_RATE_LIMITED" in line else "edge429"
            elif st:
                cls = {"201": "201", "401": "401"}.get(st.group(1), "other")
            else:
                continue
            prev = pending.pop(idn, None)
            if prev is not None:
                if prev.cls == "429" and is429:
                    prev.cls = cls
                continue
            if not im:
                continue                  # an untagged outcome with no pending shot: fast-lane 201 detail
            # legacy-path shot (no chain-done line)
            shots.append(Shot(ts or 0.0, idn, last_tcin or "?", st.group(1) if st else "429", cls, 0.0, n))
    for s_ in shots:
        if s_.cls == "429":               # 429 with no detail line seen
            s_.cls = "edge429"
    return shots


def hhmmss(t: float) -> str:
    t = t % 86400
    return f"{int(t // 3600):02d}:{int(t % 3600) // 60:02d}:{int(t % 60):02d}"


def is_pass(s: Shot) -> bool:
    """'Passed the edge limiter' = any outcome but an empty-body edge 429.
    401s are excluded from denominators by the callers."""
    return s.cls not in ("edge429", "401")
