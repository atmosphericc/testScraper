#!/usr/bin/env python3
"""CDP-responsiveness probe per network line (2026-10-23, after the first residential night).

Why: on run_20261023_001005 the account routed through the IPRoyal residential session (alt-1, local forwarder
port 23003) had its CDP go silent at every 20-min sentinel tick (180 `[TAB_HEALTH] evaluate('true') timed out >2s`
+ 90 `genuinely wedged` lines, all in alt-1's tick minutes; `/json/version` answered in 13 ms meanwhile), 17
watchdog fast-restarts, 0 on the two home accounts; the residential GUEST follower's Chrome stopped answering
`evaluate("1+1")` every ~15 min (px_wall=0 pages, no PX), the Bright Data guest followers every ~50-60 min, the
home guest never. PX-walled HOME nights (10-14, 10-19) had 0 wedges, so the PX page is not the cause. This probe
measures the thing itself on a fresh GUEST Chrome per line: CDP evaluate latency every N s, a timed PDP navigation
every M s, Network event volume, and the local forwarder's bytes / errors. Money-free: a guest profile, no cart
call, no login. One log line per sample:

    [CDP_PROBE] tag=resi t=+120s eval_ms=41 events=12 fwd_out_kb=310 fwd_in_kb=4120 fwd_err=0
    [CDP_PROBE] tag=resi NAV ms=4210 px_wall=0 title='...'
    [CDP_PROBE] tag=resi eval TIMEOUT (>20s)  <- the stall

Usage (three in parallel, 30 min):
  python tools/experiments/line_cdp_probe.py --proxy home   --tag home --minutes 30
  python tools/experiments/line_cdp_probe.py --proxy resi:2 --tag resi --minutes 30
  python tools/experiments/line_cdp_probe.py --proxy reserve:0 --tag bd0 --minutes 30
Readout: the SUMMARY line at the end (eval p50/p95/max, timeouts, nav times, px walls, traffic). Never prints a
credential (the forwarder injects the auth; resolve_proxy's errors never carry the secret).
"""
import argparse
import asyncio
import datetime as dt
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import limkey_guest_follow as F  # noqa: E402  (ProxyRoute, Guest, resolve_proxy)
import limkey_guest_probe as P  # noqa: E402  (DECOY, attach_check, probe_free_port)


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def summarize(lines: list[str]) -> dict:
    """SUMMARY numbers out of the probe's own log lines (pure; tested offline)."""
    evals, timeouts, navs, nav_timeouts, px = [], 0, [], 0, 0
    fwd_out = fwd_in = fwd_err = 0
    for l in lines:
        if "[CDP_PROBE]" not in l:
            continue
        if " eval TIMEOUT" in l:
            timeouts += 1
            continue
        if " NAV " in l:
            if "ms=TIMEOUT" in l:
                nav_timeouts += 1
            else:
                try:
                    navs.append(int(l.split(" ms=", 1)[1].split()[0]))
                except Exception:
                    pass
            if "px_wall=1" in l:
                px += 1
            continue
        if " eval_ms=" in l:
            try:
                evals.append(int(l.split(" eval_ms=", 1)[1].split()[0]))
            except Exception:
                pass
            for key, name in (("fwd_out_kb=", "out"), ("fwd_in_kb=", "in"), ("fwd_err=", "err")):
                if key in l:
                    try:
                        v = int(l.split(key, 1)[1].split()[0])
                        if name == "out":
                            fwd_out = v
                        elif name == "in":
                            fwd_in = v
                        else:
                            fwd_err = v
                    except Exception:
                        pass
    return {"evals": len(evals), "eval_p50_ms": pct(evals, 0.5), "eval_p95_ms": pct(evals, 0.95),
            "eval_max_ms": max(evals) if evals else None, "eval_timeouts": timeouts,
            "navs": len(navs), "nav_p50_ms": pct(navs, 0.5), "nav_max_ms": max(navs) if navs else None,
            "nav_timeouts": nav_timeouts, "px_walls": px,
            "fwd_out_kb": fwd_out, "fwd_in_kb": fwd_in, "fwd_err": fwd_err}


async def run(a) -> int:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = ROOT / "logs" / "experiments" / f"cdp_probe_{a.tag}_{stamp}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def out(m: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}"
        lines.append(line)
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    pdp = a.url or f"https://www.target.com/p/-/A-{a.pdp}"
    route = None
    if a.proxy and a.proxy.lower() != "home":
        info = F.resolve_proxy(a.proxy)
        route = F.ProxyRoute(info)
        await route.start()
        out(f"[CDP_PROBE] tag={a.tag} route {info['display']} via local {route.chrome_server or '-'}")
    out(f"[CDP_PROBE] tag={a.tag} start minutes={a.minutes} every_s={a.every_s} nav_every_s={a.nav_every_s} pdp={a.pdp}")
    g = F.Guest(out, a.settle_s, pdp, a.tag, route)
    rc = 0
    try:
        t0 = time.perf_counter()
        await g.start()
        out(f"[CDP_PROBE] tag={a.tag} guest up in {int((time.perf_counter() - t0) * 1000)} ms egress={g.egress_masked or '-'} page_ok={int(g.page_ok)}")
        from zendriver import cdp
        counter = {"n": 0}

        async def on_any(ev):
            counter["n"] += 1

        for ev in (cdp.network.RequestWillBeSent, cdp.network.ResponseReceived, cdp.network.LoadingFinished,
                   cdp.network.LoadingFailed, cdp.network.DataReceived):
            g.tab.add_handler(ev, on_any)

        def fwd() -> tuple[int, int, int, str]:
            if route is None or route.pool is None:
                return 0, 0, 0, "-"
            bo, bi = route.traffic()
            errs, last, active, total, idle = 0, "-", 0, 0, 0
            for up in route.pool.upstreams.values():
                errs += int(getattr(up.stats, "errors", 0) or 0)
                last = str(getattr(up.stats, "last_error", "") or "-").replace(" ", "_")[:40]
                active += int(getattr(up.stats, "connections_active", 0) or 0)
                total += int(getattr(up.stats, "connections_total", 0) or 0)
                idle += int(getattr(up.stats, "idle_closes", 0) or 0)
            return bo // 1024, bi // 1024, errs, f"{last} conns={active}/{total} idle_closes={idle}"

        async def stall_diag(label: str) -> None:
            """At a stall: is the proxy path itself alive (raw GET through the same forwarder port), is Chrome's HTTP
            endpoint alive, does a NEW tab answer evaluate (renderer-level vs browser-level hang)?"""
            import urllib.request
            msgs = []
            if route is not None and route.chrome_server:
                t1 = time.perf_counter()
                try:
                    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": f"http://{route.chrome_server}"}))
                    with op.open(urllib.request.Request("http://api.ipify.org", headers={"User-Agent": "egress-probe"}), timeout=15) as r:
                        ok = bool(r.read(64))
                    msgs.append(f"raw_get_via_forwarder={'ok' if ok else 'empty'}:{int((time.perf_counter() - t1) * 1000)}ms")
                except Exception as e:  # noqa: BLE001
                    msgs.append(f"raw_get_via_forwarder=ERR_{type(e).__name__}:{int((time.perf_counter() - t1) * 1000)}ms")
            try:
                port = g.browser.config.port
                t1 = time.perf_counter()
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                    n = len(r.read())
                msgs.append(f"json_list=ok:{n}B:{int((time.perf_counter() - t1) * 1000)}ms")
            except Exception as e:  # noqa: BLE001
                msgs.append(f"json_list=ERR_{type(e).__name__}")
            t1 = time.perf_counter()
            try:
                nt = await asyncio.wait_for(g.browser.get("about:blank", new_tab=True), timeout=15)
                v = await asyncio.wait_for(nt.evaluate("2+2"), timeout=15)
                msgs.append(f"new_tab_eval={'ok' if v == 4 else v}:{int((time.perf_counter() - t1) * 1000)}ms")
                try:
                    await asyncio.wait_for(nt.close(), timeout=5)
                except Exception:
                    pass
            except Exception as e:  # noqa: BLE001
                msgs.append(f"new_tab_eval=ERR_{type(e).__name__}:{int((time.perf_counter() - t1) * 1000)}ms")
            out(f"[CDP_PROBE] tag={a.tag} STALL-DIAG {label} " + " ".join(msgs))

        start = time.time()
        until = start + a.minutes * 60.0
        next_eval = start
        next_nav = start + a.nav_every_s
        while time.time() < until:
            now = time.time()
            if now >= next_nav:
                next_nav = now + a.nav_every_s
                t1 = time.perf_counter()
                try:
                    await asyncio.wait_for(g.tab.get(pdp), timeout=a.nav_timeout)
                    ms = f"{int((time.perf_counter() - t1) * 1000)}"
                except asyncio.TimeoutError:
                    ms = "TIMEOUT"
                except Exception as e:  # noqa: BLE001
                    ms = f"ERR_{type(e).__name__}"
                await asyncio.sleep(a.settle_s)
                try:
                    title = str(await asyncio.wait_for(g.tab.evaluate("document.title"), timeout=10) or "")[:60]
                    px = int(bool(await asyncio.wait_for(g.tab.evaluate(
                        "!!document.querySelector('#px-captcha') || /press\\s*&\\s*hold|access denied/i.test(document.body ? document.body.innerText.slice(0, 4000) : '')"),
                        timeout=10)))
                except Exception as e:  # noqa: BLE001
                    title, px = f"ERR_{type(e).__name__}", -1
                out(f"[CDP_PROBE] tag={a.tag} NAV ms={ms} px_wall={px} title={title!r}")
                continue
            if now >= next_eval:
                next_eval = now + a.every_s
                n0 = counter["n"]
                t1 = time.perf_counter()
                try:
                    v = await asyncio.wait_for(g.tab.evaluate("1+1"), timeout=a.eval_timeout)
                    ms = int((time.perf_counter() - t1) * 1000)
                    bo, bi, errs, last = fwd()
                    out(f"[CDP_PROBE] tag={a.tag} t=+{int(now - start)}s eval_ms={ms} ok={int(v == 2)} events={counter['n'] - n0} "
                        f"fwd_out_kb={bo} fwd_in_kb={bi} fwd_err={errs} fwd_last={last}")
                except asyncio.TimeoutError:
                    bo, bi, errs, last = fwd()
                    out(f"[CDP_PROBE] tag={a.tag} t=+{int(now - start)}s eval TIMEOUT (>{a.eval_timeout:.0f}s) fwd_err={errs} fwd_last={last}")
                    rc = 1
                    stalls = counter.get("stalls", 0) + 1
                    counter["stalls"] = stalls
                    if stalls in (1, 2, 5):            # diagnose the first, second and fifth stall only
                        await stall_diag(f"stall#{stalls}")
                except Exception as e:  # noqa: BLE001
                    out(f"[CDP_PROBE] tag={a.tag} t=+{int(now - start)}s eval ERR_{type(e).__name__}")
                    rc = 1
                continue
            await asyncio.sleep(min(0.5, max(0.05, min(next_eval, next_nav) - time.time())))
    except Exception as e:  # noqa: BLE001
        out(f"[CDP_PROBE] tag={a.tag} ABORT {type(e).__name__}: {e}")
        rc = 2
    finally:
        try:
            await g.close()
        except Exception:
            pass
        if route is not None:
            bo, bi = route.traffic()
            out(f"[CDP_PROBE] tag={a.tag} traffic out_mb={bo / 1e6:.2f} in_mb={bi / 1e6:.2f} total_mb={(bo + bi) / 1e6:.2f}")
            await route.stop()
    s = summarize(lines)
    out(f"[CDP_PROBE] tag={a.tag} SUMMARY " + " ".join(f"{k}={v}" for k, v in s.items()))
    return rc


async def run_direct(a) -> int:
    """FS-W-B arm D (post-run 10-23): the SAME proxy session, but Chrome talks to the vendor gateway directly
    (--proxy-server=host:port) and answers the proxy-auth challenge through CDP Fetch.authRequired, so the
    credential is never on a command line or in a log, and the bot's ForwarderPool is out of the path. If this arm
    does not stall where the forwarder arm does, the stall is in our forwarder; if both stall, it is the Chrome +
    provider path. Same log lines as run() minus the forwarder counters."""
    import hashlib
    import re as _re
    import shutil
    import tempfile
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = ROOT / "logs" / "experiments" / f"cdp_probe_{a.tag}_{stamp}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def out(m: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}"
        lines.append(line)
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    info = F.resolve_proxy(a.proxy)
    if not info.get("url"):
        out(f"[CDP_PROBE] tag={a.tag} ABORT --no-forwarder needs a credentialed proxy entry")
        return 2
    m = _re.match(r"^http://([^:@/]+):([^@/]+)@([A-Za-z0-9._-]+:\d{2,5})$", info["url"])
    if not m:
        out(f"[CDP_PROBE] tag={a.tag} ABORT unexpected proxy URL shape")
        return 2
    user, pw, hostport = m.group(1), m.group(2), m.group(3)
    pdp = a.url or f"https://www.target.com/p/-/A-{a.pdp}"
    out(f"[CDP_PROBE] tag={a.tag} DIRECT arm: Chrome --proxy-server={hostport}, auth via CDP Fetch.authRequired (no forwarder)")
    out(f"[CDP_PROBE] tag={a.tag} start minutes={a.minutes} every_s={a.every_s} nav_every_s={a.nav_every_s} pdp={a.pdp}")
    import zendriver as uc
    from zendriver import cdp
    import zendriver.core.util as _zutil
    from zendriver.core.browser import Browser
    _zutil.free_port = P.probe_free_port
    profile = Path(tempfile.mkdtemp(prefix=f"limkey_guest_{a.tag}_"))
    bargs = [f"--proxy-server={hostport}", "--window-size=1100,800", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--disable-sync", "--window-position=-32000,-32000"]
    cfg = uc.Config(user_data_dir=str(profile), headless=False, sandbox=True, browser_args=bargs,
                    browser_connection_timeout=1.0, browser_connection_max_tries=30)
    browser = Browser(cfg)
    rc = 0
    try:
        await asyncio.wait_for(browser.start(), timeout=90)
        why = await P.attach_check(browser, cdp, profile)
        if why:
            out(f"[CDP_PROBE] tag={a.tag} ABORT attach check: {why}")
            P._kill_own(browser)
            return 4
        out(f"[CDP_PROBE] tag={a.tag} attach check OK (port {browser.config.port})")
        tab = await asyncio.wait_for(browser.get("about:blank"), timeout=30)
        auth_n = {"n": 0}

        async def on_auth(ev):
            try:
                auth_n["n"] += 1
                await tab.send(cdp.fetch.continue_with_auth(
                    ev.request_id, cdp.fetch.AuthChallengeResponse(response="ProvideCredentials", username=user, password=pw)))
            except Exception:
                pass

        async def on_paused(ev):
            try:
                await tab.send(cdp.fetch.continue_request(ev.request_id))
            except Exception:
                pass

        tab.add_handler(cdp.fetch.AuthRequired, on_auth)
        tab.add_handler(cdp.fetch.RequestPaused, on_paused)
        await asyncio.wait_for(tab.send(cdp.fetch.enable(handle_auth_requests=True)), timeout=10)
        await asyncio.wait_for(tab.get("https://api.ipify.org"), timeout=40)
        await asyncio.sleep(1.0)
        ip = str(await asyncio.wait_for(tab.evaluate("document.body ? document.body.innerText : ''"), timeout=10) or "").strip()[:45]
        parts = ip.split(".")
        masked = (parts[0] + "." + parts[1] + ".x.x#" + hashlib.sha1(ip.encode()).hexdigest()[:4]) if len(parts) == 4 else "?"
        out(f"[CDP_PROBE] tag={a.tag} egress={masked} auth_challenges={auth_n['n']}")
        if masked == "?":
            out(f"[CDP_PROBE] tag={a.tag} ABORT egress unreadable through the direct proxy")
            return 3
        t1 = time.perf_counter()
        await asyncio.wait_for(tab.get(pdp), timeout=a.nav_timeout)
        await asyncio.sleep(a.settle_s)
        title = str(await asyncio.wait_for(tab.evaluate("document.title"), timeout=10) or "")[:60]
        out(f"[CDP_PROBE] tag={a.tag} guest up; first PDP in {int((time.perf_counter() - t1) * 1000)} ms title={title!r}")
        px_js = "!!document.querySelector('#px-captcha') || /press\\s*&\\s*hold|access denied/i.test(document.body ? document.body.innerText.slice(0, 4000) : '')"
        start = time.time()
        until = start + a.minutes * 60.0
        next_eval, next_nav = start, start + a.nav_every_s
        while time.time() < until:
            now = time.time()
            if now >= next_nav:
                next_nav = now + a.nav_every_s
                t1 = time.perf_counter()
                try:
                    await asyncio.wait_for(tab.get(pdp), timeout=a.nav_timeout)
                    ms = f"{int((time.perf_counter() - t1) * 1000)}"
                except asyncio.TimeoutError:
                    ms = "TIMEOUT"
                except Exception as e:  # noqa: BLE001
                    ms = f"ERR_{type(e).__name__}"
                await asyncio.sleep(a.settle_s)
                try:
                    title = str(await asyncio.wait_for(tab.evaluate("document.title"), timeout=10) or "")[:60]
                    px = int(bool(await asyncio.wait_for(tab.evaluate(px_js), timeout=10)))
                except Exception as e:  # noqa: BLE001
                    title, px = f"ERR_{type(e).__name__}", -1
                out(f"[CDP_PROBE] tag={a.tag} NAV ms={ms} px_wall={px} title={title!r}")
                continue
            if now >= next_eval:
                next_eval = now + a.every_s
                t1 = time.perf_counter()
                try:
                    v = await asyncio.wait_for(tab.evaluate("1+1"), timeout=a.eval_timeout)
                    out(f"[CDP_PROBE] tag={a.tag} t=+{int(now - start)}s eval_ms={int((time.perf_counter() - t1) * 1000)} ok={int(v == 2)} "
                        f"events=0 fwd_out_kb=0 fwd_in_kb=0 fwd_err=0 fwd_last=direct auth_challenges={auth_n['n']}")
                except asyncio.TimeoutError:
                    rc = 1
                    out(f"[CDP_PROBE] tag={a.tag} t=+{int(now - start)}s eval TIMEOUT (>{a.eval_timeout:.0f}s) fwd_err=0 fwd_last=direct auth_challenges={auth_n['n']}")
                except Exception as e:  # noqa: BLE001
                    rc = 1
                    out(f"[CDP_PROBE] tag={a.tag} t=+{int(now - start)}s eval ERR_{type(e).__name__}")
                continue
            await asyncio.sleep(min(0.5, max(0.05, min(next_eval, next_nav) - time.time())))
    except Exception as e:  # noqa: BLE001
        out(f"[CDP_PROBE] tag={a.tag} ABORT {type(e).__name__}: {str(e)[:120]}")
        rc = 2
    finally:
        try:
            await asyncio.wait_for(browser.stop(), timeout=10)
        except Exception:
            pass
        P._kill_own(browser)
        shutil.rmtree(profile, ignore_errors=True)
    s = summarize(lines)
    out(f"[CDP_PROBE] tag={a.tag} SUMMARY " + " ".join(f"{k}={v}" for k, v in s.items()))
    return rc


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", default="home", help="home | proxies:<i> | reserve:<i> | resi:<i> | host:port")
    ap.add_argument("--tag", default="home")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--every-s", type=float, default=10.0, help="evaluate('1+1') cadence")
    ap.add_argument("--nav-every-s", type=float, default=240.0, help="PDP navigation cadence (the follower refreshed every 240 s)")
    ap.add_argument("--eval-timeout", type=float, default=20.0)
    ap.add_argument("--nav-timeout", type=float, default=60.0)
    ap.add_argument("--settle-s", type=float, default=3.0)
    ap.add_argument("--pdp", default=P.DECOY, help="TCIN of the page to load (default: the probe's decoy product)")
    ap.add_argument("--url", default="", help="full URL to load instead of a Target PDP (e.g. https://example.com/ to test the proxy path without Target's page scripts)")
    ap.add_argument("--no-forwarder", action="store_true",
                    help="FS-W-B arm D: Chrome --proxy-server to the vendor directly, proxy auth via CDP Fetch.authRequired "
                         "(credential never logged); default = through the bot's ForwarderPool")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return asyncio.run(run_direct(a) if a.no_forwarder else run(a))


if __name__ == "__main__":
    sys.exit(main())
