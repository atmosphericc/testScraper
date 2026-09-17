#!/usr/bin/env python3
"""Golden snapshot: the `_api_fast_lane` JS string with every new hot-SKU flag OFF.

WHY (hot-sku 0916 plan, section 2 "default-path JS regression"): the fast-lane
chain is the ONLY path that has ever converted a hot-SKU shot. The 09-16 plan adds
several flag-gated JS insertions to it (qty guard, stage tracking, arrival stamps,
cart-qty logging). With those flags unset the JS the executor evaluates must stay
byte-identical to what commit 11797839 sent. This test renders the JS through the
real `_api_fast_lane` with a stub tab (no browser, no network) and compares it with
`tests/fixtures/fast_lane_js_golden.txt`, which was captured from 11797839 BEFORE
any hot-sku 0916 source edit.

Variants cover the pre-existing knobs that change the JS (TARGET_ATC_BYTEMATCH,
CVV digits / latch, TARGET_ATC_REFERRER_PDP) so a regression on the armed prod
shape (bat: BYTEMATCH=1, REFERRER_PDP=1, latched CVV) is caught too.

Run:      python tests/test_fast_lane_golden.py
Capture:  python tests/test_fast_lane_golden.py --write   (refuses to overwrite an
          existing fixture unless --force; only ever capture from unmodified code)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "fast_lane_js_golden.txt"

# Every flag the hot-sku 0916 plan introduces that could touch the fast-lane JS or
# its call path. All are removed from the environment before rendering, so the
# golden always describes the flags-OFF default path.
NEW_FLAGS = (
    "TARGET_FASTLANE_QTY_GUARD",
    "TARGET_WONCART_DIRECT",
    "TARGET_HELD_CART_REENTRY",
    "TARGET_FASTLANE_STAGE_TRACK",
    "TARGET_FASTLANE_T_STAMPS",
    "TARGET_FASTLANE_LOG_CART_QTY",
    "TARGET_AMBIGUOUS_COMMIT_LATCH",
    "TARGET_AMBIGUOUS_COMMIT_LATCH_S",
    "TARGET_FS_TICKET_LOG",
    "TARGET_EXPOSURE_LOG",
)
# Pre-existing knobs that change the JS; pinned per variant below.
OLD_KNOBS = ("TARGET_ATC_BYTEMATCH", "TARGET_FAST_LANE_CVV")

for _k in NEW_FLAGS + OLD_KNOBS:
    os.environ.pop(_k, None)

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

TCIN = "1010892069"
QTY = 2
HEADERS_JS = json.dumps({"X-GyJwza5Z-a": "tokA", "X-GyJwza5Z-b": "tokB",
                         "x-application-name": "web"})

# (label, env, cvv_required, account cvv digits, referrer_pdp)
VARIANTS = (
    ("plain", {}, False, "", False),
    ("bytematch", {"TARGET_ATC_BYTEMATCH": "1"}, False, "", False),
    ("cvv_unlatched", {}, False, "4567", False),
    ("prod_armed", {"TARGET_ATC_BYTEMATCH": "1"}, True, "123", True),
)

PASSED: list = []
FAILED: list = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


class _RecTab:
    def __init__(self):
        self.calls = []
        self.env_at_eval = []

    async def evaluate(self, js, await_promise=True):
        self.calls.append(js)
        self.env_at_eval.append({k: os.environ.get(k) for k in NEW_FLAGS})
        return {"atc": {"status": 401}, "pre": {}, "po": {}, "skip": "atc_401"}


class _SM:
    def __init__(self, cvv):
        self.account_id = "primary"
        self._cvv = cvv

    def _load_account_cvv(self):
        return self._cvv


def render(env, cvv_required, cvv, referrer_pdp):
    saved = {k: os.environ.get(k) for k in NEW_FLAGS + OLD_KNOBS}
    try:
        for k in NEW_FLAGS + OLD_KNOBS:
            os.environ.pop(k, None)
        os.environ.update(env)
        ex = object.__new__(PurchaseExecutor)
        ex.test_mode = False
        ex._cvv_required = cvv_required
        ex._atc_referrer_pdp = referrer_pdp
        ex._checkout_rejected = False
        ex._checkout_reject_reason = ""
        ex._checkout_reject_status = 0
        ex.session_manager = _SM(cvv)
        tab = _RecTab()
        asyncio.run(ex._api_fast_lane(tab, TCIN, QTY, HEADERS_JS))
        render.last_env = tab.env_at_eval
        return tab.calls
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def render_all():
    blocks = []
    for label, env, cvv_req, cvv, ref in VARIANTS:
        calls = render(env, cvv_req, cvv, ref)
        assert len(calls) == 1, f"{label}: expected exactly 1 evaluate, got {len(calls)}"
        blocks.append((label, calls[0]))
    return blocks


def serialize(blocks):
    parts = []
    for label, js in blocks:
        parts.append(f"##### VARIANT {label} #####\n{js}\n##### END {label} #####\n")
    return "".join(parts)


def parse(text):
    text = text.replace("\r\n", "\n")   # git autocrlf may CRLF the checkout
    out = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("##### VARIANT ") and ln.endswith(" #####"):
            label = ln[len("##### VARIANT "):-len(" #####")]
            end = f"##### END {label} #####"
            j = i + 1
            body = []
            while j < len(lines) and lines[j] != end:
                body.append(lines[j])
                j += 1
            out[label] = "\n".join(body)
            i = j + 1
        else:
            i += 1
    return out


def test_golden_matches():
    check("fixture_exists", FIXTURE.exists(), str(FIXTURE))
    if not FIXTURE.exists():
        return
    golden = parse(FIXTURE.read_bytes().decode("utf-8"))
    blocks = render_all()
    check("fixture_has_every_variant",
          sorted(golden) == sorted(l for l, _ in blocks), str(sorted(golden)))
    for label, js in blocks:
        check(f"rendered_js_has_no_cr[{label}]", "\r" not in js)
        g = golden.get(label)
        same = (g == js)
        detail = ""
        if not same and g is not None:
            for n, (a, b) in enumerate(zip(g.split("\n"), js.split("\n"))):
                if a != b:
                    detail = f"first diff at JS line {n + 1}: golden={a!r} now={b!r}"
                    break
            else:
                detail = f"length differs golden={len(g)} now={len(js)}"
        check(f"fast_lane_js_byte_identical[{label}]", same, detail)


def test_flags_are_really_unset_during_render():
    """A new flag set in the caller's environment must be scrubbed while the
    golden is rendered, and restored afterwards."""
    os.environ["TARGET_WONCART_DIRECT"] = "1"
    try:
        render({}, False, "", False)
        during = render.last_env
        after = os.environ.get("TARGET_WONCART_DIRECT")
    finally:
        os.environ.pop("TARGET_WONCART_DIRECT", None)
    check("render_scrubs_new_flags",
          bool(during) and all(v is None for d in during for v in d.values()), str(during))
    check("render_restores_caller_env", after == "1", str(after))


def main(argv):
    if "--write" in argv:
        if FIXTURE.exists() and "--force" not in argv:
            print(f"refusing to overwrite {FIXTURE} (pass --force)")
            return 2
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes(serialize(render_all()).encode("utf-8"))
        print(f"wrote {FIXTURE}")
        return 0
    for fn in (test_golden_matches, test_flags_are_really_unset_during_render):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print("  FAIL:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
