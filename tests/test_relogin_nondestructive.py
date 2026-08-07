#!/usr/bin/env python3
"""Smoke test: non-destructive credential-relogin escalation + live-bot guard.

2026-08-04 20:24: the sentinel escalated primary to a credential re-login. The
flow signs out FIRST (clears cookies+storage), then logs in — but the scripted
login is Shape-burned (died at "username field NOT found"), so a DEGRADED-but-
present session became NO session: target.json left at 24KB vs 56-57KB for the
untouched accounts. One escalation all night, after the drop window, so it cost
no units — but mid-drop it would zero an account.

Pins:
  - source contract: the jar is snapshotted BEFORE full_signout and restored
    when login fails; the success path deletes the snapshot and is untouched
  - restore/cleanup semantics against a real temp file (no browser, no network)
  - relogin_one live-bot guard: refuses while app.py runs, is record-based
    (a multi-line `python -c` mentioning "app.py" must not self-trigger),
    fails open, and never blocks the bot's own in-process self-heal (which
    calls full_signout/login directly, not main())

No browser, no network. Run: python tests/test_relogin_nondestructive.py
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SM = (ROOT / "src" / "session" / "session_manager.py").read_text(encoding="utf-8")
RL = (ROOT / "relogin_one.py").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def test_snapshot_precedes_signout():
    # The copy MUST happen before full_signout — snapshotting after the signout
    # would preserve the already-cleared jar and defeat the whole fix.
    i_snap = SM.find("pre-signout snapshot failed")
    i_copy = SM.find("_shutil.copy2(str(self.session_path), str(_bak))")
    i_sig = SM.find("await _relogin.full_signout(tab)")
    check("snapshot_copy_before_full_signout", -1 < i_copy < i_sig)
    check("snapshot_error_is_handled", -1 < i_snap < i_sig)


def test_restore_only_on_failure():
    m = re.search(r"if not ok and _bak is not None:[\s\S]{0,400}?"
                  r"_shutil\.copy2\(str\(_bak\), str\(self\.session_path\)\)", SM)
    check("restore_gated_on_login_failure", m is not None)
    m2 = re.search(r"if ok and _bak is not None:[\s\S]{0,200}?_bak\.unlink\(\)", SM)
    check("snapshot_cleaned_up_on_success", m2 is not None)
    check("kill_switch_present", "TARGET_RELOGIN_RESTORE_ON_FAIL" in SM)


def test_success_path_untouched():
    # The success branch must still flip session_active and save — the fix is
    # additive, not a rewrite of the recovery semantics.
    m = re.search(r"if ok:\s*\n\s*self\.session_active = True[\s\S]{0,300}?"
                  r"await self\.save_session_state\(\)", SM)
    check("success_path_still_saves", m is not None)


def test_restore_semantics_on_real_file():
    # Exercise the copy/restore contract itself against a real file.
    with tempfile.TemporaryDirectory() as d:
        jar = Path(d) / "target.json"
        jar.write_text("GOOD-SESSION" * 100, encoding="utf-8")
        good = jar.read_text(encoding="utf-8")
        bak = jar.with_suffix(jar.suffix + ".presignout")
        shutil.copy2(str(jar), str(bak))
        # simulate full_signout destroying the jar, then a failed login
        jar.write_text("{}", encoding="utf-8")
        check("jar_destroyed_by_signout", jar.read_text(encoding="utf-8") != good)
        shutil.copy2(str(bak), str(jar))
        check("restore_recovers_original_bytes", jar.read_text(encoding="utf-8") == good)
        bak.unlink()
        check("snapshot_removable", not bak.exists())


def test_live_bot_guard_contract():
    check("guard_refuses", "REFUSING: app.py is already running" in RL)
    check("guard_has_override", "--allow-while-running" in RL)
    # Record-based parsing: one line per process, pid split off, so the
    # exclusions can't land on the wrong fragment of a multi-line command.
    check("guard_is_record_based",
          'pid, _, cmd = line.partition("\\t")' in RL and "ProcessId" in RL)
    check("guard_excludes_self_pid", "pid.strip() == me" in RL)
    check("guard_excludes_relogin", '"relogin_one" not in low' in RL)
    # Fails open: any detection error returns False rather than blocking.
    m = re.search(r"except Exception:\s*\n\s*return False", RL)
    check("guard_fails_open", m is not None)


def test_guard_cannot_block_in_app_selfheal():
    # session_manager calls the module functions directly; the guard lives in
    # main(). If the bot ever called main(), the guard would deadlock its own
    # recovery — pin that it does not.
    check("selfheal_calls_full_signout_directly", "_relogin.full_signout(tab)" in SM)
    check("selfheal_calls_login_directly", "_relogin.login(tab, username, password)" in SM)
    check("selfheal_does_not_call_main", "_relogin.main(" not in SM)
    m = re.search(r"async def main\(\)[\s\S]{0,4000}?REFUSING: app\.py is already running", RL)
    check("guard_lives_inside_main", m is not None)


if __name__ == '__main__':
    test_snapshot_precedes_signout()
    test_restore_only_on_failure()
    test_success_path_untouched()
    test_restore_semantics_on_real_file()
    test_live_bot_guard_contract()
    test_guard_cannot_block_in_app_selfheal()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
