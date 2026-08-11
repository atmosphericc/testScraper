"""fingerprint-chromium integration — engine-level, per-account device identity.

WHY THIS EXISTS
---------------
Our JS spoof (``account_identity``) leaves the heaviest device-fingerprint axes
REAL and SHARED across every Target account on this one machine — WebGL is never
touched, canvas ``toDataURL``/``toBlob`` is unhooked (only ``getImageData`` is),
and AudioContext is never implemented. So Shape's "Device ID+" sees ONE physical
device behind all three identities (see ``docs/RETAILERS/target.md`` and the
2026-08-11 collision analysis). That shared score is the ATC 401/429 wall that
IP rotation and account swaps could not move.

fingerprint-chromium (adryfish, an Ungoogled-Chromium build) spoofs canvas /
WebGL / audio / fonts at the ENGINE level via a per-launch ``--fingerprint`` seed
— below the JS layer, so ``toString()`` still shows ``[native code]`` (no
detectable hook) and every readback path is coherent. A distinct seed per account
= a distinct, internally-consistent device from one desktop. This module is the
single, flag-gated seam that turns that on.

FULLY OPT-IN / REVERSIBLE (this is the rollback contract)
---------------------------------------------------------
Every function here is a NO-OP unless BOTH are true:
  * ``TARGET_FP_CHROMIUM`` is truthy (1/true/yes/on), AND
  * a fingerprint-chromium ``chrome.exe`` is found (``TARGET_FP_CHROMIUM_PATH``,
    or the default install dir ``~/fp-chromium/win``).
When either is missing, ``launch_overrides()`` returns ``(None, [])`` and
``profile_dir()`` returns its argument unchanged — so every caller behaves
EXACTLY as it did before this module existed. Kill-switch: unset
``TARGET_FP_CHROMIUM`` (or set it to ``0``). Nothing else needs reverting.

Pure-Python, no browser, safe to import offline. Only the PURCHASE workers and the
manual-login helper consult it; the stock sweep is intentionally left untouched.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_ENV_ENABLE = "TARGET_FP_CHROMIUM"
_ENV_PATH = "TARGET_FP_CHROMIUM_PATH"
_TRUTHY = {"1", "true", "yes", "on"}

# Where to probe for chrome.exe when TARGET_FP_CHROMIUM_PATH is unset. Matches the
# location the 2026-08-11 setup extracted the Windows build to.
_DEFAULT_DIRS: List[Path] = [
    Path.home() / "fp-chromium" / "win",
    Path.home() / "fp-chromium",
]

# Resolved exe is cached so repeated launches don't re-walk the install tree.
# reset_cache() clears it (tests toggle env between cases).
_resolved_exe: Optional[str] = None
_resolve_done: bool = False


def _truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in _TRUTHY


def reset_cache() -> None:
    """Forget the cached executable path (used by tests after changing env)."""
    global _resolved_exe, _resolve_done
    _resolved_exe = None
    _resolve_done = False


def is_enabled() -> bool:
    """True when the master switch is on.

    NOTE: this does NOT confirm a binary exists — use ``launch_overrides()`` for
    the effective on/off that also requires a usable chrome.exe.
    """
    return _truthy(os.environ.get(_ENV_ENABLE))


def _find_exe() -> Optional[str]:
    # 1) Explicit path wins — accept either the exe itself or a dir containing it.
    p = (os.environ.get(_ENV_PATH) or "").strip()
    if p:
        pp = Path(p)
        if pp.is_file():
            return str(pp.resolve())
        if pp.is_dir():
            for c in sorted(pp.rglob("chrome.exe")):
                return str(c.resolve())
        logger.warning("[FP_CHROMIUM] %s=%r not found on disk", _ENV_PATH, p)
        return None
    # 2) Probe the default install dirs.
    for d in _DEFAULT_DIRS:
        try:
            if d.is_dir():
                for c in sorted(d.rglob("chrome.exe")):
                    return str(c.resolve())
        except OSError:
            continue
    return None


def executable_path() -> Optional[str]:
    """Resolved fingerprint-chromium chrome.exe, or None if not found. Cached."""
    global _resolved_exe, _resolve_done
    if not _resolve_done:
        _resolved_exe = _find_exe()
        _resolve_done = True
    return _resolved_exe


def fingerprint_seed(account_id: str) -> int:
    """Deterministic 31-bit ``--fingerprint`` seed for an account.

    Stable across runs (like ``account_identity``): the same account always maps
    to the same engine-level device, and distinct accounts get distinct seeds.
    """
    h = hashlib.sha256(("fp-chromium:" + (account_id or "default")).encode("utf-8")).hexdigest()
    return (int(h[:8], 16) & 0x7FFFFFFF) or 1


def build_args(account_id: str, timezone: Optional[str] = None) -> List[str]:
    """fingerprint-chromium CLI flags for this account's engine-level device.

    Kept minimal + coherent: the seed drives canvas/WebGL/audio/fonts/hardware
    together, and we pin platform/brand/lang/timezone to match a real US Windows
    Chrome so nothing contradicts the seed-derived device.
    """
    seed = fingerprint_seed(account_id)
    args = [
        f"--fingerprint={seed}",
        "--fingerprint-platform=windows",
        "--fingerprint-brand=Chrome",
        "--accept-lang=en-US",
    ]
    tz = (timezone or "").strip()
    if tz:
        args.append(f"--timezone={tz}")
    return args


def launch_overrides(account_id: str, timezone: Optional[str] = None
                     ) -> Tuple[Optional[str], List[str]]:
    """The single call every launch site uses.

    Returns ``(executable_path, extra_browser_args)`` when fingerprint-chromium is
    enabled AND a binary is present, else ``(None, [])`` — callers then launch the
    system Chrome exactly as before.
    """
    if not is_enabled():
        return None, []
    exe = executable_path()
    if not exe:
        logger.warning(
            "[FP_CHROMIUM] %s is on but no chrome.exe found (set %s to the "
            "fingerprint-chromium exe). Falling back to system Chrome.",
            _ENV_ENABLE, _ENV_PATH,
        )
        return None, []
    return exe, build_args(account_id, timezone)


def profile_dir(base: str) -> str:
    """Return a sibling ``-fp`` profile dir when fingerprint-chromium is active.

    The 148 build must never open the real Chrome-150 profiles: a version
    downgrade can reset prefs and, worst case, disturb the live login-session.
    A dedicated ``-fp`` profile is also a clean, history-free device — better for
    un-linking. Unchanged (returns ``base``) whenever fp-chromium is off/absent.
    """
    base = str(base)
    if not is_enabled() or not executable_path():
        return base
    return base if base.endswith("-fp") else base + "-fp"


# --- LOGIN surface: a SEPARATE opt-in (fp-chromium on login is risky/unproven) -----
_ENV_LOGIN = "TARGET_FP_CHROMIUM_LOGIN"


def login_enabled() -> bool:
    """fp-chromium on the LOGIN surface — separate opt-in from the purchase path.

    Login is where Shape is most aggressive: BD-IP logins and antidetect browsers
    draw the generic 'Something went wrong' block, and login-device coherence is not
    proven necessary (the session cookie is portable to the purchase device). So the
    login tools stay on the proven real-Chrome path UNLESS BOTH TARGET_FP_CHROMIUM
    (master) AND TARGET_FP_CHROMIUM_LOGIN are set. Purchase (session_manager) is
    unaffected — it still fp's on TARGET_FP_CHROMIUM alone.
    """
    return is_enabled() and _truthy(os.environ.get(_ENV_LOGIN))


def login_overrides(account_id: str, timezone: Optional[str] = None
                    ) -> Tuple[Optional[str], List[str]]:
    """launch_overrides, but ONLY when login-fp is opted in (see login_enabled)."""
    if not login_enabled():
        return None, []
    exe = executable_path()
    if not exe:
        return None, []
    return exe, build_args(account_id, timezone)


def login_profile_dir(base: str) -> str:
    """profile_dir, but ONLY when login-fp is opted in (see login_enabled)."""
    base = str(base)
    if not login_enabled() or not executable_path():
        return base
    return base if base.endswith("-fp") else base + "-fp"


def describe() -> str:
    """One-line status string for boot/diagnostic logs."""
    if not is_enabled():
        return "fp-chromium: OFF (TARGET_FP_CHROMIUM unset)"
    exe = executable_path()
    return f"fp-chromium: ON exe={exe or 'MISSING — set TARGET_FP_CHROMIUM_PATH'}"


if __name__ == "__main__":
    # Offline self-check: seeds are deterministic + distinct; no browser launched.
    ids = ["primary", "business", "alt-1"]
    seeds = {a: fingerprint_seed(a) for a in ids}
    assert all(fingerprint_seed(a) == seeds[a] for a in ids), "seed not deterministic"
    assert len(set(seeds.values())) == len(ids), "seed collision across accounts"
    print("fp_chromium self-check OK")
    print(f"  {describe()}")
    for a in ids:
        print(f"  {a:8s} seed={seeds[a]:>10d}  args={build_args(a, 'America/Chicago')}")
        print(f"           profile('nodriver-profile') -> {profile_dir('nodriver-profile')}")
