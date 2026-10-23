"""EXP-LIMKEY-GUEST (2026-10-16): is Target's edge limiter state on a hot TCIN a STATIC
per-TCIN policy, or is it switched on by live demand?

Why: 1,208/1,208 drop shots on 10-16 were stopped at the edge limiter (429
ERR_A2C_TCIN_RATE_LIMITED, no x-ssx-hop), and the home line's first contact stopped
getting past it after 10-01 (C-1016-06). Nothing in what we send changed (C-1016-14), so
the next step is an experiment, not a feature (post-run v3 Experiment stage, C-1016-16).
If the limiter already answers 429 off-window on a hot out-of-stock TCIN, every key
question (network line, account, cadence) can be tested off-window at no drop cost.

MONEY-SAFE BY CONSTRUCTION (replaces the in-bot S2-LIMKEY-PROBE, whose money guards
did not converge over 4 Opus evaluator rounds):
  - a FRESH temporary Chrome profile, deleted at the end; never an account profile, no
    login, no account file read: the cart is a GUEST cart with no card, address or
    payment method; the script never calls pre_checkout / checkout, and no bot code path
    ever sees that cart;
  - NO SHARED DEBUGGING PORT: zendriver (bot and probe alike) picks the DevTools port with
    bind(0), which on Windows draws from the dynamic range 49152-65535, and two processes
    can draw the same port within milliseconds (measured 10-16, 5 in 1,200). The probe
    replaces zendriver's free_port IN ITS OWN PROCESS with a port from 31000-39999 confirmed
    by an exclusive bind - a range the bot's bind(0) never yields and its fixed ports
    (5001, 8080, 22000+, 23000+, 24000+) never touch - so no bot Chrome can attach to the
    probe's Chrome and the probe cannot attach to a bot Chrome.
  - ATTACH CHECK (defence in depth): before any navigation the script proves the Chrome it
    drives is its own - the process listening on the DevTools port is the process it
    spawned, the port is in the probe range, exactly one blank page target, zero
    target.com cookies. On a failure it sends nothing, never calls browser.stop() (that
    would close someone else's Chrome), kills only the process it spawned and exits
    without the library's exit hooks.
  - the bot's own Chromes are untouched: `tools/ops/bot_ctl.py` kills only
    nodriver-profile / session_profiles / smoke_profiles, the pool's orphan-kill only its
    own profile paths, and this profile matches none of them;
  - the edge limiter answers BEFORE Shape / SSX and the cart service (C-0930-03), so a
    guest gets the same wall-1 verdict as a member.
  Shared state it DOES touch: the home network line (no proxy anywhere on this host, the
  same line as the 3 buyers), so the probe's POSTs count toward whatever Target tracks
  per network location. Off-window, one POST per (gap_s / live TCINs) seconds - the bot's
  own heartbeat decoys already send ~4 per minute from the same line.

Request: the fast lane's add-to-cart as armed (TARGET_ATC_BYTEMATCH=1 URL with %2C + the
public web key; body {cart_item:{item_channel_id,tcin,quantity}, cart_type, channel_id,
shopping_context}; the fast lane's base headers; PDP referrer, TARGET_ATC_REFERRER_PDP=1),
fired with page fetch() from a target.com PDP, so Shape headers come from the page's own
scripts (page-signed), not from a banked harvest as on most bot shots. Response headers
(tgt-cart-error-key, x-ssx-hop) come from CDP Network.responseReceivedExtraInfo, because
CORS hides them from page JS.

PRE-REGISTERED RULE (written 2026-10-16 before any data; tightened after the 10-16
verifier, still before any data):
  each probe is one row, classified:
    no_post      CDP saw no cart_items POST (fetch threw, or only a CORS preflight
                 went out - its status is kept as opt_status) - NOT answered
    unread       a POST, but CDP gave no response headers (key / hop unknown) - NOT answered
    wall         HTTP 403, or a HUMAN/PX / access-denied body - NOT answered; any wall row
                 makes the run INCONCLUSIVE
    unclassified HTTP >= 500 or 0 - NOT answered
    limited      429 and key ERR_A2C_TCIN_RATE_LIMITED, or 429 with no key, empty body and
                 no x-ssx-hop
    passed       any other answer (2xx, 400, 401, 404, 409, 422, 424, a 429 with another
                 key or a body) - the request got past the edge limiter
  answered = limited + passed
  control  = the bot heartbeat's bogus decoy TCIN 81926151 (never 2xx in 105,259 bot
             decoys); VALID when it has >= 10 answered and 0 limited
  TESTBED          every hot TCIN >= N answered and limited share >= 50%, control VALID
                   -> the limiter is a static per-TCIN policy off-window; run the line
                      test (same script over a second network) next
  DEMAND-SWITCHED  every hot TCIN >= N answered and 0 limited, control VALID
                   -> the limiter follows live demand; only in-window tests can answer
                      the line question
  SPLIT            every hot TCIN >= N answered, at least one >= 50% and one at 0,
                   control VALID -> per-TCIN policy; line test on the limited TCIN(s)
  INCONCLUSIVE     anything else (a wall row, control not valid, < N answered, shares
                   strictly between 0 and 50%) - the summary names why
  N = --per-tcin (default 30; the control always gets max(N, 10)). A single clock hour is
  one sample of the policy; repeat in a second and third hour before treating a TESTBED /
  DEMAND-SWITCHED as settled. A 2xx POST on a hot TCIN (a line landed in the guest cart)
  stops probing that TCIN and is reported: the limiter let it through and it was addable.

    python tools/experiments/limkey_guest_probe.py --tcins 1010892076,1012644665
    python tools/experiments/limkey_guest_probe.py --tcins 1010892076 --per-tcin 3 --gap-s 60   # smoke

Runs only 06:00-23:05 local: the 00-06 drop band, and the 23:15 ensure-armed wrapper start
(whose chrome_target_signout.py kills every chrome.exe on the host), are out. The window is
checked before the start and before EVERY POST. Output: logs/experiments/
limkey_guest_<stamp>.log (one [LIMKEY_GUEST] line per probe) + .json (summary + verdict).
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
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "logs" / "experiments"
DECOY = "81926151"
ATC_URL = ("https://carts.target.com/web_checkouts/v1/cart_items"
           "?field_groups=CART%2CCART_ITEMS%2CSUMMARY"
           "&key=9f36aeafbe60771e321a7cc95a78140772ab3e96")
ERR_A2C = "ERR_A2C_TCIN_RATE_LIMITED"
ALLOW_FROM_MIN, ALLOW_TO_MIN = 6 * 60, 23 * 60 + 5        # local minutes of day, [from, to)
WALL_RE = re.compile(r"px-captcha|press\s*&\s*hold|perimeterx|access denied|human verification", re.I)
BLANK_URLS = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/", "")


def in_allowed_window(t: float | None = None) -> bool:
    lt = time.localtime(t)
    m = lt.tm_hour * 60 + lt.tm_min
    return ALLOW_FROM_MIN <= m < ALLOW_TO_MIN


def window_end_ts(now: float) -> float:
    """Epoch seconds of today's 23:05 local (the end of the allowed window)."""
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, ALLOW_TO_MIN // 60, ALLOW_TO_MIN % 60, 0, 0, 0, -1))


def atc_js(tcin: str, qty: int, buynow: bool = False) -> str:
    """The fast lane's add-to-cart (TARGET_ATC_BYTEMATCH=1 URL + body, base headers, PDP
    referrer) as one page fetch; returns a JSON string {s, n, b, ms}. buynow=True adds
    cart_subchannel:'BUYNOW' (the Buy-Now-from-PDP path, ktorng capture) to test whether that
    subchannel shares the add-to-cart limiter (2026-10-23 /goal); money path is unchanged."""
    if not re.fullmatch(r"\d{6,13}", tcin):
        raise ValueError(f"bad tcin {tcin!r}")
    q = int(qty)
    sub = ", cart_subchannel: 'BUYNOW'" if buynow else ""
    ref = f"https://www.target.com/p/-/A-{tcin}"
    return f"""(async () => {{
    const t0 = performance.now();
    try {{
        const r = await fetch('{ATC_URL}', {{
            method: 'POST', credentials: 'include',
            referrer: '{ref}', referrerPolicy: 'no-referrer-when-downgrade',
            headers: {{'Content-Type': 'application/json', 'Accept': 'application/json',
                       'Origin': 'https://www.target.com', 'Referer': '{ref}',
                       'x-application-name': 'web'}},
            body: JSON.stringify({{cart_item: {{item_channel_id: '10', tcin: '{tcin}', quantity: {q}}},
                                   cart_type: 'REGULAR', channel_id: '10', shopping_context: 'DIGITAL'{sub}}})
        }});
        let b = ''; try {{ b = await r.text(); }} catch (e) {{}}
        return JSON.stringify({{s: r.status, n: b.length, b: b.slice(0, 160), ms: Math.round(performance.now() - t0)}});
    }} catch (e) {{
        return JSON.stringify({{s: 0, n: -1, b: String(e).slice(0, 160), ms: Math.round(performance.now() - t0)}});
    }}
}})()"""


def classify(rec: dict) -> str:
    """One probe row -> no_post / unread / wall / unclassified / limited / passed (rule above)."""
    if not rec.get("post_seen"):
        return "no_post"
    st = int(rec.get("status") or 0)
    body = str(rec.get("body") or "")
    if st == 403 or WALL_RE.search(body):
        return "wall"
    if not rec.get("cdp"):
        return "unread"
    if st == 0 or st >= 500:
        return "unclassified"
    if st == 429:
        key = str(rec.get("key") or "")
        if key == ERR_A2C:
            return "limited"
        if not key and rec.get("n", -1) == 0 and not rec.get("hop"):
            return "limited"
        if not key and rec.get("n", -1) < 0:
            return "unread"            # a keyless 429 whose body page JS could not read: not classifiable"
    return "passed"


def verdict(hot: list[str], recs: list[dict], n_min: int) -> tuple[str, str, dict]:
    by = defaultdict(lambda: {"answered": 0, "limited": 0, "s2xx": 0, "cls": defaultdict(int),
                              "status": defaultdict(int)})
    walls = 0
    for r in recs:
        c = r.get("cls") or classify(r)
        b = by[r["tcin"]]
        b["cls"][c] += 1
        walls += int(c == "wall")
        if c in ("limited", "passed"):
            b["answered"] += 1
            b["limited"] += int(c == "limited")
            b["s2xx"] += int(200 <= int(r["status"]) < 300)
            b["status"][f'{r["status"]}:{r.get("key") or "-"}'] += 1
    table = {t: {"answered": v["answered"], "limited": v["limited"], "s2xx": v["s2xx"],
                 "share": (round(v["limited"] / v["answered"], 3) if v["answered"] else None),
                 "rows": dict(v["cls"]), "status": dict(v["status"])} for t, v in by.items()}
    if walls:
        return "INCONCLUSIVE", f"{walls} wall row(s) (403 / HUMAN-PX body) - the line or browser was challenged", table
    c = table.get(DECOY, {"answered": 0, "limited": 0})
    if c["answered"] < 10 or c["limited"] > 0:
        return "INCONCLUSIVE", f"control {DECOY} not valid ({c['limited']}/{c['answered']} limited; needs >=10 answered, 0 limited)", table
    short = [t for t in hot if table.get(t, {"answered": 0})["answered"] < n_min]
    if short:
        return "INCONCLUSIVE", f"under N={n_min} answered: {', '.join(short)}", table
    shares = {t: table[t]["share"] for t in hot}
    if all(s >= 0.5 for s in shares.values()):
        return "TESTBED", "every hot TCIN limited >= 50% off-window, control clean", table
    if all(table[t]["limited"] == 0 for t in hot):
        return "DEMAND-SWITCHED", "0 limited on every hot TCIN off-window, control clean", table
    if any(s >= 0.5 for s in shares.values()) and any(table[t]["limited"] == 0 for t in hot):
        return "SPLIT", "per-TCIN policy: " + ", ".join(f"{t} {shares[t]}" for t in hot), table
    return "INCONCLUSIVE", "shares between 0 and 50%: " + ", ".join(f"{t} {shares[t]}" for t in hot), table


PROBE_PORT_LO, PROBE_PORT_HI = 31000, 40000        # [lo, hi): never yielded by the bot's bind(0)


def probe_free_port() -> int:
    """A DevTools port in 31000-39999 that is free right now (exclusive bind); replaces
    zendriver.core.util.free_port in THIS process only."""
    import random
    import socket
    rng = random.SystemRandom()
    for _ in range(300):
        p = rng.randrange(PROBE_PORT_LO, PROBE_PORT_HI)
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sk.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sk.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            sk.close()
    raise RuntimeError("no free probe port in 31000-39999")


def dynamic_range_clear() -> str:
    """'' when Windows' TCP dynamic port range (what bind(0) draws from - the bot's DevTools
    ports) lies entirely outside the probe range, else the reason. Fails closed."""
    import subprocess
    if sys.platform != "win32":
        return ""
    for fam in ("ipv4", "ipv6"):
        try:
            o = subprocess.run(["netsh", "int", fam, "show", "dynamicport", "tcp"],
                               capture_output=True, text=True, timeout=15).stdout
            start = int(re.search(r"Start Port\s*:\s*(\d+)", o).group(1))
            num = int(re.search(r"Number of Ports\s*:\s*(\d+)", o).group(1))
        except Exception as e:
            return f"could not read the {fam} dynamic port range ({type(e).__name__})"
        if start < PROBE_PORT_HI and start + num > PROBE_PORT_LO:
            return f"{fam} dynamic range {start}-{start + num - 1} overlaps the probe range"
    return ""


def listener_pid(port: int):
    """PID of the process LISTENing on TCP port `port`, or None."""
    import psutil
    for c in psutil.net_connections(kind="tcp"):
        try:
            if c.status == psutil.CONN_LISTEN and c.laddr and int(c.laddr.port) == int(port):
                return c.pid
        except Exception:
            continue
    return None


def _kill_own(browser) -> None:
    """Kill ONLY the process this script spawned (its own handle), never by name or port."""
    proc = getattr(browser, "_process", None) if browser is not None else None
    try:
        if proc is not None and getattr(proc, "returncode", 0) is None:
            proc.kill()
    except Exception:
        pass


async def attach_check(browser, cdp, profile: Path) -> str:
    """'' when the driven Chrome is provably our fresh one, else the reason. Sends no
    navigation and no request to any site."""
    try:
        port = int(getattr(browser.config, "port", 0) or 0)
        if not (PROBE_PORT_LO <= port < PROBE_PORT_HI):
            return f"DevTools port {port} is outside the probe range (free_port patch not applied?)"
        proc = getattr(browser, "_process", None)
        own = int(getattr(proc, "pid", 0) or 0)
        lp = listener_pid(port)
        if not own or lp != own:
            return f"port {port} is held by pid {lp}, not our Chrome pid {own}"
        tab = browser.main_tab
        targets = await asyncio.wait_for(tab.send(cdp.target.get_targets()), timeout=10)
        pages = [t for t in targets if str(getattr(t, "type_", "")) == "page"]
        if len(pages) != 1:
            return f"{len(pages)} page targets (a fresh Chrome has 1)"
        url = str(getattr(pages[0], "url", "") or "")
        if url not in BLANK_URLS:
            return f"page target is not blank ({url[:60]!r})"
        cookies = await asyncio.wait_for(tab.send(cdp.storage.get_cookies()), timeout=10)
        tc = [c for c in (cookies or []) if "target.com" in str(getattr(c, "domain", "") or "")]
        if tc:
            return f"{len(tc)} target.com cookies present (a fresh profile has 0)"
        try:
            cl = await asyncio.wait_for(tab.send(cdp.browser.get_browser_command_line()), timeout=5)
            joined = " ".join(str(x) for x in (cl or []))
            if joined and str(profile).lower() not in joined.lower():
                return "the browser command line does not carry our profile path"
        except Exception:
            pass                       # only available with --enable-automation; the 3 checks above stand
        return ""
    except Exception as e:
        return f"attach check failed ({type(e).__name__})"


async def run(a) -> int:
    import zendriver as uc
    from zendriver import cdp

    hot = list(dict.fromkeys(t for t in re.split(r"[,;\s]+", a.tcins) if t))
    if not hot:
        print("no hot TCIN given")
        return 2
    for t in hot:
        if not re.fullmatch(r"\d{6,13}", t) or t == DECOY:
            print(f"bad hot TCIN {t!r}")
            return 2
    why_range = dynamic_range_clear()
    if why_range:
        print(f"refused: {why_range}")
        return 5
    now = time.time()
    if not in_allowed_window(now):
        print("refused: outside 06:00-23:05 local (the drop band and the 23:15 wrapper start are out)")
        return 3
    t_end = min(now + a.max_minutes * 60, window_end_ts(now))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = OUT_DIR / f"limkey_guest_{stamp}.log"
    sum_path = OUT_DIR / f"limkey_guest_{stamp}.json"
    logf = log_path.open("a", encoding="utf-8")

    def out(line: str) -> None:
        s = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}"
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    profile = Path(tempfile.mkdtemp(prefix="limkey_guest_"))
    out(f"[LIMKEY_GUEST] start hot={','.join(hot)} control={DECOY} per_tcin={a.per_tcin} gap_s={a.gap_s} "
        f"qty={a.qty} profile=fresh-temp (guest, no login) net=default-route until={time.strftime('%H:%M', time.localtime(t_end))}")
    browser = None
    recs: list[dict] = []
    reason_end = "done"
    try:
        import zendriver.core.util as _zutil
        _zutil.free_port = probe_free_port          # this process only; see NO SHARED DEBUGGING PORT
        cfg = uc.Config(user_data_dir=str(profile), headless=False, sandbox=True,
                        browser_args=["--window-size=1100,800", "--no-first-run",
                                      "--no-default-browser-check", "--disable-extensions",
                                      "--disable-sync", "--window-position=-32000,-32000"],
                        browser_connection_timeout=1.0, browser_connection_max_tries=30)
        browser = await uc.start(cfg)
        why = await attach_check(browser, cdp, profile)
        if why:
            out(f"[LIMKEY_GUEST] ABORT attach check: {why} - nothing sent; killing only our own process; "
                f"exiting without browser.stop()")
            _kill_own(browser)
            logf.close()
            shutil.rmtree(profile, ignore_errors=True)
            os._exit(4)                 # skip the library's exit hooks (they would stop() the attached Chrome)
        out(f"[LIMKEY_GUEST] attach check OK (port {browser.config.port} held by our Chrome, 1 blank page target, 0 target.com cookies)")

        tab = await browser.get(f"https://www.target.com/p/-/A-{hot[0]}")
        await asyncio.sleep(a.settle_s)
        title = str(await tab.evaluate("document.title") or "")
        px = bool(await tab.evaluate(
            "!!document.querySelector('#px-captcha') || /press\\s*&\\s*hold|access denied/i.test(document.body ? document.body.innerText.slice(0, 4000) : '')"))
        out(f"[LIMKEY_GUEST] page title={title[:80]!r} px_wall={int(px)}")
        if px:
            reason_end = "HUMAN/PX wall on the PDP"
            raise RuntimeError(reason_end)

        hdrs: dict[str, dict] = {}
        reqs: list[tuple[str, str]] = []           # (request_id, method) of cart_items requests, in order

        async def on_send(ev):
            try:
                r = ev.request
                if "cart_items" in str(r.url) and str(r.method).upper() in ("POST", "OPTIONS"):
                    reqs.append((str(ev.request_id), str(r.method).upper()))
            except Exception:
                pass

        async def on_extra(ev):
            try:
                hdrs[str(ev.request_id)] = {"status": int(ev.status_code),
                                            **{k.lower(): v for k, v in dict(ev.headers).items()}}
            except Exception:
                pass

        tab.add_handler(cdp.network.RequestWillBeSent, on_send)
        tab.add_handler(cdp.network.ResponseReceivedExtraInfo, on_extra)
        await tab.send(cdp.network.enable())

        target_n = {t: a.per_tcin for t in hot}
        target_n[DECOY] = max(a.per_tcin, 10)
        order = hot + [DECOY]
        answered = defaultdict(int)
        stopped: set[str] = set()
        seq = 0
        while seq < a.max_total:
            live = [t for t in order if t not in stopped and answered[t] < target_n[t]]
            if not live:
                break
            for t in live:
                if not in_allowed_window() or time.time() >= t_end or seq >= a.max_total:
                    reason_end = "end of the allowed window / max-minutes / max-total"
                    break
                seq += 1
                n_before = len(reqs)
                raw = await asyncio.wait_for(tab.evaluate(atc_js(t, a.qty), await_promise=True), timeout=25)
                try:
                    j = json.loads(raw) if isinstance(raw, str) else {}
                except Exception:
                    j = {}
                await asyncio.sleep(0.6)            # let the ExtraInfo event land
                new = reqs[n_before:]
                post_ids = [r for r, m in new if m == "POST"]
                opt_ids = [r for r, m in new if m == "OPTIONS"]
                hx = hdrs.get(post_ids[0], {}) if post_ids else {}
                ox = hdrs.get(opt_ids[-1], {}) if opt_ids else {}
                status = int(hx.get("status") or 0) or (int(j.get("s") or 0) if post_ids else 0)
                body = str(j.get("b", "") or "").replace("\n", " ")
                rec = {"seq": seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hour": time.localtime().tm_hour,
                       "tcin": t, "role": "control" if t == DECOY else "hot", "post_seen": bool(post_ids),
                       "status": status, "key": str(hx.get("tgt-cart-error-key", "") or ""),
                       "hop": "x-ssx-hop" in hx, "retry_after": hx.get("retry-after"), "n": j.get("n", -1),
                       "ms": j.get("ms"), "cdp": int(bool(hx)), "body": body[:160],
                       "opt_status": int(ox.get("status") or 0), "opt_key": str(ox.get("tgt-cart-error-key", "") or ""),
                       "first": answered[t] == 0}
                rec["cls"] = classify(rec)
                recs.append(rec)
                if rec["cls"] in ("limited", "passed"):
                    answered[t] += 1
                out(f"[LIMKEY_GUEST] seq={seq} tcin={t} role={rec['role']} cls={rec['cls']} status={status} "
                    f"key={rec['key'] or '-'} hop={int(rec['hop'])} n={rec['n']} ms={rec['ms']} cdp={rec['cdp']} "
                    f"post={int(rec['post_seen'])} opt={rec['opt_status']}:{rec['opt_key'] or '-'} body={body[:100]!r}")
                if rec["cls"] == "wall":
                    reason_end = "wall row (403 / HUMAN-PX body) - stopped"
                    break
                if rec["role"] == "hot" and rec["post_seen"] and 200 <= status < 300:
                    stopped.add(t)
                    out(f"[LIMKEY_GUEST] LANDED tcin={t} (guest cart, no card; never checked out) - probing of {t} stopped")
                await asyncio.sleep(a.gap_s / max(1, len(live)))
            if reason_end != "done":
                break
    except Exception as e:
        if reason_end == "done":
            reason_end = f"error {type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            if browser is not None:
                await asyncio.wait_for(browser.stop(), timeout=10)
        except Exception:
            pass
        _kill_own(browser)
        await asyncio.sleep(1.0)
        shutil.rmtree(profile, ignore_errors=True)

    v, why, table = verdict(hot, recs, a.per_tcin)
    hours = sorted({r["hour"] for r in recs})
    first = {r["tcin"]: {"cls": r["cls"], "status": r["status"], "key": r["key"], "ts": r["ts"]}
             for r in recs if r.get("first") and r["cls"] in ("limited", "passed")}
    summary = {"experiment": "EXP-LIMKEY-GUEST", "started": stamp, "hot": hot, "control": DECOY,
               "first_contact": first, "per_tcin": a.per_tcin, "end": reason_end, "verdict": v, "why": why,
               "clock_hours": hours, "table": table, "log": str(log_path)}
    sum_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    out(f"[LIMKEY_GUEST] END reason={reason_end!r} verdict={v} why={why!r} hours={hours}")
    for t, row in table.items():
        out(f"[LIMKEY_GUEST] table tcin={t} answered={row['answered']} limited={row['limited']} "
            f"share={row['share']} 2xx={row['s2xx']} rows={row['rows']} status={row['status']}")
    logf.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tcins", required=True, help="hot out-of-stock TCINs, comma-separated")
    ap.add_argument("--per-tcin", type=int, default=30, help="answered POSTs per hot TCIN (N), default 30")
    ap.add_argument("--gap-s", type=float, default=120.0,
                    help="seconds per round: each TCIN is hit once per round, POSTs spread evenly (20-600)")
    ap.add_argument("--qty", type=int, default=2, help="quantity in the body (the bot sends 2)")
    ap.add_argument("--max-total", type=int, default=200)
    ap.add_argument("--max-minutes", type=float, default=150.0)
    ap.add_argument("--settle-s", type=float, default=8.0, help="wait after the PDP load")
    a = ap.parse_args(argv)
    a.gap_s = max(20.0, min(600.0, a.gap_s))
    a.per_tcin = max(1, min(200, a.per_tcin))
    a.max_total = max(1, min(400, a.max_total))
    a.max_minutes = max(1.0, min(600.0, a.max_minutes))
    a.qty = max(1, min(2, a.qty))
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
