#!/usr/bin/env python3
"""E-SHOOTER (2026-10-23): can a harvested, UNUSED Shape-signed add-to-cart request be replayed from a non-browser
TLS-impersonating client (curl_cffi) and still pass Shape? If yes, residential shooter lanes need no Chrome
(no 17-min freeze, ~1 KB per shot instead of ~45 MB per page load). Money-free: a GUEST Chrome, the control
TCIN 81926151 (never buyable: 424 ITEM_NOT_READY_FOR_LAUNCH when both walls pass, keyless 401 when Shape says
no), nothing is bought and no account is touched.

Per arm: a fresh guest Chrome on the given line loads the control PDP, fires the fast-lane add-to-cart fetch twice:
shot 1 CONTINUES (the in-browser baseline), shot 2 is ABORTED at CDP Fetch request stage after its headers and
body are captured (the intercept-and-abort harvest: the Shape tokens are unused), then curl_cffi replays the
captured request within seconds through the replay line. Lines: 'home' or a credentialed proxy entry
(resi:<i> / reserve:<i>); the credential goes to curl's proxy option, never to a log.

  python tools/experiments/shape_replay_probe.py --harvest home --replay home          # same IP, non-browser TLS
  python tools/experiments/shape_replay_probe.py --harvest home --replay resi:1        # token minted on home, sent from resi
  python tools/experiments/shape_replay_probe.py --harvest resi:1 --replay resi:1      # same residential IP both

Readout (logs/experiments/shape_replay_<stamp>.log): baseline=<status/key/hop>, replay=<status/key/hop/ms>.
PASS = replay 424 (or 201/200/FAST_SELLING) with x-ssx-hop; SHAPE-DENIED = 401 keyless; LIMITED = 429 ERR_A2C.
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import limkey_guest_follow as F  # noqa: E402
import limkey_guest_probe as P  # noqa: E402

CONTROL = P.DECOY
ORDER = ["sec-ch-ua", "x-application-name", "sec-ch-ua-mobile", "user-agent", "content-type", "accept",
         "sec-ch-ua-platform", "origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "referer",
         "accept-encoding", "accept-language", "cookie", "priority"]


def mask(ip: str) -> str:
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip or ""):
        return "?"
    a, b = ip.split(".")[:2]
    return f"{a}.{b}.x.x#{hashlib.sha1(ip.encode()).hexdigest()[:4]}"


def order_headers(h: dict) -> dict:
    """Chrome's wire order for a cross-site fetch, Shape tokens after accept (as Chrome sends them), the rest kept."""
    low = {k.lower(): v for k, v in h.items()}
    low.setdefault("sec-fetch-site", "same-site")
    low.setdefault("sec-fetch-mode", "cors")
    low.setdefault("sec-fetch-dest", "empty")
    low.setdefault("accept-language", "en-US,en;q=0.9")
    low.setdefault("accept-encoding", "gzip, deflate, br, zstd")
    low.setdefault("priority", "u=1, i")
    out = {}
    shape = [k for k in low if k.startswith("x-") and k not in ("x-application-name",)]
    for k in ORDER:
        if k == "accept":
            out["accept"] = low.get("accept", "application/json")
            for s in shape:
                out[s] = low[s]
            continue
        if k in low:
            out[k] = low[k]
    for k, v in low.items():
        if k not in out and not k.startswith(":"):
            out[k] = v
    return out


def impersonate_for(ua: str) -> str:
    m = re.search(r"Chrome/(\d+)", ua or "")
    major = int(m.group(1)) if m else 0
    try:
        from curl_cffi import requests as cr
        names = [x.name for x in cr.BrowserType] if hasattr(cr, "BrowserType") else []
    except Exception:
        names = []
    cands = sorted({int(re.sub(r"\D", "", n) or 0): n for n in names if n.startswith("chrome") and re.sub(r"\D", "", n)}.items())
    best = None
    for num, name in cands:
        if num <= major:
            best = name
    return best or "chrome"


async def donor_cookies(line: str, out, settle_s: float = 6.0) -> str:
    """A SECOND fresh guest Chrome on the same line loads the control PDP; its target.com cookies (a different
    visitor / Shape cookie set) become the replay's Cookie header. Values are never logged."""
    from zendriver import cdp
    route = None
    if line != "home":
        route = F.ProxyRoute(F.resolve_proxy(line))
        await route.start()
    pdp = f"https://www.target.com/p/-/A-{CONTROL}"
    g = F.Guest(out, settle_s, pdp, "donor" if line == "home" else "dn_" + re.sub(r"[^a-z0-9]", "", line.lower())[:8], route)
    try:
        await g.start()
        cookies = await asyncio.wait_for(g.tab.send(cdp.network.get_cookies(urls=["https://carts.target.com/", "https://www.target.com/"])), timeout=10)
        pairs = [(c.name, c.value) for c in cookies]
        out(f"[SHAPE_REPLAY] donor={line} cookies={len(pairs)} names={sorted(n for n, _ in pairs)[:10]}")
        return "; ".join(f"{n}={v}" for n, v in pairs)
    finally:
        try:
            await g.close()
        except Exception:
            pass
        if route is not None:
            await route.stop()


async def harvest(line: str, out, settle_s: float = 6.0) -> dict:
    """Guest Chrome on `line`: baseline shot (continued) + captured-and-aborted shot. Returns the capture."""
    from zendriver import cdp
    route = None
    if line != "home":
        route = F.ProxyRoute(F.resolve_proxy(line))
        await route.start()
    pdp = f"https://www.target.com/p/-/A-{CONTROL}"
    g = F.Guest(out, settle_s, pdp, "home" if line == "home" else "rp_" + re.sub(r"[^a-z0-9]", "", line.lower())[:8], route)
    cap = {"baseline": None, "headers": None, "body": None, "ua": None, "egress": None}
    try:
        await g.start()
        cap["egress"] = g.egress_masked or "home"
        tab = g.tab
        mode = {"abort": False}
        resp = {}

        async def on_paused(ev):
            try:
                rq = ev.request
                if "cart_items" in str(rq.url) and str(rq.method).upper() == "POST":
                    if mode["abort"]:
                        cap["headers"] = dict(rq.headers)
                        cap["body"] = rq.post_data
                        cap["ua"] = dict(rq.headers).get("User-Agent") or dict(rq.headers).get("user-agent")
                        await tab.send(cdp.fetch.fail_request(ev.request_id, cdp.network.ErrorReason.ABORTED))
                        return
                await tab.send(cdp.fetch.continue_request(ev.request_id))
            except Exception as e:  # noqa: BLE001
                out(f"[SHAPE_REPLAY] paused-handler error {type(e).__name__}")

        async def on_extra(ev):
            try:
                resp[str(ev.request_id)] = {"status": int(ev.status_code), **{k.lower(): v for k, v in dict(ev.headers).items()}}
            except Exception:
                pass

        tab.add_handler(cdp.fetch.RequestPaused, on_paused)
        await asyncio.wait_for(tab.send(cdp.fetch.enable(
            patterns=[cdp.fetch.RequestPattern(url_pattern="*carts.target.com*", request_stage=cdp.fetch.RequestStage.REQUEST)])), timeout=10)
        # baseline: the page's own fetch, continued
        raw = await asyncio.wait_for(tab.evaluate(P.atc_js(CONTROL, 2), await_promise=True), timeout=25)
        j = json.loads(raw) if isinstance(raw, str) else {}
        await asyncio.sleep(0.8)
        hx = {}
        for rid, m in list(g.reqs)[::-1]:
            if m == "POST" and rid in g.hdrs:
                hx = g.hdrs[rid]
                break
        cap["baseline"] = {"status": int(hx.get("status") or j.get("s") or 0), "key": hx.get("tgt-cart-error-key", ""),
                           "hop": "x-ssx-hop" in hx, "body": str(j.get("b", ""))[:100]}
        out(f"[SHAPE_REPLAY] harvest={line} egress={cap['egress']} baseline status={cap['baseline']['status']} key={cap['baseline']['key'] or '-'} hop={int(cap['baseline']['hop'])} body={cap['baseline']['body'][:60]!r}")
        await asyncio.sleep(2.0)
        # capture: the next fetch is aborted at the request stage with its (unused) Shape tokens in hand
        mode["abort"] = True
        raw = await asyncio.wait_for(tab.evaluate(P.atc_js(CONTROL, 2), await_promise=True), timeout=25)
        await asyncio.sleep(0.5)
        n_shape = len([k for k in (cap["headers"] or {}) if k.lower().startswith("x-") and k.lower() != "x-application-name"])
        out(f"[SHAPE_REPLAY] captured headers={len(cap['headers'] or {})} shape_tokens={n_shape} body_len={len(cap['body'] or '')} ua_major={re.search(r'Chrome/(\\d+)', cap['ua'] or '') and re.search(r'Chrome/(\\d+)', cap['ua']).group(1)}")
    finally:
        try:
            await g.close()
        except Exception:
            pass
        if route is not None:
            await route.stop()
    return cap


def replay(cap: dict, line: str, out) -> dict:
    from curl_cffi import requests as cr
    proxies = None
    if line != "home":
        info = F.resolve_proxy(line)
        if not info.get("url"):
            raise SystemExit("replay line needs a credentialed proxy entry")
        proxies = {"http": info["url"], "https": info["url"]}
    hdrs = order_headers(cap["headers"] or {})
    imp = impersonate_for(cap.get("ua") or "")
    t0 = time.perf_counter()
    try:
        r = cr.post(P.ATC_URL, headers=hdrs, data=cap["body"] or "", impersonate=imp, proxies=proxies, timeout=25, http_version=None)
        ms = int((time.perf_counter() - t0) * 1000)
        rh = {k.lower(): v for k, v in r.headers.items()}
        res = {"status": r.status_code, "key": rh.get("tgt-cart-error-key", ""), "hop": "x-ssx-hop" in rh,
               "body": r.text[:120].replace("\n", " "), "ms": ms, "impersonate": imp, "http": str(getattr(r, "http_version", "?"))}
    except Exception as e:  # noqa: BLE001
        res = {"status": 0, "key": "", "hop": False, "body": f"ERR_{type(e).__name__}: {str(e)[:80]}", "ms": int((time.perf_counter() - t0) * 1000), "impersonate": imp, "http": "?"}
    st, key, hop = res["status"], res["key"], res["hop"]
    if st == 401 and not key:
        verdict = "SHAPE-DENIED"
    elif st == 429 and "ERR_A2C" in (key or ""):
        verdict = "LIMITED"
    elif st in (424, 201, 200) or "FAST_SELLING" in (key or ""):
        verdict = "PASS"
    else:
        verdict = "OTHER"
    out(f"[SHAPE_REPLAY] replay={line} impersonate={imp} status={st} key={key or '-'} hop={int(hop)} ms={res['ms']} body={res['body'][:80]!r} -> {verdict}")
    res["verdict"] = verdict
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harvest", default="home", help="home | resi:<i> | reserve:<i>: the line the guest Chrome harvests on")
    ap.add_argument("--replay", default="home", help="home | resi:<i> | reserve:<i>: the line curl_cffi replays through")
    ap.add_argument("--settle-s", type=float, default=6.0)
    ap.add_argument("--member-jar", default="", help="path to an account session jar (config target_accounts.json session_path): replay with ITS target.com cookies (values never logged)")
    ap.add_argument("--cookie-donor", action="store_true",
                    help="replay with the cookies of a SECOND guest Chrome on the harvest line (is the Shape token bound to the cookie set?)")
    a = ap.parse_args(argv)
    log = ROOT / "logs" / "experiments" / f"shape_replay_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def out(m: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}"
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    out(f"[SHAPE_REPLAY] arm harvest={a.harvest} replay={a.replay} control={CONTROL}")
    donor = asyncio.run(donor_cookies(a.harvest, out, a.settle_s)) if a.cookie_donor else None
    cap = asyncio.run(harvest(a.harvest, out, a.settle_s))
    if not cap.get("headers"):
        out("[SHAPE_REPLAY] ABORT no capture")
        return 2
    if donor:
        h = {k: v for k, v in (cap["headers"] or {}).items() if k.lower() != "cookie"}
        h["Cookie"] = donor
        cap["headers"] = h
        out("[SHAPE_REPLAY] replaying with the DONOR guest's cookies (token minted by the other guest)")
    if a.member_jar:
        jar = json.loads(Path(a.member_jar).read_text(encoding="utf-8"))
        cl = jar.get("cookies") if isinstance(jar, dict) else jar
        pairs = [(c.get("name"), c.get("value")) for c in (cl or []) if isinstance(c, dict) and "target.com" in str(c.get("domain", "")) and c.get("name") and c.get("value") is not None]
        h = {k: v for k, v in (cap["headers"] or {}).items() if k.lower() != "cookie"}
        h["Cookie"] = "; ".join(f"{n}={v}" for n, v in pairs)
        cap["headers"] = h
        has = {n for n, _ in pairs}
        out(f"[SHAPE_REPLAY] replaying with a MEMBER jar's cookies: n={len(pairs)} member_tokens={int('accessToken' in has)}/{int('login-session' in has)} saved_at={str(jar.get('saved_at', '?'))[:19] if isinstance(jar, dict) else '?'}")
    res = replay(cap, a.replay, out)
    out(f"[SHAPE_REPLAY] RESULT harvest={a.harvest} replay={a.replay} donor={int(bool(donor))} baseline={cap['baseline']['status']}/{cap['baseline']['key'] or '-'} replay={res['status']}/{res['key'] or '-'}/hop{int(res['hop'])} verdict={res['verdict']}")
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
