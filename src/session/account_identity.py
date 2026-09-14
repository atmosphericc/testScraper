"""Per-account device identity — a stable, DISTINCT browser fingerprint per Target account.

WHY THIS EXISTS
---------------
The existing SessionManager generates a fingerprint dict (session_manager.py:95-138)
but never actually applies it: the UA is overwritten from the live browser
(session_manager.py:208-222) and timezone/viewport/locale are never pushed to the
page via CDP. Net effect: every account profile presents the SAME real device
fingerprint (canvas / WebGL / audio / timezone / hardware). For a single account
that is fine. For a multi-account farm it is the single strongest cross-account
linker — Target/Shape can tie N "different" accounts back to one physical device
and ban the whole cluster, no matter how well cookies and IPs are isolated.

WHAT THIS DOES
--------------
Derives a stable fingerprint DETERMINISTICALLY from the account_id (so the same
account always looks like the same device across runs — real users keep one
device), and applies it to a live tab via CDP Emulation BEFORE any Target
navigation. Distinct accounts get distinct UA build / platform / viewport /
timezone / hardware / canvas-noise seed.

DESIGN NOTES
------------
- Deterministic: seeded from sha256(account_id). Same id -> same identity, always.
- Conservative variation: we vary OS/minor-build/viewport/timezone/hardware, NOT
  the rendering engine — a UA wildly out of step with the real Chrome JA3/TLS is
  itself a Shape detection vector. Keep the installed Chrome within ~1-2 majors of
  the spoofed build.
- Pure-Python + CDP only. No network. Safe to import and run offline; this file's
  __main__ block self-checks determinism + distinctness without a browser.

This module is additive and imported only by the new harvester / opt-in callers.
It does not change any existing code path.
"""

from __future__ import annotations

import hashlib
import os
import random as _random
import re
from typing import Any, Dict, List, Optional


def _spoof_js_enabled() -> bool:
    """Whether to inject the JS fingerprint spoof (``_spoof_js``).

    DEFAULT OFF as of 2026-09-08. ``_spoof_js`` overrides
    ``CanvasRenderingContext2D.prototype.getImageData`` and installs
    ``navigator`` ``defineProperty`` getters via
    ``add_script_to_evaluate_on_new_document`` — both are JS-detectable tamper
    tells (``getImageData.toString()`` and the property-descriptor getters no
    longer read ``[native code]``, exactly the prototype-tamper signal F5 Shape
    and HUMAN/PerimeterX look for), while the heavy axes (WebGL, canvas
    ``toDataURL``, audio) stay unmasked — so it never actually unlinked the
    accounts (see memory ``reference_why_our_spoof_fails_engine_level_antidetect``).
    Detectable but ineffective. The CDP UA/timezone/locale/viewport overrides in
    ``apply_identity`` are unaffected — they are the genuine emulation path (not
    JS patches) and ``_px3`` is UA-signed, so identity coherence with login is
    preserved. Restore the pre-09-08 behaviour: ``TARGET_SPOOF_JS=1``.
    """
    return os.environ.get('TARGET_SPOOF_JS', '0').strip().lower() in ('1', 'true', 'yes', 'on')

# Realistic desktop Chrome builds. MUST track the real installed Chrome major on
# the host — a UA claiming a different major than the real JA3/TLS handshake is
# itself the Shape detection vector that BLOCKED logins on 2026-06-24 (the module
# was pinned to 130-132 while the host ran 149/150).
# 2026-08-22: the host auto-updated 150 -> 151.0.7922.173 on 2026-08-20 (the day
# before the 08-21 drop), so every login + the business real-Chrome purchase arm
# spent 08-21 presenting a Chrome/150 UA on a 151 engine — the exact UA-vs-engine
# incoherence this list exists to prevent. Bumped to the real installed build.
# 2026-08-25: host auto-updated again to 151.0.7922.174 — bumped to match.
# 2026-09-03: host auto-updated to 152.0.7977.82 (caught by the pre-drop check for
# the 09-04 restock; the 09-01 jars were minted under 151). Bumped to match —
# hand_login_all.bat MUST be re-run so every jar mints under the 152 UA.
# Verify after each Chrome auto-update: this string must equal the ProductVersion
# of C:\Program Files\Google\Chrome\Application\chrome.exe. fp-chromium accounts
# (primary/alt-1) suppress this JS spoof and present their own 148 engine UA, so
# this only governs the login browsers and the business (real-Chrome) purchase arm.
_CHROME_BUILDS: List[str] = ["152.0.7977.82"]

# 2026-09-13: the pinned build drifted AGAIN (.82 pinned, 152.0.7977.84 installed) --
# the fourth time (06-24, 08-21, 09-03, 09-13). Read the installed version from the
# Chrome install dir at runtime instead; the list above is only the fallback when
# nothing is found. TARGET_CHROME_VERSION=<a.b.c.d> forces a value (tests / other
# hosts); TARGET_UA_AUTODETECT=0 restores the pinned-list behaviour.
_CHROME_APP_DIRS: List[str] = [
    r"C:\Program Files\Google\Chrome\Application",
    r"C:\Program Files (x86)\Google\Chrome\Application",
    os.path.join(os.environ.get("LOCALAPPDATA", "") or "", "Google", "Chrome", "Application"),
]
_installed_cache: Dict[str, Optional[str]] = {}


def installed_chrome_version() -> Optional[str]:
    """The installed real Chrome's full version ('152.0.7977.84') or None."""
    forced = (os.environ.get("TARGET_CHROME_VERSION") or "").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", forced):
        return forced
    if (os.environ.get("TARGET_UA_AUTODETECT", "1") or "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if "v" in _installed_cache:
        return _installed_cache["v"]
    found: List[str] = []
    for d in _CHROME_APP_DIRS:
        try:
            if d and os.path.isdir(d):
                found += [n for n in os.listdir(d)
                          if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", n) and os.path.isdir(os.path.join(d, n))]
        except OSError:
            continue
    if not found:
        _installed_cache["v"] = None
        return None
    found.sort(key=lambda v: [int(x) for x in v.split(".")])
    _installed_cache["v"] = found[-1]
    return found[-1]


def reset_installed_cache() -> None:
    _installed_cache.clear()


def ua_mode() -> str:
    """TARGET_UA_MODE: 'legacy' (default = the pre-09-13 CDP User-Agent override with a
    FULL-version UA string and a GREASE-less brand list) or 'engine' (no UA/brand
    override at all: the tab keeps the real Chrome's reduced 'Chrome/<major>.0.0.0'
    UA and its real sec-ch-ua brand list, byte-identical to the harvest/warmup tabs
    that mint the Shape sensor). 2026-09-13 (09-11 forensics): the purchase tab
    presented 'Chrome/152.0.7977.82' while the harvest tab whose banked sensor it
    replayed had sent 'Chrome/152.0.0.0' + '"Not?A_Brand";v="24"' -- two identities
    inside one browser. Real Chrome 101+ never sends a full-version UA header."""
    m = (os.environ.get("TARGET_UA_MODE") or "legacy").strip().lower()
    return "engine" if m == "engine" else "legacy"


class _EngineMode(Exception):
    """Internal: apply_identity skips the UA override in engine mode."""

# (UA platform token, navigator.platform, UA-CH platform, UA-CH platformVersion)
# Windows-only: the host is Windows 11, and "macOS-on-Windows" is an incoherent
# fingerprint Shape flags (2026-06-24). Win11 reports UA-CH platformVersion "15.0.0".
_PLATFORMS = [
    ("Windows NT 10.0; Win64; x64", "Win32", "Windows", "15.0.0"),
]

_VIEWPORTS: List[Dict[str, int]] = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 2560, "height": 1440},
]

# US timezones — kept selectable so callers can match the account's pinned proxy
# geo when that is known (passing timezone=... overrides the seeded pick).
_TIMEZONES: List[str] = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
]

_HW_CONCURRENCY: List[int] = [4, 8, 12, 16]
_DEVICE_MEMORY: List[int] = [8, 16, 32]
_LOCALES: List[str] = ["en-US"]


def _seeded_rng(account_id: str) -> _random.Random:
    """A deterministic RNG keyed by account_id (stable across processes/runs)."""
    digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
    return _random.Random(int(digest[:16], 16))


def build_identity(account_id: str, timezone: Optional[str] = None) -> Dict[str, Any]:
    """Build a stable, distinct device identity for `account_id`.

    Args:
        account_id: free-form account label (e.g. "primary", "alt-1").
        timezone: optional override (e.g. matched to the account's proxy geo).
                  When None, a timezone is picked deterministically from the id.

    Returns a plain dict (JSON-serialisable) describing the device. Apply it to a
    live tab with `apply_identity(tab, identity)`.
    """
    rng = _seeded_rng(account_id)
    ua_platform, nav_platform, ch_platform, ch_platform_version = rng.choice(_PLATFORMS)
    build = rng.choice(_CHROME_BUILDS)     # keeps the seeded draw order stable
    _inst = installed_chrome_version()
    if _inst:
        build = _inst                      # 2026-09-13: track the installed build
    major = build.split(".")[0]
    _mode = ua_mode()
    # engine mode: exactly what real Chrome puts on the wire (UA reduction, 101+)
    _ua_ver = f"{major}.0.0.0" if _mode == "engine" else build
    ua = (
        f"Mozilla/5.0 ({ua_platform}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{_ua_ver} Safari/537.36"
    )
    viewport = rng.choice(_VIEWPORTS)
    tz = timezone or rng.choice(_TIMEZONES)
    return {
        "account_id": account_id,
        "user_agent": ua,
        "ua_full_version": build,
        "ua_major_version": major,
        "ua_mode": _mode,
        "platform": nav_platform,
        "ua_ch_platform": ch_platform,
        "ua_ch_platform_version": ch_platform_version,
        "viewport": dict(viewport),
        "timezone": tz,
        "locale": rng.choice(_LOCALES),
        "hardware_concurrency": rng.choice(_HW_CONCURRENCY),
        "device_memory": rng.choice(_DEVICE_MEMORY),
        # Seed for deterministic per-account canvas/audio noise (see _spoof_js).
        "canvas_seed": rng.randint(1, 1_000_000),
    }


def identity_signature(identity: Dict[str, Any]) -> tuple:
    """A comparable tuple of the linkable fingerprint axes (for tests/audits)."""
    return (
        identity.get("user_agent"),
        identity.get("platform"),
        identity.get("timezone"),
        (identity.get("viewport") or {}).get("width"),
        (identity.get("viewport") or {}).get("height"),
        identity.get("hardware_concurrency"),
        identity.get("device_memory"),
        identity.get("canvas_seed"),
    )


def _spoof_js(identity: Dict[str, Any]) -> str:
    """JS injected on every new document to harden the fingerprint beyond UA/TZ.

    Overrides navigator.hardwareConcurrency / deviceMemory and adds a tiny,
    DETERMINISTIC per-account perturbation to canvas + audio readbacks so two
    accounts on the same physical GPU do not hash to the same canvas fingerprint.
    All wrapped in try/catch so a failure can never break page rendering.
    """
    hw = int(identity.get("hardware_concurrency", 8))
    mem = int(identity.get("device_memory", 8))
    seed = int(identity.get("canvas_seed", 1))
    return f"""
(() => {{
  try {{
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw} }});
  }} catch (e) {{}}
  try {{
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {mem} }});
  }} catch (e) {{}}
  try {{
    // Deterministic LCG from the per-account seed.
    let _s = {seed} >>> 0;
    const _rand = () => {{ _s = (_s * 1664525 + 1013904223) >>> 0; return _s / 4294967296; }};
    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
      const data = _origGetImageData.apply(this, args);
      try {{
        const d = data.data;
        for (let i = 0; i < d.length; i += 4) {{
          if (_rand() < 0.02) {{ d[i] = d[i] ^ (1 & (_s & 1)); }}
        }}
      }} catch (e) {{}}
      return data;
    }};
  }} catch (e) {{}}
}})();
"""


async def apply_identity(tab, identity: Dict[str, Any]) -> Dict[str, bool]:
    """Apply `identity` to a live zendriver tab via CDP, BEFORE Target navigation.

    Each override is independent and best-effort: one failing CDP call (e.g. a
    method absent in the running CDP build) never aborts the rest, and never
    raises. Returns a dict of {override_name: applied?} for logging/auditing.

    NOTE: correctness of the CDP calls themselves can only be confirmed against a
    live browser — this is intentionally defensive so it degrades gracefully.
    """
    from zendriver import cdp  # local import: keep this module importable offline

    results: Dict[str, bool] = {}
    ua = identity.get("user_agent")
    viewport = identity.get("viewport") or {}

    # 1) User-Agent (+ UA Client Hints metadata so navigator.userAgentData agrees)
    try:
        # 2026-09-13 engine mode: SKIP the override. Real Chrome already sends the
        # reduced UA + its own brand list (with the GREASE entry this metadata never
        # carried), and that is exactly what the harvest/warmup tabs -- where the
        # Shape sensor is minted -- present. One identity per browser, no per-tab drift.
        if str(identity.get("ua_mode") or ua_mode()) == "engine":
            raise _EngineMode()
        ua_metadata = None
        try:
            ua_metadata = cdp.emulation.UserAgentMetadata(
                platform=identity.get("ua_ch_platform", "Windows"),
                platform_version=identity.get("ua_ch_platform_version", "15.0.0"),
                architecture="x86",
                model="",
                mobile=False,
                brands=[
                    cdp.emulation.UserAgentBrandVersion(
                        brand="Chromium", version=identity.get("ua_major_version", "131")
                    ),
                    cdp.emulation.UserAgentBrandVersion(
                        brand="Google Chrome", version=identity.get("ua_major_version", "131")
                    ),
                ],
            )
        except Exception:
            ua_metadata = None
        await tab.send(cdp.emulation.set_user_agent_override(
            user_agent=ua,
            platform=identity.get("platform"),
            user_agent_metadata=ua_metadata,
        ))
        results["user_agent"] = True
    except _EngineMode:
        results["user_agent"] = False
        results["ua_mode"] = "engine"      # the engine's real UA + brands stay in force
    except Exception:
        results["user_agent"] = False

    # 2) Timezone
    try:
        await tab.send(cdp.emulation.set_timezone_override(
            timezone_id=identity.get("timezone", "America/New_York")
        ))
        results["timezone"] = True
    except Exception:
        results["timezone"] = False

    # 3) Locale
    try:
        await tab.send(cdp.emulation.set_locale_override(locale=identity.get("locale", "en-US")))
        results["locale"] = True
    except Exception:
        results["locale"] = False

    # 4) Viewport / device metrics
    try:
        await tab.send(cdp.emulation.set_device_metrics_override(
            width=int(viewport.get("width", 1920)),
            height=int(viewport.get("height", 1080)),
            device_scale_factor=1,
            mobile=False,
        ))
        results["viewport"] = True
    except Exception:
        results["viewport"] = False

    # 5) navigator hardening + canvas/audio noise on every new document.
    # 2026-09-08: DEFAULT OFF (see _spoof_js_enabled). The injected getImageData
    # override + navigator defineProperty getters are a detectable prototype
    # tamper that never unlinked the accounts; skipping the injection removes the
    # tell while the CDP UA/tz/locale/viewport overrides above (which _px3's
    # UA-signature and login rely on) stay in force. Re-enable: TARGET_SPOOF_JS=1.
    if _spoof_js_enabled():
        try:
            await tab.send(cdp.page.add_script_to_evaluate_on_new_document(source=_spoof_js(identity)))
            results["spoof_js"] = True
        except Exception:
            results["spoof_js"] = False
    else:
        results["spoof_js"] = False

    return results


if __name__ == "__main__":
    # Offline self-check: determinism + cross-account distinctness. No browser.
    ids = ["primary", "alt-1", "alt-2", "alt-3", "alt-4"]
    built = {a: build_identity(a) for a in ids}

    # Determinism: rebuilding yields an identical identity.
    for a in ids:
        assert build_identity(a) == built[a], f"identity for {a} is not deterministic"

    # Distinctness: no two accounts share the full linkable signature.
    sigs = [identity_signature(built[a]) for a in ids]
    assert len(set(sigs)) == len(sigs), "two accounts share a fingerprint signature"

    print("account_identity self-check OK")
    for a in ids:
        i = built[a]
        print(
            f"  {a:8s} ua=...Chrome/{i['ua_full_version']:9s} "
            f"plat={i['platform']:8s} tz={i['timezone']:20s} "
            f"vp={i['viewport']['width']}x{i['viewport']['height']} "
            f"hw={i['hardware_concurrency']} mem={i['device_memory']} "
            f"canvas={i['canvas_seed']}"
        )
