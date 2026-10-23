#!/usr/bin/env python3
"""Does a proxy session stop answering new CONNECTs after N connections? (2026-10-23)

Why: through two IPRoyal sticky sessions a guest Chrome's CDP went silent at Chrome age ~17-21 min, each time after
~370 upstream connections through the local forwarder (tools/experiments/line_cdp_probe.py, tags resiB / resiB2 /
resiD); the bot's alt-1 Chrome wedged at age 16-20 min on 23 lifetimes (run_20261023_001005). This probe removes
Chrome and the forwarder: plain Python, one NEW proxied HTTPS connection per request (a CONNECT each time), at a
fixed cadence, until the proxy stops answering; then it waits and checks recovery. Arms:

  --via direct     urllib straight to the vendor gateway with the credentialed URL from config (never printed)
  --via forwarder  the bot's ForwarderPool in its own thread (the bot's shape), Python -> 127.0.0.1:port -> vendor

Log: logs/experiments/conn_cap_<tag>_<stamp>.log, one line per request slower than --slow-s or failed, a summary
every 25, and END with the count at the first stall and the recovery result. Bytes: ~1 KB per request.
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "experiments"))

ECHO = "https://api.ipify.org"


def mask(ip: str) -> str:
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip or ""):
        return "?"
    a, b = ip.split(".")[:2]
    return f"{a}.{b}.x.x#{hashlib.sha1(ip.encode()).hexdigest()[:4]}"


def one(proxy: str, timeout: float) -> tuple[str, int]:
    """('ok:<masked ip>' | 'ERR_<type>', ms) over a brand-new connection (no keep-alive: a fresh CONNECT every time)."""
    t0 = time.perf_counter()
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        req = urllib.request.Request(ECHO, headers={"User-Agent": "conn-cap-probe", "Connection": "close"})
        with op.open(req, timeout=timeout) as r:
            ip = r.read(64).decode("ascii", "replace").strip()
        return f"ok:{mask(ip)}", int((time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        why = str(getattr(e, "reason", "") or e).replace(" ", "_")[:70]
        return f"ERR_{type(e).__name__}({why})", int((time.perf_counter() - t0) * 1000)


class ForwarderThread:
    """The bot's ForwarderPool on its own asyncio loop in a thread (as app.py runs it)."""

    def __init__(self, url: str):
        from src.proxy.local_forwarder import ForwarderPool
        import limkey_guest_follow as F
        self.port = F.pick_forwarder_port()
        self.pool = ForwarderPool()
        self.pool.add_upstream(url, self.port)
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()
        self.ready.wait(20)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.pool.start_all())
        self.ready.set()
        self.loop.run_forever()

    def stats(self) -> str:
        parts = []
        for up in self.pool.upstreams.values():
            s = up.stats
            parts.append(f"conns={s.connections_active}/{s.connections_total} err={s.errors} last={str(s.last_error or '-').replace(' ', '_')[:40]}")
        return " ".join(parts)

    def stop(self):
        try:
            fut = asyncio.run_coroutine_threadsafe(self.pool.stop_all(), self.loop)
            fut.result(10)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", required=True, help="resi:<i> | reserve:<i> | proxies:<i> (from config/proxyIps.json)")
    ap.add_argument("--via", choices=["direct", "forwarder"], default="direct")
    ap.add_argument("--tag", default="cap")
    ap.add_argument("--max", type=int, default=600)
    ap.add_argument("--every-s", type=float, default=1.5)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--slow-s", type=float, default=4.0)
    ap.add_argument("--stall-after", type=int, default=3, help="consecutive failures that count as a stall")
    ap.add_argument("--recover-wait-s", type=float, default=90.0)
    a = ap.parse_args(argv)

    import limkey_guest_follow as F
    info = F.resolve_proxy(a.proxy)
    if not info.get("url"):
        print("need a credentialed proxy entry"); return 2
    log = ROOT / "logs" / "experiments" / f"conn_cap_{a.tag}_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def out(m: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [CONN_CAP] tag={a.tag} {m}"
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    fwd = None
    if a.via == "forwarder":
        fwd = ForwarderThread(info["url"])
        proxy = f"http://127.0.0.1:{fwd.port}"
    else:
        proxy = info["url"]
    out(f"start via={a.via} display={info['display']} max={a.max} every_s={a.every_s} timeout={a.timeout}")
    t_start = time.time()
    n = ok = fails = consec = 0
    first_stall = None
    lat = []
    try:
        while n < a.max:
            n += 1
            res, ms = one(proxy, a.timeout)
            lat.append(ms)
            if res.startswith("ok"):
                ok += 1
                consec = 0
                if ms >= a.slow_s * 1000:
                    out(f"#{n} SLOW {ms} ms {res} age={int(time.time() - t_start)}s" + (f" {fwd.stats()}" if fwd else ""))
            else:
                fails += 1
                consec += 1
                out(f"#{n} FAIL {res} {ms} ms age={int(time.time() - t_start)}s" + (f" {fwd.stats()}" if fwd else ""))
                if consec >= a.stall_after and first_stall is None:
                    first_stall = n - consec + 1
                    out(f"STALL at request #{first_stall} (age {int(time.time() - t_start)}s, {ok} ok before); waiting {a.recover_wait_s:.0f}s")
                    time.sleep(a.recover_wait_s)
                    rec = [one(proxy, a.timeout) for _ in range(5)]
                    out("RECOVERY after wait: " + " ".join(f"{r}:{m}ms" for r, m in rec))
                    break
            if n % 25 == 0:
                s = sorted(lat[-25:])
                out(f"#{n} ok={ok} fails={fails} last25 p50={s[len(s)//2]} max={s[-1]} ms age={int(time.time() - t_start)}s" + (f" {fwd.stats()}" if fwd else ""))
            time.sleep(a.every_s)
    finally:
        if fwd:
            fwd.stop()
    s = sorted(lat)
    out(f"END n={n} ok={ok} fails={fails} first_stall={first_stall} p50={s[len(s)//2] if s else None} p95={s[int(len(s)*0.95)] if s else None} max={s[-1] if s else None} age={int(time.time() - t_start)}s")
    return 0 if first_stall is None else 1


if __name__ == "__main__":
    sys.exit(main())
