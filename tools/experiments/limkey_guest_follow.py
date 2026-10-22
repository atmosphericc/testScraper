"""EXP-LIMKEY-INWINDOW (2026-10-16; operator 10-16 ~15:45: "Yes, build it"): during a
real drop, does a GUEST browser on the same home line get past Target's edge limiter
when the three member accounts do not?

Why: off-window the limiter does not limit hot TCINs at all (EXP-LIMKEY-GUEST,
DEMAND-SWITCHED, C-1016-17); in the 10-16 drop the members went 0/1,208. What the
limiter keys on (network line vs account / session / cart) can therefore only be
measured IN-WINDOW. A guest on the same line, firing in the same seconds, separates
"the line / the TCIN" from "the account / session".

How: follows the bot's newest logs/runs/run_*.log. When the bot logs a hot TCIN in stock
([STOCK][FLIP] tcin=, [API_CYCLE] IN STOCK: [...], [STOCK STATUS] ... in_stock=1), the
guest fires the fast lane's add-to-cart at it every --cadence-s (default 5 s) while the
TCIN had an in-stock log line within --stock-grace-s (35 s; the log carries evidence every <=30 s),
plus the decoy control 81926151 every 30 s while any window is live. (The log carries in-stock evidence
at least every ~15 s while a TCIN is live; a [STOCK WATCH] in_stock=False ends the window at once.) Hot TCINs = the
ENABLED TCINs of config/product_config.json (the bot's list). Idle, it reloads its PDP
every --refresh-s (240) so the page is fresh at the flip.

MONEY-SAFE BY CONSTRUCTION - the same guest session as tools/experiments/
limkey_guest_probe.py (3 Opus verifier rounds, 10-16), imported from there: a fresh
temp profile, no login, no account file read, no pre_checkout / checkout ever; its own
DevTools port range 31000-39999 (no attach race with bot Chromes); the listener-pid
attach check before any navigation, aborting without browser.stop(). A 2xx (a line in
the GUEST cart, no card) stops that TCIN for the rest of its window. It never touches
the bot's files or processes; it only READS the run log and the product config.
Cost (operator accepted 10-16): ~1 extra add-to-cart per 5 s per live TCIN from the home
line during a drop.

Caps: --max-per-window 40 guest shots per live period of a TCIN (re-opens <= 60 s apart are
the same period, so a flicker does not reset the cap, the cadence or the stop-after-2xx),
--max-per-night 1500, the control every 60 s; ends at --until (default the next 06:30
local), at most 9 h. (10-09 replay: 600 would have run out at ~04:15, before the only 3
windows where members got past the limiter.) If its Chrome dies (e.g. the
wrapper's start kills every chrome.exe), it launches a fresh one (new temp profile,
attach check again), at most 6 times a night.

PRE-REGISTERED READOUT (written 2026-10-16 before any in-window data):
  window   = one live period of one hot TCIN (follower windows of the same TCIN <= 60 s
             apart are merged: in-stock flickers) in which the guest has >= 3 answered shots
             (answered = limited + passed, the probe's classify())
  G        = the guest had >= 1 passed shot in the window
  M        = the members had >= 1 shot past the limiter on that TCIN between the window's
             start - 2 s and end + 10 s: event store `shots` with gate in (wall2_denied, cart,
             admitted_fs, inventory, cart_limit). Rows with gate unknown / other (never
             POSTed, keyless 429, 5xx) are counted apart and never scored as passed (close
             to, not identical with, classify() on the guest side: a guest 400/404/409/422
             is "passed"; 42 such member rows exist in the whole store). A window with no
             scoreable member row (0 shots, or all unknown / other) is counted as
             no-member-data and not scored; a window never closed (process killed) is
             counted as unclosed and not scored. (Amended 10-16 after two verifier rounds,
             before any in-window data.)
  After >= 5 scored windows (pooled across drops: --readout takes several JSON files):
    GUEST-DIFFERS  windows with G and not M >= 3 AND >= 2 x windows with M and not G
                   -> the limiter key includes the account / session / cart; next test:
                      fresh member sessions vs aged ones
    SAME-FATE      G == M in >= 80% of windows AND windows with G and not M <= 1
                   -> the guest shares the members' fate: the key is the line or the
                      TCIN; next test: the same guest over a second network
                   (any_pass == 0, i.e. every window both_zero, is labelled 'SAME-FATE (concordant
                   zero ...)': it rules out member-account flagging only)
    INCONCLUSIVE   anything else
  Descriptive, not decisive: shot-level pass shares of guest vs members per window.

    python tools/experiments/limkey_guest_follow.py                   # follow until 06:30
    python tools/experiments/limkey_guest_follow.py --simulate 1010892076 --sim-s 30   # off-window path test
    python tools/experiments/limkey_guest_follow.py --readout logs/experiments/guest_follow_<stamp>.json

EXP-LINE (2026-10-20): a SECOND follower on a different network line in the same drop window.
The home-line guest alone cannot separate "the edge limiter keys on the network line" from "the
limiter is a TCIN-global demand state" (its readout says so). Options, all default-off (no option =
today's behaviour, byte-for-byte in the output files):
  --proxy SPEC      proxies:<i> / reserve:<i> / resi:<i> (index into config/proxyIps.json: proxies, reserve_proxies,
                    residential_proxies - the last added 2026-10-21 for a Bright Data RESIDENTIAL zone; so no secret is ever on a
                    command line or in a scheduled task) or a literal http://user:pass@host:port / host:port.
                    A URL with credentials is routed like the bot does it: ForwarderPool (src/proxy/
                    local_forwarder.py) on a free local port -> Chrome --proxy-server=127.0.0.1:<port>; the
                    forwarder is stopped on exit. FAIL CLOSED: before any shot one GET to api.ipify.org through
                    the browser; if the egress equals the home IP (tools/ops/home_ip.py) or the proof fails it
                    logs "[GUEST_FOLLOW] egress FAILED ..." and exits 1 without shooting. One line
                    "[GUEST_FOLLOW] egress tag= ip= home= proxy=" (IPs masked to two octets, host:port only).
  --tag NAME        [a-z0-9_-]{1,16}, default home. Non-home tags go into the file names
                    (guest_follow_<tag>_<stamp>.{log,json}), the JSON ("tag"), every [GUEST_FOLLOW] line
                    (tag=<tag>), the readout rows and the Chrome profile prefix.
  --lock-name NAME  state/<name>.lock + state/<name>_night.json (default guest_follow), so a "bd" instance
                    runs beside the "home" one. state/guest_follow.enable stays shared: deleting it stops all.
                    A lock file whose pid is dead or foreign is logged ("stale lock ... taking over") and
                    overwritten; a live follower's lock still refuses.
Second scheduled task: venv/Scripts/pythonw.exe tools/experiments/limkey_guest_follow.py --scheduled
--tag bd --lock-name guest_follow_bd --proxy reserve:1

PRE-REGISTERED CROSS-LINE READOUT (written 2026-10-20 before any two-line data; --readout with files of
>= 2 tags from the same night; the per-file rows and verdict above are kept, computed over the home-tag
files when present):
  cross-window = windows of different tags with the same tcin and start_ms within 5 s (one per tag), each
                 with >= 3 answered guest shots; M (members past the limiter, event store, start - 2 s ..
                 end + 10 s) must be scoreable. Reference line = home (else the first tag); X = another tag.
  X_pass         = X's guest had >= 1 passed shot (no ERR_A2C_TCIN_RATE_LIMITED) in the window
  bd_only_pass   = X_pass and not home_pass and not M
  reverse        = (home_pass or M) and not X_pass;   home_only_pass = home_pass and not X_pass
  same           = home_pass == X_pass == M
    LINE-DIFFERS         bd_only_pass >= 3 AND bd_only_pass >= 2 x reverse
                         -> the limiter keys on the network line (the home line is limited, X's is not)
    SAME-FATE-ALL-LINES  windows >= 5 AND same >= 80% of windows
                         -> the lines share a fate. A concordant ZERO ("concordant zero") is only
                            "every line limited in every matched window": at 5 s cadence with caps it
                            is underpowered and does NOT establish a TCIN-global state (10-21 critic)
    INCONCLUSIVE         anything else
  Also per tag, wall 2 of the shots that got past wall 1: keyless 401 (the Shape verdict) vs 424/201
  with x-ssx-hop (past Shape).

Started by its own scheduled tasks ("TargetBot/guest-follow 2340" / "0135" in Task Scheduler, venv pythonw, no
console) with --scheduled: it exits at once unless state/guest_follow.enable exists and no
other follower holds state/guest_follow.lock; deleting the enable file also stops a running
follower within ~1 s. A HUMAN/PX wall on its PDP, or a wall row, stops firing until a
refresh shows a clean page. The night cap survives a restarted follower
(state/guest_follow_night.json). Independent of
ensure_armed.py and the bot. Output: logs/experiments/guest_follow_<stamp>.log + .json
(rewritten at every window end); the lock file holds its pid.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import limkey_guest_probe as P                      # noqa: E402  (the verified guest session parts)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "ops"))
import home_ip as H                                 # noqa: E402  (public_ip / mask)
from src.proxy.local_forwarder import ForwarderPool  # noqa: E402  (the bot's own Bright Data forwarder)
RUNS = ROOT / "logs" / "runs"
OUT_DIR = ROOT / "logs" / "experiments"
STATE = ROOT / "state"
LOCK = STATE / "guest_follow.lock"
PROXY_CFG = ROOT / "config" / "proxyIps.json"
TAG_RE = re.compile(r"[a-z0-9_-]{1,16}")
LOCK_RE = re.compile(r"[a-z0-9_-]{1,32}")
CONFIG = ROOT / "config" / "product_config.json"
EVENTS = ROOT / "logs" / "events" / "events.sqlite"
FLIP_RE = re.compile(r"\[STOCK\]\[FLIP\] tcin=(\d{6,13})")
CYCLE_RE = re.compile(r"\[API_CYCLE\] IN STOCK: \[([^\]]*)\]")
STATUS_RE = re.compile(r"\[STOCK STATUS\] tcin=(\d{6,13}) .*?in_stock=([01])")
WATCH_RE = re.compile(r"\[STOCK WATCH\] (\d{6,13}): in_stock=(True|False)")      # every 30 s per TCIN
TRUTH_RE = re.compile(r"\[GROUND-TRUTH\][^\n]*?in_stock=\[([^\]]*)\]")           # every ~30 s, offset 15 s
MAX_RELAUNCH = 6


class EgressFailed(RuntimeError):
    """The proxy egress could not be proven different from the home IP: nothing may be shot."""


def set_lock_name(name: str) -> None:
    """Point LOCK / NIGHT at state/<name>.lock and state/<name>_night.json."""
    global LOCK, NIGHT
    LOCK = STATE / f"{name}.lock"
    NIGHT = STATE / f"{name}_night.json"


def resolve_proxy(spec: str, cfg_path: Path = None) -> dict:
    """{'url': 'http://u:p@h:p' or '', 'server': 'h:p' for a credential-less proxy, 'display': 'h:p'}.
    Errors never carry the secret."""
    cfg_path = cfg_path or PROXY_CFG
    spec = (spec or "").strip()
    m = re.fullmatch(r"(proxies|reserve|resi):(\d{1,3})", spec)
    if m:
        key = {"proxies": "proxies", "reserve": "reserve_proxies", "resi": "residential_proxies"}[m.group(1)]
        try:
            lst = json.loads(Path(cfg_path).read_text(encoding="utf-8")).get(key) or []
        except Exception as e:
            raise ValueError(f"cannot read {Path(cfg_path).name} ({type(e).__name__})")
        i = int(m.group(2))
        if i >= len(lst) or not isinstance(lst[i], str) or not lst[i].strip():
            raise ValueError(f"{m.group(1)}:{i} not available ({len(lst)} entries in {key})")
        spec = lst[i].strip()
    if "@" in spec:
        no_scheme = re.sub(r"^https?://", "", spec)
        creds, hp = no_scheme.rsplit("@", 1)
        if ":" not in creds or not re.fullmatch(r"[A-Za-z0-9._-]+:\d{2,5}", hp):
            raise ValueError("bad --proxy URL (want http://user:pass@host:port)")
        return {"url": "http://" + no_scheme, "server": "", "display": hp}
    hp = re.sub(r"^https?://", "", spec).rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]+:\d{2,5}", hp):
        raise ValueError("bad --proxy (want proxies:<i>, reserve:<i>, resi:<i>, http://user:pass@host:port or host:port)")
    return {"url": "", "server": hp, "display": hp}


def pick_forwarder_port() -> int:
    """A free local port in 22900-22999 (the bot's forwarders use 22000+i)."""
    import random
    import socket
    for _ in range(100):
        p = random.randrange(22900, 23000)
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sk.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            sk.close()
    raise RuntimeError("no free local forwarder port")


class ProxyRoute:
    """The Chrome --proxy-server for one --proxy: a local ForwarderPool port for a credentialed URL
    (exactly the bot's pattern), the host:port itself otherwise. stop() ends the forwarder."""

    def __init__(self, info: dict):
        self.info, self.pool, self.chrome_server = info, None, info.get("server", "")

    async def start(self) -> None:
        if self.info.get("url"):
            port = pick_forwarder_port()
            self.pool = ForwarderPool()
            self.pool.add_upstream(self.info["url"], port)
            await self.pool.start_all()
            self.chrome_server = f"127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.pool is not None:
            try:
                await self.pool.stop_all()
            except Exception:
                pass
            self.pool = None


def _valid_ip(ip: str) -> bool:
    p = str(ip).split(".")
    return len(p) == 4 and all(x.isdigit() and 0 <= int(x) <= 255 for x in p)


async def egress_gate(out, tag: str, display: str, fetch_ip) -> str:
    """Fail-closed egress proof: the proxy's public IP (fetch_ip, a coroutine fn) must be a real IP
    and differ from the home IP. Returns the masked egress IP or raises EgressFailed."""
    try:
        ip = str(await fetch_ip()).strip()
    except Exception as e:
        ip = f"ERR {type(e).__name__}"
    home = await asyncio.to_thread(H.public_ip)
    if not _valid_ip(ip) or not _valid_ip(home):
        out(f"[GUEST_FOLLOW] egress FAILED tag={tag} proxy={display} reason=proof_failed "
            f"egress={'ok' if _valid_ip(ip) else 'unreadable'} home={'ok' if _valid_ip(home) else 'unreadable'} - nothing sent")
        raise EgressFailed("proof failed")
    if ip == home:
        out(f"[GUEST_FOLLOW] egress FAILED tag={tag} ip={H.mask(ip)} home={H.mask(home)} proxy={display} "
            f"reason=same_as_home - nothing sent")
        raise EgressFailed("egress equals home")
    out(f"[GUEST_FOLLOW] egress tag={tag} ip={H.mask(ip)} home={H.mask(home)} proxy={display}")
    return H.mask(ip)


def hot_from_config(path: Path = CONFIG) -> list[str]:
    """ENABLED TCINs of the product config (the bot's own list), minus the decoy."""
    d = json.loads(path.read_text(encoding="utf-8"))
    items = d.get("products") if isinstance(d, dict) else d
    if not isinstance(items, list):
        items = next((v for v in d.values() if isinstance(v, list)), []) if isinstance(d, dict) else []
    out = []
    for p in items:
        if isinstance(p, dict) and p.get("enabled") and re.fullmatch(r"\d{6,13}", str(p.get("tcin", ""))):
            t = str(p["tcin"])
            if t != P.DECOY and t not in out:
                out.append(t)
    return out


def next_until(hhmm: str, now: float) -> float:
    h, m = (int(x) for x in hhmm.split(":"))
    lt = time.localtime(now)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    if t <= now:
        t += 86400
    return min(t, now + 9 * 3600)


class LogFollower:
    """Tails the newest run log from its end; switches when the bot starts a new run."""

    def __init__(self, runs_dir: Path = RUNS):
        self.runs_dir, self.path, self.f, self.buf, self.next_check = runs_dir, None, None, "", 0.0

    def _newest(self):
        try:
            logs = list(self.runs_dir.glob("run_*.log"))
            return max(logs, key=lambda p: p.stat().st_mtime) if logs else None
        except Exception:
            return None

    def poll(self, now: float) -> list[str]:
        if now >= self.next_check or self.f is None:
            self.next_check = now + 20
            n = self._newest()
            if n is not None and n != self.path:
                try:
                    if self.f:
                        self.f.close()
                    self.f = n.open("r", encoding="utf-8", errors="replace")
                    self.f.seek(0, os.SEEK_END)
                    self.path, self.buf = n, ""
                except Exception:
                    self.f = None
        if self.f is None:
            return []
        try:
            chunk = self.f.read()
        except Exception:
            return []
        if not chunk:
            return []
        data = self.buf + chunk
        lines = data.split("\n")
        self.buf = lines.pop()               # keep a partial last line for the next poll
        return lines


class Live:
    """Which hot TCINs the bot is reading in stock right now (by its own log). In-stock
    evidence: [STOCK][FLIP], [API_CYCLE] IN STOCK, [STOCK STATUS] in_stock=1, [STOCK WATCH]
    in_stock=True, [GROUND-TRUTH] in_stock=[...] (together at least every ~15 s while a TCIN
    is live). Out-of-stock: [STOCK WATCH] in_stock=False (the 30 s snapshot; a [STOCK STATUS]
    in_stock=0 is often a 30 ms flicker and is ignored)."""

    def __init__(self, hot: list[str]):
        self.hot, self.last_in, self.last_oos = set(hot), {}, {}

    def feed(self, line: str, now: float) -> None:
        for m in FLIP_RE.finditer(line):
            self.last_in[m.group(1)] = now
        for m in CYCLE_RE.finditer(line):
            for t in re.findall(r"\d{6,13}", m.group(1)):
                self.last_in[t] = now
        for m in STATUS_RE.finditer(line):
            if m.group(2) == "1":
                self.last_in[m.group(1)] = now
        for m in WATCH_RE.finditer(line):
            if m.group(2) == "True":
                self.last_in[m.group(1)] = now
            else:
                self.last_oos[m.group(1)] = now
        for m in TRUTH_RE.finditer(line):
            for t in re.findall(r"\d{6,13}", m.group(1)):
                self.last_in[t] = now

    def live(self, now: float, grace: float) -> list[str]:
        return sorted(t for t in self.hot
                      if now - self.last_in.get(t, -1e18) <= grace and self.last_in.get(t, -1e18) > self.last_oos.get(t, -1e18))


class Guest:
    """One fresh guest Chrome (the probe's verified launch + attach check + capture)."""

    def __init__(self, out, settle_s: float, first_pdp: str, tag: str = "home", route=None):
        self.out, self.settle_s, self.first_pdp = out, settle_s, first_pdp
        self.tag, self.route, self.egress_masked = tag, route, ""
        self.page_ok = False                     # False while the PDP shows a HUMAN/PX wall (or was unreadable)
        self.browser = self.tab = self.profile = None
        self.hdrs: dict = {}
        self.reqs: list = []

    async def start(self) -> None:
        import zendriver as uc
        from zendriver import cdp
        import zendriver.core.util as _zutil
        _zutil.free_port = P.probe_free_port     # this process only (no attach race with bot Chromes)
        self.profile = Path(tempfile.mkdtemp(prefix="limkey_guest_" if self.tag == "home" else f"limkey_guest_{self.tag}_"))
        bargs = ["--window-size=1100,800", "--no-first-run",
                 "--no-default-browser-check", "--disable-extensions",
                 "--disable-sync", "--window-position=-32000,-32000"]
        if self.route is not None and self.route.chrome_server:
            bargs.insert(0, f"--proxy-server={self.route.chrome_server}")
        cfg = uc.Config(user_data_dir=str(self.profile), headless=False, sandbox=True,
                        browser_args=bargs,
                        browser_connection_timeout=1.0, browser_connection_max_tries=30)
        from zendriver.core.browser import Browser
        self.browser = Browser(cfg)              # hold the handle first: a timed-out start() is still killable
        await asyncio.wait_for(self.browser.start(), timeout=90)
        why = await P.attach_check(self.browser, cdp, self.profile)
        if why:
            self.out(f"[GUEST_FOLLOW] ABORT attach check: {why} - nothing sent; killing only our own process; "
                     f"exiting without browser.stop()")
            P._kill_own(self.browser)
            shutil.rmtree(self.profile, ignore_errors=True)
            try:
                LOCK.unlink()
            except Exception:
                pass
            os._exit(4)                          # skip the library's exit hooks
        self.out(f"[GUEST_FOLLOW] attach check OK (port {self.browser.config.port})")
        if self.route is not None:               # fail closed: prove the egress before any navigation to Target
            self.egress_masked = await egress_gate(self.out, self.tag, self.route.info["display"], self._browser_ip)
        self.tab = await asyncio.wait_for(self.browser.get(self.first_pdp), timeout=30)
        await asyncio.sleep(self.settle_s)

        async def on_send(ev):
            try:
                r = ev.request
                if "cart_items" in str(r.url) and str(r.method).upper() in ("POST", "OPTIONS"):
                    self.reqs.append((str(ev.request_id), str(r.method).upper()))
            except Exception:
                pass

        async def on_extra(ev):
            try:
                self.hdrs[str(ev.request_id)] = {"status": int(ev.status_code),
                                                 **{k.lower(): v for k, v in dict(ev.headers).items()}}
            except Exception:
                pass

        self.tab.add_handler(cdp.network.RequestWillBeSent, on_send)
        self.tab.add_handler(cdp.network.ResponseReceivedExtraInfo, on_extra)
        await asyncio.wait_for(self.tab.send(cdp.network.enable()), timeout=10)
        self.page_ok = await self.page_state("start")

    async def _browser_ip(self) -> str:
        t = await asyncio.wait_for(self.browser.get("https://api.ipify.org"), timeout=40)
        await asyncio.sleep(1.0)
        return str(await asyncio.wait_for(t.evaluate("document.body ? document.body.innerText : ''"), timeout=10) or "").strip()[:45]

    async def page_state(self, why: str) -> bool:
        try:
            title = str(await asyncio.wait_for(self.tab.evaluate("document.title"), timeout=10) or "")
            px = bool(await asyncio.wait_for(self.tab.evaluate(
                "!!document.querySelector('#px-captcha') || /press\\s*&\\s*hold|access denied/i.test(document.body ? document.body.innerText.slice(0, 4000) : '')"),
                timeout=10))
            self.out(f"[GUEST_FOLLOW] page ({why}) title={title[:70]!r} px_wall={int(px)}")
            return not px
        except Exception as e:
            self.out(f"[GUEST_FOLLOW] page ({why}) check failed {type(e).__name__}")
            return False

    async def refresh(self) -> None:
        await asyncio.wait_for(self.tab.get(self.first_pdp), timeout=30)
        await asyncio.sleep(self.settle_s)
        self.page_ok = await self.page_state("refresh")

    async def alive(self) -> bool:
        try:
            return (await asyncio.wait_for(self.tab.evaluate("1+1"), timeout=8)) == 2
        except Exception:
            return False

    async def fire(self, tcin: str, qty: int) -> dict:
        n_before = len(self.reqs)
        raw = await asyncio.wait_for(self.tab.evaluate(P.atc_js(tcin, qty), await_promise=True), timeout=25)
        try:
            j = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            j = {}
        await asyncio.sleep(0.6)
        new = self.reqs[n_before:]
        post_ids = [r for r, m in new if m == "POST"]
        opt_ids = [r for r, m in new if m == "OPTIONS"]
        hx = self.hdrs.get(post_ids[0], {}) if post_ids else {}
        ox = self.hdrs.get(opt_ids[-1], {}) if opt_ids else {}
        status = int(hx.get("status") or 0) or (int(j.get("s") or 0) if post_ids else 0)
        body = str(j.get("b", "") or "").replace("\n", " ")
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ts_ms": int(time.time() * 1000), "tcin": tcin,
               "role": "control" if tcin == P.DECOY else "hot", "post_seen": bool(post_ids), "status": status,
               "key": str(hx.get("tgt-cart-error-key", "") or ""), "hop": "x-ssx-hop" in hx,
               "n": j.get("n", -1), "ms": j.get("ms"), "cdp": int(bool(hx)), "body": body[:160],
               "opt_status": int(ox.get("status") or 0)}
        rec["cls"] = P.classify(rec)
        return rec

    async def close(self) -> None:
        try:
            if self.browser is not None:
                await asyncio.wait_for(self.browser.stop(), timeout=10)
        except Exception:
            pass
        P._kill_own(self.browser)
        await asyncio.sleep(1.0)
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
        self.browser = self.tab = None


def lock_held() -> bool:
    """True when another live follower holds the lock."""
    try:
        pid = int(LOCK.read_text().strip())
    except Exception:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and "limkey_guest_follow" in " ".join(p.cmdline())
    except Exception:
        return False


ENABLE = STATE / "guest_follow.enable"                  # shared by every instance
NIGHT = STATE / "guest_follow_night.json"     # the night cap survives a restarted follower


async def start_guest(out, a, pdp: str, until: float, route=None):
    """A started Guest, or raise after 3 tries (each failure cleans up what it can) or at until."""
    last = None
    for i in range(3):
        if time.time() >= until:
            break
        g = Guest(out, a.settle_s, pdp, getattr(a, "tag", "home"), route)
        try:
            await g.start()
            return g
        except EgressFailed:
            await g.close()
            raise
        except Exception as e:
            last = e
            out(f"[GUEST_FOLLOW] guest start failed {type(e).__name__} (try {i + 1}/3)")
            await g.close()
            await asyncio.sleep(30)
    raise RuntimeError(f"guest start failed 3x ({type(last).__name__})")


async def follow(a) -> int:
    if a.scheduled and not ENABLE.exists():
        print("scheduled start: state/guest_follow.enable absent - not running")
        return 0
    tag = getattr(a, "tag", "home")
    set_lock_name(getattr(a, "lock_name", "guest_follow"))
    route = None
    if getattr(a, "proxy", ""):
        try:
            route = ProxyRoute(resolve_proxy(a.proxy))
        except ValueError as e:
            print(f"refused: {e}")
            return 2
    why_range = P.dynamic_range_clear()
    if why_range:
        print(f"refused: {why_range}")
        return 5
    if lock_held():
        print("refused: another follower is running (state/guest_follow.lock)")
        return 6
    hot = [a.simulate] if a.simulate else hot_from_config()
    if not hot:
        print("no enabled hot TCIN in the product config")
        return 2
    now = time.time()
    if a.simulate and not P.in_allowed_window(now):
        print("refused: --simulate runs only 06:00-23:05 local")
        return 3
    until = now + a.sim_s + 60 if a.simulate else next_until(a.until, now)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    stale = ""
    try:
        stale = LOCK.read_text().strip() if LOCK.exists() else ""      # lock_held() was False: dead / foreign pid
    except Exception:
        pass
    LOCK.write_text(str(os.getpid()))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ftag = "" if tag == "home" else f"{tag}_"
    log_path = OUT_DIR / f"guest_follow_{ftag}{stamp}.log"
    sum_path = OUT_DIR / f"guest_follow_{ftag}{stamp}.json"
    logf = log_path.open("a", encoding="utf-8")

    def out(line: str) -> None:
        if tag != "home" and line.startswith("[GUEST_FOLLOW] ") and not line.startswith("[GUEST_FOLLOW] egress"):
            line = f"[GUEST_FOLLOW] tag={tag} " + line[len("[GUEST_FOLLOW] "):]
        s = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}"
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    if stale:
        out(f"[GUEST_FOLLOW] stale lock {LOCK} pid={stale} -- taking over")
    out(f"[GUEST_FOLLOW] start hot={len(hot)} TCINs cadence_s={a.cadence_s} grace_s={a.stock_grace_s} "
        f"until={time.strftime('%Y-%m-%d %H:%M', time.localtime(until))} simulate={a.simulate or '-'}")
    follower, live = LogFollower(), Live(hot)
    pdp = f"https://www.target.com/p/-/A-{hot[0]}"
    guest = None
    recs: list[dict] = []
    windows: list[dict] = []
    open_w: dict[str, dict] = {}
    period: dict[str, dict] = {}            # per TCIN: shots / landed / last_end across flicker re-opens
    next_shot: dict[str, float] = {}
    night = relaunch = errs = 0
    night_key = time.strftime("%Y-%m-%d", time.localtime(now - 12 * 3600))   # a "night" runs noon to noon
    try:
        nf = json.loads(NIGHT.read_text(encoding="utf-8"))
        if nf.get("night") == night_key and not a.simulate:
            night = int(nf.get("count") or 0)
    except Exception:
        pass
    next_ctrl = next_refresh = next_alive = 0.0
    sim_end = now + a.sim_s if a.simulate else 0.0
    reason = "until"
    rc = 0
    egress = ""

    def dump(end_reason: str) -> None:
        try:
            if not a.simulate:
                NIGHT.write_text(json.dumps({"night": night_key, "count": night}), encoding="utf-8")
        except Exception:
            pass
        try:
            ws = windows + [dict(w, end_ms=None) for w in open_w.values()]
            doc = {"experiment": "EXP-LIMKEY-INWINDOW", "started": stamp, "end": end_reason,
                   "hot_n": len(hot), "windows": ws, "shots": recs, "night_total": night,
                   "log": str(log_path)}
            if tag != "home":
                doc.update({"tag": tag, "proxy": route.info["display"] if route else "",
                            "egress": getattr(guest, "egress_masked", "") or egress})
            sum_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        except Exception as e:
            out(f"[GUEST_FOLLOW] summary write failed {type(e).__name__}")

    try:
        if route is not None:
            await route.start()
        guest = await start_guest(out, a, pdp, until, route)
        egress = getattr(guest, "egress_masked", "")
        next_refresh = time.time() + a.refresh_s
        while True:
            now = time.time()
            if now >= until:
                break
            if a.scheduled and not ENABLE.exists():
                reason = "state/guest_follow.enable removed"
                break
            for line in follower.poll(now):
                live.feed(line, now)
            if a.simulate and now < sim_end:
                live.last_in[a.simulate] = now
            cur = live.live(now, a.stock_grace_s)
            for t in list(open_w):               # close windows that went quiet
                if t not in cur:
                    w = open_w.pop(t)
                    w["end_ms"] = int(now * 1000)
                    windows.append(w)
                    period[t]["last_end"] = now
                    out(f"[GUEST_FOLLOW] window end tcin={t} shots={w['shots']} period_shots={period[t]['shots']}")
                    dump("running")
            for t in cur:
                if t not in open_w:
                    pr = period.get(t)
                    if pr is None or pr.get("last_end") is None or now - pr["last_end"] > 60:
                        period[t] = {"shots": 0, "landed": False, "last_end": None}
                    else:
                        period[t]["last_end"] = None  # a flicker re-open: same live period
                    open_w[t] = {"tcin": t, "start_ms": int(now * 1000), "end_ms": None, "shots": 0,
                                 "log": follower.path.name if follower.path else "-"}
                    next_shot[t] = max(now, next_shot.get(t, 0.0))
                    out(f"[GUEST_FOLLOW] window start tcin={t} run_log={open_w[t]['log']} period_shots={period[t]['shots']}")
            shootable = [t for t in cur if not period[t]["landed"] and not period[t].get("wall")
                         and period[t]["shots"] < a.max_per_window]
            if not guest.page_ok:
                shootable = []                   # a HUMAN/PX wall on the guest's PDP: do not fire into it
            if shootable and night < a.max_per_night:
                for t in shootable:
                    if time.time() < next_shot.get(t, 0) or night >= a.max_per_night or time.time() >= until:
                        continue
                    next_shot[t] = time.time() + a.cadence_s
                    night += 1                         # a failed shot counts toward the caps too
                    period[t]["shots"] += 1
                    w = open_w[t]
                    w["shots"] += 1
                    try:
                        rec = await guest.fire(t, a.qty)
                        errs = 0
                    except Exception as e:
                        errs += 1
                        out(f"[GUEST_FOLLOW] shot tcin={t} failed {type(e).__name__} ({errs} in a row)")
                        if errs >= 3:
                            next_alive = 0.0          # force the health check below on the next idle pass
                            break
                        continue
                    rec["window_start_ms"] = w["start_ms"]
                    recs.append(rec)
                    out(f"[GUEST_FOLLOW] shot tcin={t} cls={rec['cls']} status={rec['status']} key={rec['key'] or '-'} "
                        f"hop={int(rec['hop'])} n={rec['n']} ms={rec['ms']} cdp={rec['cdp']} body={rec['body'][:80]!r}")
                    if rec["cls"] == "wall":
                        period[t]["wall"] = True
                        guest.page_ok = False        # stop firing until a refresh shows a clean page
                        out(f"[GUEST_FOLLOW] WALL tcin={t} - this period stopped; next refresh re-checks the page")
                    if rec["post_seen"] and 200 <= rec["status"] < 300:
                        period[t]["landed"] = True
                        out(f"[GUEST_FOLLOW] LANDED tcin={t} (guest cart, no card; never checked out) - stopped for this window")
                if time.time() >= next_ctrl and night < a.max_per_night and errs < 3 and guest.page_ok and time.time() < until:
                    next_ctrl = time.time() + 60
                    night += 1
                    try:
                        rec = await guest.fire(P.DECOY, a.qty)
                        rec["window_start_ms"] = None
                        recs.append(rec)
                        out(f"[GUEST_FOLLOW] control cls={rec['cls']} status={rec['status']} key={rec['key'] or '-'}")
                    except Exception as e:
                        errs += 1
                        out(f"[GUEST_FOLLOW] control failed {type(e).__name__}")
            if not shootable or errs >= 3 or night >= a.max_per_night or not guest.page_ok:
                # idle (or stuck / capped): keep the guest healthy and its page fresh
                if now >= next_alive:
                    next_alive = now + 60
                    if errs >= 3 or not await guest.alive():
                        relaunch += 1
                        errs = 0
                        out(f"[GUEST_FOLLOW] guest Chrome not answering - relaunch {relaunch}/{MAX_RELAUNCH}")
                        await guest.close()
                        if relaunch > MAX_RELAUNCH:
                            reason = "too many relaunches"
                            break
                        guest = await start_guest(out, a, pdp, until, route)
                        next_refresh = time.time() + a.refresh_s
                        continue
                if (not cur or not guest.page_ok) and now >= next_refresh:
                    next_refresh = now + a.refresh_s
                    try:
                        await guest.refresh()
                    except Exception as e:
                        out(f"[GUEST_FOLLOW] refresh failed {type(e).__name__}")
            await asyncio.sleep(0.25)
    except EgressFailed as e:
        reason = f"egress failed: {e}"
        rc = 1
    except Exception as e:
        reason = f"error {type(e).__name__}: {str(e)[:120]}"
    finally:
        if guest is not None:
            await guest.close()
        if route is not None:
            await route.stop()
        try:
            LOCK.unlink()
        except Exception:
            pass
    for t, w in list(open_w.items()):
        w["end_ms"] = int(time.time() * 1000)
        windows.append(w)
    open_w.clear()
    dump(reason)
    by = defaultdict(lambda: defaultdict(int))
    for r in recs:
        by[r["role"]][r["cls"]] += 1
    out(f"[GUEST_FOLLOW] END reason={reason!r} windows={len(windows)} shots={night} by_class={ {k: dict(v) for k, v in by.items()} }")
    logf.close()
    return rc


PAST_GATES = ("wall2_denied", "cart", "admitted_fs", "inventory", "cart_limit")


def _tag_of(x: dict) -> str:
    return str(x.get("tag") or "home")


def _pool(files) -> dict:
    d = {"windows": [], "shots": []}
    for x in files:
        d["windows"] += x.get("windows", [])
        d["shots"] += x.get("shots", [])
    return d


def readout(paths, db: Path = EVENTS) -> dict:
    """The pre-registered in-window readout: guest vs members per window, pooled over one or
    more follow JSON files (members from the event store `shots`; build it for each run
    first: tools/events/build.py --only <run>). With files of >= 2 tags (EXP-LINE) the legacy
    rows / verdict are those of the reference (home, else first) tag's files, and by_tag, wall2 and
    cross_line are added."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    files = [json.loads(Path(pth).read_text(encoding="utf-8")) for pth in paths]
    tags = sorted({_tag_of(x) for x in files})
    con = sqlite3.connect(str(db))
    try:
        if len(tags) < 2:
            return _readout_rows(_pool(files), con, tags[0] if tags else "home")
        ref = "home" if "home" in tags else tags[0]
        res = _readout_rows(_pool([x for x in files if _tag_of(x) == ref]), con, ref)
        res["tags"] = tags
        res["by_tag"] = {t: _readout_rows(_pool([x for x in files if _tag_of(x) == t]), con, t) for t in tags}
        res["wall2"] = {t: _wall2(_pool([x for x in files if _tag_of(x) == t])["shots"]) for t in tags}
        res["cross_line"] = _cross_line(files, tags, ref, con)
        return res
    finally:
        con.close()


def _merge(d: dict) -> list:
    """One window per live period: flicker splits <= 60 s apart are merged."""
    merged: list[dict] = []
    for w in sorted((w for w in d.get("windows", []) if w.get("end_ms")), key=lambda x: (x["tcin"], x["start_ms"])):
        if merged and merged[-1]["tcin"] == w["tcin"] and w["start_ms"] - (merged[-1]["end_ms"] or 0) <= 60000:
            merged[-1]["end_ms"] = max(merged[-1]["end_ms"] or 0, w["end_ms"] or 0)
            merged[-1]["starts"].add(w["start_ms"])
        else:
            merged.append(dict(w, starts={w["start_ms"]}))
    return merged


def _member_counts(con, tcin: str, lo: int, hi: int):
    q = ("select count(*), sum(case when gate in (%s) then 1 else 0 end), "
         "sum(case when gate in ('unknown','other') or gate is null then 1 else 0 end) "
         "from shots where tcin=? and ts_ms between ? and ?") % ",".join("?" * len(PAST_GATES))
    m = con.execute(q, (*PAST_GATES, tcin, lo, hi)).fetchone()
    return int(m[0] or 0), int(m[1] or 0), int(m[2] or 0)


def _guest_windows(d: dict) -> list:
    """Merged windows with >= 3 answered guest shots: tcin, start_ms, end_ms, answered, passed."""
    out = []
    for w in _merge(d):
        gs = [r for r in d.get("shots", []) if r.get("tcin") == w["tcin"] and r.get("window_start_ms") in w["starts"]]
        ans = [r for r in gs if r.get("cls") in ("limited", "passed")]
        if len(ans) >= 3:
            out.append({"tcin": w["tcin"], "start_ms": w["start_ms"], "end_ms": w["end_ms"], "answered": len(ans),
                        "passed": sum(1 for r in ans if r["cls"] == "passed")})
    return out


def _wall2(shots: list) -> dict:
    """Wall 2 of the shots that got past wall 1 (cls passed, hot role): the Shape verdict of the line."""
    k = h = o = 0
    for r in shots:
        if r.get("cls") != "passed" or r.get("role") == "control":
            continue
        st, hop = int(r.get("status") or 0), bool(r.get("hop"))
        if st == 401 and not hop:
            k += 1
        elif hop and st in (424, 201):
            h += 1
        else:
            o += 1
    return {"past_wall1": k + h + o, "keyless_401": k, "hop_424_201": h, "other_passed": o}


def _cross_line(files, tags, ref, con) -> dict:
    per = {t: _guest_windows(_pool([x for x in files if _tag_of(x) == t])) for t in tags}
    res = {"ref": ref}
    for x in tags:
        if x == ref:
            continue
        rows, no_member, used = [], 0, set()
        for wr in per[ref]:
            j = next((i for i, wx in enumerate(per[x]) if i not in used and wx["tcin"] == wr["tcin"]
                      and abs(wx["start_ms"] - wr["start_ms"]) <= 5000), None)
            if j is None:
                continue
            used.add(j)
            wx = per[x][j]
            lo = min(wr["start_ms"], wx["start_ms"]) - 2000
            hi = max(wr["end_ms"] or 0, wx["end_ms"] or 0) + 10000
            m_n, m_pass, m_unsc = _member_counts(con, wr["tcin"], lo, hi)
            if m_n - m_unsc == 0:
                no_member += 1
                continue
            rows.append({"tcin": wr["tcin"], "start_ms": wr["start_ms"], "ref_pass": wr["passed"] > 0,
                         "tag_pass": wx["passed"] > 0, "M": m_pass > 0, "tag": x})
        bd = sum(1 for r in rows if r["tag_pass"] and not r["ref_pass"] and not r["M"])
        rev = sum(1 for r in rows if (r["ref_pass"] or r["M"]) and not r["tag_pass"])
        ref_only = sum(1 for r in rows if r["ref_pass"] and not r["tag_pass"])
        same = sum(1 for r in rows if r["ref_pass"] == r["tag_pass"] == r["M"])
        any_pass = sum(1 for r in rows if r["ref_pass"] or r["tag_pass"] or r["M"])
        n = len(rows)
        if bd >= 3 and bd >= 2 * rev:
            v = "LINE-DIFFERS"
        elif n >= 5 and same >= 0.8 * n:
            v = "SAME-FATE-ALL-LINES" + (" (concordant zero: every line limited in every matched window - at a 5 s cadence with per-period caps this is UNDERPOWERED and does NOT establish a TCIN-global state; post-run 2026-10-21 critic)" if any_pass == 0 else "")
        else:
            v = "INCONCLUSIVE"
        res[x] = {"verdict": v, "windows": n, "no_member_data": no_member, "bd_only_pass": bd,
                  "home_only_pass": ref_only, "reverse": rev, "same": same, "any_pass": any_pass, "rows": rows}
    return res


def _readout_rows(d: dict, con, tag=None) -> dict:
    rows, no_member = [], 0
    unclosed = sum(1 for w in d.get("windows", []) if not w.get("end_ms"))
    merged = _merge(d)
    for w in merged:
        gs = [r for r in d.get("shots", []) if r.get("tcin") == w["tcin"] and r.get("window_start_ms") in w["starts"]]
        ans = [r for r in gs if r.get("cls") in ("limited", "passed")]
        if len(ans) < 3:
            continue
        g_pass = sum(1 for r in ans if r["cls"] == "passed")
        lo, hi = w["start_ms"] - 2000, (w["end_ms"] or w["start_ms"]) + 10000
        m_n, m_pass, m_unscored = _member_counts(con, w["tcin"], lo, hi)
        if m_n - m_unscored == 0:            # no member row that can be scored (none, or all unknown / other)
            no_member += 1
            continue
        row = {"tcin": w["tcin"], "start_ms": w["start_ms"], "guest_answered": len(ans), "guest_passed": g_pass,
               "members_shots": m_n, "members_passed": m_pass, "members_unscored": m_unscored,
               "G": g_pass > 0, "M": m_pass > 0}
        if tag not in (None, "home"):
            row["tag"] = tag
        rows.append(row)
    gm = sum(1 for r in rows if r["G"] and not r["M"])
    mg = sum(1 for r in rows if r["M"] and not r["G"])
    same = sum(1 for r in rows if r["G"] == r["M"])
    both_zero = sum(1 for r in rows if not r["G"] and not r["M"])
    any_pass = sum(1 for r in rows if r["G"] or r["M"])
    n = len(rows)
    if n < 5:
        v = f"INCONCLUSIVE (only {n} windows; needs >= 5)"
    elif gm >= 3 and gm >= 2 * mg:
        v = "GUEST-DIFFERS"
    elif same >= 0.8 * n and gm <= 1:
        v = "SAME-FATE"
        if any_pass == 0:
            v = ("SAME-FATE (concordant zero: rules out member-account flagging only; "
                 "line vs TCIN-global not separated)")
    else:
        v = "INCONCLUSIVE"
    return {"verdict": v, "windows": n, "no_member_data": no_member, "unclosed_windows": unclosed,
            "guest_only": gm, "members_only": mg, "same": same,
            "both_zero": both_zero, "any_pass": any_pass, "rows": rows}


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--until", default="06:30", help="local HH:MM to stop (default the next 06:30; at most 9 h)")
    ap.add_argument("--cadence-s", type=float, default=5.0, help="seconds between guest shots per live TCIN (3-60)")
    ap.add_argument("--stock-grace-s", type=float, default=35.0, help="a TCIN stays live this long after its last in-stock log line")
    ap.add_argument("--max-per-window", type=int, default=40, help="guest shots per live period of a TCIN")
    ap.add_argument("--max-per-night", type=int, default=1500)
    ap.add_argument("--scheduled", action="store_true",
                    help="scheduled-task start: run only if state/guest_follow.enable exists")
    ap.add_argument("--refresh-s", type=float, default=240.0, help="idle PDP reload interval")
    ap.add_argument("--settle-s", type=float, default=8.0)
    ap.add_argument("--qty", type=int, default=2)
    ap.add_argument("--simulate", default="", help="off-window path test: treat this TCIN as in stock for --sim-s")
    ap.add_argument("--sim-s", type=float, default=30.0)
    ap.add_argument("--proxy", default="", help="second network line: proxies:<i> | reserve:<i> | resi:<i> (config/proxyIps.json; resi = the residential_proxies list) | http://user:pass@host:port | host:port")
    ap.add_argument("--tag", default="home", help="line tag [a-z0-9_-]{1,16}; non-home tags go into file names, JSON, log lines")
    ap.add_argument("--lock-name", default="guest_follow", help="state/<name>.lock and state/<name>_night.json")
    ap.add_argument("--readout", nargs="+", default=None, help="print the pre-registered readout for follow JSON file(s) and exit")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.readout:
        print(json.dumps(readout(a.readout), indent=1))
        return 0
    if not TAG_RE.fullmatch(a.tag):
        print("bad --tag (want [a-z0-9_-]{1,16})")
        return 2
    if not LOCK_RE.fullmatch(a.lock_name):
        print("bad --lock-name (want [a-z0-9_-]{1,32})")
        return 2
    if a.simulate and not re.fullmatch(r"\d{6,13}", a.simulate):
        print("bad --simulate TCIN")
        return 2
    a.cadence_s = max(3.0, min(60.0, a.cadence_s))
    a.stock_grace_s = max(3.0, min(60.0, a.stock_grace_s))
    a.max_per_window = max(1, min(200, a.max_per_window))
    a.max_per_night = max(1, min(1500, a.max_per_night))
    a.refresh_s = max(60.0, min(1800.0, a.refresh_s))
    a.sim_s = max(5.0, min(300.0, a.sim_s))
    a.qty = max(1, min(2, a.qty))
    return asyncio.run(follow(a))


if __name__ == "__main__":
    sys.exit(main())
