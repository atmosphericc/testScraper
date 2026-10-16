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
    payment method, and no bot code path ever sees it;
  - the bot's own Chromes are untouched (separate process / profile / debugging port);
    `tools/ops/bot_ctl.py` kills only nodriver-profile / session_profiles /
    smoke_profiles, and this profile matches none of them;
  - the edge limiter answers BEFORE Shape / SSX and the cart service (C-0930-03), so
    a guest gets the same wall-1 verdict as a member.

Request: the fast lane's add-to-cart, byte-for-byte as armed (TARGET_ATC_BYTEMATCH=1:
URL with %2C + the public web key, body {cart_item:{item_channel_id,tcin,quantity},
cart_type, channel_id, shopping_context}, the fast lane's header set, PDP referrer),
fired with page fetch() from a target.com PDP so the page's own scripts sign it. The
response headers (tgt-cart-error-key, x-ssx-hop) are read from CDP
Network.responseReceivedExtraInfo, because CORS hides them from page JS.

PRE-REGISTERED RULE (written 2026-10-16 before any data):
  answered  = a cart_items POST with a non-zero HTTP status seen by CDP or page JS
  limited   = status 429 and (key ERR_A2C_TCIN_RATE_LIMITED, or key unseen and body empty
              and no x-ssx-hop)
  control   = the bot heartbeat's bogus decoy TCIN (default 81926151); VALID when it
              has >= 10 answered and 0 limited (otherwise the IP / session is blanket
              blocked and nothing about the TCINs can be read)
  TESTBED          every hot TCIN >= N answered and limited share >= 50%, control VALID
                   -> the limiter is a static per-TCIN policy off-window; run the line
                      test (same script over a second network) next
  DEMAND-SWITCHED  every hot TCIN >= N answered and 0 limited, control VALID
                   -> the limiter follows live demand; only in-window tests can answer
                      the line question
  SPLIT            every hot TCIN >= N answered, at least one >= 50% and one at 0,
                   control VALID -> per-TCIN policy; line test on the limited TCIN(s)
  INCONCLUSIVE     anything else (control not valid, a HUMAN/PX wall, < N answered,
                   shares between 0 and 50%) - the summary names why
  N = --per-tcin (default 30). A single clock hour is one sample of the policy; repeat in
  a second and third hour before treating a TESTBED / DEMAND-SWITCHED as settled.
  A 2xx on a hot TCIN (a line landed in the guest cart) stops probing that TCIN and is
  reported: it means the limiter let it through and the item was addable.

    python tools/experiments/limkey_guest_probe.py --tcins 1010892076,1012644665
    python tools/experiments/limkey_guest_probe.py --tcins 1010892076 --per-tcin 5 --gap-s 30   # smoke

Refuses to run 00:00-06:00 local (the drop band). Output: logs/experiments/
limkey_guest_<stamp>.log (one [LIMKEY_GUEST] line per POST) + .json (summary + verdict).
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
QUIET_START_H, QUIET_END_H = 0, 6


def atc_js(tcin: str, qty: int) -> str:
    """The fast lane's add-to-cart (TARGET_ATC_BYTEMATCH=1 URL + body, its header set,
    the PDP referrer) as one page fetch; returns a JSON string {s, n, b, ms}."""
    if not re.fullmatch(r"\d{6,13}", tcin):
        raise ValueError(f"bad tcin {tcin!r}")
    q = int(qty)
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
                                   cart_type: 'REGULAR', channel_id: '10', shopping_context: 'DIGITAL'}})
        }});
        let b = ''; try {{ b = await r.text(); }} catch (e) {{}}
        return JSON.stringify({{s: r.status, n: b.length, b: b.slice(0, 160), ms: Math.round(performance.now() - t0)}});
    }} catch (e) {{
        return JSON.stringify({{s: 0, n: -1, b: String(e).slice(0, 160), ms: Math.round(performance.now() - t0)}});
    }}
}})()"""


def is_limited(rec: dict) -> bool:
    if rec.get("status") != 429:
        return False
    key = rec.get("key")
    if key:
        return key == ERR_A2C
    return rec.get("n", -1) == 0 and not rec.get("hop")


def verdict(hot: list[str], recs: list[dict], n_min: int) -> tuple[str, str, dict]:
    by = defaultdict(lambda: {"answered": 0, "limited": 0, "s2xx": 0, "status": defaultdict(int)})
    for r in recs:
        if not r.get("status"):
            continue
        b = by[r["tcin"]]
        b["answered"] += 1
        b["limited"] += int(is_limited(r))
        b["s2xx"] += int(200 <= r["status"] < 300)
        b["status"][f'{r["status"]}:{r.get("key") or "-"}'] += 1
    table = {t: {"answered": v["answered"], "limited": v["limited"], "s2xx": v["s2xx"],
                 "share": (round(v["limited"] / v["answered"], 3) if v["answered"] else None),
                 "status": dict(v["status"])} for t, v in by.items()}
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


async def run(a) -> int:
    import zendriver as uc
    from zendriver import cdp

    hot = [t for t in re.split(r"[,;\s]+", a.tcins) if t]
    for t in hot:
        if not re.fullmatch(r"\d{6,13}", t) or t == DECOY:
            print(f"bad hot TCIN {t!r}")
            return 2
    h = time.localtime().tm_hour
    if QUIET_START_H <= h < QUIET_END_H:
        print(f"refused: {h:02d}h is inside the {QUIET_START_H:02d}-{QUIET_END_H:02d} drop band")
        return 3
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
        f"qty={a.qty} profile=fresh-temp (guest, no login) net=default-route")
    browser = None
    recs: list[dict] = []
    reason_end = "done"
    try:
        cfg = uc.Config(user_data_dir=str(profile), headless=False, sandbox=True,
                        browser_args=["--window-size=1100,800", "--no-first-run",
                                      "--no-default-browser-check", "--disable-extensions",
                                      "--disable-sync", "--window-position=-32000,-32000"],
                        browser_connection_timeout=1.0, browser_connection_max_tries=30)
        browser = await uc.start(cfg)
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
        posts: list[tuple[str, str]] = []          # (request_id, method) of cart_items requests, in order

        async def on_send(ev):
            try:
                r = ev.request
                if "cart_items" in str(r.url) and str(r.method).upper() in ("POST", "OPTIONS"):
                    posts.append((str(ev.request_id), str(r.method).upper()))
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

        order = hot + [DECOY]
        answered = defaultdict(int)
        stopped: set[str] = set()
        seq = 0
        t_end = time.time() + a.max_minutes * 60
        while time.time() < t_end and seq < a.max_total:
            live = [t for t in order if t not in stopped and answered[t] < a.per_tcin]
            if not live:
                break
            if QUIET_START_H <= time.localtime().tm_hour < QUIET_END_H:
                reason_end = "entered the drop band"
                break
            for t in live:
                seq += 1
                n_before = len(posts)
                raw = await asyncio.wait_for(tab.evaluate(atc_js(t, a.qty), await_promise=True), timeout=25)
                try:
                    j = json.loads(raw) if isinstance(raw, str) else {}
                except Exception:
                    j = {}
                await asyncio.sleep(0.5)            # let the ExtraInfo event land
                new = posts[n_before:]
                post_ids = [r for r, m in new if m == "POST"]
                opt_ids = [r for r, m in new if m == "OPTIONS"]
                rid = post_ids[0] if post_ids else (opt_ids[-1] if opt_ids else "")
                hx = hdrs.get(rid, {})
                status = int(j.get("s") or 0) or int(hx.get("status") or 0)
                preflight_only = bool(opt_ids) and not post_ids
                rec = {"seq": seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hour": time.localtime().tm_hour,
                       "tcin": t, "role": "control" if t == DECOY else "hot", "status": status,
                       "key": str(hx.get("tgt-cart-error-key", "") or ""), "hop": "x-ssx-hop" in hx,
                       "retry_after": hx.get("retry-after"), "n": j.get("n", -1), "ms": j.get("ms"),
                       "cdp": int(bool(hx)), "preflight_only": preflight_only, "first": answered[t] == 0}
                rec["limited"] = is_limited(rec)
                recs.append(rec)
                if status:
                    answered[t] += 1
                body = str(j.get("b", "") or "").replace("\n", " ")[:100]
                out(f"[LIMKEY_GUEST] seq={seq} tcin={t} role={rec['role']} status={status} key={rec['key'] or '-'} "
                    f"hop={int(rec['hop'])} n={rec['n']} ms={rec['ms']} limited={int(rec['limited'])} "
                    f"cdp={rec['cdp']} preflight_only={int(preflight_only)} first={int(rec['first'])} body={body!r}")
                if rec["role"] == "hot" and 200 <= status < 300:
                    stopped.add(t)
                    out(f"[LIMKEY_GUEST] LANDED tcin={t} (guest cart, no card) - probing of {t} stopped")
                await asyncio.sleep(a.gap_s / max(1, len(live)))
    except Exception as e:
        if reason_end == "done":
            reason_end = f"error {type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            if browser is not None:
                await asyncio.wait_for(browser.stop(), timeout=10)
        except Exception:
            pass
        try:
            proc = getattr(browser, "_process", None)
            if proc is not None and proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        await asyncio.sleep(1.0)
        shutil.rmtree(profile, ignore_errors=True)

    v, why, table = verdict(hot, recs, a.per_tcin)
    hours = sorted({r["hour"] for r in recs})
    first = {r["tcin"]: {"status": r["status"], "key": r["key"], "limited": r["limited"], "ts": r["ts"]}
             for r in recs if r.get("first") and r.get("status")}
    summary = {"experiment": "EXP-LIMKEY-GUEST", "started": stamp, "hot": hot, "control": DECOY,
               "first_contact": first,
               "per_tcin": a.per_tcin, "end": reason_end, "verdict": v, "why": why, "clock_hours": hours,
               "table": table, "log": str(log_path)}
    sum_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    out(f"[LIMKEY_GUEST] END reason={reason_end!r} verdict={v} why={why!r} hours={hours}")
    for t, row in table.items():
        out(f"[LIMKEY_GUEST] table tcin={t} answered={row['answered']} limited={row['limited']} "
            f"share={row['share']} 2xx={row['s2xx']} status={row['status']}")
    logf.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tcins", required=True, help="hot out-of-stock TCINs, comma-separated")
    ap.add_argument("--per-tcin", type=int, default=30, help="answered POSTs per TCIN (N), default 30")
    ap.add_argument("--gap-s", type=float, default=120.0, help="seconds per round: each TCIN is hit once per round, POSTs spread evenly (min 20)")
    ap.add_argument("--qty", type=int, default=2, help="quantity in the body (the bot sends 2)")
    ap.add_argument("--max-total", type=int, default=200)
    ap.add_argument("--max-minutes", type=float, default=150.0)
    ap.add_argument("--settle-s", type=float, default=8.0, help="wait after the PDP load")
    a = ap.parse_args(argv)
    a.gap_s = max(20.0, a.gap_s)
    a.per_tcin = max(1, min(200, a.per_tcin))
    a.max_total = max(1, min(400, a.max_total))
    a.qty = max(1, min(2, a.qty))
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
