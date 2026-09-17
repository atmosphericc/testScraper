#!/usr/bin/env python3
"""Smoke test: optimistic-2 quantity decision (_decide_target_qty).

The bulk RedSky feed usually omits the per-customer limit, so the hint arrives as
1 even on limit-2 SKUs. Optimistic-2 targets the ceiling when the limit is
unreported and respects a genuinely-reported limit; the executor self-heals a
true limit-1 via its 422/409 retry. Covers the env knobs too.

No browser, no network. Run: python tests/test_qty_decision_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as B  # noqa: E402

_QTY_ENV = ("TARGET_FORCE_QTY_1", "TARGET_QTY_CEILING", "TARGET_QTY_OPTIMISTIC")


def _clear_env():
    for k in _QTY_ENV:
        os.environ.pop(k, None)


def _q(redsky):
    return B._decide_target_qty(redsky)[0]


def test_unreported_limit_goes_optimistic_2():
    _clear_env()
    assert _q(1) == 2, "unreported (hint=1) should target ceiling 2"
    assert _q(0) == 2
    assert _q(None) == 2


def test_reported_limit_respected_and_capped():
    _clear_env()
    assert _q(2) == 2          # reported 2, ceiling 2
    assert _q(5) == 2          # reported 5 capped to ceiling 2
    assert _q(3) == 2


def test_force_qty_1_overrides_everything():
    _clear_env()
    os.environ["TARGET_FORCE_QTY_1"] = "true"
    try:
        assert _q(5) == 1 and _q(1) == 1
    finally:
        _clear_env()


def test_optimistic_off_falls_back_to_1():
    _clear_env()
    os.environ["TARGET_QTY_OPTIMISTIC"] = "0"
    try:
        assert _q(1) == 1          # unreported -> 1 when optimistic disabled
        assert _q(2) == 2          # reported still respected
    finally:
        _clear_env()


def test_custom_ceiling():
    _clear_env()
    os.environ["TARGET_QTY_CEILING"] = "3"
    try:
        assert _q(1) == 3          # optimistic uses the ceiling
        assert _q(5) == 3          # reported capped to ceiling
        assert _q(2) == 2          # reported below ceiling kept
    finally:
        _clear_env()


# ── 2026-09-16 hot-sku plan P11 (CFG-2): per-TCIN qty pin + [QTY] label ──────
import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402

from src.purchasing import bulletproof_purchase_manager as _bpm  # noqa: E402

_PIN_ENV = _QTY_ENV + ("TARGET_QTY_PER_TCIN", "TARGET_AMBIGUOUS_COMMIT_LATCH",
                       "TARGET_WARMUP_CYCLE_SKIP_ON_STOCK")
_OPTIMISTIC_REASON = ("RedSky limit unreported — optimistic ceiling=2 "
                      "(executor 422/409 self-heals true limit-1)")


def _clear_pin_env():
    for k in _PIN_ENV:
        os.environ.pop(k, None)


def _pin(products, tcin, redsky=1):
    return _bpm._qty_pin_decision(products, tcin, redsky)


def test_pin_decision_values():
    _clear_pin_env()
    try:
        cfg = [{"tcin": "111", "qty": 1}, {"tcin": "222", "qty": 5},
               {"tcin": 333, "qty": "1"}, {"tcin": "444"}, {"tcin": "555", "qty": 0},
               "garbage", {"tcin": "666", "qty": 2.9}, {"tcin": "777", "qty": -3}]
        assert _pin(cfg, "111")[0] == 1, "pin 1 -> 1"
        assert _pin(cfg, "222")[0] == 2, "pin 5 -> ceiling 2"
        assert _pin(cfg, "333")[0] == 1, "int tcin + string qty match"
        assert _pin(cfg, "444") is None, "no qty -> no pin"
        assert _pin(cfg, "555") is None, "qty 0 -> no pin"
        assert _pin(cfg, "999") is None, "unlisted TCIN -> no pin"
        assert _pin(cfg, "666")[0] == 2, "float pin truncated, capped"
        assert _pin(cfg, "777")[0] == 1, "negative pin floors at 1"
        assert _pin([], "111") is None and _pin(None, "111") is None
        assert "per-TCIN pin qty=1" in _pin(cfg, "111")[1]
        os.environ["TARGET_QTY_CEILING"] = "3"
        assert _pin(cfg, "222")[0] == 3, "pin 5 -> custom ceiling 3"
        assert _pin(cfg, "222", redsky=2)[0] == 2, "a reported RedSky limit still caps the pin"
        os.environ["TARGET_FORCE_QTY_1"] = "true"
        assert _pin(cfg, "222") is None, "TARGET_FORCE_QTY_1 beats any pin"
    finally:
        _clear_pin_env()


def test_pin_decision_malformed_raises():
    _clear_pin_env()
    for bad in ("abc", True, "nan", float("inf"), [1], {"q": 1}):
        try:
            _pin([{"tcin": "111", "qty": bad}], "111")
        except (ValueError, OverflowError):
            continue
        raise AssertionError(f"malformed pin {bad!r} did not raise")


def _mgr_stub(dispatched):
    m = object.__new__(B)
    m._state_lock = threading.Lock()
    m._load_states_unsafe = lambda: {}
    m._save_states_unsafe = lambda s: None
    m._active_purchases = {}
    m._warmup_cycle_counter = 0
    m.worker_pool = None
    m._maybe_run_session_sentinel = lambda: None

    def _start(tcin, title, max_qty=1):
        dispatched[tcin] = max_qty
        return {"success": False, "reason": "stub"}
    m.start_purchase = _start
    return m


def _dispatch(config, flag, stock=None):
    """Run the real process_stock_data in a temp cwd whose
    config/product_config.json holds `config` (dict -> JSON, str -> raw text,
    None -> no file). Returns ({tcin: max_qty}, stdout)."""
    _clear_pin_env()
    if flag is not None:
        os.environ["TARGET_QTY_PER_TCIN"] = flag
    stock = stock or {"111": {"in_stock": True, "max_qty": 1, "title": "T111"}}
    dispatched = {}
    buf = io.StringIO()
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        try:
            if config is not None:
                os.makedirs(os.path.join(d, "config"))
                with open(os.path.join(d, "config", "product_config.json"), "w", encoding="utf-8") as f:
                    f.write(config if isinstance(config, str) else json.dumps(config))
            os.chdir(d)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                _mgr_stub(dispatched).process_stock_data(stock)
        finally:
            os.chdir(old_cwd)
            _clear_pin_env()
    return dispatched, buf.getvalue()


def _qty_lines(out):
    return [ln for ln in out.splitlines() if ln.startswith("[QTY]")]


def test_dispatch_pin_1_flag_on():
    d, out = _dispatch({"products": [{"tcin": "111", "qty": 1}]}, "1")
    assert d == {"111": 1}, d
    assert _qty_lines(out) == ["[QTY] 111: targeting qty=1 — per-TCIN pin qty=1 from "
                               "product_config.json (TARGET_QTY_PER_TCIN=1; cap=2)"], _qty_lines(out)


def test_dispatch_pin_5_capped_to_ceiling():
    d, _ = _dispatch({"products": [{"tcin": "111", "qty": 5}]}, "1")
    assert d == {"111": 2}, d


def test_dispatch_no_pin_unchanged():
    cfg = {"products": [{"tcin": "111"}, {"tcin": "222", "qty": 1}]}
    stock = {"111": {"in_stock": True, "max_qty": 1}, "222": {"in_stock": True, "max_qty": 1}}
    d_on, out_on = _dispatch(cfg, "1", stock)
    d_off, out_off = _dispatch(cfg, None, stock)
    assert d_on == {"111": 2, "222": 1}, d_on
    assert d_off == {"111": 2, "222": 2}, d_off
    line = f"[QTY] 111: targeting qty=2 — {_OPTIMISTIC_REASON}"
    assert line in _qty_lines(out_on) and line in _qty_lines(out_off)


def test_dispatch_flag_off_ignores_pin():
    for flag in (None, "0", "", " 0 "):
        d, out = _dispatch({"products": [{"tcin": "111", "qty": 1}]}, flag)
        assert d == {"111": 2}, (flag, d)
        assert _qty_lines(out) == [f"[QTY] 111: targeting qty=2 — {_OPTIMISTIC_REASON}"], (flag, out)
        assert "per-TCIN" not in out
    d, _ = _dispatch({"products": [{"tcin": "111", "qty": 1}]}, " 1 ")
    assert d == {"111": 1}, "flag value is stripped"


def test_dispatch_config_load_failure_still_dispatches():
    for cfg in (None, "{not json", "[]"):
        for flag in ("1", None):
            d, out = _dispatch(cfg, flag)
            assert d == {"111": 2}, (cfg, flag, d)
            assert "NameError" not in out and "UnboundLocalError" not in out, out


def test_dispatch_malformed_pin_falls_back():
    d, out = _dispatch({"products": [{"tcin": "111", "qty": "abc"}]}, "1")
    assert d == {"111": 2}, d
    assert any("per-TCIN pin ignored (ValueError" in ln for ln in _qty_lines(out)), out
    assert f"[QTY] 111: targeting qty=2 — {_OPTIMISTIC_REASON}" in _qty_lines(out)


def test_thread_qty_label_text():
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    assert ('print(f"[REAL_PURCHASE_THREAD] [QTY] qty={qty} '
            '(manager decision; see [QTY] reason line)")') in src
    assert "from RedSky purchase_limit (executor will skip PDP poll)" not in src


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} smoke tests passed.")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
