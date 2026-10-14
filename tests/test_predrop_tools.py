#!/usr/bin/env python3
"""Offline tests for tools/predrop (2026-10-08): boot_check.py and preflight.py.

Synthetic run / wrapper / relogin logs and a temp repo root only: no browser, no
network, no bot, no PowerShell (host checks are skipped with --no-host), no git.
Run: python tests/test_predrop_tools.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "predrop"))

import boot_check as BC   # noqa: E402
import preflight as PF    # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


BOOT = datetime(2026, 10, 7, 0, 25, 57)
BOOT_TS = BOOT.timestamp()
TCINS = ["1010892076", "1010892067", "1010892074"]
ACCTS = ["primary", "business", "alt-1"]


def stamp(sec: float) -> str:
    return datetime.fromtimestamp(BOOT_TS + sec).strftime("%Y-%m-%d %H:%M:%S,000")


def logger(sec, msg):
    return f"{stamp(sec)} INFO src.x: {msg}"


def healthy_log(n_accounts=3, n_tcins=3, e403=0, pool="18/18", gt_n=None, banner=False, extra=()):
    gt_n = n_tcins if gt_n is None else gt_n
    L = [
        "[LOG] run output -> run_20261007_002557.log",
        logger(1, "waitress: Serving on http://127.0.0.1:5001"),
        "[STARTUP_CLEANUP] Checking 42 purchase states for stale entries...",
        "[INIT]   use_real_purchasing: True2026-10-07 00:25:58,020 INFO x: a logger line glued onto the print",
        "[INIT]   TEST_MODE: false",
        f"[WORKER_POOL] sized from target_accounts.json: {n_accounts} account(s) -> {ACCTS[:n_accounts]}",
        logger(7, "src.session.session_manager:  Loaded fingerprint from target.json"),
        "[LOGIN_CHECK] 'Hi,' greeting found — logged in",
        "[BACKGROUND] Login check result: True",
        "✅ LOGGED IN TO TARGET.COM — starting monitoring with REAL purchases",
        "[MONITORING_LOOP] Starting bulletproof event-driven architecture...",
        logger(12, "src.proxy.proxy_state: [PROXY_STATE] loaded 30 entries"),
        "[PROXY] Loaded 18 proxies — rate: 1.20 checks/sec",
        f"[STOCK] started: sweeps/sec=3.0 behavioral_mix=0.00 tcins={n_tcins} proxies=18",
        "[FX1006] multi-SKU dispatch flags: FAIR=1 MISS=1 KEEP=1 YIELD=1 LOCK=1",
        "[STALL_WATCHDOG] armed -- every 30 s: ...",
        "[MONITORING_LOOP] [OK] stock_thread started (is_alive: True)",
        "[MONITORING_LOOP] [OK] purchase_thread started (is_alive: True)",
        "[MONITORING_LOOP] ✅ All threads started successfully",
        logger(40, "x: [HEALTH] rss=134MB os_threads=37 py_threads=29 handles=809 chrome_procs_all=103 sys_cpu=17% sys_mem=37% disk_free=198GB active_purchases=0 reservations=0"),
        "[HEALTH] rss=134MB os_threads=37 py_threads=29 handles=809 chrome_procs_all=103 sys_cpu=17% sys_mem=37% disk_free=198GB active_purchases=0 reservations=0",
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "ConnectionResetError: [WinError 10054] An existing connection was forcibly closed by the remote host",
    ]
    for i, a in enumerate(ACCTS[:n_accounts]):
        L.append(logger(45 + i, f"src.session.purchase_executor: [HARVEST/{a}] SELFTEST verdict: probes=2 replayed=2 accepted=[424] rejected=[401] control_page_signed=401"))
        L.append(f"[WARMUP#0/{a}] dummy POST status 424 — write-auth alive")
    L.append(logger(216, f"src.session.multi_session_pool: [MULTI_SESSION] started — {pool} sessions ready, dispatcher cleared to begin sweeping"))
    for i, t in enumerate(TCINS[:n_tcins]):
        L.append(logger(217, f"src.monitoring.stock_check_resilient: [STOCK STATUS] tcin={t} #1 old=(first) new=OUT_OF_STOCK|SA|svc0|OUT_OF_STOCK in_stock=0 services=0 atp=0.0 reason=- via=s"))
    L.append(logger(246, f"src.monitoring.stock_check_resilient: [STOCK STATS] t=30.0s sweeps=89 (2.97/s) 200={87 - e403} 403={e403} 429=0 other=0 beh=0 outstanding=2 sessions=18r/0c/0rc pool A=30 P=0 B=0"))
    if banner:
        L.append(logger(246, f"src.monitoring.stock_check_resilient: [TCIN-VISIBILITY] 1 of {n_tcins} configured TCIN(s) are INVISIBLE to RedSky (absent from the bulk response) -- a drop on them CANNOT be detected until Target publishes them: ['{TCINS[2]}']. Verify each number"))
    L.append(logger(247, f"src.monitoring.stock_check_resilient: [GROUND-TRUTH] pool cache-bust ok: in_stock=[] ({gt_n} TCINs)"))
    L.extend(extra)
    return "\n".join(L) + "\n"


def broken_log():
    L = healthy_log().splitlines()
    cut = L.index("[MONITORING_LOOP] Starting bulletproof event-driven architecture...")
    out = L[:cut + 1] + [
        "[MONITORING_LOOP] Starting stock_thread...",
        "Exception in thread MonitoringLoop:",
        "Traceback (most recent call last):",
        '  File "app.py", line 1, in monitoring_loop',
        "AttributeError: 'StockMonitorThread' object has no attribute 'purchase_manager'. Did you mean: '_purchase_manager'?",
        logger(45, "src.session.purchase_executor: [HARVEST/primary] SELFTEST verdict: probes=2 replayed=2 accepted=[424, 424] rejected=[] control_page_signed=401"),
        "[WARMUP#0/primary] dummy POST status 424 — write-auth alive",
    ]
    return "\n".join(out) + "\n"


WRAPPER = """[Wed 10/07/2026  0:25:14.83] chrome target sign-out done code=0
[Wed 10/07/2026  0:25:14.83] account login: relogin_one.py all
[Wed 10/07/2026  0:25:56.23] account login done code=0
[Wed 10/07/2026  0:25:56.55] drop-readiness check done code=0
[Wed 10/07/2026  0:25:56.56] launch #1 app.py
"""
RELOGIN = """2026-10-07T00:25:15 [PLAN] 3 account(s): primary(e***), business(m***), alt-1(a***)
2026-10-07T00:25:16 [ACCOUNT] === primary | e*** | profile=nodriver-profile | session=target.json | creds=OK ===
2026-10-07T00:25:20 [RESULT] primary: already logged in ✅ (cookies refreshed, 59 cookies)
2026-10-07T00:25:32 [ACCOUNT] === business | m*** | profile=nodriver-profile-2 | session=target-2.json | creds=OK ===
2026-10-07T00:25:36 [RESULT] business: already logged in ✅ (cookies refreshed, 55 cookies)
2026-10-07T00:25:47 [ACCOUNT] === alt-1 | a*** | profile=nodriver-profile-3 | session=target-3.json | creds=OK ===
2026-10-07T00:25:52 [RESULT] alt-1: already logged in ✅ (cookies refreshed, 56 cookies)
"""
BAT = ("@echo off\r\nREM test wrapper\r\nset TARGET_MULTI_SKU_FAIR_SHARE=1\r\nset TARGET_MULTI_SKU_MISS_REARM=1\r\n"
       "set TARGET_STOCK_RESET_KEEP_LIVE_FAILED=1\r\nset TARGET_WAVE_FIRST_YIELD_MISSED=1\r\nset TARGET_SHARED_LOCK_ORDER_FIX=1\r\n"
       "set TARGET_STALL_WATCHDOG=1\r\nset TARGET_QTY_PER_TCIN=1\r\nset RESILIENT_REDSKY_CHANNEL=apps_raw\r\nset TARGET_SWEEPS_PER_SEC=3.0\r\n"
       "set USE_RESILIENT_STACK=1\r\nset TARGET_BOOT_SKIP_TTL_H=12\r\n")


def make_root(tmp: Path, bat=BAT, tcins=TCINS, run_log=None, wrapper=WRAPPER, relogin=RELOGIN, qty=2):
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "logs" / "runs").mkdir(parents=True, exist_ok=True)
    (tmp / "state").mkdir(exist_ok=True)
    (tmp / "src" / "session").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "product_config.json").write_text(json.dumps({"products": [
        {"tcin": t, "name": f"P {t}", "enabled": True, **({"qty": qty} if qty else {})} for t in tcins]}), encoding="utf-8")
    (tmp / "config" / "target_accounts.json").write_text(json.dumps({"accounts": [
        {"account_id": a, "enabled": True, "username": "u", "password": "p", "cvv": "9999" if a != "business" else "123",
         "session_path": f"target-{i}.json", "profile_dir": f"prof-{i}"} for i, a in enumerate(ACCTS)]}), encoding="utf-8")
    for i, a in enumerate(ACCTS):
        (tmp / f"target-{i}.json").write_text("{}", encoding="utf-8")
        (tmp / f"prof-{i}").mkdir(exist_ok=True)
    (tmp / "config" / "proxyIps.json").write_text(json.dumps({"proxies": ["http://x"] * 18, "reserve_proxies": ["http://y"] * 2}), encoding="utf-8")
    (tmp / "run_bot_with_nightly_restart.bat").write_bytes(bat.encode("ascii"))
    # a code file that "reads" every flag in the bat except the ones a test removes
    names = [ln.split("=")[0].split()[-1] for ln in bat.replace("\r", "").splitlines() if ln.startswith("set ")]
    (tmp / "src" / "session" / "purchase_executor.py").write_text(
        "CARD_CVV = '123'\n" + "\n".join(f"x = os.environ.get('{n}')" for n in names) + "\n", encoding="utf-8")
    (tmp / "app.py").write_text("os.environ.setdefault('TARGET_API_PLACE_ORDER', 'true')\n", encoding="utf-8")
    (tmp / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "state" / "CURRENT_STATE.md").write_text("**As of: 2099-01-01**\n", encoding="utf-8")
    # the config files predate the boot (a config edited AFTER a boot makes count mismatches
    # informational, which a separate test covers)
    for f in ("product_config.json", "target_accounts.json"):
        os.utime(tmp / "config" / f, (BOOT_TS - 3600, BOOT_TS - 3600))
    if run_log is not None:
        (tmp / "logs" / "runs" / "run_20261007_002557.log").write_text(run_log, encoding="utf-8")
    (tmp / "logs" / "bot_restart_wrapper.log").write_text(wrapper, encoding="utf-8")
    (tmp / "logs" / "relogin.log").write_text(relogin, encoding="utf-8")
    return tmp


def ids(res, status=None):
    return {r["id"]: r for r in res["rows"] if status is None or r["status"] == status}


# ── boot_check ───────────────────────────────────────────────────────────────

def test_boot_check():
    with tempfile.TemporaryDirectory() as d:
        root = make_root(Path(d), run_log=healthy_log())
        log = root / "logs" / "runs" / "run_20261007_002557.log"
        now = BOOT_TS + 600
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_healthy_green", res["verdict"] == "GREEN", [r for r in res["rows"] if r["status"] in ("FAIL", "PENDING")])
        ok = ids(res, "OK")
        for k in ("worker_pool", "login_check", "logged_in", "stock_thread", "purchase_thread", "all_threads", "pool_ready",
                  "first_stats", "ground_truth", "tcin_visibility", "first_sighting", "fx1006_flags", "stall_watchdog",
                  "wrapper_login_pass", "relogin:primary", "write_auth:alt-1", "harvest:business"):
            check(f"bc_ok_{k}", k in ok, sorted(ok))
        check("bc_elapsed_from_logger_stamp", abs(ids(res)["pool_ready"]["t"] - 216) < 1, ids(res)["pool_ready"])
        check("bc_loaded_tcins_ok", ids(res)["loaded_tcins"]["status"] == "OK", ids(res).get("loaded_tcins"))
        check("bc_real_purchasing_glued_stamp_ok", ids(res)["real_purchasing"]["status"] == "OK" and ids(res)["test_mode"]["status"] == "OK", (ids(res).get("real_purchasing"), ids(res).get("test_mode")))
        log.write_text(healthy_log().replace("tcins=3 proxies", "tcins=2 proxies"), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_loaded_tcins_mismatch_fail", ids(res)["loaded_tcins"]["status"] == "FAIL" and res["verdict"] == "RED", ids(res).get("loaded_tcins"))
        log.write_text(healthy_log(), encoding="utf-8")
        check("bc_benign_traceback_info", ids(res)["tracebacks"]["status"] == "INFO" and "1 benign" in ids(res)["tracebacks"]["detail"], ids(res)["tracebacks"])
        # exit code + render through main()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = BC.main(["--root", str(root), "--log", str(log), "--now", str(now), "--no-port"])
        check("bc_main_exit0_green", rc == 0 and "VERDICT: GREEN" in buf.getvalue(), buf.getvalue()[-300:])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = BC.main(["--root", str(root), "--log", str(log), "--now", str(now), "--no-port", "--json"])
        check("bc_main_json", rc == 0 and json.loads(buf.getvalue())["verdict"] == "GREEN")

        # the 10-07 signature: exception while starting threads, no purchase_thread
        log.write_text(broken_log(), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        f = ids(res, "FAIL")
        check("bc_broken_red", res["verdict"] == "RED")
        check("bc_broken_names_attributeerror", "thread_start_exception" in f and "AttributeError" in f["thread_start_exception"]["detail"], f.get("thread_start_exception"))
        check("bc_broken_purchase_thread_missing", "purchase_thread" in f and "MISSING" in f["purchase_thread"]["detail"])
        check("bc_broken_exception_in_thread", "exception_in_thread" in f)
        check("bc_broken_tracebacks_warn", ids(res)["tracebacks"]["status"] == "WARN")

        # PENDING: evaluated 60 s after boot, before the pool is due
        log.write_text("\n".join(healthy_log().splitlines()[:16]) + "\n", encoding="utf-8")
        res = BC.run_once(root, log, BOOT_TS + 60, check_port=False)
        check("bc_pending_early", res["verdict"] == "PENDING" and ids(res)["pool_ready"]["status"] == "PENDING", res["verdict"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = BC.main(["--root", str(root), "--log", str(log), "--now", str(BOOT_TS + 60), "--no-port"])
        check("bc_main_exit2_pending", rc == 2 and "waiting on:" in buf.getvalue())
        # the same truncated log past its deadlines -> RED
        res = BC.run_once(root, log, BOOT_TS + 900, check_port=False)
        check("bc_missing_after_deadline_red", res["verdict"] == "RED" and "MISSING" in ids(res)["pool_ready"]["detail"])

        # fleet size mismatch, 403 on the first STATS, ground-truth count, banner, short pool
        log.write_text(healthy_log(n_accounts=2), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_worker_pool_mismatch_fail", ids(res)["worker_pool"]["status"] == "FAIL", ids(res)["worker_pool"])
        log.write_text(healthy_log(e403=3), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_stats_403_fail", ids(res)["first_stats"]["status"] == "FAIL")
        # 2 visible + 1 invisible (named by the banner) = 3 enabled -> OK, not a count failure
        log.write_text(healthy_log(gt_n=2, banner=True), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_ground_truth_visible_plus_invisible_ok", ids(res)["ground_truth"]["status"] == "OK" and "invisible" in ids(res)["ground_truth"]["detail"], ids(res)["ground_truth"])
        check("bc_first_sighting_excludes_invisible", ids(res)["first_sighting"]["status"] == "OK", ids(res)["first_sighting"])
        # 2 of 3 with NO banner: pending before +600 s, FAIL after
        log.write_text(healthy_log(gt_n=2, banner=False), encoding="utf-8")
        res = BC.run_once(root, log, BOOT_TS + 300, check_port=False)
        check("bc_ground_truth_short_no_banner_pending", ids(res)["ground_truth"]["status"] == "PENDING", ids(res)["ground_truth"])
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_ground_truth_count_fail", ids(res)["ground_truth"]["status"] == "FAIL", ids(res)["ground_truth"])
        # banner count that does not reconcile (1 invisible but 1 of 3 seen) -> FAIL
        log.write_text(healthy_log(gt_n=1, banner=True), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_ground_truth_unreconciled_fail", ids(res)["ground_truth"]["status"] == "FAIL")
        # the same mismatch is INFO when the config was edited after the boot (a pre-drop adds TCINs)
        log.write_text(healthy_log(gt_n=2, banner=False), encoding="utf-8")
        os.utime(root / "config" / "product_config.json", None)
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_ground_truth_mismatch_info_after_config_edit", ids(res)["ground_truth"]["status"] == "INFO" and res["verdict"] != "RED", ids(res)["ground_truth"])
        os.utime(root / "config" / "product_config.json", (BOOT_TS - 3600, BOOT_TS - 3600))
        log.write_text(healthy_log(gt_n=2, banner=True), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_visibility_banner_warn", ids(res)["tcin_visibility"]["status"] == "WARN" and TCINS[2] in ids(res)["tcin_visibility"]["detail"])
        log.write_text(healthy_log(pool="15/18"), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_pool_short_warn", ids(res)["pool_ready"]["status"] == "WARN")
        log.write_text(healthy_log(pool="9/18"), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_pool_very_short_fail", ids(res)["pool_ready"]["status"] == "FAIL")

        # a WRITE-AUTH DEAD inside the boot window is a WARN; one after 15 min is ignored
        log.write_text(healthy_log(extra=["[WARMUP#3/primary] WRITE-AUTH DEAD — dummy POST returned 401 (carts writes will fail)"]), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_write_auth_dead_in_window_warn", ids(res)["write_auth:primary"]["status"] == "WARN")
        log.write_text(healthy_log(extra=[logger(2000, "x: later"), "[WARMUP#3/primary] WRITE-AUTH DEAD — dummy POST returned 401 (carts writes will fail)"]), encoding="utf-8")
        res = BC.run_once(root, log, BOOT_TS + 2100, check_port=False)
        check("bc_write_auth_dead_after_window_ignored", ids(res)["write_auth:primary"]["status"] == "OK")

        # wrapper: the process exited after this launch; the login pass was aborted
        log.write_text(healthy_log(), encoding="utf-8")
        (root / "logs" / "bot_restart_wrapper.log").write_text(WRAPPER + "[Wed 10/07/2026  0:30:00.00] app.py exited code=-1073741819\n", encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_wrapper_exit_fail", ids(res).get("wrapper_exit", {}).get("status") == "FAIL" and res["verdict"] == "RED")
        (root / "logs" / "bot_restart_wrapper.log").write_text(WRAPPER.replace("login done code=0", "login done code=-1073741510"), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_wrapper_login_aborted_fail", ids(res)["wrapper_login_pass"]["status"] == "FAIL")
        (root / "logs" / "bot_restart_wrapper.log").write_text(WRAPPER + "[Wed 10/07/2026  0:30:00.00] exit 87: not-logged-in ? relogin cycle 1/3\n", encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_wrapper_exit87_fail", "wrapper_exit87" in ids(res, "FAIL"))
        # relogin pass with no RESULT for an account
        (root / "logs" / "bot_restart_wrapper.log").write_text(WRAPPER, encoding="utf-8")
        (root / "logs" / "relogin.log").write_text("\n".join(RELOGIN.splitlines()[:-1]) + "\n", encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_relogin_no_result_fail", ids(res)["relogin:alt-1"]["status"] == "FAIL", ids(res)["relogin:alt-1"])
        # no run log at all
        log.unlink()
        res = BC.run_once(root, None, now, check_port=False)
        check("bc_no_log_red", res["verdict"] == "RED" and "no logs/runs" in res["rows"][0]["detail"])
        # the wrapper-vs-log FX1006 mismatch
        (root / "run_bot_with_nightly_restart.bat").write_bytes(BAT.replace("TARGET_MULTI_SKU_FAIR_SHARE=1", "TARGET_MULTI_SKU_FAIR_SHARE=0").encode("ascii"))
        log.write_text(healthy_log(), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("bc_fx1006_mismatch_warn", ids(res)["fx1006_flags"]["status"] == "WARN")


# ── preflight ────────────────────────────────────────────────────────────────

READINESS_SAMPLE = """==============
DROP-READINESS PRE-CHECK
primary    saved 2h ago
           login-session=27.1d   refreshToken=89.9d   accessToken=MEMBER (sut=R)   idToken sut=R
           ✅ healthy MEMBER session — will start hot; member token expires 2026-10-08 23:33:55 (1.8h left)

business   saved 2h ago
           login-session=MISSING   refreshToken=89.9d   accessToken=guest/none (sut=G)   idToken sut=G
           ❌ SIGNED OUT — no accessToken and the idToken is a GUEST's (sut=G). Force a hand login

alt-1      saved 28h ago
           login-session=0.3d   refreshToken=88.8d   accessToken=MEMBER (sut=R)   idToken sut=R
           ⚠  persisted token was GUEST (sut=G) — startup must re-mint MEMBER
VERDICT: ⚠ one or more accounts will NOT start hot — see above.
TCIN VISIBILITY: OK -- all 17 enabled TCIN(s) were visible to RedSky in the last run (1.2d old)
BOOT SKIP: ⚠ alt-1 will be LEFT OUT of the next boot for 3.0 h more (probe, x1) -- hand_login_alt1_force.bat puts it back
"""


def test_preflight():
    p = PF.parse_readiness(READINESS_SAMPLE)
    check("pf_parse_marks", {a: d["mark"] for a, d in p["accounts"].items()} == {"primary": "ok", "business": "red", "alt-1": "warn"}, p["accounts"])
    check("pf_parse_login_session", p["accounts"]["primary"]["login_session_days"] == 27.1 and p["accounts"]["business"]["login_session_days"] == 0.0, p["accounts"])
    check("pf_parse_member_exp", p["accounts"]["primary"]["member_exp"] == "2026-10-08 23:33:55")
    check("pf_parse_verdict_skip_vis", p["verdict"].startswith("VERDICT:") and len(p["boot_skip"]) == 1 and p["visibility"].startswith("TCIN VISIBILITY: OK"))

    check("pf_drop_parse_iso", PF._parse_drop("2026-10-09 02:00") == datetime(2026, 10, 9, 2, 0).timestamp())
    check("pf_drop_parse_bad", PF._parse_drop("tonightish") is None)

    with tempfile.TemporaryDirectory() as d:
        root = make_root(Path(d), run_log=healthy_log())
        res = PF.run(root, drop="", tcins=TCINS[:2], host=False, readiness=False, git=False)
        st = ids(res)
        check("pf_run_verdict_go", res["verdict"] in ("GO", "GO-WITH-WARNINGS"), res["verdict"])
        check("pf_no_fail_on_clean_root", not ids(res, "FAIL"), ids(res, "FAIL"))
        check("pf_config_count_ok", st["config.count"]["status"] == "OK" and "3 enabled" in st["config.count"]["detail"])
        check("pf_config_requested_ok", st["config.requested"]["status"] == "OK")
        check("pf_config_extra_info", st["config.enabled_not_requested"]["status"] == "INFO" and TCINS[2] in st["config.enabled_not_requested"]["detail"])
        check("pf_config_qty_ok", st["config.qty"]["status"] == "OK" and st["config.qty_flag"]["status"] == "OK")
        check("pf_bat_eol_ok", st["repo.bat_eol"]["status"] == "OK" and st["repo.bat_dupes"]["status"] == "OK")
        check("pf_flags_dead_ok", st["repo.flags_dead"]["status"] == "OK", st["repo.flags_dead"])
        check("pf_setdefaults_listed", "TARGET_API_PLACE_ORDER=true" in st["repo.app_setdefaults"]["detail"])
        check("pf_cvv_default_warn", st["accounts.prereq:business"]["status"] == "WARN" and st["accounts.prereq:primary"]["status"] == "OK")
        check("pf_latches_ok", st["accounts.cvv_latch"]["status"] == "OK" and st["accounts.ac1_latch"]["status"] == "OK" and st["accounts.boot_skip_file"]["status"] == "OK")
        check("pf_monitor_pool_ok", st["monitor.pool_config"]["status"] == "OK" and "18 active" in st["monitor.pool_config"]["detail"])
        check("pf_monitor_channel_chunk", st["monitor.channel"]["status"] == "OK" and st["monitor.chunking"]["status"] == "OK")
        check("pf_validate_cmd", "RESILIENT_REDSKY_CHANNEL=apps_raw" in res["validate_cmd"] and "VALIDATE_POOL=proxies" in res["validate_cmd"]
              and "STOCK_DIAG_PROBES=1" in res["validate_cmd"] and "CHROME_STAGGER_TOTAL_S=30" in res["validate_cmd"], res["validate_cmd"])
        check("pf_last_run_boot_green", st["last_run.boot"]["status"] == "OK", st["last_run.boot"])
        check("pf_last_run_stats", st["last_run.stats"]["status"] == "OK" and "403=0" in st["last_run.stats"]["detail"])
        check("pf_no_secret_values", "http://x" not in json.dumps(res) and "9999" not in json.dumps(res) and "123" not in json.dumps(res["rows"]), "a secret leaked into the report")

        # drop mode + Windows-Update overlap flag; the rendered report
        res = PF.run(root, drop=datetime.fromtimestamp(time.time() + 5 * 3600).strftime("%Y-%m-%d %H:%M"), tcins=None, host=False, readiness=False, git=False)
        check("pf_mode_full", res["mode"] == "FULL" and ids(res)["drop.time"]["status"] == "INFO")
        res2 = PF.run(root, drop=datetime.fromtimestamp(time.time() + 40 * 60).strftime("%Y-%m-%d %H:%M"), tcins=None, host=False, readiness=False, git=False)
        check("pf_mode_last_call", res2["mode"] == "LAST-CALL" and ids(res2)["drop.time"]["status"] == "WARN")
        out = PF.render(res)
        check("pf_render_sections", "VERDICT:" in out and "-- config " in out and "-- last_run " in out)

        # failures: >30 TCINs, duplicates, a requested TCIN absent, LF line in the bat, duplicate set, dead flag,
        # a CVV latch, a parked exit, a RED last boot
        many = [str(1010000000 + i) for i in range(31)]
        make_root(Path(d), tcins=many + [many[0]], run_log=broken_log())
        (root / "run_bot_with_nightly_restart.bat").write_bytes((BAT + "set TARGET_DEAD_FLAG=1\r\nset TARGET_QTY_PER_TCIN=1\r\nset X=1\n").encode("ascii"))
        (root / "state" / "cvv_challenge_alt-1.flag").write_text("2026-07-28T03:34:00", encoding="utf-8")
        (root / "state" / "proxy_state.json").write_text(json.dumps({"1.2.3.4": {"status": "parked", "parked_until": time.time() + 3600}, "5.6.7.8": {"status": "active", "parked_until": 0}}), encoding="utf-8")
        res = PF.run(root, drop="", tcins=["1099999999"], host=False, readiness=False, git=False)
        f, w = ids(res, "FAIL"), ids(res, "WARN")
        check("pf_verdict_nogo", res["verdict"] == "NO-GO")
        check("pf_count_fail_over_30", "config.count" in f and "> 30" in f["config.count"]["detail"], f.get("config.count"))
        check("pf_duplicates_fail", "config.duplicates" in f)
        check("pf_requested_absent_fail", "config.requested_absent" in f and "1099999999" in f["config.requested_absent"]["detail"])
        check("pf_bat_lf_fail", "repo.bat_eol" in f)
        check("pf_bat_dupes_warn", "repo.bat_dupes" in w and "TARGET_QTY_PER_TCIN" in w["repo.bat_dupes"]["detail"])
        check("pf_dead_flag_warn", "repo.flags_dead" in w and "TARGET_DEAD_FLAG" in w["repo.flags_dead"]["detail"], w.get("repo.flags_dead"))
        check("pf_cvv_latch_warn", "accounts.cvv_latch" in w and "alt-1" in w["accounts.cvv_latch"]["detail"])
        check("pf_parked_exit_warn", "monitor.proxy_state" in w and "1.2.3.4" in w["monitor.proxy_state"]["detail"], w.get("monitor.proxy_state"))
        check("pf_last_boot_red_fail", "last_run.boot" in f and "NOT able to buy" in f["last_run.boot"]["detail"])
        acts = [a["id"] for a in res["operator_actions"]]
        check("pf_actions_fail_first", acts and all(ids(res)[a]["status"] == "FAIL" for a in acts[:len(f)]), acts[:5])

        # 29 TCINs = two chunks -> WARN, not FAIL; no qty -> WARN
        make_root(Path(d), tcins=many[:29], run_log=healthy_log(), qty=0)
        res = PF.run(root, drop="", tcins=None, host=False, readiness=False, git=False)
        check("pf_29_tcins_warn", ids(res)["config.count"]["status"] == "WARN" and ids(res)["monitor.chunking"]["status"] == "WARN")
        check("pf_over_24_gt_unproven_warn", ids(res).get("monitor.gt_read_unproven", {}).get("status") == "WARN")
        check("pf_no_qty_warn", ids(res)["config.qty"]["status"] == "WARN")

        # an empty root never raises: every check degrades to UNKNOWN / INFO and main() exits 0
        empty = Path(d) / "empty"
        empty.mkdir()
        res = PF.run(empty, drop="", tcins=None, host=False, readiness=False, git=False)
        check("pf_empty_root_no_raise", res["verdict"] in ("GO", "GO-WITH-WARNINGS", "NO-GO") and len(res["rows"]) >= 5)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = PF.main(["--root", str(empty), "--no-host", "--no-readiness", "--no-git", "--json"])
        check("pf_main_empty_exit0_json", rc == 0 and "rows" in json.loads(buf.getvalue()))

    # the real scripts run as subprocesses on this repo with host / readiness / git skipped (read-only)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "predrop" / "preflight.py"), "--no-host", "--no-readiness", "--no-git", "--json"],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT), encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    try:
        doc = json.loads(r.stdout)
        ok = ok and doc["verdict"] in ("GO", "GO-WITH-WARNINGS", "NO-GO") and any(x["id"] == "config.count" for x in doc["rows"])
    except Exception as e:
        ok, doc = False, str(e)
    check("pf_subprocess_real_repo", ok, (r.stdout[-300:], r.stderr[-300:]))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "predrop" / "boot_check.py"), "--no-port", "--json"],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT), encoding="utf-8", errors="replace")
    check("bc_subprocess_real_repo", r.returncode in (0, 1, 2) and '"verdict"' in r.stdout, (r.returncode, r.stdout[-200:], r.stderr[-300:]))


def main() -> int:
    for t in (test_boot_check, test_preflight):
        try:
            t()
        except Exception as e:
            FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAILED:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
