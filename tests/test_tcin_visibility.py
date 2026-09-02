#!/usr/bin/env python3
"""Smoke test: TCIN visibility state + alert (2026-08-25, hardened after review).

On the 08-24 overnight run 4 of 13 armed TCINs were ABSENT from every RedSky
bulk response (unpublished on Target) and the only trace was a logger.warning
nobody read. This pins the loud path and the review hardening:

  - ResilientStockChecker._note_tcin_visibility: a TCIN is INVISIBLE only if
    absent from the cache-bust read AND unseen by ANY 200 response within
    RESILIENT_TCIN_INVISIBLE_GRACE_S (debounce); alert on first sighting,
    re-alert only after RESILIENT_TCIN_INVISIBLE_REALERT_S; _invisible_alerted_at
    is never popped (a flap inside the TTL is quiet); NOW VISIBLE fires once per
    TTL per TCIN and only for a TCIN that had an invisible alert; the state file
    is written on change, on force_write and on the first successful read
  - ResilientStockChecker._note_ground_truth_failure: 10 consecutive failed
    ground-truth reads -> one tcin_visibility_unknown alert per TTL AND (round 3)
    a verified=false state write (gt_fail_streak / verification_failed_since_unix);
    nothing is written before the threshold; the probe loop's success reset
    (streak=0, since=None, unknown-alert stamp=0.0) re-arms the gate so a second
    blind period alerts and writes again
  - round 3 ordering: the visibility block sits AFTER the C0 cold/stale fire
    loop; alert stamps land BEFORE any I/O; every print goes through
    _safe_print (a raising print() cannot break bookkeeping); the reader never
    sleeps on the loop thread
  - _ingest_bulk_response stamps _last_seen_at for every tcin RedSky returned
  - env flags RESILIENT_TCIN_VISIBILITY_ALERT / _STATE / _GRACE_S / _REALERT_S
  - on_alert exceptions swallowed
  - src/monitoring/tcin_visibility.py reader helpers + write_state_atomic
  - check_session_readiness.py (OK / PARTIAL / WARNING / UNKNOWN verdicts, in a
    temp-dir sandbox) and preflight_fp_drop.py [8b] (real repo, read-only)

No browser, no network. Never writes into <repo>/state (temp dirs only).
Run: venv/Scripts/python.exe tests/test_tcin_visibility.py
"""
from __future__ import annotations

import asyncio
import builtins
import contextlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Keep logger.error/warning from the checker off stderr (lastResort handler).
logging.getLogger().addHandler(logging.NullHandler())

from src.monitoring.stock_check_resilient import ResilientStockChecker  # noqa: E402
from src.monitoring.tab_dispatcher import BulkResult  # noqa: E402
from src.monitoring.tcin_visibility import (  # noqa: E402
    STATE_FILENAME, VisibilitySummary, format_invisible_warning, load_state,
    summarize, write_state_atomic,
)

PASS = 0
FAIL = 0

VIS_ENV = (
    "RESILIENT_TCIN_VISIBILITY_ALERT",
    "RESILIENT_TCIN_INVISIBLE_REALERT_S",
    "RESILIENT_TCIN_VISIBILITY_STATE",
    "RESILIENT_TCIN_INVISIBLE_GRACE_S",
)

CONTRACT_KEYS = {
    "schema", "updated_at", "updated_at_unix", "run_started_at_unix",
    "configured", "visible", "invisible", "last_seen_unix",
    # round 3 (additive, schema stays 1)
    "verified", "gt_fail_streak", "verification_failed_since_unix",
}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


@contextlib.contextmanager
def env_override(**kv):
    """Set/unset env vars for the block; restore os.environ afterwards."""
    saved = {k: os.environ.get(k) for k in set(VIS_ENV) | set(kv)}
    try:
        for k in VIS_ENV:
            os.environ.pop(k, None)
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Sink:
    def __init__(self, raise_exc=False):
        self.calls = []
        self.raise_exc = raise_exc

    def __call__(self, kind, level, message):
        self.calls.append((kind, level, message))
        if self.raise_exc:
            raise RuntimeError("callback boom")

    def kinds(self):
        return [k for k, _, _ in self.calls]


def make_checker(tmp, cb, tcins=("a", "b", "c")):
    return ResilientStockChecker(
        proxy_urls=[], tcins=list(tcins), state_dir=Path(tmp),
        preflight=False, log_per_request=False, on_alert=cb,
    )


def note(chk, missing, now, force_write=False):
    """Call _note_tcin_visibility with stdout captured -> (result, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = chk._note_tcin_visibility(list(missing), now, force_write=force_write)
    return res, buf.getvalue()


def gt_fail(chk, reason="x"):
    """Call _note_ground_truth_failure with stdout captured -> stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        chk._note_ground_truth_failure(reason)
    return buf.getvalue()


def read_state(tmp):
    p = Path(tmp) / STATE_FILENAME
    if not p.is_file():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def tmp_files(tmp):
    return sorted(p.name for p in Path(tmp).glob("*.tmp"))


# --------------------------------------------------------------------------
# 1. first sighting -> one error alert + state file per contract
# --------------------------------------------------------------------------
def test_first_sighting_alert_and_state():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        res, out = note(chk, ["b", "c"], now)

        check("t1_returns_dict", isinstance(res, dict)
              and set(res) >= {"newly_missing", "became_visible", "invisible", "alerted",
                               "visible_alerted", "changed"}, str(res))
        check("t1_one_alert", len(cb.calls) == 1, f"calls={cb.calls}")
        if cb.calls:
            kind, level, msg = cb.calls[0]
            check("t1_alert_kind_level", kind == "tcin_invisible" and level == "error",
                  f"{kind}/{level}")
            check("t1_alert_lists_both", "'b'" in msg and "'c'" in msg, msg)
            check("t1_alert_ascii", msg.isascii())
        check("t1_banner_printed", "[TCIN-VISIBILITY]" in out and "INVISIBLE" in out)
        check("t1_banner_ascii", out.isascii())
        check("t1_banner_real_state_path", str(Path(tmp) / STATE_FILENAME) in out, out)
        check("t1_banner_readiness_cmd", "check_session_readiness.py" in out, out)
        check("t1_result_fields", res.get("newly_missing") == ["b", "c"]
              and res.get("alerted") == ["b", "c"] and res.get("changed") is True
              and res.get("became_visible") == [] and res.get("invisible") == ["b", "c"]
              and res.get("visible_alerted") == [], str(res))

        st = read_state(tmp)
        check("t1_state_written", st is not None)
        check("t1_written_flag", chk._tcin_vis_written is True)
        if st:
            check("t1_state_keys_exact", set(st) == CONTRACT_KEYS, str(sorted(st)))
            check("t1_schema_1", st.get("schema") == 1)
            check("t1_configured", st.get("configured") == ["a", "b", "c"])
            check("t1_visible", st.get("visible") == ["a"])
            check("t1_invisible", st.get("invisible") == ["b", "c"])
            check("t1_last_seen_all_null",
                  st.get("last_seen_unix") == {"a": None, "b": None, "c": None},
                  str(st.get("last_seen_unix")))
            check("t1_updated_at_unix", abs(float(st.get("updated_at_unix")) - now) < 1e-6)
            check("t1_updated_at_iso", isinstance(st.get("updated_at"), str)
                  and "T" in st["updated_at"] and st["updated_at"].isascii())
            check("t1_run_started_null_before_start", st.get("run_started_at_unix") is None)
            check("t1_normal_write_verified_true", st.get("verified") is True, str(st.get("verified")))
            check("t1_normal_write_streak_0", st.get("gt_fail_streak") == 0
                  and isinstance(st.get("gt_fail_streak"), int), str(st.get("gt_fail_streak")))
            check("t1_normal_write_since_null",
                  "verification_failed_since_unix" in st
                  and st.get("verification_failed_since_unix") is None,
                  str(st.get("verification_failed_since_unix")))
        check("t1_no_tmp_left", tmp_files(tmp) == [], str(tmp_files(tmp)))


# --------------------------------------------------------------------------
# 2. same missing within TTL -> no new alert; force_write rewrites the file
# --------------------------------------------------------------------------
def test_within_ttl_no_realert_force_write():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        note(chk, ["b", "c"], now)
        first = read_state(tmp)
        res, out = note(chk, ["b", "c"], now + 1, force_write=True)
        check("t2_no_new_alert", len(cb.calls) == 1, f"calls={len(cb.calls)}")
        check("t2_no_banner", "[TCIN-VISIBILITY]" not in out)
        check("t2_unchanged_result", res.get("changed") is False and res.get("alerted") == []
              and res.get("newly_missing") == [], str(res))
        second = read_state(tmp)
        check("t2_force_write_rewrote",
              second is not None and first is not None
              and second["updated_at_unix"] > first["updated_at_unix"],
              f"{first and first['updated_at_unix']} -> {second and second['updated_at_unix']}")
        # without force_write and no change (and already written once) -> NOT rewritten
        res, _ = note(chk, ["b", "c"], now + 2)
        third = read_state(tmp)
        check("t2_no_force_no_change_no_rewrite",
              third["updated_at_unix"] == second["updated_at_unix"])


# --------------------------------------------------------------------------
# 3. after REALERT_S + 1 -> re-alert exactly once
# --------------------------------------------------------------------------
def test_realert_after_ttl():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        check("t3_default_realert_3600", chk._tcin_vis_realert_s == 3600.0,
              str(chk._tcin_vis_realert_s))
        now = time.time()
        note(chk, ["b", "c"], now)
        later = now + chk._tcin_vis_realert_s + 1
        res, out = note(chk, ["b", "c"], later)
        check("t3_realert_once", len(cb.calls) == 2, f"calls={len(cb.calls)}")
        check("t3_realert_kind", cb.calls[-1][0] == "tcin_invisible" and cb.calls[-1][1] == "error")
        check("t3_realert_result", res.get("alerted") == ["b", "c"] and res.get("changed") is False,
              str(res))
        check("t3_realert_banner", "[TCIN-VISIBILITY]" in out)
        # immediately after the re-alert -> quiet again
        res, out = note(chk, ["b", "c"], later + 1)
        check("t3_quiet_after_realert", len(cb.calls) == 2 and res.get("alerted") == [])


# --------------------------------------------------------------------------
# 4. missing shrinks [b,c] -> [c]: one success alert naming b only; then the
#    hardened flap semantics (alerted map never popped, TTL in both directions)
# --------------------------------------------------------------------------
def test_became_visible():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        note(chk, ["b", "c"], now)
        res, out = note(chk, ["c"], now + 2)
        check("t4_one_new_alert", len(cb.calls) == 2, f"calls={cb.calls}")
        if len(cb.calls) >= 2:
            kind, level, msg = cb.calls[-1]
            check("t4_alert_kind_level", kind == "tcin_visible_again" and level == "success",
                  f"{kind}/{level}")
            check("t4_names_b_only", "'b'" in msg and "'c'" not in msg, msg)
            check("t4_msg_ascii", msg.isascii())
        check("t4_now_visible_printed", "NOW VISIBLE" in out and out.isascii())
        check("t4_result", res.get("became_visible") == ["b"] and res.get("newly_missing") == []
              and res.get("alerted") == [] and res.get("visible_alerted") == ["b"]
              and res.get("changed") is True, str(res))
        st = read_state(tmp)
        check("t4_state_invisible_c_only", st is not None and st.get("invisible") == ["c"]
              and st.get("visible") == ["a", "b"], str(st))
        # hardened: the alerted map is NEVER popped
        check("t4_b_stays_in_alerted_map",
              "b" in chk._invisible_alerted_at and "c" in chk._invisible_alerted_at,
              str(chk._invisible_alerted_at))
        check("t4_b_visible_again_stamped", chk._visible_again_at.get("b") == now + 2,
              str(chk._visible_again_at))
        # b goes missing again inside the TTL -> newly_missing (state change) but NO
        # second invisible alert (quiet re-flap)
        res, out = note(chk, ["b", "c"], now + 3)
        check("t4_b_missing_again_quiet",
              len(cb.calls) == 2 and res.get("newly_missing") == ["b"]
              and res.get("alerted") == [] and res.get("changed") is True
              and "[TCIN-VISIBILITY]" not in out, f"{len(cb.calls)} {res}")
        st = read_state(tmp)
        check("t4_state_tracks_reflap", st is not None and st.get("invisible") == ["b", "c"], str(st))
        # b reappears again inside the TTL -> became_visible but NO second NOW VISIBLE
        res, out = note(chk, ["c"], now + 4)
        check("t4_second_visible_quiet",
              len(cb.calls) == 2 and res.get("became_visible") == ["b"]
              and res.get("visible_alerted") == [] and "NOW VISIBLE" not in out,
              f"{len(cb.calls)} {res}")
        # after the TTL, NOW VISIBLE for b may fire again (once)
        note(chk, ["b", "c"], now + 5)
        res, out = note(chk, ["c"], now + 2 + chk._tcin_vis_realert_s + 1)
        check("t4_visible_again_after_ttl",
              cb.kinds().count("tcin_visible_again") == 2 and res.get("visible_alerted") == ["b"],
              f"{cb.kinds()} {res}")

    # NOW VISIBLE only for a TCIN that actually had an invisible alert: simulate a
    # TCIN that was invisible without an alert (nothing in _invisible_alerted_at).
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        chk._invisible_prev = {"b"}
        res, out = note(chk, [], time.time())
        check("t4_no_visible_alert_without_prior_invisible_alert",
              res.get("became_visible") == ["b"] and res.get("visible_alerted") == []
              and cb.calls == [] and "NOW VISIBLE" not in out, f"{cb.calls} {res}")


# --------------------------------------------------------------------------
# 5. env flags
# --------------------------------------------------------------------------
def test_env_flags():
    with env_override(RESILIENT_TCIN_VISIBILITY_ALERT="0"), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        res, out = note(chk, ["b", "c"], time.time())
        check("t5_alert_off_no_callback", cb.calls == [], str(cb.calls))
        check("t5_alert_off_no_banner", out.strip() == "", repr(out))
        check("t5_alert_off_result", res.get("alerted") == [] and res.get("changed") is True, str(res))
        st = read_state(tmp)
        check("t5_alert_off_state_written", st is not None and st.get("invisible") == ["b", "c"])
        # alert off -> the success side is quiet too
        res, out = note(chk, [], time.time() + 1)
        check("t5_alert_off_no_visible_alert", cb.calls == [] and res.get("visible_alerted") == []
              and out.strip() == "", f"{cb.calls} {res}")

    with env_override(RESILIENT_TCIN_VISIBILITY_STATE="0"), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        res, out = note(chk, ["b", "c"], time.time(), force_write=True)
        check("t5_state_off_no_file", read_state(tmp) is None and tmp_files(tmp) == [])
        check("t5_state_off_written_flag_false", chk._tcin_vis_written is False)
        check("t5_state_off_alert_fires", len(cb.calls) == 1 and cb.calls[0][0] == "tcin_invisible")
        check("t5_state_off_banner", "[TCIN-VISIBILITY]" in out)

    with env_override(RESILIENT_TCIN_INVISIBLE_REALERT_S="5"), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("t5_realert_floor_60", chk._tcin_vis_realert_s == 60.0, str(chk._tcin_vis_realert_s))
    with env_override(RESILIENT_TCIN_INVISIBLE_REALERT_S="garbage"), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("t5_realert_bad_value_3600", chk._tcin_vis_realert_s == 3600.0)
    with env_override(RESILIENT_TCIN_INVISIBLE_REALERT_S="120"), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        note(chk, ["b"], now)
        note(chk, ["b"], now + 119)
        check("t5_custom_ttl_not_due", len(cb.calls) == 1)
        note(chk, ["b"], now + 121)
        check("t5_custom_ttl_due", len(cb.calls) == 2)

    # grace: default 90, floor 30, garbage -> 90
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("t5_grace_default_90", chk._tcin_vis_grace_s == 90.0, str(chk._tcin_vis_grace_s))
    with env_override(RESILIENT_TCIN_INVISIBLE_GRACE_S="5"), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("t5_grace_floor_30", chk._tcin_vis_grace_s == 30.0, str(chk._tcin_vis_grace_s))
    with env_override(RESILIENT_TCIN_INVISIBLE_GRACE_S="garbage"), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("t5_grace_bad_value_90", chk._tcin_vis_grace_s == 90.0, str(chk._tcin_vis_grace_s))
    with env_override(RESILIENT_TCIN_INVISIBLE_GRACE_S="300"), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        chk._last_seen_at["b"] = now - 200      # > default 90 but < custom 300
        res, _ = note(chk, ["b"], now)
        check("t5_grace_custom_honoured", res.get("invisible") == [] and cb.calls == [], str(res))

    # defaults restored / all ON when unset
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("t5_defaults_on", chk._tcin_vis_alert is True and chk._tcin_vis_state is True)

    # no on_alert at all -> still fine
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, None)
        res, out = note(chk, ["b"], time.time())
        check("t5_no_callback_ok", res.get("alerted") == ["b"] and read_state(tmp) is not None)


# --------------------------------------------------------------------------
# 6. raising on_alert is swallowed
# --------------------------------------------------------------------------
def test_raising_callback_swallowed():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink(raise_exc=True)
        chk = make_checker(tmp, cb)
        now = time.time()
        try:
            res, _ = note(chk, ["b", "c"], now)
            raised = False
        except Exception:
            res, raised = None, True
        check("t6_not_raised", not raised)
        check("t6_returns_dict", isinstance(res, dict) and res.get("alerted") == ["b", "c"], str(res))
        check("t6_callback_was_called", len(cb.calls) == 1)
        check("t6_state_written", read_state(tmp) is not None)
        # reappearance path also swallows
        try:
            res, _ = note(chk, ["c"], now + 1)
            raised = False
        except Exception:
            res, raised = None, True
        check("t6_visible_again_not_raised", not raised and res.get("became_visible") == ["b"]
              and res.get("visible_alerted") == ["b"], str(res))
        # ground-truth failure alert path also swallows
        try:
            for _ in range(10):
                gt_fail(chk)
            raised = False
        except Exception:
            raised = True
        check("t6_gt_failure_not_raised", not raised and cb.kinds()[-1] == "tcin_visibility_unknown",
              str(cb.kinds()))


# --------------------------------------------------------------------------
# 7. last_seen_unix reflects _last_seen_at (what _ingest_bulk_response stamps)
# --------------------------------------------------------------------------
def test_last_seen_propagates():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        t = 1700000000.5
        chk._last_seen_at["a"] = t
        res, _ = note(chk, ["b"], time.time(), force_write=True)
        st = read_state(tmp)
        ls = (st or {}).get("last_seen_unix", {})
        check("t7_last_seen_a", ls.get("a") == t, str(ls))
        check("t7_last_seen_b_none", "b" in ls and ls.get("b") is None, str(ls))
        check("t7_last_seen_keys_configured", set(ls) == {"a", "b", "c"}, str(ls))
        # run_started_at_unix mirrors _start_time once set
        chk._start_time = 1700000001.0
        note(chk, ["b"], time.time(), force_write=True)
        st = read_state(tmp)
        check("t7_run_started_at", st.get("run_started_at_unix") == 1700000001.0)


# --------------------------------------------------------------------------
# 8. reader helpers
# --------------------------------------------------------------------------
def test_reader_helpers():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        check("t8_load_missing_none", load_state(d) is None)
        check("t8_load_missing_dir_none", load_state(d / "nope") is None)

        payload = {
            "schema": 1, "updated_at": "2026-08-25T08:00:00", "updated_at_unix": 1000.0,
            "run_started_at_unix": None, "configured": ["1", "2", "3"],
            "visible": ["1"], "invisible": ["2", "3"],
            "last_seen_unix": {"1": 999.0, "2": None, "3": None},
        }
        check("t8_write_returns_true", write_state_atomic(d / STATE_FILENAME, payload) is True)
        check("t8_no_tmp_left", tmp_files(d) == [], str(list(d.iterdir())))
        with open(d / STATE_FILENAME, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        check("t8_file_parses", parsed == payload)
        check("t8_load_roundtrip", load_state(d) == payload)

        # write into a not-yet-existing subdir -> parent created, no raise
        check("t8_write_creates_parent",
              write_state_atomic(d / "sub" / "x" / STATE_FILENAME, payload) is True
              and (d / "sub" / "x" / STATE_FILENAME).is_file())

        # unwritable path (parent is a FILE) -> False, no raise, no tmp anywhere
        try:
            rc = write_state_atomic(d / STATE_FILENAME / "child.json", payload)
            check("t8_write_never_raises", True)
            check("t8_write_unwritable_returns_false", rc is False, repr(rc))
        except Exception as e:
            check("t8_write_never_raises", False, repr(e))
        leftovers = sorted(str(p.relative_to(d)) for p in d.rglob("*.tmp"))
        check("t8_write_unwritable_no_tmp", leftovers == [], str(leftovers))
        check("t8_write_unwritable_original_intact", load_state(d) == payload)

        bad = dict(payload, schema=2)
        write_state_atomic(d / STATE_FILENAME, bad)
        check("t8_schema_mismatch_none", load_state(d) is None)
        with open(d / STATE_FILENAME, "w", encoding="utf-8") as f:
            f.write("{not json")
        check("t8_corrupt_none", load_state(d) is None)
        with open(d / STATE_FILENAME, "w", encoding="utf-8") as f:
            f.write("[1,2]")
        check("t8_non_dict_none", load_state(d) is None)

    # summarize
    state = {
        "schema": 1, "updated_at_unix": 1000.0, "configured": ["1", "2", "3"],
        "visible": ["1"], "invisible": ["2", "3"],
    }
    s = summarize(state, ["1", "2", "4"], now=1600.0)
    check("t8_summary_type", isinstance(s, VisibilitySummary))
    check("t8_age", s.age_s == 600.0, str(s.age_s))
    check("t8_invisible_enabled", s.invisible_enabled == ["2"], str(s.invisible_enabled))
    check("t8_unchecked_enabled", s.unchecked_enabled == ["4"], str(s.unchecked_enabled))
    check("t8_configured_visible", s.configured == ["1", "2", "3"] and s.visible == ["1"])
    check("t8_not_stale", s.stale is False)
    s2 = summarize(state, [1, 2], now=1000.0 + 86400.0 + 1)
    check("t8_stale_default_24h", s2.stale is True)
    check("t8_int_tcins_normalized", s2.invisible_enabled == ["2"] and s2.unchecked_enabled == [])
    s3 = summarize(state, ["1"], now=2000.0, stale_after_s=500.0)
    check("t8_stale_custom", s3.stale is True and s3.invisible_enabled == [] and s3.unchecked_enabled == [])
    s4 = summarize({"schema": 1}, ["1"], now=5.0)
    check("t8_empty_state_all_unchecked", s4.unchecked_enabled == ["1"] and s4.invisible_enabled == [])

    # format_invisible_warning
    check("t8_fmt_nothing_to_warn", format_invisible_warning(s3) == [])
    check("t8_fmt_nothing_clean",
          format_invisible_warning(summarize(state, ["1"], now=1100.0)) == [])
    lines = format_invisible_warning(s)
    joined = "\n".join(lines)
    check("t8_fmt_lines_nonempty", isinstance(lines, list) and len(lines) >= 3)
    check("t8_fmt_ascii", all(isinstance(x, str) and x.isascii() for x in lines))
    check("t8_fmt_names_invisible", "['2']" in joined and "INVISIBLE" in joined)
    check("t8_fmt_names_unchecked", "['4']" in joined and "NOT monitored" in joined)
    check("t8_fmt_not_stale_no_marker", "STALE" not in joined)
    lines2 = format_invisible_warning(s2)
    check("t8_fmt_stale_marker", any("STALE" in x for x in lines2) and all(x.isascii() for x in lines2))
    only_unchecked = format_invisible_warning(summarize(state, ["1", "9"], now=1100.0))
    check("t8_fmt_unchecked_only", only_unchecked != [] and "['9']" in "\n".join(only_unchecked))

    # round 3: verified / gt_fail_streak / verification_failed_since_unix
    check("t8_summary_verified_default_true",
          s.verified is True and s.gt_fail_streak == 0 and s.verification_failed_since_unix is None,
          str(s))
    unv = dict(state, verified=False, gt_fail_streak=12, verification_failed_since_unix=900.0)
    su = summarize(unv, ["1"], now=1100.0)        # nothing invisible, nothing unchecked
    check("t8_summary_verified_false", su.verified is False, str(su))
    check("t8_summary_streak_int", isinstance(su.gt_fail_streak, int) and su.gt_fail_streak == 12,
          str(su.gt_fail_streak))
    check("t8_summary_since_float", su.verification_failed_since_unix == 900.0
          and isinstance(su.verification_failed_since_unix, float), str(su))
    lines_u = format_invisible_warning(su)
    joined_u = "\n".join(lines_u)
    check("t8_fmt_unverified_nonempty", lines_u != [] and "could NOT verify" in joined_u
          and "12" in joined_u, joined_u)
    check("t8_fmt_unverified_ascii", all(x.isascii() for x in lines_u))
    check("t8_fmt_unverified_no_invisible_claim", "INVISIBLE" not in joined_u
          and "NOT monitored" not in joined_u, joined_u)
    # unverified + invisible + unchecked: all three lines present
    su2 = summarize(unv, ["1", "2", "4"], now=1100.0)
    joined_u2 = "\n".join(format_invisible_warning(su2))
    check("t8_fmt_unverified_plus_lists", "could NOT verify" in joined_u2 and "['2']" in joined_u2
          and "['4']" in joined_u2, joined_u2)
    # key missing / null -> verified True; garbage streak -> 0; garbage since -> None
    check("t8_summary_missing_key_true", summarize(state, ["1"], now=1100.0).verified is True)
    sn = summarize(dict(state, verified=None, gt_fail_streak="x", verification_failed_since_unix="y"),
                   ["1"], now=1100.0)
    check("t8_summary_null_verified_true", sn.verified is True and sn.gt_fail_streak == 0
          and sn.verification_failed_since_unix is None, str(sn))
    check("t8_summary_truthy_verified", summarize(dict(state, verified=1), ["1"], now=1100.0).verified is True
          and summarize(dict(state, verified=0), ["1"], now=1100.0).verified is False)


# --------------------------------------------------------------------------
# 10a. DEBOUNCE: absent from the cache-bust read but seen by a 200 within GRACE_S
#      -> NOT invisible (a partial/odd 200 body cannot fake "all unpublished")
# --------------------------------------------------------------------------
def test_debounce_grace():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        chk._last_seen_at["b"] = now - 5
        res, out = note(chk, ["b"], now)
        check("ta_recent_sighting_not_invisible",
              res.get("invisible") == [] and res.get("newly_missing") == []
              and res.get("alerted") == [] and res.get("changed") is False, str(res))
        check("ta_recent_sighting_no_alert", cb.calls == [] and "[TCIN-VISIBILITY]" not in out)
        st = read_state(tmp)
        check("ta_state_lists_b_visible", st is not None and st.get("visible") == ["a", "b", "c"]
              and st.get("invisible") == [] and st["last_seen_unix"]["b"] == now - 5, str(st))
        # exactly at the grace boundary -> still visible (strict '>')
        chk._last_seen_at["b"] = now - chk._tcin_vis_grace_s
        res, _ = note(chk, ["b"], now)
        check("ta_at_boundary_still_visible", res.get("invisible") == [] and cb.calls == [], str(res))
        # last sighting older than GRACE_S (90) -> invisible + alert
        chk._last_seen_at["b"] = now - 200
        res, out = note(chk, ["b"], now)
        check("ta_stale_sighting_invisible",
              res.get("invisible") == ["b"] and res.get("newly_missing") == ["b"]
              and res.get("alerted") == ["b"] and res.get("changed") is True, str(res))
        check("ta_stale_sighting_alert", len(cb.calls) == 1 and cb.calls[0][0] == "tcin_invisible"
              and "[TCIN-VISIBILITY]" in out, str(cb.calls))
        st = read_state(tmp)
        check("ta_state_lists_b_invisible", st is not None and st.get("invisible") == ["b"]
              and st.get("visible") == ["a", "c"], str(st))
        # a sweep sighting (what _ingest_bulk_response stamps) flips it back to visible
        chk._last_seen_at["b"] = now + 1
        res, _ = note(chk, ["b"], now + 1)
        check("ta_sweep_sighting_restores_visible",
              res.get("invisible") == [] and res.get("became_visible") == ["b"], str(res))
        # 13-of-13: all configured absent, none ever seen -> all invisible (unpublished)
        cb2 = Sink()
        chk2 = make_checker(tmp, cb2, tcins=[str(i) for i in range(13)])
        res, _ = note(chk2, [str(i) for i in range(13)], now)
        check("ta_never_seen_all_invisible", len(res.get("invisible")) == 13 and len(cb2.calls) == 1,
              str(res))
        # 13-of-13 absent from the cache-bust read but ALL seen by the sweep just now
        # (odd/partial body) -> none invisible
        cb3 = Sink()
        chk3 = make_checker(tmp, cb3, tcins=[str(i) for i in range(13)])
        for i in range(13):
            chk3._last_seen_at[str(i)] = now - 1
        res, _ = note(chk3, [str(i) for i in range(13)], now)
        check("ta_partial_body_cannot_fake_13of13", res.get("invisible") == [] and cb3.calls == [],
              str(res))


# --------------------------------------------------------------------------
# 10b. FLAP BOUND: invisible/visible alternating every 30 s -> exactly one alert
#      in each direction per TTL
# --------------------------------------------------------------------------
def test_flap_bound():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        now = time.time()
        changes = 0
        for i in range(12):
            missing = ["b"] if i % 2 == 0 else []
            res, _ = note(chk, missing, now + 30 * i)
            changes += 1 if res.get("changed") else 0
        kinds = cb.kinds()
        check("tb_one_invisible_alert", kinds.count("tcin_invisible") == 1, str(kinds))
        check("tb_one_visible_alert", kinds.count("tcin_visible_again") == 1, str(kinds))
        check("tb_only_two_alerts_total", len(kinds) == 2, str(kinds))
        check("tb_state_changes_still_tracked", changes == 12, str(changes))
        check("tb_b_never_popped", "b" in chk._invisible_alerted_at
              and chk._invisible_alerted_at["b"] == now, str(chk._invisible_alerted_at))
        st = read_state(tmp)
        check("tb_state_final_visible", st is not None and st.get("invisible") == []
              and st["updated_at_unix"] == now + 30 * 11, str(st))
        # after the TTL -> a second invisible alert
        res, out = note(chk, ["b"], now + 3601)
        kinds = cb.kinds()
        check("tb_realert_after_ttl", kinds.count("tcin_invisible") == 2
              and res.get("alerted") == ["b"] and "[TCIN-VISIBILITY]" in out, str(kinds))
        # NOW VISIBLE is gated on ITS OWN stamp (now+30, the first reappearance):
        # at now+3602 it is still inside that TTL -> quiet; at now+3700 -> fires once
        res, _ = note(chk, [], now + 3602)
        check("tb_visible_still_gated_on_own_ttl", cb.kinds().count("tcin_visible_again") == 1
              and res.get("became_visible") == ["b"] and res.get("visible_alerted") == [],
              f"{cb.kinds()} {res}")
        note(chk, ["b"], now + 3650)
        res, _ = note(chk, [], now + 3700)
        check("tb_visible_realert_after_ttl", cb.kinds().count("tcin_visible_again") == 2
              and res.get("visible_alerted") == ["b"], str(cb.kinds()))


# --------------------------------------------------------------------------
# 10c. FIRST-READ WRITE: nothing invisible, nothing changed, no force -> the
#      state file is still written once (readiness echo has data after one cycle)
# --------------------------------------------------------------------------
def test_first_read_write():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        check("tc_written_flag_starts_false", chk._tcin_vis_written is False)
        now = time.time()
        res, out = note(chk, [], now)
        check("tc_nothing_invisible", res.get("invisible") == [] and res.get("changed") is False
              and res.get("alerted") == [] and out.strip() == "", str(res))
        st = read_state(tmp)
        check("tc_state_written_on_first_read", st is not None and st.get("visible") == ["a", "b", "c"]
              and st.get("invisible") == [] and st["updated_at_unix"] == now, str(st))
        check("tc_written_flag_set", chk._tcin_vis_written is True)
        p = Path(tmp) / STATE_FILENAME
        mtime = p.stat().st_mtime_ns
        res, _ = note(chk, [], now + 1)
        st2 = read_state(tmp)
        check("tc_second_identical_no_rewrite",
              st2["updated_at_unix"] == now and p.stat().st_mtime_ns == mtime, str(st2))
        check("tc_no_tmp_left", tmp_files(tmp) == [])

    # write failure keeps the flag False so the next cycle retries the first write
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "blocked"
        blocker.write_text("x", encoding="utf-8")
        chk = make_checker(tmp, Sink())
        chk.state_dir = blocker                   # state_dir is now a FILE -> unwritable
        res, _ = note(chk, [], time.time())
        check("tc_unwritable_flag_stays_false", chk._tcin_vis_written is False
              and isinstance(res, dict), str(res))
        check("tc_unwritable_no_tmp", sorted(Path(tmp).rglob("*.tmp")) == [])


# --------------------------------------------------------------------------
# 10d. GROUND-TRUTH FAILURE STREAK -> UNKNOWN alert (silence != all visible)
# --------------------------------------------------------------------------
def test_ground_truth_failure_streak():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        check("td_threshold_10", chk._gt_fail_alert_n == 10 and chk._gt_fail_streak == 0
              and chk._gt_fail_since is None)
        t_before_first = time.time()
        outs = [gt_fail(chk, "no ready session") for _ in range(9)]
        check("td_nine_failures_quiet", cb.calls == [] and all(o.strip() == "" for o in outs)
              and chk._gt_fail_streak == 9, f"{cb.calls} streak={chk._gt_fail_streak}")
        # round 3: _gt_fail_since is stamped on the FIRST failure and held
        check("td_since_stamped_on_first_failure",
              isinstance(chk._gt_fail_since, float) and chk._gt_fail_since >= t_before_first
              and chk._gt_fail_since <= time.time(), str(chk._gt_fail_since))
        first_since = chk._gt_fail_since
        # round 3: NOTHING is written before the threshold
        check("td_no_write_before_threshold", read_state(tmp) is None and tmp_files(tmp) == [],
              str(sorted(Path(tmp).iterdir())))
        out = gt_fail(chk, "http=400")
        check("td_since_held_across_streak", chk._gt_fail_since == first_since)
        # round 3: the 10th failure WRITES a verified=false state (readers -> UNKNOWN)
        st = read_state(tmp)
        check("td_tenth_writes_state", st is not None, str(sorted(Path(tmp).iterdir())))
        if st:
            check("td_tenth_state_keys_exact", set(st) == CONTRACT_KEYS, str(sorted(st)))
            check("td_tenth_verified_false", st.get("verified") is False, str(st.get("verified")))
            check("td_tenth_streak_10", st.get("gt_fail_streak") == 10, str(st.get("gt_fail_streak")))
            check("td_tenth_since_float",
                  isinstance(st.get("verification_failed_since_unix"), float)
                  and st["verification_failed_since_unix"] == first_since,
                  str(st.get("verification_failed_since_unix")))
            check("td_tenth_lists_last_known", st.get("configured") == ["a", "b", "c"]
                  and st.get("visible") == ["a", "b", "c"] and st.get("invisible") == []
                  and st.get("schema") == 1, str(st))
            check("td_tenth_no_tmp_left", tmp_files(tmp) == [])
        tenth_write_unix = (st or {}).get("updated_at_unix")
        check("td_tenth_alerts_once", len(cb.calls) == 1
              and cb.calls[0][0] == "tcin_visibility_unknown" and cb.calls[0][1] == "warning",
              str(cb.calls))
        if cb.calls:
            msg = cb.calls[0][2]
            check("td_msg_content", "[TCIN-VISIBILITY] UNKNOWN" in msg and "10 consecutive" in msg
                  and "http=400" in msg and msg.isascii(), msg)
        check("td_tenth_printed", "[TCIN-VISIBILITY] UNKNOWN" in out and out.isascii(), out)
        for _ in range(10):
            out = gt_fail(chk)
        check("td_11_to_20_within_ttl_quiet", len(cb.calls) == 1 and out.strip() == ""
              and chk._gt_fail_streak == 20, f"{len(cb.calls)} streak={chk._gt_fail_streak}")
        # quiet failures inside the TTL do not rewrite the state file
        st = read_state(tmp)
        check("td_quiet_failures_no_rewrite",
              st is not None and st.get("updated_at_unix") == tenth_write_unix
              and st.get("gt_fail_streak") == 10, str(st))
        # a successful read resets the streak (the probe loop sets it to 0)
        chk._gt_fail_streak = 0
        chk._gt_fail_since = None
        chk._vis_unknown_alerted_at = 0.0          # expire the TTL to isolate the streak gate
        for _ in range(9):
            gt_fail(chk)
        check("td_after_reset_nine_quiet", len(cb.calls) == 1 and chk._gt_fail_streak == 9,
              f"{len(cb.calls)} streak={chk._gt_fail_streak}")
        gt_fail(chk)
        check("td_after_reset_tenth_alerts", len(cb.calls) == 2
              and cb.calls[-1][0] == "tcin_visibility_unknown", str(cb.kinds()))
        # TTL: once expired while the streak continues -> one more alert
        chk._vis_unknown_alerted_at = time.time() - chk._tcin_vis_realert_s - 1
        gt_fail(chk)
        check("td_realert_after_ttl", len(cb.calls) == 3, str(cb.kinds()))
        gt_fail(chk)
        check("td_quiet_again_after_realert", len(cb.calls) == 3, str(cb.kinds()))
        # the failure path never touches the visibility maps (only the state file,
        # and only at alert time -- verified=false, never a fake verdict)
        st = read_state(tmp)
        check("td_failure_path_leaves_visibility_maps_alone",
              chk._invisible_alerted_at == {} and chk._invisible_prev == set()
              and chk._tcin_vis_written is False
              and st is not None and st.get("verified") is False and st.get("invisible") == [],
              f"{chk._invisible_alerted_at} {chk._invisible_prev} {st}")
        # a later SUCCESSFUL read (verified=true write) clears the blind marker
        chk._gt_fail_streak = 0
        chk._gt_fail_since = None
        chk._vis_unknown_alerted_at = 0.0
        note(chk, [], time.time(), force_write=True)
        st = read_state(tmp)
        check("td_success_write_clears_blind_marker",
              st is not None and st.get("verified") is True and st.get("gt_fail_streak") == 0
              and st.get("verification_failed_since_unix") is None, str(st))

    with env_override(RESILIENT_TCIN_VISIBILITY_ALERT="0"), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        outs = [gt_fail(chk) for _ in range(25)]
        check("td_alert_off_never_alerts", cb.calls == [] and all(o.strip() == "" for o in outs)
              and chk._gt_fail_streak == 25, f"{cb.calls} streak={chk._gt_fail_streak}")
        # round 4: the blind marker follows STATE, not ALERT -- a silenced console
        # must still leave verified=false for the pre-drop readers
        st_off = read_state(tmp)
        check("td_alert_off_still_writes_blind_marker",
              st_off is not None and st_off.get("verified") is False
              and st_off.get("gt_fail_streak") == 10 and tmp_files(tmp) == [], str(st_off))

    # STATE=0: the 10th failure still alerts but never writes the file
    with env_override(RESILIENT_TCIN_VISIBILITY_STATE="0"), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        out = ""
        for _ in range(10):
            out = gt_fail(chk)
        check("td_state_off_alerts_no_file",
              len(cb.calls) == 1 and cb.calls[0][0] == "tcin_visibility_unknown"
              and "[TCIN-VISIBILITY] UNKNOWN" in out
              and read_state(tmp) is None and tmp_files(tmp) == [],
              f"{cb.kinds()} {sorted(Path(tmp).iterdir())}")

    # no on_alert -> still prints, still fine
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, None)
        out = ""
        for _ in range(10):
            out = gt_fail(chk)
        check("td_no_callback_prints", "[TCIN-VISIBILITY] UNKNOWN" in out, out)

    # the probe loop wires the streak: success resets it, every failure branch notes it
    src = (ROOT / "src" / "monitoring" / "stock_check_resilient.py").read_text(encoding="utf-8")
    check("td_loop_resets_streak_on_success", "self._gt_fail_streak = 0" in src)
    check("td_loop_notes_all_failure_branches",
          src.count("self._note_ground_truth_failure(") >= 3
          and "0 TCINs parsed" in src and '"no ready session"' in src,
          str(src.count("self._note_ground_truth_failure(")))
    loop = src[src.index("async def _ground_truth_probe_loop"):src.index("def _note_ground_truth_failure")]
    check("td_loop_success_reset_full",
          "self._gt_fail_streak = 0" in loop and "self._gt_fail_since = None" in loop
          and "self._vis_unknown_alerted_at = 0.0" in loop)
    check("td_loop_logs_resumed", "verification RESUMED after" in loop
          and "consecutive failed ground-truth reads" in loop)
    check("td_loop_stamps_parsed_as_seen", "for _t in parsed:" in loop
          and "self._last_seen_at[str(_t)] = _vis_now" in loop)
    check("td_loop_resumed_forces_write",
          "force_write=(cycle % 5 == 1) or _resumed" in src and "_resumed = self._gt_fail_streak >= self._gt_fail_alert_n" in src)
    check("td_failure_writes_verified_false",
          "self._write_visibility_state(now, verified=False)" in src
          and "self._write_visibility_state(now, verified=True)" in src)


# --------------------------------------------------------------------------
# 10d2. SECOND UNKNOWN: after the loop's success reset a second blind period
#       alerts immediately (inside the TTL) and writes verified=false again
# --------------------------------------------------------------------------
def test_second_unknown_after_resume():
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        for _ in range(10):
            gt_fail(chk, "http=400")
        check("t2u_first_alert", cb.kinds() == ["tcin_visibility_unknown"], str(cb.kinds()))
        first = read_state(tmp)
        check("t2u_first_write", first is not None and first.get("verified") is False
              and first.get("gt_fail_streak") == 10, str(first))
        first_alert_at = chk._vis_unknown_alerted_at
        check("t2u_alert_stamp_set", first_alert_at > 0.0)
        # what _ground_truth_probe_loop does on a successful parse
        chk._gt_fail_streak = 0
        chk._gt_fail_since = None
        chk._vis_unknown_alerted_at = 0.0
        outs = [gt_fail(chk, "no ready session") for _ in range(9)]
        check("t2u_nine_quiet_after_reset", len(cb.calls) == 1 and all(o.strip() == "" for o in outs)
              and chk._gt_fail_streak == 9, f"{len(cb.calls)} streak={chk._gt_fail_streak}")
        st = read_state(tmp)
        check("t2u_no_rewrite_before_second_threshold",
              st is not None and st.get("updated_at_unix") == first.get("updated_at_unix"), str(st))
        out = gt_fail(chk, "no ready session")
        check("t2u_second_alert_within_ttl",
              cb.kinds() == ["tcin_visibility_unknown", "tcin_visibility_unknown"]
              and "10 consecutive" in cb.calls[-1][2] and "no ready session" in cb.calls[-1][2]
              and "[TCIN-VISIBILITY] UNKNOWN" in out,
              f"{cb.kinds()} {out!r}")
        check("t2u_second_alert_inside_first_ttl",
              (chk._vis_unknown_alerted_at - first_alert_at) < chk._tcin_vis_realert_s
              and chk._vis_unknown_alerted_at >= first_alert_at)
        second = read_state(tmp)
        check("t2u_second_write",
              second is not None and second.get("verified") is False
              and second.get("gt_fail_streak") == 10
              and isinstance(second.get("verification_failed_since_unix"), float)
              and second["verification_failed_since_unix"] >= first["verification_failed_since_unix"]
              and second["updated_at_unix"] >= first["updated_at_unix"]
              and second["verification_failed_since_unix"] == chk._gt_fail_since,
              f"{first} -> {second}")
        check("t2u_no_tmp_left", tmp_files(tmp) == [])
        # without the reset (streak keeps climbing) the TTL still gates
        gt_fail(chk)
        check("t2u_ttl_still_gates_without_reset", len(cb.calls) == 2, str(cb.kinds()))


# --------------------------------------------------------------------------
# 10h. INGEST: _ingest_bulk_response stamps _last_seen_at for every tcin the
#      200 body carried (the sweep-side "seen" that debounces the verdict)
# --------------------------------------------------------------------------
def _summary(tcin, status="OUT_OF_STOCK"):
    return {
        "tcin": tcin,
        "item": {"product_description": {"title": f"Product {tcin}"},
                 "relationship_type_code": "SA"},
        "fulfillment": {"shipping_options": {"availability_status": status,
                                             "available_to_promise_quantity": 0,
                                             "services": []}},
    }


def _bulk(raw, status=200):
    return BulkResult(session_id="s0", pinned_ip="0.0.0.0", http_status=status,
                      latency_ms=1, raw=raw, error=None)


def test_ingest_stamps_last_seen():
    fired = []
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        cb = Sink()
        chk = make_checker(tmp, cb)
        chk.on_in_stock = lambda s: fired.append(s.tcin)
        res = _bulk({"data": {"product_summaries": [_summary("a"), _summary("b")]}})
        buf = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(buf):
            asyncio.run(chk._ingest_bulk_response(res))
        check("th_seen_exactly_a_b", set(chk._last_seen_at) == {"a", "b"}, str(chk._last_seen_at))
        check("th_seen_stamps_are_now",
              all(t0 <= v <= time.time() for v in chk._last_seen_at.values()), str(chk._last_seen_at))
        check("th_seen_keys_are_str", all(isinstance(k, str) for k in chk._last_seen_at))
        # _tcin_status is pre-populated for every configured TCIN at construction;
        # only the two RedSky returned are touched (c keeps its defaults)
        check("th_status_tracked_oos",
              set(chk._tcin_status) == {"a", "b", "c"}
              and all(chk._tcin_status[t].in_stock is False
                      and chk._tcin_status[t].last_status_code == 200
                      and chk._tcin_status[t].availability_status == "OUT_OF_STOCK"
                      for t in ("a", "b"))
              and chk._tcin_status["c"].last_status_code == 0
              and chk._tcin_status["c"].availability_status == "UNKNOWN",
              str({k: (v.in_stock, v.last_status_code, v.availability_status)
                   for k, v in chk._tcin_status.items()}))
        check("th_no_purchase_fired", fired == [], str(fired))
        # c was never returned by RedSky -> the only invisible one
        now = time.time()
        r, out = note(chk, ["a", "b", "c"], now)
        check("th_c_only_invisible", r.get("invisible") == ["c"] and r.get("alerted") == ["c"]
              and r.get("newly_missing") == ["c"], str(r))
        check("th_alert_names_c_only", len(cb.calls) == 1 and "'c'" in cb.calls[0][2]
              and "'a'" not in cb.calls[0][2] and "'b'" not in cb.calls[0][2], str(cb.calls))
        st = read_state(tmp)
        check("th_state_last_seen_a_b",
              st is not None and st["last_seen_unix"]["a"] == chk._last_seen_at["a"]
              and st["last_seen_unix"]["b"] == chk._last_seen_at["b"]
              and st["last_seen_unix"]["c"] is None and st.get("visible") == ["a", "b"]
              and st.get("invisible") == ["c"] and st.get("verified") is True, str(st))
        # a tcin outside the configured list is stamped too (harmless) but the
        # verdict only ever covers configured TCINs
        with contextlib.redirect_stdout(buf):
            asyncio.run(chk._ingest_bulk_response(
                _bulk({"data": {"product_summaries": [_summary("zzz")]}})))
        r, _ = note(chk, ["a", "b", "c"], time.time())
        check("th_unconfigured_ignored_in_verdict", "zzz" in chk._last_seen_at
              and r.get("invisible") == ["c"] and set(read_state(tmp)["last_seen_unix"]) == {"a", "b", "c"},
              str(r))

    # summaries without 'tcin' stamp nothing; empty/odd bodies stamp nothing
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            asyncio.run(chk._ingest_bulk_response(_bulk({"data": {"product_summaries": [
                {"item": {}, "fulfillment": {}}, {"tcin": "", "item": {}, "fulfillment": {}}]}})))
        check("th_no_tcin_stamps_nothing",
              chk._last_seen_at == {}
              and all(s.last_status_code == 0 for s in chk._tcin_status.values()),
              str(chk._last_seen_at))
        with contextlib.redirect_stdout(buf):
            asyncio.run(chk._ingest_bulk_response(_bulk({"data": {}})))
            asyncio.run(chk._ingest_bulk_response(_bulk({"data": {"product_summaries": []}})))
            asyncio.run(chk._ingest_bulk_response(_bulk(None)))
            asyncio.run(chk._ingest_bulk_response(_bulk({})))
        check("th_empty_bodies_stamp_nothing", chk._last_seen_at == {}, str(chk._last_seen_at))
        # an odd/partial body can never fake "all unpublished": nothing seen -> all
        # configured invisible; then a full sweep body -> none invisible
        r, _ = note(chk, ["a", "b", "c"], time.time())
        check("th_nothing_seen_all_invisible", r.get("invisible") == ["a", "b", "c"], str(r))
        with contextlib.redirect_stdout(buf):
            asyncio.run(chk._ingest_bulk_response(_bulk({"data": {"product_summaries": [
                _summary("a"), _summary("b"), _summary("c")]}})))
        r, _ = note(chk, ["a", "b", "c"], time.time())
        check("th_sweep_sighting_beats_absent_cachebust", r.get("invisible") == []
              and r.get("became_visible") == ["a", "b", "c"], str(r))


# --------------------------------------------------------------------------
# 10i. SAFE PRINT: a raising print() (closed/broken stdout) cannot break the
#      alert stamps, the callback or the state write; stamps land BEFORE I/O
# --------------------------------------------------------------------------
def test_safe_print_and_stamp_order():
    real_print = builtins.print

    def boom(*a, **k):
        raise OSError("stdout closed")

    with env_override(), tempfile.TemporaryDirectory() as tmp:
        stamp_seen_at_callback = {}

        def cb(kind, level, message):
            # stamps must already be in place when on_alert runs (before any I/O)
            stamp_seen_at_callback[kind] = (
                dict(chk._invisible_alerted_at), dict(chk._visible_again_at),
                chk._vis_unknown_alerted_at)

        chk = make_checker(tmp, cb)
        now = time.time()
        builtins.print = boom
        try:
            try:
                res = chk._note_tcin_visibility(["b"], now)
                raised = None
            except Exception as e:
                res, raised = None, e
        finally:
            builtins.print = real_print
        check("ti_first_sighting_not_raised", raised is None, repr(raised))
        check("ti_first_sighting_alerted", isinstance(res, dict) and res.get("alerted") == ["b"]
              and res.get("invisible") == ["b"], str(res))
        check("ti_stamp_set", chk._invisible_alerted_at.get("b") == now, str(chk._invisible_alerted_at))
        check("ti_stamp_before_callback",
              stamp_seen_at_callback.get("tcin_invisible", ({},))[0].get("b") == now,
              str(stamp_seen_at_callback))
        st = read_state(tmp)
        check("ti_state_written_despite_print", st is not None and st.get("invisible") == ["b"]
              and st.get("verified") is True, str(st))
        check("ti_no_tmp_left", tmp_files(tmp) == [])
        # NOW VISIBLE path with a raising print
        builtins.print = boom
        try:
            try:
                res = chk._note_tcin_visibility([], now + 1)
                raised = None
            except Exception as e:
                res, raised = None, e
        finally:
            builtins.print = real_print
        check("ti_visible_again_not_raised", raised is None and res.get("visible_alerted") == ["b"],
              f"{raised!r} {res}")
        check("ti_visible_stamp_before_callback",
              chk._visible_again_at.get("b") == now + 1
              and stamp_seen_at_callback.get("tcin_visible_again", ({}, {}))[1].get("b") == now + 1,
              str(stamp_seen_at_callback))
        # UNKNOWN path with a raising print: still alerts + writes verified=false
        builtins.print = boom
        try:
            try:
                for _ in range(10):
                    chk._note_ground_truth_failure("http=400")
                raised = None
            except Exception as e:
                raised = e
        finally:
            builtins.print = real_print
        check("ti_unknown_not_raised", raised is None
              and "tcin_visibility_unknown" in stamp_seen_at_callback, repr(raised))
        check("ti_unknown_stamp_before_callback",
              stamp_seen_at_callback.get("tcin_visibility_unknown", (0, 0, 0.0))[2] > 0.0,
              str(stamp_seen_at_callback.get("tcin_visibility_unknown")))
        st = read_state(tmp)
        check("ti_unknown_written_despite_print", st is not None and st.get("verified") is False
              and st.get("gt_fail_streak") == 10, str(st))
        check("ti_print_restored", builtins.print is real_print)

    # _safe_print itself: static, swallows, prints when stdout works
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ResilientStockChecker._safe_print("line-1", "line-2")
    check("ti_safe_print_prints", buf.getvalue() == "line-1\nline-2\n", repr(buf.getvalue()))
    builtins.print = boom
    try:
        try:
            ResilientStockChecker._safe_print("x")
            raised = None
        except Exception as e:
            raised = e
    finally:
        builtins.print = real_print
    check("ti_safe_print_swallows", raised is None, repr(raised))
    check("ti_safe_print_static", isinstance(ResilientStockChecker.__dict__.get("_safe_print"), staticmethod))


# --------------------------------------------------------------------------
# 10j. ORDER (source inspection): bookkeeping AFTER the C0 fire loop; the
#      reader never sleeps on the loop thread; print goes through _safe_print
# --------------------------------------------------------------------------
def test_order_and_no_sleep():
    src = (ROOT / "src" / "monitoring" / "stock_check_resilient.py").read_text(encoding="utf-8")
    start = src.index("async def _ground_truth_probe_loop")
    end = src.index("def _note_ground_truth_failure")
    loop = src[start:end]
    i_fire = loop.find("FIRING PURCHASE (cold/stale catch)")
    i_vis = loop.find("self._note_tcin_visibility(missing")
    check("tj_both_markers_in_loop", i_fire >= 0 and i_vis >= 0, f"fire={i_fire} vis={i_vis}")
    check("tj_visibility_after_fire_loop", 0 <= i_fire < i_vis, f"fire={i_fire} vis={i_vis}")
    # the fire loop's on_in_stock call precedes the visibility block too
    i_call = loop.find("self.on_in_stock(s)")
    check("tj_on_in_stock_before_visibility", 0 <= i_call < i_vis, f"call={i_call} vis={i_vis}")
    # the visibility block runs exactly once per cycle (single call site in the loop)
    check("tj_single_visibility_call_site", loop.count("self._note_tcin_visibility(") == 1,
          str(loop.count("self._note_tcin_visibility(")))
    reader = (ROOT / "src" / "monitoring" / "tcin_visibility.py").read_text(encoding="utf-8")
    check("tj_reader_no_time_sleep", "time.sleep" not in reader)
    check("tj_reader_no_sleep_at_all", "sleep(" not in reader)
    check("tj_reader_write_retries", "for attempt in (1, 2)" in reader)
    # every print in the visibility helpers goes through _safe_print
    vis_start = src.index("def _note_ground_truth_failure")
    vis_end = src.index("async def _canary_loop")
    helpers = src[vis_start:vis_end]
    bare_prints = [ln for ln in helpers.splitlines() if re.match(r"^\s*print\(", ln)]
    # the one allowed bare print call is inside _safe_print's own try
    check("tj_helpers_print_via_safe_print",
          [ln.strip() for ln in bare_prints] == ["print(line)"], str(bare_prints))
    check("tj_helpers_use_safe_print", helpers.count("self._safe_print(") >= 3,
          str(helpers.count("self._safe_print(")))
    check("tj_safe_print_defined_once", src.count("def _safe_print(") == 1)


# --------------------------------------------------------------------------
# 10e/f. write_state_atomic contract + updated_at derivation
# --------------------------------------------------------------------------
def test_write_atomic_and_updated_at():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        payload = {"schema": 1, "updated_at_unix": 1.0}
        rc = write_state_atomic(d / STATE_FILENAME, payload)
        check("te_returns_true", rc is True, repr(rc))
        check("te_no_tmp_after_success", tmp_files(d) == [] and sorted(p.name for p in d.iterdir())
              == [STATE_FILENAME], str(list(d.iterdir())))
        check("te_pid_tmp_name_not_left", not (d / f"{STATE_FILENAME}.{os.getpid()}.tmp").exists())
        # overwrite in place (os.replace) keeps a single file
        rc = write_state_atomic(d / STATE_FILENAME, dict(payload, updated_at_unix=2.0))
        check("te_overwrite_true", rc is True and load_state(d)["updated_at_unix"] == 2.0)
        check("te_overwrite_no_tmp", tmp_files(d) == [])

        # parent is a FILE -> False, no raise, no tmp anywhere
        blocker = d / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        try:
            rc = write_state_atomic(blocker / STATE_FILENAME, payload)
            check("te_unwritable_no_raise", True)
        except Exception as e:
            rc = "raised"
            check("te_unwritable_no_raise", False, repr(e))
        check("te_unwritable_returns_false", rc is False, repr(rc))
        check("te_unwritable_no_tmp", sorted(d.rglob("*.tmp")) == [], str(sorted(d.rglob("*"))))
        check("te_blocker_intact", blocker.read_text(encoding="utf-8") == "not a dir")
        # accepts str paths too
        check("te_str_path_ok", write_state_atomic(str(d / "s" / STATE_FILENAME), payload) is True
              and (d / "s" / STATE_FILENAME).is_file())

    # f. updated_at derived from the same 'now' as updated_at_unix
    with env_override(), tempfile.TemporaryDirectory() as tmp:
        chk = make_checker(tmp, Sink())
        now = 1756100000.25
        note(chk, ["b"], now, force_write=True)
        st = read_state(tmp)
        expected = datetime.fromtimestamp(now).isoformat(timespec="seconds")
        check("tf_updated_at_matches_unix",
              st is not None and st["updated_at_unix"] == now and st["updated_at"] == expected,
              f"{st and st.get('updated_at')} vs {expected}")
        check("tf_updated_at_roundtrip",
              st is not None and st["updated_at"]
              == datetime.fromtimestamp(st["updated_at_unix"]).isoformat(timespec="seconds"))


# --------------------------------------------------------------------------
# 9/10g. script integration
#   - real repo: read-only ('no state yet' branch is fine)
#   - sandbox: copy check_session_readiness.py + reader into a temp dir and drive
#     every verdict branch with a fabricated schema-1 state
# --------------------------------------------------------------------------
def _run_script(name, cwd=None):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cwd = Path(cwd) if cwd else ROOT
    p = subprocess.run(
        [sys.executable, str(cwd / name)], cwd=str(cwd), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    return p


def _verdict_line(out):
    for line in out.splitlines():
        if line.startswith("TCIN VISIBILITY:"):
            return line
    return ""


def test_scripts_real_repo():
    before = (ROOT / "state" / STATE_FILENAME).exists()
    p = _run_script("check_session_readiness.py")
    out = p.stdout + "\n" + p.stderr
    check("t9_readiness_exit_0", p.returncode == 0, f"rc={p.returncode} tail={out[-600:]!r}")
    check("t9_readiness_section", "TCIN visibility" in p.stdout, out[-600:])
    check("t9_readiness_verdict", "TCIN VISIBILITY:" in p.stdout, out[-600:])
    vl = _verdict_line(p.stdout)
    check("t9_readiness_verdict_status_word",
          any(f"TCIN VISIBILITY: {w} --" in vl for w in ("OK", "PARTIAL", "WARNING", "UNKNOWN")), vl)
    check("t9_readiness_no_stale_timing_claim",
          "60 s after boot" not in out and "60s after boot" not in out
          and not any(ph in out for ph in STALE_TIMING_PHRASES), out[-600:])
    if not before:
        check("t9_readiness_no_state_says_first_read",
              "first successful ground-truth read" in p.stdout and "[STOCK STATS]" in p.stdout
              and "TCIN VISIBILITY: UNKNOWN" in vl, out[-600:])

    p = _run_script("preflight_fp_drop.py")
    out = p.stdout + "\n" + p.stderr
    check("t9_preflight_8b", "[8b]" in p.stdout, f"rc={p.returncode} tail={out[-600:]!r}")
    check("t9_preflight_ran_to_result", "RESULT:" in p.stdout, out[-600:])
    check("t9_preflight_no_stale_timing_claim", "60 s after boot" not in out
          and not any(ph in out for ph in STALE_TIMING_PHRASES), out[-600:])
    check("t9_preflight_8b_section_title", "TCIN visibility (last bot run)" in p.stdout,
          out[-600:])
    if not before:
        check("t9_preflight_no_state_warns", "no visibility state yet" in p.stdout
              and "[STOCK STATS]" in p.stdout and BANNER_TIMING_PHRASE in p.stdout, out[-600:])
    after = (ROOT / "state" / STATE_FILENAME).exists()
    check("t9_scripts_did_not_create_state", before == after)
    check("t9_scripts_left_no_tmp", sorted((ROOT / "state").glob("tcin_visibility*.tmp")) == [])


def _sandbox(tmp, products, state, cfg_text=None):
    """Lay out a minimal repo copy: script + reader package + config + state."""
    d = Path(tmp)
    shutil.copy(ROOT / "check_session_readiness.py", d / "check_session_readiness.py")
    (d / "src" / "monitoring").mkdir(parents=True, exist_ok=True)
    (d / "src" / "__init__.py").write_text("", encoding="utf-8")
    (d / "src" / "monitoring" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(ROOT / "src" / "monitoring" / "tcin_visibility.py",
                d / "src" / "monitoring" / "tcin_visibility.py")
    (d / "config").mkdir(exist_ok=True)
    if cfg_text is None:
        cfg_text = json.dumps({"products": products})
    (d / "config" / "product_config.json").write_text(cfg_text, encoding="utf-8")
    (d / "state").mkdir(exist_ok=True)
    sp = d / "state" / STATE_FILENAME
    if sp.exists():
        sp.unlink()
    if state is not None:
        sp.write_text(json.dumps(state), encoding="utf-8")


def _state(now, configured, visible, invisible, age_s, verified=True, streak=0, since=None):
    return {
        "schema": 1,
        "updated_at": datetime.fromtimestamp(now - age_s).isoformat(timespec="seconds"),
        "updated_at_unix": now - age_s, "run_started_at_unix": now - age_s - 60,
        "configured": configured, "visible": visible, "invisible": invisible,
        "last_seen_unix": {t: (now - age_s if t in visible else None) for t in configured},
        "verified": verified, "gt_fail_streak": streak, "verification_failed_since_unix": since,
    }


BANNER_TIMING_PHRASE = "same second as the first [STOCK STATS]"
STALE_TIMING_PHRASES = ("about 30 s after", "~30 s after", "30s after the first", "60 s after boot")


def test_scripts_sandbox_verdicts():
    now = time.time()
    prods = [{"tcin": "1000000001", "name": "p1", "enabled": True},
             {"tcin": "1000000002", "name": "p2", "enabled": True},
             {"tcin": "1000000003", "name": "p3", "enabled": True},
             {"tcin": "1000000004", "name": "p4-off", "enabled": False}]
    conf = ["1000000001", "1000000002", "1000000003"]

    def run(tag, products, state, cfg_text=None):
        with tempfile.TemporaryDirectory() as tmp:
            _sandbox(tmp, products, state, cfg_text)
            p = _run_script("check_session_readiness.py", cwd=tmp)
            out = p.stdout + "\n" + p.stderr
            check(f"tg_{tag}_exit_0", p.returncode == 0, f"rc={p.returncode} tail={out[-800:]!r}")
            vl = _verdict_line(p.stdout)
            check(f"tg_{tag}_ascii_verdict", vl.isascii(), vl)
            check(f"tg_{tag}_no_stale_timing_phrase",
                  not any(ph in out for ph in STALE_TIMING_PHRASES), out[-800:])
            return p.stdout, vl

    # OK: fresh, every enabled TCIN configured + visible (disabled one ignored)
    out, vl = run("ok", prods, _state(now, conf, conf, [], 600))
    check("tg_ok_verdict", vl.startswith("TCIN VISIBILITY: OK --") and "all 3 enabled TCIN(s)" in vl, vl)
    check("tg_ok_no_warning_block", "[TCIN-VISIBILITY]" not in out and "STALE" not in out, out[-800:])

    # PARTIAL (unchecked): enabled TCIN not in the last run's configured set
    prods_plus = prods + [{"tcin": "1000000009", "name": "new", "enabled": True}]
    out, vl = run("partial_unchecked", prods_plus, _state(now, conf, conf, [], 600))
    check("tg_partial_unchecked_verdict",
          vl.startswith("TCIN VISIBILITY: PARTIAL --") and "NOT yet checked" in vl
          and "1000000009" in vl and "STALE" not in vl, vl)
    check("tg_partial_unchecked_not_monitored_line", out.count("NOT monitored") == 1, out[-1200:])
    check("tg_partial_unchecked_confirm_hint",
          "confirm the [TCIN-VISIBILITY] banner" in vl and BANNER_TIMING_PHRASE in vl
          and "re-run this script" in vl, vl)

    # PARTIAL (stale): all visible but >24h old
    out, vl = run("partial_stale", prods, _state(now, conf, conf, [], 200000))
    check("tg_partial_stale_verdict",
          vl.startswith("TCIN VISIBILITY: PARTIAL --") and "STALE" in vl and "[STOCK STATS]" in vl
          and "NOT yet checked" not in vl, vl)
    check("tg_partial_stale_timing_hint", "[TCIN-VISIBILITY] STALE: re-check" in out
          and "~3-4 min after launch" in out and BANNER_TIMING_PHRASE in out
          and "23:22:33" in out and "23:23:03" in out, out[-1200:])
    check("tg_partial_stale_confirm_hint", "confirm the [TCIN-VISIBILITY] banner" in vl
          and BANNER_TIMING_PHRASE in vl, vl)

    # PARTIAL (stale + unchecked): both clauses on one line
    out, vl = run("partial_both", prods_plus, _state(now, conf, conf, [], 200000))
    check("tg_partial_both_verdict",
          vl.startswith("TCIN VISIBILITY: PARTIAL --") and "NOT yet checked" in vl and "STALE" in vl, vl)

    # WARNING: an enabled TCIN was invisible in the last run (beats partial)
    out, vl = run("warning", prods_plus,
                  _state(now, conf, ["1000000001", "1000000002"], ["1000000003"], 200000))
    check("tg_warning_verdict",
          vl.startswith("TCIN VISIBILITY: WARNING --") and "1000000003" in vl
          and "1 enabled TCIN(s) invisible" in vl, vl)
    check("tg_warning_block_printed", "INVISIBLE to RedSky" in out and "!!!!" in out, out[-1500:])

    # not-a-warning: the invisible TCIN is DISABLED in config -> OK
    prods_off = [dict(p, enabled=(p["enabled"] and p["tcin"] != "1000000003")) for p in prods]
    out, vl = run("ok_invisible_disabled", prods_off,
                  _state(now, conf, ["1000000001", "1000000002"], ["1000000003"], 600))
    check("tg_ok_invisible_disabled_verdict", vl.startswith("TCIN VISIBILITY: OK --")
          and "all 2 enabled TCIN(s)" in vl, vl)

    # UNKNOWN: no state file
    out, vl = run("unknown_nostate", prods, None)
    check("tg_unknown_nostate_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --")
          and "no state" in vl, vl)
    check("tg_unknown_nostate_hint", "first successful ground-truth read" in out
          and "in the same second as the first [STOCK STATS] t=30.0s line" in out
          and "[MULTI_SESSION] started -- N/N sessions ready" in out
          and "~3-4 min after launch" in out and "23:19:20" in out,
          out[-1200:])

    # UNKNOWN: config unreadable (state present must NOT read as OK)
    out, vl = run("unknown_badcfg", prods, _state(now, conf, conf, [], 600), cfg_text="{bad json")
    check("tg_unknown_badcfg_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --")
          and "could not read config/product_config.json" in vl, vl)

    # UNKNOWN: state present but schema mismatch -> treated as no state
    bad_schema = dict(_state(now, conf, conf, [], 600), schema=2)
    out, vl = run("unknown_schema", prods, bad_schema)
    check("tg_unknown_schema_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --"), vl)

    # UNKNOWN: zero enabled TCINs must not be a false OK
    out, vl = run("unknown_noenabled", [dict(p, enabled=False) for p in prods],
                  _state(now, conf, conf, [], 600))
    check("tg_unknown_noenabled_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --")
          and "no enabled TCINs" in vl, vl)

    # UNKNOWN (round 3): the last run could NOT verify (verified=false) -- even a
    # fresh all-visible list must never read as OK
    unv = _state(now, conf, conf, [], 900, verified=False, streak=12, since=now - 7200)
    out, vl = run("unknown_unverified", prods, unv)
    check("tg_unknown_unverified_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --")
          and "could NOT verify" in vl and "12 consecutive" in vl and "unverified" in vl, vl)
    check("tg_unknown_unverified_block", "could NOT verify visibility" in out
          and "12 consecutive ground-truth reads failed" in out and "!!!!" in out, out[-1500:])
    check("tg_unknown_unverified_no_ok_partial", "TCIN VISIBILITY: OK" not in out
          and "TCIN VISIBILITY: PARTIAL" not in out, out[-800:])
    # unverified beats WARNING too (lists are last-known, not a verdict)
    unv_inv = _state(now, conf, ["1000000001", "1000000002"], ["1000000003"], 900,
                     verified=False, streak=10, since=now - 300)
    out, vl = run("unknown_unverified_invisible", prods_plus, unv_inv)
    check("tg_unknown_unverified_invisible_verdict",
          vl.startswith("TCIN VISIBILITY: UNKNOWN --") and "could NOT verify" in vl, vl)
    check("tg_unknown_unverified_invisible_lists_shown", "INVISIBLE" in out and "1000000003" in out
          and "NOT monitored" in out and "1000000009" in out, out[-1500:])
    # verified=true explicitly -> normal OK path
    out, vl = run("ok_verified_true", prods, _state(now, conf, conf, [], 600, verified=True))
    check("tg_ok_verified_true_verdict", vl.startswith("TCIN VISIBILITY: OK --"), vl)

    # UNKNOWN (round 3): >30 enabled TCINs -> the unchunked ground-truth read
    # cannot succeed (RedSky 30/req cap); warn BEFORE reading any state
    many = [{"tcin": str(1000000100 + i), "name": f"m{i}", "enabled": True} for i in range(31)]
    many_conf = [p["tcin"] for p in many]
    out, vl = run("unknown_29", many, _state(now, many_conf, many_conf, [], 600))
    check("tg_unknown_29_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --")
          and "31 enabled TCINs > 30" in vl and "unchunked" in vl and "30/req" in vl, vl)
    check("tg_unknown_29_section", "TCIN visibility: 31 enabled TCINs > 30" in out, out[-1200:])
    check("tg_unknown_29_no_ok", "TCIN VISIBILITY: OK" not in out)
    # exactly 28 is fine
    ok28 = many[:28]
    ok28_conf = many_conf[:28]
    out, vl = run("ok_28", ok28, _state(now, ok28_conf, ok28_conf, [], 600))
    check("tg_ok_28_verdict", vl.startswith("TCIN VISIBILITY: OK --") and "all 28 enabled TCIN(s)" in vl, vl)
    # 31 enabled but only 30 configured in the state: the >30 gate wins regardless
    out, vl = run("unknown_29_state28", many, _state(now, ok28_conf, ok28_conf, [], 600))
    check("tg_unknown_29_state28_verdict", vl.startswith("TCIN VISIBILITY: UNKNOWN --")
          and "> 30" in vl, vl)


if __name__ == "__main__":
    test_first_sighting_alert_and_state()
    test_within_ttl_no_realert_force_write()
    test_realert_after_ttl()
    test_became_visible()
    test_env_flags()
    test_raising_callback_swallowed()
    test_last_seen_propagates()
    test_reader_helpers()
    test_debounce_grace()
    test_flap_bound()
    test_first_read_write()
    test_ground_truth_failure_streak()
    test_second_unknown_after_resume()
    test_ingest_stamps_last_seen()
    test_safe_print_and_stamp_order()
    test_order_and_no_sleep()
    test_write_atomic_and_updated_at()
    test_scripts_real_repo()
    test_scripts_sandbox_verdicts()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
