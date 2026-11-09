#!/usr/bin/env python3
"""Offline money-safety + behaviour test for tools/experiments/harvest_px_probe.py (EXP-HARVEST-OFFLINE).

The probe drives a real guest Chrome against Target's home line, so its money-safety is structural and
must be pinned so a future edit cannot quietly add a transaction path:
  * it imports ONLY the three pure helper modules (shape_harvest, px_challenge, limkey_guest_probe) --
    never purchase_executor, the account config, or any relogin/login code;
  * the ONLY page script it names is the read-only detector SH.HARVEST_MISS_PROBE_JS;
  * its CODE (docstrings and comments stripped via tokenize) contains no add-to-cart / cart / checkout /
    password / account-file / fetch-POST fingerprint;
  * it calls the verified attach check and os._exit on an attach failure before any navigation.
Plus unit tests of validate_tcins and the pre-registered summarize() verdict. No browser, no network.

    python tests/test_harvest_px_probe_safety.py
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import harvest_px_probe as HP  # noqa: E402

SRC_PATH = ROOT / "tools" / "experiments" / "harvest_px_probe.py"
SRC = SRC_PATH.read_text(encoding="utf-8")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def _code_only(src: str) -> str:
    """The source with every STRING and COMMENT token removed -- i.e. executable code only, so
    docstrings and comments (which legitimately describe the forbidden things) are ignored."""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        out.append(tok.string)
    return " ".join(out)


CODE = _code_only(SRC)

# transaction / credential fingerprints that must never appear in the probe's CODE
FORBIDDEN = [
    "cart_items", "atc_js", "pre_checkout", "place_order", "continue_request", "buynow",
    "password", "cvv", "target_accounts", "relogin", "requestsubmit", "fetch",
    "add_to_cart", "addtocart", "checkout",
]
# money-safe primitives / read-only detector the probe MUST use
REQUIRED = [
    "attach_check", "probe_free_port", "dynamic_range_clear", "in_allowed_window",
    "_kill_own", "HARVEST_MISS_PROBE_JS", "is_px_challenge",
]


def test_no_transaction_fingerprints_in_code():
    hits = [w for w in FORBIDDEN if w.lower() in CODE.lower()]
    check("code_has_no_transaction_fingerprint", not hits, f"found {hits}")


def test_required_primitives_present():
    missing = [w for w in REQUIRED if w not in CODE]
    check("code_uses_money_safe_primitives", not missing, f"missing {missing}")


def test_only_the_readonly_detector_js():
    # the probe must name ONLY the read-only miss-probe detector, not any ATC/button/fetch JS
    for bad_js in ("FIND_ATC_BUTTON_JS", "SELECT_SHIPPING_JS", "VISIBILITY_PROBE_JS", "atc_js"):
        check(f"no_{bad_js}", bad_js not in CODE, f"{bad_js} referenced")
    check("uses_HARVEST_MISS_PROBE_JS", "HARVEST_MISS_PROBE_JS" in CODE)


def test_imports_are_whitelisted():
    tree = ast.parse(SRC)
    srcmods, plain = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("src"):
                srcmods.add(node.module)
                for n in node.names:
                    srcmods.add(f"{node.module}.{n.name}")
            plain.add(node.module)
        elif isinstance(node, ast.Import):
            for n in node.names:
                plain.add(n.name)
    allowed_src = {"src.session", "src.session.shape_harvest", "src.session.px_challenge"}
    bad_src = {m for m in srcmods if m not in allowed_src}
    check("only_whitelisted_src_imports", not bad_src, f"unexpected src imports: {bad_src}")
    # explicitly forbid the heavy transaction modules and the account config
    for forbidden_mod in ("src.session.purchase_executor", "src.session.session_manager",
                          "src.purchasing", "config", "relogin", "relogin_one"):
        check(f"does_not_import_{forbidden_mod.replace('.', '_')}",
              not any(forbidden_mod in m for m in plain | srcmods), f"imports {forbidden_mod}")
    check("imports_limkey_guest_probe", "limkey_guest_probe" in plain)


def test_attach_failure_aborts_before_nav():
    # os._exit on an attach failure, and the abort path kills only our own process
    check("os_exit_on_attach_fail", "os._exit(4)" in SRC)
    check("kill_own_on_fail", "_kill_own" in CODE)


def test_validate_tcins():
    check("validate_accepts_decoys", HP.validate_tcins("21516452,50225561") == ["21516452", "50225561"])
    check("validate_dedupes", HP.validate_tcins("21516452 21516452") == ["21516452"])
    for bad in ("abc", "123", "", "9999999999999999999"):
        try:
            v = HP.validate_tcins(bad)
            # empty string -> [] is fine; a too-long/garbage token must raise
            check(f"validate_rejects_{bad or 'empty'}", bad == "" and v == [], f"got {v!r}")
        except ValueError:
            check(f"validate_rejects_{bad or 'empty'}", bad != "", "raised as expected")
    # the ATC control decoy is refused as a harvest PDP
    try:
        HP.validate_tcins(HP.P.DECOY)
        check("validate_refuses_control_decoy", False, "accepted the control decoy")
    except ValueError:
        check("validate_refuses_control_decoy", True)


def _rec(tcin, i, px=None, err=False):
    if err:
        return {"tcin": tcin, "i": i, "error": "x"}
    return {"tcin": tcin, "i": i, "px": px}


def test_summarize_verdicts():
    decoys = ["1111111", "2222222", "3333333"]
    # all walled -> LINE-BOUND
    allwall = [_rec(t, i, True) for t in decoys for i in range(3)]
    check("summarize_line_bound", summarize_v(decoys, allwall) == "LINE-BOUND", summarize_v(decoys, allwall))
    # all clean -> PROFILE-CLEAN
    allclean = [_rec(t, i, False) for t in decoys for i in range(3)]
    check("summarize_profile_clean", summarize_v(decoys, allclean) == "PROFILE-CLEAN", summarize_v(decoys, allclean))
    # a 50/50 mix -> LINE-BOUND (>=50%)
    mix = [_rec(t, i, i % 2 == 0) for t in decoys for i in range(4)]
    check("summarize_mix_line_bound", summarize_v(decoys, mix) == "LINE-BOUND", summarize_v(decoys, mix))
    # one decoy walls fully, two clean: overall 1/3 -> INCONCLUSIVE (not <=10%, not >=50%)
    part = ([_rec(decoys[0], i, True) for i in range(3)]
            + [_rec(decoys[1], i, False) for i in range(3)]
            + [_rec(decoys[2], i, False) for i in range(3)])
    check("summarize_partial_inconclusive", summarize_v(decoys, part) == "INCONCLUSIVE", summarize_v(decoys, part))
    # too few answered -> INCONCLUSIVE even if all clean
    few = [_rec(decoys[0], 0, False), _rec(decoys[1], 0, False)]
    check("summarize_too_few_inconclusive", summarize_v(decoys, few) == "INCONCLUSIVE", summarize_v(decoys, few))
    # errors are not counted as answered
    errs = [_rec(t, i, err=True) for t in decoys for i in range(5)]
    check("summarize_errors_inconclusive", summarize_v(decoys, errs) == "INCONCLUSIVE", summarize_v(decoys, errs))


def test_during_drop_relaxes_only_clock():
    # --during-drop lets the probe run in the 00-06 drop band beside the live bot. It must relax
    # ONLY the clock window -- never a money-safety primitive. Default OFF == exact prior behaviour.
    import re as _re
    ns = HP.build_parser().parse_args([])
    check("during_drop_default_off", ns.during_drop is False, f"default was {ns.during_drop!r}")
    ns2 = HP.build_parser().parse_args(["--during-drop"])
    check("during_drop_flag_parses", ns2.during_drop is True)
    # the ONLY guard --during-drop bypasses is the in_allowed_window clock check
    check("during_drop_guards_only_clock",
          bool(_re.search(r"not\s+a\.during_drop\s+and\s+not\s+P\.in_allowed_window", SRC)),
          "the --during-drop bypass must wrap in_allowed_window and nothing else")
    # it must never share a line with a money-safety primitive (i.e. cannot gate one off)
    for prim, label in (("attach_check", "attach_check"), ("_kill_own", "kill_own"),
                        ("dynamic_range_clear", "dynamic_range_clear"), ("os._exit", "os_exit")):
        bad = [ln.strip() for ln in SRC.splitlines() if "during_drop" in ln and prim in ln]
        check(f"during_drop_not_coupled_to_{label}", not bad, f"{bad}")


def summarize_v(decoys, recs):
    return HP.summarize(decoys, recs)["verdict"]


def main() -> int:
    for t in (test_no_transaction_fingerprints_in_code, test_required_primitives_present,
              test_only_the_readonly_detector_js, test_imports_are_whitelisted,
              test_attach_failure_aborts_before_nav, test_validate_tcins, test_summarize_verdicts,
              test_during_drop_relaxes_only_clock):
        try:
            t()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(ex).__name__}: {ex}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print(f"  FAILED: {f}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
