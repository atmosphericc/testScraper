#!/usr/bin/env python3
"""2026-11-05 (P3): boot_check fleet-wide harvest rollup.

On run_20261104_063459 harvest was ARMED on every account but each harvest tab was
HUMAN/PX-walled on the decoy PDPs, so ZERO authentic Shape sensors were banked and ZERO
SELFTEST verdicts emitted -- every shot silently fell back to the never-carted page_signed
path, yet the boot read GREEN-with-WARN. The new `harvest_fleet` row turns that degraded
fleet RED (flag-gated by TARGET_BOOTCHECK_HARVEST_RED, deadline by ...DEADLINE_S).

Reuses the synthetic-log + temp-root harness from test_predrop_tools (make_root /
healthy_log / ids / BC.run_once with check_port=False). No browser, no network, no bot.
Run: python tests/test_boot_check_harvest_1105.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_predrop_tools as T   # noqa: E402 -- the boot_check harness (make_root, healthy_log, ids, BC)

BC = T.BC

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


def armed_log(selftest_accts=(), px_walls=0, armed=True, n_accounts=3):
    """An otherwise-healthy boot where harvest is ARMED (a `[HARVEST/<acct>] loop started:`
    line per account) but SELFTEST verdicts are emitted ONLY for `selftest_accts` -- so the
    default is the 11-04 degraded fleet: armed, zero authentic sensors. `px_walls` adds that
    many `MISS-PROBE px=yes ... hits=px_container` lines (the HUMAN/PX wall signature).

    The per-account SELFTEST lines healthy_log() injects for every account are stripped so
    the test controls the verdict count exactly via `selftest_accts`."""
    base = [l for l in T.healthy_log(n_accounts=n_accounts).splitlines()
            if "SELFTEST verdict" not in l]
    tail = []
    if armed:
        for a in T.ACCTS[:n_accounts]:
            tail.append(T.logger(50, f"src.session.purchase_executor: [HARVEST/{a}] loop started: "
                                     f"bank=6 ttl=300s interval=40s replay=on selftest=on in_window=on "
                                     f"tcins=['{T.TCINS[0]}']"))
    for a in selftest_accts:
        tail.append(T.logger(55, f"src.session.purchase_executor: [HARVEST/{a}] SELFTEST verdict: "
                                 f"probes=2 replayed=2 accepted=[424] rejected=[] control_page_signed=401"))
    for i in range(px_walls):
        a = T.ACCTS[i % n_accounts]
        tail.append(T.logger(60, f"src.session.purchase_executor: [HARVEST/{a}] MISS-PROBE px=yes "
                                 f"tcin={T.TCINS[0]} hits=px_container url='https://www.target.com/p/-/A-{T.TCINS[0]}'"))
    return "\n".join(base + tail) + "\n"


def test_harvest_fleet_rollup():
    # a clean baseline for the flag (the parent process may have it set)
    import os
    for k in ("TARGET_BOOTCHECK_HARVEST_RED", "TARGET_BOOTCHECK_HARVEST_DEADLINE_S"):
        os.environ.pop(k, None)

    now = T.BOOT_TS + 600          # age 600 s, past the 360 s default deadline
    early = T.BOOT_TS + 120        # age 120 s, before the deadline

    with tempfile.TemporaryDirectory() as d:
        root = T.make_root(Path(d), run_log=armed_log(px_walls=2))
        log = root / "logs" / "runs" / "run_20261007_002557.log"

        # (a) armed + zero SELFTEST verdicts + age >= deadline -> RED, harvest_fleet FAIL
        res = BC.run_once(root, log, now, check_port=False)
        row = T.ids(res).get("harvest_fleet")
        check("hf_a_present", row is not None, sorted(T.ids(res)))
        check("hf_a_fail", row and row["status"] == "FAIL", row)
        check("hf_a_verdict_red", res["verdict"] == "RED", res["verdict"])
        check("hf_a_detail_degraded", row and "HARVEST DEGRADED: 0/3" in row["detail"], row)
        check("hf_a_detail_pxwall", row and "HUMAN/PX wall" in row["detail"] and "2 px_container" in row["detail"], row)
        check("hf_a_detail_kill_switch", row and "TARGET_BOOTCHECK_HARVEST_RED=0" in row["detail"], row)
        # it is the only FAIL (nothing else about the boot regressed to RED)
        check("hf_a_only_fail", set(T.ids(res, "FAIL")) == {"harvest_fleet"}, T.ids(res, "FAIL"))

        # (b) same log, TARGET_BOOTCHECK_HARVEST_RED=0 -> WARN, verdict NOT RED on this row
        os.environ["TARGET_BOOTCHECK_HARVEST_RED"] = "0"
        try:
            res = BC.run_once(root, log, now, check_port=False)
        finally:
            os.environ.pop("TARGET_BOOTCHECK_HARVEST_RED", None)
        row = T.ids(res).get("harvest_fleet")
        check("hf_b_warn", row and row["status"] == "WARN", row)
        check("hf_b_verdict_not_red", res["verdict"] != "RED", res["verdict"])
        check("hf_b_no_fail_from_row", "harvest_fleet" not in T.ids(res, "FAIL"), T.ids(res, "FAIL"))

        # (c) one account banks a good 424-only SELFTEST verdict -> no harvest_fleet FAIL
        log.write_text(armed_log(selftest_accts=("primary",), px_walls=0), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        row = T.ids(res).get("harvest_fleet")
        check("hf_c_not_fail", row is not None and row["status"] != "FAIL", row)
        check("hf_c_partial_warn", row and row["status"] == "WARN" and "1/3" in row["detail"], row)
        check("hf_c_no_fleet_fail", "harvest_fleet" not in T.ids(res, "FAIL"), T.ids(res, "FAIL"))

        # (d) harvest NOT armed (no loop started lines) -> no harvest_fleet row at all
        log.write_text(armed_log(armed=False, selftest_accts=()), encoding="utf-8")
        res = BC.run_once(root, log, now, check_port=False)
        check("hf_d_absent_when_unarmed", "harvest_fleet" not in T.ids(res), sorted(T.ids(res)))

        # (bonus) armed + zero SELFTEST but BEFORE the deadline -> PENDING, not FAIL
        log.write_text(armed_log(px_walls=1), encoding="utf-8")
        res = BC.run_once(root, log, early, check_port=False)
        row = T.ids(res).get("harvest_fleet")
        check("hf_e_pending_before_deadline", row and row["status"] == "PENDING", row)


def main() -> int:
    for t in (test_harvest_fleet_rollup,):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAILED:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
