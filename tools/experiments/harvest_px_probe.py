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

# EXP-SURFACE (2026-11-18): NON-product-page surfaces, to test whether the Press & Hold wall is
# PDP-specific or site-wide. Pure DOM-read targets run through the SAME is_px_challenge detector as the
# decoy PDPs, interleaved on the same line/time. NO login, NO cart, NO checkout, NO click -- just nav+read.
# If a surface here is CLEAN while the decoy PDPs wall, it is a candidate place to mint a Shape sensor
# off the Press & Hold (the a0 token is not PDP-URL-bound -- banked_replay already swaps decoy sensors
# onto hot-SKU cart POSTs). target.com/s is search results; the homepage; a public category browse page.
DEFAULT_SURFACES = [
    "https://www.target.com/",
    "https://www.target.com/s?searchTerm=pokemon+trading+cards",
    "https://www.target.com/s?searchTerm=paper+towels",
]


def _free_local_port() -> int:
    """A free loopback TCP port for the local forwarder listener."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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


def _surface_label(url: str) -> str:
    """A short stable label for a non-PDP surface URL, for per-surface wall-rate rows."""
    u = str(url or "")
    if "searchTerm=" in u:
        term = u.split("searchTerm=", 1)[1].split("&", 1)[0]
        return "search:" + term.replace("+", " ").replace("%20", " ")[:24]
    if u.rstrip("/").endswith("target.com"):
        return "home"
    tail = u.rstrip("/").rsplit("/", 1)[-1]
    return (tail or "surface")[:24]


class Probe:
    """One fresh guest Chrome (the verified money-safe launch + attach check), read-only navigation."""

    def __init__(self, out, settle_s: float, proxy: str = ""):
        self.out, self.settle_s = out, settle_s
        self.proxy = proxy or ""          # "host:port" of a local forwarder; "" = home line
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
        if self.proxy:                                 # route page traffic through a local forwarder;
            bargs.append(f"--proxy-server={self.proxy}")   # DevTools stays on the direct loopback socket,
            bargs.append("--proxy-bypass-list=localhost;127.0.0.1")  # and loopback bypasses the proxy
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

    async def exit_ip(self) -> str:
        """One nav to a plain IP-echo (NOT target.com) to confirm the egress IP.
        Lets us prove the resi route is actually carrying traffic (not a silent
        home read) before we trust the wall rate. Best-effort; returns '' on error."""
        try:
            if self.tab is None:
                self.tab = await asyncio.wait_for(self.browser.get("https://api.ipify.org/?format=text"), timeout=30)
            else:
                await asyncio.wait_for(self.tab.get("https://api.ipify.org/?format=text"), timeout=30)
            await asyncio.sleep(1.0)
            txt = await asyncio.wait_for(
                self.tab.evaluate("document.body ? document.body.innerText.trim() : ''"), timeout=5.0)
            return str(txt or "")[:60]
        except Exception as e:
            return f"<err {type(e).__name__}>"

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
    if not a.during_drop and not P.in_allowed_window(time.time()):
        print("refused: runs only 06:00-23:05 local; pass --during-drop to run in the 00-06 drop "
              "band beside the live bot (money-safe guards intact; relaxes ONLY the clock window)")
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

    if a.during_drop:
        out("[HARVEST_PX] DURING-DROP mode: running in the drop band beside the live bot. Money-safe "
            "guards intact (fresh guest profile; NO login; NO add-to-cart / cart / checkout; attach-check "
            "before any nav). Relaxes ONLY the 06:00-23:05 clock window. Read the verdict against the bot's "
            "MISS-PROBE wall presence for this window -- see docs/experiments/HARVEST_PX_DURING_DROP_READOUT.md.")
    # --- optional SECOND-LINE routing (EXP-LINE): fresh guest over an IPRoyal resi exit ---
    # Answers: does a DIFFERENT exit escape the Press & Hold wall that is up on the home
    # line right now? resi walls too (LINE-BOUND) => Target-global; resi clean
    # (PROFILE-CLEAN) => the wall keys on the home LINE/IP and resi is the automation path.
    pool = None
    proxy = a.proxy or ""
    if a.resi_index is not None:
        from src.lanes import lane_pool            # proxy-pool loader (reads proxyIps.json; no transaction code)
        from src.proxy import local_forwarder      # auth-fronting CONNECT forwarder (network routing only)
        resi = lane_pool._load_proxy_config().get("residential", [])
        if not resi or a.resi_index >= len(resi):
            print(f"refused: --resi-index {a.resi_index} but only {len(resi)} residential proxies configured")
            return 2
        fport = _free_local_port()
        pool = local_forwarder.ForwarderPool()
        pool.add_upstream(resi[a.resi_index], port=fport)          # credential never printed
        await pool.start_all()
        proxy = f"127.0.0.1:{fport}"
        out(f"[HARVEST_PX] SECOND-LINE: routing fresh-guest Chrome through IPRoyal resi "
            f"index={a.resi_index} via local forwarder 127.0.0.1:{fport} (metered; short run)")

    out(f"[HARVEST_PX] start decoys={decoys} loads={a.loads} cadence_s={a.cadence_s} "
        f"settle_s={a.settle_s} max_min={a.max_min} during_drop={a.during_drop} "
        f"line={'resi:'+str(a.resi_index) if a.resi_index is not None else ('proxy:'+proxy if proxy else 'home')}")
    probe = Probe(out, a.settle_s, proxy=proxy)
    recs: list = []
    deadline = time.time() + a.max_min * 60
    rc = 0
    try:
        await probe.start()
        if proxy:
            ip = await probe.exit_ip()
            out(f"[HARVEST_PX] exit_ip={ip} (confirm this is NOT the home IP before trusting the wall rate)")
        surfaces = [u.strip() for u in (a.urls or "").split(",") if u.strip()]
        # targets: (kind, ident, url). PDP decoys are the control; non-PDP surfaces are interleaved on the
        # SAME line/time so the wall-rate comparison is apples-to-apples (EXP-SURFACE, 2026-11-18).
        targets = [("pdp", t, SH.pdp_url(t)) for t in decoys] + \
                  [("surface", _surface_label(u), u) for u in surfaces]
        if surfaces:
            out(f"[HARVEST_PX] EXP-SURFACE: comparing {len(decoys)} decoy PDPs vs {len(surfaces)} non-PDP "
                f"surfaces={[_surface_label(u) for u in surfaces]} (nav+read only; NO click/login/cart)")
        stop = False
        eff_loads = a.loads if not a.during_drop else 10 ** 9   # during-drop: run until the --max-min deadline
        for li in range(eff_loads):
            if stop:
                break
            for kind, ident, url in targets:
                if time.time() >= deadline:
                    out("[HARVEST_PX] max_min reached; stopping")
                    stop = True
                    break
                info = await probe.load(url)
                now_iso, now_ms = time.strftime("%Y-%m-%dT%H:%M:%S"), int(time.time() * 1000)
                if "_probe_error" in info:
                    out(f"[HARVEST_PX] load {kind}={ident} i={li} PROBE_ERROR {info['_probe_error']}")
                    recs.append({"ts": now_iso, "ts_ms": now_ms, "kind": kind, "ident": ident,
                                 "tcin": ident if kind == "pdp" else None, "i": li, "error": info["_probe_error"]})
                    continue
                px = bool(PX.is_px_challenge(info))
                desc = PX.describe(info)
                out(f"[HARVEST_PX] MISS-PROBE px={'yes' if px else 'no'} {kind}={ident} i={li} {desc} "
                    f"{SH.miss_probe_fields(info)}")
                recs.append({"ts": now_iso, "ts_ms": now_ms, "kind": kind, "ident": ident,
                             "tcin": ident if kind == "pdp" else None, "i": li, "px": px,
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
        if pool is not None:
            try:
                await pool.stop_all()
            except Exception:
                pass
    # PDP decoys are the control -- summarize() keeps its original PDP-only semantics (the safety test pins it).
    pdp_recs = [r for r in recs if r.get("kind", "pdp") == "pdp"]
    summary = summarize(decoys, pdp_recs)
    # EXP-SURFACE: the decisive comparison -- is the wall PDP-specific (non-PDP clean) or site-wide?
    surf_ans = [r for r in recs if r.get("kind") == "surface" and "error" not in r]
    surface_summary = None
    if surf_ans:
        s_walled = [r for r in surf_ans if r.get("px")]
        s_rate = round(len(s_walled) / len(surf_ans), 3) if surf_ans else None
        per_surf = {}
        for u in [u.strip() for u in (a.urls or "").split(",") if u.strip()]:
            lab = _surface_label(u)
            a_ = [r for r in surf_ans if r.get("ident") == lab]
            w_ = [r for r in a_ if r.get("px")]
            per_surf[lab] = {"answered": len(a_), "walled": len(w_),
                             "wall_rate": round(len(w_) / len(a_), 3) if a_ else None}
        surface_summary = {"non_pdp_wall_rate": s_rate, "answered": len(surf_ans), "per_surface": per_surf}
        out(f"[HARVEST_PX] SURFACE-BREAKDOWN non_pdp_wall_rate={s_rate} answered={len(surf_ans)} "
            f"per_surface={ {k: per_surf[k]['wall_rate'] for k in per_surf} }  "
            f"(PDP wall_rate={summary['overall_wall_rate']})")
        if s_rate is not None and s_rate <= 0.10 and summary["overall_wall_rate"] and summary["overall_wall_rate"] >= 0.50:
            out("[HARVEST_PX] => NON-PDP CLEAN while PDP WALLS: a non-PDP surface is a CANDIDATE to mint a "
                "Shape sensor off the Press & Hold (free/automated). Next: a sensor-mint capture test on it.")
        elif s_rate is not None and s_rate >= 0.50:
            out("[HARVEST_PX] => NON-PDP ALSO WALLS: the 'mint on a non-PDP surface' bypass is DEAD "
                "(the wall is not PDP-specific on this line).")
        else:
            out("[HARVEST_PX] => inconclusive surface read (need more loads or the PDP control did not wall).")
    doc = {"experiment": "EXP-HARVEST-OFFLINE", "started": stamp, "decoys": decoys, "loads": a.loads,
           "cadence_s": a.cadence_s, "settle_s": a.settle_s, "during_drop": bool(a.during_drop),
           "shots": recs, "summary": summary, "surface_summary": surface_summary, "log": str(log_path)}
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
    ap.add_argument("--during-drop", action="store_true", dest="during_drop",
                    help="allow running in the 00-06 drop band beside the live bot to settle profile-bound vs "
                         "line-bound (money-safe guards intact; relaxes ONLY the clock window)")
    ap.add_argument("--resi-index", type=int, default=None, dest="resi_index",
                    help="EXP-LINE: route the fresh-guest Chrome through residential_proxies[N] (IPRoyal) via a "
                         "local forwarder, to test whether a SECOND line escapes the home-line wall (metered data)")
    ap.add_argument("--proxy", default="", help="route Chrome through an existing local forwarder host:port")
    ap.add_argument("--urls", default=",".join(DEFAULT_SURFACES),
                    help="EXP-SURFACE: comma-separated NON-PDP target.com URLs to test for the Press & Hold "
                         "wall alongside the decoy PDPs (nav+read only; default: homepage + two search pages). "
                         "Pass --urls '' to test PDPs only.")
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
