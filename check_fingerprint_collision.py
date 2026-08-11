#!/usr/bin/env python3
"""GO/NO-GO for engine-level antidetect + validation of fingerprint-chromium.

Three modes:
  (default)      real Chrome + production JS spoof (account_identity) applied.
  --no-spoof     real Chrome, no spoof — baseline.
  --fp-chromium  launch the fingerprint-chromium build with each account's
                 deterministic --fingerprint seed (fresh throwaway profiles, no
                 JS spoof). This is the FREE validation: it should flip the heavy
                 device axes from COLLIDE -> distinct.

In every mode it reads WebGL renderer + canvas(toDataURL) + audio + a getImageData
control axis from each enabled account and prints a COLLIDE/distinct report.

  * default / --no-spoof: expect WebGL/canvas/audio COLLIDE (one shared device) —
    the smoking gun that our JS spoof cannot un-link the accounts.
  * --fp-chromium: expect WebGL/canvas/audio DISTINCT — proof engine-level masking
    yields 3 devices from one machine, i.e. the go-signal to run it live.

SAFE / READ-ONLY: launches each browser on about:blank, reads its own fingerprint
via JS, closes it. No Target, no proxy, no login, no add-to-cart. --fp-chromium
uses fresh temp profiles, so the real logged-in profiles are never touched.

Run:  venv/Scripts/python.exe check_fingerprint_collision.py                 (spoof on)
      venv/Scripts/python.exe check_fingerprint_collision.py --no-spoof      (baseline)
      TARGET_FP_CHROMIUM_PATH=<chrome.exe> \\
        venv/Scripts/python.exe check_fingerprint_collision.py --fp-chromium (validation)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import zendriver as uc

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session.account_identity import build_identity, apply_identity  # noqa: E402
from src.session.fp_chromium import (  # noqa: E402
    executable_path as fp_executable_path,
    build_args as fp_build_args,
)

FP_CHROMIUM = "--fp-chromium" in sys.argv
APPLY_SPOOF = "--no-spoof" not in sys.argv and not FP_CHROMIUM

# Reads WebGL renderer/vendor, a canvas 2D hash (toDataURL — the standard FP path),
# a getImageData control hash (the one path our JS spoof perturbs), and an
# AudioContext hash — the hardware-anchored axes Device ID+ weights most.
_FP_JS = r"""
(async () => {
  const out = {};
  try {
    const gl = document.createElement('canvas').getContext('webgl')
            || document.createElement('canvas').getContext('experimental-webgl');
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    out.webgl_vendor   = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
    out.webgl_renderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
  } catch (e) { out.webgl_renderer = 'ERR:' + e; }
  try {
    const c = document.createElement('canvas'); c.width = 260; c.height = 60;
    const x = c.getContext('2d');
    x.textBaseline = 'top'; x.font = "15px 'Arial'";
    x.fillStyle = '#f60'; x.fillRect(10, 10, 120, 30);
    x.fillStyle = '#069'; x.fillText('Cwm fjord bank glyphs — 8!', 14, 15);
    out.canvas = c.toDataURL();                  // standard FP path — our spoof does NOT hook this
    // Control axis: getImageData IS the one path our spoof perturbs (per-account
    // canvas_seed). Expect this to DIFFER while toDataURL COLLIDES — proof we are
    // masking the path nobody fingerprints through and leaving the real one bare.
    const gid = x.getImageData(0, 0, c.width, c.height).data;
    let h = 2166136261 >>> 0;                     // FNV-1a over the pixel bytes
    for (let i = 0; i < gid.length; i += 4) { h = ((h ^ gid[i]) * 16777619) >>> 0; }
    out.canvas_gid = 'fnv:' + (h >>> 0).toString(16);
  } catch (e) { out.canvas = 'ERR:' + e; }
  try {
    const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const ac = new Ctx(1, 44100, 44100);
    const osc = ac.createOscillator(); osc.type = 'triangle'; osc.frequency.value = 10000;
    const comp = ac.createDynamicsCompressor();
    osc.connect(comp); comp.connect(ac.destination); osc.start(0);
    const buf = await ac.startRendering();
    const d = buf.getChannelData(0); let s = 0;
    for (let i = 0; i < d.length; i++) s += Math.abs(d[i]);
    out.audio = s.toString();
  } catch (e) { out.audio = 'ERR:' + e; }
  out.ua = navigator.userAgent;
  out.hw = String(navigator.hardwareConcurrency);
  out.mem = String(navigator.deviceMemory);
  out.platform = navigator.platform;
  return JSON.stringify(out);
})()
"""


def _short(v: str) -> str:
    """Hash long readbacks (canvas dataURL / audio) to a stable short digest."""
    if not isinstance(v, str) or v.startswith("ERR:"):
        return v
    if len(v) > 40:
        return "sha1:" + hashlib.sha1(v.encode("utf-8")).hexdigest()[:16]
    return v


async def _stop(browser) -> None:
    try:
        r = browser.stop()
        if asyncio.iscoroutine(r):
            await r
    except Exception:
        pass


def _load_accounts():
    cfg = json.load(open(ROOT / "config" / "target_accounts.json", encoding="utf-8"))
    return [a for a in cfg.get("accounts", []) if a.get("enabled", True)]


async def _probe(acc: dict) -> dict:
    acc_id = acc.get("account_id", "?")
    profile = acc.get("profile_dir") or "nodriver-profile"
    tz = acc.get("timezone") or None
    _tmp = None
    if FP_CHROMIUM:
        exe = fp_executable_path()
        if not exe:
            raise RuntimeError(
                "fingerprint-chromium not found — set TARGET_FP_CHROMIUM_PATH to its chrome.exe")
        fp_args = fp_build_args(acc_id, tz)
        _tmp = tempfile.mkdtemp(prefix=f"fpcollide_{acc_id}_")
        print(f"\n[{acc_id}] launching fingerprint-chromium (fresh profile, args={fp_args})...")
        cfg = uc.Config(user_data_dir=_tmp, headless=False, browser_args=list(fp_args))
        cfg.browser_executable_path = exe
        browser = await uc.start(cfg)
    else:
        print(f"\n[{acc_id}] launching (profile={profile}, spoof={'ON' if APPLY_SPOOF else 'OFF'})...")
        browser = await uc.start(user_data_dir=str((ROOT / profile).resolve()), headless=False)
    try:
        tab = browser.tabs[0] if browser.tabs else await browser.get("about:blank")
        if APPLY_SPOOF:
            ident = build_identity(acc_id, timezone=tz)
            applied = await apply_identity(tab, ident)
            print(f"[{acc_id}] spoof applied: {applied}")
        await tab.get("about:blank")
        raw = await tab.evaluate(_FP_JS, await_promise=True)
        fp = json.loads(raw if isinstance(raw, str) else raw[0])
        print(f"[{acc_id}] WebGL : {fp.get('webgl_renderer')}")
        print(f"[{acc_id}] canvas: {_short(fp.get('canvas',''))}")
        print(f"[{acc_id}] audio : {_short(fp.get('audio',''))}   ua=...{str(fp.get('ua',''))[-28:]}")
        fp["account_id"] = acc_id
        return fp
    finally:
        await _stop(browser)
        await asyncio.sleep(0.8)
        if _tmp:
            shutil.rmtree(_tmp, ignore_errors=True)


async def main() -> int:
    accts = _load_accounts()
    if len(accts) < 2:
        print("Need >=2 enabled accounts to compare.")
        return 2
    if FP_CHROMIUM and not fp_executable_path():
        print("fingerprint-chromium not found. Set TARGET_FP_CHROMIUM_PATH to the chrome.exe "
              "(or place the build under ~/fp-chromium/win).")
        return 3
    fps = []
    for a in accts:
        try:
            fps.append(await _probe(a))
        except Exception as e:  # noqa: BLE001
            print(f"[{a.get('account_id')}] PROBE FAILED: {e}")

    print("\n" + "=" * 74)
    _mode = ("fingerprint-chromium (engine-level)" if FP_CHROMIUM
             else ("spoof ON — production" if APPLY_SPOOF else "baseline, no spoof"))
    print(f"COLLISION REPORT  ({_mode})")
    print("=" * 74)
    axes = [
        ("WebGL renderer", "webgl_renderer"),
        ("canvas hash", "canvas"),
        ("canvas getImageData", "canvas_gid"),
        ("audio hash", "audio"),
        ("user-agent", "ua"),
        ("hardwareConcurrency", "hw"),
        ("deviceMemory", "mem"),
    ]
    device_axes_collide = True
    for label, key in axes:
        vals = {_short(f.get(key, "")) for f in fps}
        collide = len(vals) == 1
        mark = "COLLIDE (identical)" if collide else "distinct"
        print(f"  {label:22s} {mark:20s} {'' if collide else '  '.join(sorted(str(v)[:24] for v in vals))}")
        if key in ("webgl_renderer", "canvas", "audio") and not collide:
            device_axes_collide = False

    print("-" * 74)
    if device_axes_collide:
        if FP_CHROMIUM:
            print("VERDICT: fingerprint-chromium did NOT diversify WebGL/canvas/audio — the")
            print("  --fingerprint seeds are not taking effect. Verify the flags/build before")
            print("  relying on it (DISTINCT axes were expected here).")
        else:
            print("VERDICT: SHARED DEVICE CONFIRMED — WebGL + canvas + audio are identical")
            print("  across all accounts even with the spoof on. Shape's Device ID+ sees ONE")
            print("  device. Engine-level antidetect (fingerprint-chromium) is the justified fix.")
    else:
        if FP_CHROMIUM:
            print("VERDICT: SUCCESS — fingerprint-chromium gives each account a DISTINCT")
            print("  WebGL/canvas/audio device from this ONE machine. Engine-level un-linking")
            print("  works. Next: re-login each account under fp-chromium, then go live.")
        else:
            print("VERDICT: device axes DIFFER — re-examine before spending on antidetect.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
