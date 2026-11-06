#!/usr/bin/env python3
"""EXP-HARVEST-OFFLINE (2026-11-06): is the harvest-tab HUMAN/PX wall PROFILE-bound or LINE-bound?

On the 2026-11-05/06 drop the edge limiter opened at volume for the first time in the zero-night era,
but a HUMAN/PX wall hit the bot's (logged-in) harvest tabs at ~04:34 and drained the Shape bank, so
all 273 admitted shots fired the page_signed fallback (0.3% cart) instead of banked_replay (29.7%).
The recoverable-units lever is keeping the harvest bank alive through that wall. The first question is
WHERE the wall keys: does it follow our persistent ACCOUNT profiles, or our home LINE/IP? This probe
measures the PX-wall rate the bot's own decoy harvest PDPs serve to a BRAND-NEW guest profile on the
home line, right now, with the bot down.

PRE-REGISTERED READOUT (written 2026-11-06, before any run):
  wall(load) = the loaded page carries a PX marker (px_container / px_iframe / press&hold /
               access-denied), judged by the bot's OWN detector: shape_harvest.HARVEST_MISS_PROBE_JS
               scored by px_challenge.is_px_challenge -- byte-identical to a bot `MISS-PROBE px=yes`.
  answered   = a load that reached a labelled page (not a nav/probe error).
  Over N loads per decoy on ONE fresh guest profile from the home line (>= 6 answered total):
    LINE-BOUND     overall wall rate >= 50%  -> a brand-new profile hits the same wall our accounts
                   did, so the wall keys on the home LINE / IP (or is Target-global), NOT on our
                   account profiles. Next: a fresh profile over a SECOND network line (resi/hotspot).
    PROFILE-CLEAN  overall wall rate <= 10% AND >= 1 clean load per decoy -> a fresh profile harvests
                   clean on the home line, so the drop-night wall was tied to our aged ACCOUNT profiles
                   / their logged-in state. Next: profile hygiene / rotation for the account harvest tabs.
    INCONCLUSIVE   anything between, < 6 answered, or mostly Target errors unrelated to PX.
  LIMITS this run acknowledges: a SINGLE-TIME snapshot on ONE line with the bot DOWN. It cannot by
  itself separate a home-LINE wall from a Target-GLOBAL wall (that needs the second line, EXP-LINE).
  It CAN rule PROFILE in or out: a brand-new profile walling is strong evidence the wall is not our
  account profiles; a brand-new profile staying clean is evidence it is (or was time-bound).

MONEY-SAFE BY CONSTRUCTION (reuses the money-safe primitives of tools/experiments/
limkey_guest_probe.py, which converged over 3 Opus verifier rounds 2026-10-16):
  - a FRESH temporary Chrome profile, deleted at the end; NEVER an account profile; NO login, NO read
    of config/target_accounts.json, NO add-to-cart / cart / pre_checkout / checkout / payment EVER.
    It ONLY navigates to public product-detail-page URLs (target.com/p/-/A-<tcin>) and READS the DOM
    (HARVEST_MISS_PROBE_JS is a pure DOM query -- no clicks, no fetch, no POST).
  - its OWN DevTools port range 31000-39999 (P.probe_free_port; no attach race with the bot's
    Chromes), the dynamic-range guard, and P.attach_check before ANY navigation (our port, our pid
    listening, exactly one blank page target, zero target.com cookies). On an attach failure it
    navigates nothing, never calls browser.stop() (that would close someone else's Chrome), kills
    only the process it spawned, and exits without the library's exit hooks.
  - the bot's Chromes are untouched: `tools/ops/bot_ctl.py` kills only nodriver-profile /
    session_profiles / smoke_profiles paths, and this temp profile matches none of them.
  Shared state it touches: the home network line (a handful of PDP loads; the bot's own harvest loads
  these exact decoys ~4x/min when it runs -- and the bot is DOWN now, so the line is quiet).

    venv\\Scripts\\python.exe tools\\experiments\\harvest_px_probe.py                  # 3 bot decoys, 8 loads each
    venv\\Scripts\\python.exe tools\\experiments\\harvest_px_probe.py --loads 6 --cadence-s 20
    venv\\Scripts\\python.exe tools\\experiments\\harvest_px_probe.py --tcins 21516452 --loads 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import limkey_guest_probe as P                       # noqa: E402  (verified money-safe launch primitives)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.session import shape_harvest as SH          # noqa: E402  (the bot's OWN detector + pdp_url)
from src.session import px_challenge as PX           # noqa: E402  (the bot's OWN px verdict)

OUT_DIR = ROOT / "logs" / "experiments"
# run_bot_with_nightly_restart.bat TARGET_HARVEST_TCINS as of 2026-11-06 (the decoys the bot harvests on).
DEFAULT_DECOYS = ["21516452", "50225561", "53274278"]
MIN_ANSWERED = 6


def validate_tcins(raw: str) -> list:
    """Decoy TCINs to probe: digits only, never the ATC control decoy. Raises on anything else."""
    out = []
    for t in re.split(r"[,;\s]+", raw or ""):
        t = t.strip()
        if not t:
            continue
        if not re.fullmatch(r"\d{6,13}", t):
            raise ValueError(f"bad tcin {t!r}")
        if t == P.DECOY:
            raise ValueError(f"{t} is the ATC control decoy, not a harvest PDP")
        if t not in out:
            out.append(t)
    return out


def summarize(decoys: list, recs: list) -> dict:
    """Per-decoy and overall PX-wall rate + the pre-registered verdict."""
    answered = [r for r in recs if "error" not in r]
    walled = [r for r in answered if r.get("px")]
    per = {}
    for t in decoys:
        a = [r for r in answered if r["tcin"] == t]
        w = [r for r in a if r.get("px")]
        per[t] = {"answered": len(a), "walled": len(w), "clean": len(a) - len(w),
                  "wall_rate": round(len(w) / len(a), 3) if a else None}
    n = len(answered)
    rate = (len(walled) / n) if n else None
    every_decoy_has_clean = all((per[t]["answered"] - per[t]["walled"]) >= 1 for t in decoys)
    if n < MIN_ANSWERED:
        verdict = "INCONCLUSIVE"
    elif rate is not None and rate >= 0.50:
        verdict = "LINE-BOUND"
    elif rate is not None and rate <= 0.10 and every_decoy_has_clean:
        verdict = "PROFILE-CLEAN"
    else:
        verdict = "INCONCLUSIVE"
    line = (f"verdict={verdict} answered={n} walled={len(walled)} "
            f"overall_wall_rate={rate if rate is None else round(rate, 3)} "
            f"per_decoy={ {t: per[t]['wall_rate'] for t in decoys} }")
    return {"verdict": verdict, "answered": n, "walled": len(walled),
            "overall_wall_rate": rate, "per_decoy": per, "line": line}


class Probe:
    """One fresh guest Chrome (the verified money-safe launch + attach check), read-only navigation."""

    def __init__(self, out, settle_s: float):
        self.out, self.settle_s = out, settle_s
        self.browser = self.tab = self.profile = None

    async def start(self) -> None:
        import zendriver as uc
        from zendriver import cdp
        import zendriver.core.util as _zutil
        _zutil.free_port = P.probe_free_port          # this process only (no attach race with bot Chromes)
        self.cdp = cdp
        self.profile = Path(tempfile.mkdtemp(prefix="harvest_px_probe_"))
        bargs = ["--window-size=1100,800", "--no-first-run", "--no-default-browser-check",
                 "--disable-extensions", "--disable-sync", "--window-position=-32000,-32000"]
        cfg = uc.Config(user_data_dir=str(self.profile), headless=False, sandbox=True,
                        browser_args=bargs, browser_connection_timeout=1.0, browser_connection_max_tries=30)
        from zendriver.core.browser import Browser
        self.browser = Browser(cfg)                   # hold the handle first: a timed-out start() is still killable
        await asyncio.wait_for(self.browser.start(), timeout=90)
        why = await P.attach_check(self.browser, cdp, self.profile)
        if why:
            self.out(f"[HARVEST_PX] ABORT attach check: {why} - nothing navigated; killing only our own "
                     f"process; exiting without browser.stop()")
            P._kill_own(self.browser)
            shutil.rmtree(self.profile, ignore_errors=True)
            os._exit(4)                               # skip the library's exit hooks
        self.out(f"[HARVEST_PX] attach check OK (port {self.browser.config.port})")

    async def load(self, url: str) -> dict:
        """Navigate to url (a validated PDP), settle, and return the bot's HARVEST_MISS_PROBE_JS dict."""
        if self.tab is None:
            self.tab = await asyncio.wait_for(self.browser.get(url), timeout=40)
        else:
            await asyncio.wait_for(self.tab.get(url), timeout=40)
        await asyncio.sleep(self.settle_s)
        try:
            info = await asyncio.wait_for(self.tab.evaluate(SH.HARVEST_MISS_PROBE_JS), timeout=5.0)
        except Exception as e:
            return {"_probe_error": f"{type(e).__name__}: {str(e)[:120]}"}
        return info if isinstance(info, dict) else {}

    async def close(self) -> None:
        try:
            if self.browser is not None:
                await asyncio.wait_for(self.browser.stop(), timeout=10)   # our own browser: stop() is correct
        except Exception:
            pass
        P._kill_own(self.browser)
        await asyncio.sleep(1.0)
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
        self.browser = self.tab = None


async def run(a) -> int:
    why = P.dynamic_range_clear()
    if why:
        print(f"refused: {why}")
        return 5
    if not P.in_allowed_window(time.time()):
        print("refused: runs only 06:00-23:05 local (off-window harvest probe)")
        return 3
    try:
        decoys = validate_tcins(a.tcins)
    except ValueError as e:
        print(f"refused: {e}")
        return 2
    if not decoys:
        print("no decoy TCIN")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = OUT_DIR / f"harvest_px_probe_{stamp}.log"
    json_path = OUT_DIR / f"harvest_px_probe_{stamp}.json"
    logf = log_path.open("a", encoding="utf-8")

    def out(line: str) -> None:
        s = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}"
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    out(f"[HARVEST_PX] start decoys={decoys} loads={a.loads} cadence_s={a.cadence_s} "
        f"settle_s={a.settle_s} max_min={a.max_min}")
    probe = Probe(out, a.settle_s)
    recs: list = []
    deadline = time.time() + a.max_min * 60
    rc = 0
    try:
        await probe.start()
        stop = False
        for li in range(a.loads):
            if stop:
                break
            for tcin in decoys:
                if time.time() >= deadline:
                    out("[HARVEST_PX] max_min reached; stopping")
                    stop = True
                    break
                url = SH.pdp_url(tcin)
                info = await probe.load(url)
                now_iso, now_ms = time.strftime("%Y-%m-%dT%H:%M:%S"), int(time.time() * 1000)
                if "_probe_error" in info:
                    out(f"[HARVEST_PX] load tcin={tcin} i={li} PROBE_ERROR {info['_probe_error']}")
                    recs.append({"ts": now_iso, "ts_ms": now_ms, "tcin": tcin, "i": li,
                                 "error": info["_probe_error"]})
                    continue
                px = bool(PX.is_px_challenge(info))
                desc = PX.describe(info)
                out(f"[HARVEST_PX] MISS-PROBE px={'yes' if px else 'no'} tcin={tcin} i={li} {desc} "
                    f"{SH.miss_probe_fields(info)}")
                recs.append({"ts": now_iso, "ts_ms": now_ms, "tcin": tcin, "i": li, "px": px,
                             "hint": info.get("hint"), "px_container": bool(info.get("px_container")),
                             "px_iframe": bool(info.get("px_iframe")), "press_hold": bool(info.get("press_hold")),
                             "denied": bool(info.get("denied")), "url": str(info.get("url") or ""),
                             "title": str(info.get("title") or "")[:80]})
                await asyncio.sleep(a.cadence_s)
    except KeyboardInterrupt:
        out("[HARVEST_PX] interrupted")
    except Exception as e:
        out(f"[HARVEST_PX] ERROR {type(e).__name__}: {str(e)[:160]}")
        rc = 1
    finally:
        await probe.close()
    summary = summarize(decoys, recs)
    doc = {"experiment": "EXP-HARVEST-OFFLINE", "started": stamp, "decoys": decoys, "loads": a.loads,
           "cadence_s": a.cadence_s, "settle_s": a.settle_s, "shots": recs, "summary": summary,
           "log": str(log_path)}
    try:
        json_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    except Exception as e:
        out(f"[HARVEST_PX] summary write failed {type(e).__name__}")
    out(f"[HARVEST_PX] END {summary['line']}")
    out(f"[HARVEST_PX] readout {json_path}")
    logf.close()
    return rc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="EXP-HARVEST-OFFLINE: harvest-PDP PX-wall rate on a fresh guest profile")
    ap.add_argument("--tcins", default=",".join(DEFAULT_DECOYS), help="decoy harvest TCINs (default: the bot's 3)")
    ap.add_argument("--loads", type=int, default=8, help="loads per decoy")
    ap.add_argument("--cadence-s", type=float, default=20.0, dest="cadence_s", help="seconds between loads")
    ap.add_argument("--settle-s", type=float, default=3.0, dest="settle_s", help="settle after each nav before reading")
    ap.add_argument("--max-min", type=float, default=20.0, dest="max_min", help="hard wall-clock cap (minutes)")
    return ap


def main() -> int:
    a = build_parser().parse_args()
    try:
        return asyncio.run(run(a))
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
