#!/usr/bin/env python3
"""Armed-flag manifest pin (2026-10-18, readiness audit S4).

Pins what run_bot_with_nightly_restart.bat ARMS today, so an edit that silently drops,
duplicates or flips an armed flag -- or arms a flag that was deliberately left off -- fails
the offline suite instead of surfacing at a drop.

  * ARMED: exactly one `set NAME=VALUE` per name, with the pinned value.
  * NOT ARMED: no `set` line (or only an empty value) -- these are built-but-refuted /
    unproven features (S2-LIMKEY-PROBE refuted money-safe, JOIN, KICK, DCO probe, EGRESS bind,
    FAIR-FRESH, CHAIN_NET_META).

A deliberate arming change MUST edit MANIFEST_ARMED / MANIFEST_OFF below in the same commit.
Text-only: reads the .bat, never runs it. No browser, no network, no bot.

    python tests/test_armed_manifest_1018.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "run_bot_with_nightly_restart.bat"

PASSED: list = []
FAILED: list = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


# name -> pinned value. Names are the full variable names exactly as the bat spells them.
MANIFEST_ARMED = {
    "ENSURE_ARMED_PROFILE_SIGNALS": "1",  # armed 2026-10-19 after an Opus pre-arming verifier
    "TARGET_START_VALIDATE_RETRIES": "2",  # armed 2026-10-19 after an Opus pre-arming verifier
    "TARGET_THREAD_START_GUARD": "1",  # armed 2026-10-19 after an Opus pre-arming verifier
    "TARGET_HELD_LINE_FLIP_STRIKE": "1",
    "TARGET_REBUY_NONBUYERS": "1",
    "TARGET_JAR_GUEST_GUARD": "1",
    "TARGET_HARVEST_PX_RENAV": "1",
    "TARGET_PO_2XX_AMBIGUOUS": "1",
    "TARGET_FOREIGN_KEEP_WON": "1",
    "TARGET_BOOT_SKIP_FAILED_W1": "1",
    "TARGET_TOKEN_KEEPFRESH": "0",
    "TARGET_RELOGIN_MAX_PER_6H": "0",
    # read by the audit at bat:1228 / :1350 (lane note); pinned as they are today
    "TARGET_WONCART_LIVE_CAP_EXEMPT": "1",
    "TARGET_FAST_CADENCE_IDENTS": "all",
    # 1019b (pins lane, r1:arming F1): armed flags the manifest did not pin; line numbers at 3278ce7f
    "TARGET_MULTI_SKU_FAIR_SHARE": "1",  # bat:1314
    "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": "1",  # bat:1323
    "TARGET_WAVE_FIRST_YIELD_MISSED": "1",  # bat:1340
    "TARGET_SHARED_LOCK_ORDER_FIX": "1",  # bat:1344
    "TARGET_ATC_DCO_BURST_STUCK_NOWAIT": "1",  # bat:981
    "ENSURE_ARMED_EXIT87": "1",  # bat:1757 (wrapper exit-87 branch -> ensure_armed --mode exit87)
    "TARGET_SENTINEL_MEMBER_CHECK": "1",  # bat:1387
    # deliberate "=0" pins (a missing line is NOT the same as 0: the code default differs)
    "TARGET_ATC_DCO_BURST_REQUIRE_BANK": "0",  # bat:969
    "TARGET_WONCART_YIELD_FLEET": "0",  # bat:1222 (code default is 1; RIDE_MAX 300 would lock out siblings)
}
MANIFEST_OFF = (
    "TARGET_WARMUP_PROBE_TCINS",
    "TARGET_WARMUP_PROBE_IDENTS",
    "TARGET_RACE_JOIN_FREE",
    "TARGET_REARM_KICK",
    "TARGET_DCO_PROBE_P",
    "TARGET_ACCOUNT_EGRESS_BIND",
    "TARGET_FAIR_FRESH_FIX",
    "TARGET_CHAIN_NET_META",
)

_REM = re.compile(r"^\s*(rem\b|::)", re.IGNORECASE)


def set_values(text: str, name: str) -> list:
    """Every value assigned to `name` by an executable (non-REM) `set` line, in file order.
    Handles `set NAME=V`, `set "NAME=V"` and a `set` after `if ... (`/`&`."""
    pat = re.compile(r'(?:^|[\s(&])set\s+"?' + re.escape(name) + r"=([^\r\n\"&|)]*)", re.IGNORECASE)
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _REM.match(line):
            continue
        for m in pat.finditer(line):
            out.append(m.group(1).strip())
    return out


def evaluate(text: str):
    """Return (armed_problems, off_problems) for a bat text."""
    armed, off = [], []
    for name, want in MANIFEST_ARMED.items():
        vals = set_values(text, name)
        if vals != [want]:
            armed.append((name, want, vals))
    for name in MANIFEST_OFF:
        vals = [v for v in set_values(text, name) if v != ""]
        if vals:
            off.append((name, vals))
    return armed, off


def test_bat_is_crlf():
    raw = BAT.read_bytes()
    check("bat_exists_nonempty", len(raw) > 1000, len(raw))
    check("bat_is_crlf_no_bare_lf", b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""),
          "bare LF found" if b"\n" in raw.replace(b"\r\n", b"") else "no CRLF")


def test_armed_manifest():
    text = BAT.read_text(encoding="utf-8", errors="replace")
    armed, off = evaluate(text)
    for name, want in MANIFEST_ARMED.items():
        vals = set_values(text, name)
        check(f"armed[{name}={want}]", vals == [want], f"found {vals}")
    for name in MANIFEST_OFF:
        vals = [v for v in set_values(text, name) if v != ""]
        check(f"not_armed[{name}]", not vals, f"found {vals}")
    check("manifest_clean", not armed and not off, (armed, off))


def test_parser_catches_drift():
    """The pin must itself fail on drift: duplicate, flipped, missing, REM-only, and a newly
    armed 'off' flag (synthetic bat text, not the real file)."""
    good = "\r\n".join(
        [f"set {k}={v}" for k, v in MANIFEST_ARMED.items()]
        + ["REM set TARGET_REARM_KICK=1", "set TARGET_ACCOUNT_EGRESS_BIND="]) + "\r\n"
    a, o = evaluate(good)
    check("synthetic_good_is_clean", not a and not o, (a, o))
    a, o = evaluate(good + "set TARGET_REBUY_NONBUYERS=1\r\n")
    check("synthetic_duplicate_caught", [x[0] for x in a] == ["TARGET_REBUY_NONBUYERS"], a)
    a, o = evaluate(good.replace("set TARGET_JAR_GUEST_GUARD=1", "set TARGET_JAR_GUEST_GUARD=0"))
    check("synthetic_flipped_caught", [x[0] for x in a] == ["TARGET_JAR_GUEST_GUARD"], a)
    a, o = evaluate(good.replace("set TARGET_FOREIGN_KEEP_WON=1", "REM set TARGET_FOREIGN_KEEP_WON=1"))
    check("synthetic_rem_only_counts_as_missing", [x[0] for x in a] == ["TARGET_FOREIGN_KEEP_WON"], a)
    a, o = evaluate(good + "set TARGET_RACE_JOIN_FREE=1\r\n")
    check("synthetic_armed_off_flag_caught", [x[0] for x in o] == ["TARGET_RACE_JOIN_FREE"], o)
    a, o = evaluate(good + 'set "TARGET_DCO_PROBE_P=0.2"\r\n')
    check("synthetic_quoted_set_caught", [x[0] for x in o] == ["TARGET_DCO_PROBE_P"], o)
    a, o = evaluate(good.replace("set TARGET_ACCOUNT_EGRESS_BIND=", "set TARGET_ACCOUNT_EGRESS_BIND=primary=10.0.0.2"))
    check("synthetic_egress_bind_value_caught", [x[0] for x in o] == ["TARGET_ACCOUNT_EGRESS_BIND"], o)


def test_wiring():
    suite = (ROOT / "tests" / "run_offline_suite.py").read_text(encoding="utf-8")
    check("in_offline_suite", '"test_armed_manifest_1018"' in suite)


def main():
    for t in (test_bat_is_crlf, test_armed_manifest, test_parser_catches_drift, test_wiring):
        try:
            t()
        except Exception as ex:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(ex).__name__}: {ex}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
