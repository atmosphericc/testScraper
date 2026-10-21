#!/usr/bin/env python3
"""Offline test for EXP-LINE (2026-10-20): the guest follower's --proxy / --tag / --lock-name and the
cross-line readout. No Chrome, no network, no bot: the forwarder, the guest and the egress lookup are fakes.

    python tests/test_limkey_guest_follow_lines.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import limkey_guest_probe as P  # noqa: E402
import limkey_guest_follow as F  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


SECRET = "S3CRETPW9"
URL0 = f"http://brd-customer-C1-zone-z-ip-1.2.3.4:{SECRET}@brd.superproxy.io:33335"
URL1 = f"http://brd-customer-C1-zone-z-ip-5.6.7.8:{SECRET}R@brd.superproxy.io:33335"

# ── arg parsing + defaults ───────────────────────────────────────────────────
a = F.build_parser().parse_args([])
check("defaults", (a.proxy, a.tag, a.lock_name) == ("", "home", "guest_follow"))
a = F.build_parser().parse_args(["--scheduled", "--tag", "bd", "--lock-name", "guest_follow_bd", "--proxy", "reserve:1"])
check("args_parsed", (a.proxy, a.tag, a.lock_name, a.scheduled) == ("reserve:1", "bd", "guest_follow_bd", True))
check("main_bad_tag", F.main(["--tag", "BAD TAG"]) == 2 and F.main(["--tag", "x" * 17]) == 2)
check("main_bad_lock_name", F.main(["--lock-name", "../x"]) == 2)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # ── proxy resolution against a temp proxyIps.json ────────────────────────
    cfg = td / "proxyIps.json"
    cfg.write_text(json.dumps({"proxies": [URL0], "reserve_proxies": ["x", URL1]}), encoding="utf-8")
    r0 = F.resolve_proxy("proxies:0", cfg)
    r1 = F.resolve_proxy("reserve:1", cfg)
    check("resolve_proxies_0", r0["url"] == URL0 and r0["display"] == "brd.superproxy.io:33335")
    check("resolve_reserve_1", r1["url"] == URL1 and r1["display"] == "brd.superproxy.io:33335")
    errs = []
    for spec in ("reserve:5", "proxies:1", "junk", "http://u@h"):
        try:
            F.resolve_proxy(spec, cfg)
            errs.append(None)
        except ValueError as e:
            errs.append(str(e))
    check("resolve_errors", all(e for e in errs) and not any(SECRET in e for e in errs), str(errs))
    lit = F.resolve_proxy("http://u:p@host.example:8080")
    check("resolve_literal_url", lit["url"] == "http://u:p@host.example:8080" and lit["display"] == "host.example:8080")
    lit2 = F.resolve_proxy("10.0.0.5:3128")
    check("resolve_literal_hostport", lit2["url"] == "" and lit2["server"] == "10.0.0.5:3128")

    # ── file naming + lock isolation ─────────────────────────────────────────
    F.STATE = td / "state"
    F.STATE.mkdir()
    F.ENABLE = F.STATE / "guest_follow.enable"
    F.set_lock_name("guest_follow")
    check("lock_default", F.LOCK.name == "guest_follow.lock" and F.NIGHT.name == "guest_follow_night.json")
    F.set_lock_name("guest_follow_bd")
    check("lock_named", F.LOCK.name == "guest_follow_bd.lock" and F.NIGHT.name == "guest_follow_bd_night.json")
    F.set_lock_name("guest_follow")
    F.LOCK.write_text(str(os.getpid()))             # this test's cmdline contains "limkey_guest_follow"
    held_home = F.lock_held()
    F.set_lock_name("guest_follow_bd")
    check("lock_isolation", held_home and not F.lock_held())
    F.set_lock_name("guest_follow")
    F.LOCK.unlink()

    # ── follow(): fakes ──────────────────────────────────────────────────────
    F.OUT_DIR = td / "out"
    F.PROXY_CFG = cfg
    F.hot_from_config = lambda *x, **k: ["1010892076"]
    P.dynamic_range_clear = lambda: ""
    FIRED, POOLS = [], []

    class FakePool:
        def __init__(self):
            self.ups, self.started, self.stopped = [], False, False
            POOLS.append(self)

        def add_upstream(self, url, port):
            self.ups.append((url, port))

        async def start_all(self):
            self.started = True

        async def stop_all(self):
            self.stopped = True

    ip_state = {"egress": "9.9.9.9", "home": "9.9.9.9"}

    class FakeGuest:
        page_ok = True
        egress_masked = ""

        def __init__(self, out, settle_s, pdp, tag="home", route=None):
            self.out, self.tag, self.route = out, tag, route

        async def start(self):
            async def fetch():
                return ip_state["egress"]
            self.egress_masked = await F.egress_gate(self.out, self.tag, self.route.info["display"], fetch)

        async def fire(self, *x, **k):
            FIRED.append(x)
            raise AssertionError("shot fired")

        async def alive(self):
            return True

        async def close(self):
            pass

    F.ForwarderPool = FakePool
    F.Guest = FakeGuest
    F.H.public_ip = lambda timeout=6.0: ip_state["home"]

    def run_follow(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = asyncio.run(F.follow(F.build_parser().parse_args(argv)))
        return rc, buf.getvalue()

    # egress equal to home -> exit 1, no shot, forwarder stopped, no secret anywhere, named files
    F.LOCK = F.STATE / "x"      # reset by follow via set_lock_name
    rc, text = run_follow(["--tag", "bd", "--lock-name", "guest_follow_bd", "--proxy", "reserve:1"])
    logs = list(F.OUT_DIR.glob("guest_follow_bd_*.log"))
    alltext = text + "".join(p.read_text(encoding="utf-8") for p in F.OUT_DIR.glob("*"))
    check("egress_equal_home_exit1_no_shot", rc == 1 and not FIRED, f"rc={rc}")
    check("egress_failed_line", "[GUEST_FOLLOW] egress FAILED tag=bd" in text and "reason=same_as_home" in text, text)
    check("forwarder_started_then_stopped", len(POOLS) == 1 and POOLS[0].started and POOLS[0].stopped
          and POOLS[0].ups[0][0] == URL1 and 22900 <= POOLS[0].ups[0][1] < 23000)
    check("secret_never_logged", SECRET not in alltext and "brd-customer" not in alltext)
    check("named_files_with_tag", len(logs) == 1 and len(list(F.OUT_DIR.glob("guest_follow_bd_*.json"))) == 1)
    check("tag_on_log_lines", all("tag=bd" in l for l in logs[0].read_text(encoding="utf-8").splitlines() if "[GUEST_FOLLOW]" in l))
    check("lock_released", not (F.STATE / "guest_follow_bd.lock").exists())
    js = json.loads(next(F.OUT_DIR.glob("guest_follow_bd_*.json")).read_text(encoding="utf-8"))
    check("summary_tag", js.get("tag") == "bd" and js.get("proxy") == "brd.superproxy.io:33335")

    # egress proof failure (unreadable) -> exit 1 as well
    ip_state.update(egress="ERR timeout", home="8.8.8.8")
    rc, text = run_follow(["--tag", "bd", "--lock-name", "guest_follow_bd", "--proxy", "proxies:0"])
    check("egress_unreadable_exit1", rc == 1 and not FIRED and "egress FAILED" in text and SECRET not in text)

    # good egress: gate returns the masked ip, one egress line, two octets only
    ip_state.update(egress="5.6.7.8", home="1.2.3.4")
    lines = []
    m = asyncio.run(F.egress_gate(lines.append, "bd", "brd.superproxy.io:33335", lambda: asyncio.sleep(0, "5.6.7.8")))
    check("egress_ok_line", m == "5.6.x.x" and lines == ["[GUEST_FOLLOW] egress tag=bd ip=5.6.x.x home=1.2.x.x proxy=brd.superproxy.io:33335"], str(lines))

    # default run: names without tag, no tag text, no summary tag key
    ip_state.update(egress="5.6.7.8", home="5.6.7.8")
    F.OUT_DIR = td / "out2"
    rc, text = run_follow(["--proxy", "proxies:0"])
    check("default_tag_names", rc == 1 and len(list(F.OUT_DIR.glob("guest_follow_2*.log"))) == 1)
    js = json.loads(next(F.OUT_DIR.glob("guest_follow_2*.json")).read_text(encoding="utf-8"))
    check("default_summary_no_tag_key", "tag" not in js)

    # stale lock: dead pid -> takes over (logs it); live own pid -> refuses
    F.OUT_DIR = td / "out3"
    (F.STATE / "guest_follow.lock").write_text("2147480000")
    rc, text = run_follow(["--proxy", "proxies:0"])
    logtxt = next(F.OUT_DIR.glob("guest_follow_*.log")).read_text(encoding="utf-8")
    check("stale_lock_taken_over", rc == 1 and "stale lock" in logtxt and "pid=2147480000 -- taking over" in logtxt and "refused" not in text, text)
    (F.STATE / "guest_follow.lock").write_text(str(os.getpid()))
    rc, text = run_follow(["--proxy", "proxies:0"])
    check("live_own_pid_refuses", rc == 6 and "refused: another follower" in text)
    (F.STATE / "guest_follow.lock").unlink()

    # ── cross-line readout on synthetic files ────────────────────────────────
    db = td / "e.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("create table shots (tcin TEXT, ts_ms INTEGER, status INTEGER, err_key TEXT, gate TEXT)")
    T, B = "1010892076", 1_800_000_000_000
    starts = [B + i * 600_000 for i in range(6)]
    for s0 in starts:
        for k in range(3):
            con.execute("insert into shots values (?,?,?,?,?)", (T, s0 + k * 5000, 429, "ERR_A2C_TCIN_RATE_LIMITED", "wall1_limited"))
    con.commit()
    con.close()

    def mkfile(name, tag, offset, classes, shot_extra=None):
        """classes: list of cls (one per window, 3 shots each, the first carries the class)."""
        ws = [{"tcin": T, "start_ms": s + offset, "end_ms": s + offset + 60_000} for s in starts]
        sh = []
        for w, cl in zip(ws, classes):
            for i in range(3):
                r = {"tcin": T, "window_start_ms": w["start_ms"], "cls": cl if i == 0 else "limited", "role": "hot",
                     "status": 429, "hop": False}
                if cl == "passed" and i == 0:
                    r.update(status=401, hop=False)
                if shot_extra and i == 1 and cl == "passed":
                    r.update(shot_extra, cls="passed")
                sh.append(r)
        doc = {"windows": ws, "shots": sh}
        if tag:
            doc["tag"] = tag
        pth = td / name
        pth.write_text(json.dumps(doc), encoding="utf-8")
        return str(pth)

    L6, P6 = ["limited"] * 6, ["passed"] * 6
    home = mkfile("home.json", None, 0, L6)
    r = F.readout([home], db)
    check("single_tag_unchanged_no_cross", "cross_line" not in r and "tags" not in r and "tag" not in r["rows"][0])
    r = F.readout([home, mkfile("bd_diff.json", "bd", 2000, P6, {"status": 424, "hop": True})], db)
    cl = r["cross_line"]["bd"]
    check("cross_line_differs", cl["verdict"] == "LINE-DIFFERS" and cl["bd_only_pass"] == 6 and cl["home_only_pass"] == 0, json.dumps({k: cl[k] for k in cl if k != "rows"}))
    check("cross_keeps_legacy_home_rows", r["windows"] == 6 and r["tags"] == ["bd", "home"] and "tag" not in r["rows"][0])
    check("wall2_per_tag", r["wall2"]["bd"] == {"past_wall1": 12, "keyless_401": 6, "hop_424_201": 6, "other_passed": 0}
          and r["wall2"]["home"]["past_wall1"] == 0, json.dumps(r["wall2"]))
    r = F.readout([home, mkfile("bd_same.json", "bd", 2000, L6)], db)
    cl = r["cross_line"]["bd"]
    check("cross_line_same_fate", cl["verdict"].startswith("SAME-FATE-ALL-LINES") and "concordant zero" in cl["verdict"] and cl["same"] == 6, cl["verdict"])
    r = F.readout([home, mkfile("bd_mix.json", "bd", 2000, ["passed", "passed", "limited", "limited", "limited", "limited"])], db)
    cl = r["cross_line"]["bd"]
    check("cross_line_inconclusive", cl["verdict"] == "INCONCLUSIVE" and cl["bd_only_pass"] == 2, json.dumps({k: cl[k] for k in cl if k != "rows"}))
    r = F.readout([home, mkfile("bd_far.json", "bd", 9000, P6)], db)      # 9 s apart: not the same window
    check("cross_line_5s_matching", r["cross_line"]["bd"]["windows"] == 0 and r["cross_line"]["bd"]["verdict"] == "INCONCLUSIVE")
    # reverse: home passes, bd does not -> home_only_pass / reverse, never LINE-DIFFERS
    home_p = mkfile("home_p.json", None, 0, P6)
    r = F.readout([home_p, mkfile("bd_l.json", "bd", 2000, L6)], db)
    cl = r["cross_line"]["bd"]
    check("cross_line_reverse", cl["home_only_pass"] == 6 and cl["reverse"] == 6 and cl["verdict"] == "INCONCLUSIVE", cl["verdict"])

print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
sys.exit(1 if FAILED else 0)
