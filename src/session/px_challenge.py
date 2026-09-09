"""HUMAN Security (PerimeterX) "Press & Hold" challenge — DETECTION helpers.

2026-09-07. Target's storefront runs HUMAN Security's sensor (formerly
PerimeterX) in addition to F5 Shape: every one of our three Target account
jars carries the ``_px2 / _px3 / _pxhd / _pxvid / pxcts`` set on ``.target.com``,
and a flagged browser gets the "Press & Hold" interstitial (HUMAN's Human
Challenge) on page navigations, while API reads answer 403 with a captcha
envelope (RedSky: ``{"captchaRelativeURL": "/captcha?trackingId=..."}``).

This module only RECOGNISES the challenge so the bot can stop, alert, and hand
the widget to a person. It never presses, drags, dispatches input into, or
otherwise interacts with the widget — human verification stays human.

Pure helpers (no browser, no I/O) so they are unit-testable; the async DOM
probe lives on SessionManager and uses ``PX_MARKERS_JS`` below (a read-only
querySelector/innerText snapshot).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

FLAG_ENV = 'TARGET_PX_CHALLENGE_GUARD'          # 1 (default) / 0
PARK_ENV = 'TARGET_PX_CHALLENGE_PARK_S'         # seconds the heavy sentinel rungs stay parked
_DEFAULT_PARK_S = 300.0
_MIN_PARK_S = 30.0


def guard_enabled() -> bool:
    """Flag-gated (default ON). ``TARGET_PX_CHALLENGE_GUARD=0`` restores the
    exact pre-09-07 ladder (challenge page == dead session)."""
    return os.environ.get(FLAG_ENV, '1').strip().lower() not in ('0', 'false', 'no', 'off', '')


def park_seconds() -> float:
    try:
        return max(_MIN_PARK_S, float(os.environ.get(PARK_ENV, str(_DEFAULT_PARK_S))))
    except (TypeError, ValueError):
        return _DEFAULT_PARK_S


# Read-only DOM snapshot. querySelector + innerText + location only — no events,
# no clicks, nothing dispatched into the page.
PX_MARKERS_JS = r"""(() => {
  const q = (s) => { try { return !!document.querySelector(s); } catch (e) { return false; } };
  const txt = ((document.body && document.body.innerText) || '').slice(0, 20000);
  return {
    url: String(location.href || ''),
    title: String(document.title || ''),
    px_container: q('#px-captcha, [id^="px-captcha"], [class*="px-captcha"]'),
    px_iframe: q('iframe[src*="px-cdn.net"], iframe[src*="px-cloud.net"], iframe[src*="/captcha/"]'),
    press_hold: /press\s*(&|&amp;|and)\s*hold/i.test(txt),
    denied: /access to this page has been denied|verify (that )?you are (a )?human|are you a human\??/i.test(txt),
    ready: String(document.readyState || '')
  };
})()"""

_URL_HINTS = ('/captcha', '/blocked', 'px-captcha', 'trackingid=')


def is_px_challenge(markers: Any) -> bool:
    """True iff a ``PX_MARKERS_JS`` snapshot (or an equivalent dict) shows the
    HUMAN challenge / block page. Conservative: an ordinary Target page (no
    widget container, no Press & Hold copy, normal URL) is never a challenge,
    even though HUMAN's sensor script is present on every page."""
    if not isinstance(markers, Mapping):
        return False
    if markers.get('px_container') or markers.get('px_iframe'):
        return True
    if markers.get('press_hold') or markers.get('denied'):
        return True
    url = str(markers.get('url') or '').lower()
    return any(h in url for h in _URL_HINTS)


def describe(markers: Any) -> str:
    """One-line human summary for logs."""
    if not isinstance(markers, Mapping):
        return 'no-markers'
    hits = [k for k in ('px_container', 'px_iframe', 'press_hold', 'denied') if markers.get(k)]
    url = str(markers.get('url') or '')[:120]
    title = str(markers.get('title') or '')[:60]
    return f"hits={','.join(hits) or 'url-only'} url={url!r} title={title!r}"


def body_looks_px_blocked(status: Optional[int], body: Any) -> bool:
    """API-response classifier: a HUMAN block/captcha envelope on a 403 (or the
    RedSky captcha JSON). Used to label ATC / place-order / stock 403s as
    ``px_block`` instead of the generic 'Shape 403 HTML'."""
    try:
        st = int(status) if status is not None else 0
    except (TypeError, ValueError):
        return False                           # garbage status — never classify as a block
    if st not in (403, 429, 0):
        return False
    low = (body or '').lower() if isinstance(body, str) else ''
    if not low:
        return False
    if 'captcha' in low:                       # RedSky captchaRelativeURL / px-captcha / captcha.js
        return True
    if 'blockscript' in low and 'appid' in low:  # HUMAN Advanced Blocking Response JSON
        return True
    if 'press & hold' in low or 'press and hold' in low or 'press &amp; hold' in low:
        return True
    return False
