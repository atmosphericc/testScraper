#!/usr/bin/env python3
"""Unit tests for src/session/fp_chromium.py — the flag-gated fingerprint-chromium
seam. NO browser is launched. This proves the OFF path is a strict no-op and the
ON path produces distinct, deterministic, per-account launch overrides.

Run: venv/Scripts/python.exe tests/test_fp_chromium.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session import fp_chromium as fp  # noqa: E402

_PASS = 0
_FAIL = 0


def check(cond: bool, label: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}")


def _clear_env() -> None:
    os.environ.pop("TARGET_FP_CHROMIUM", None)
    os.environ.pop("TARGET_FP_CHROMIUM_PATH", None)
    fp.reset_cache()


def main() -> int:
    _clear_env()  # start from a known-clean env

    # A fake chrome.exe lets us exercise "enabled + binary present" without the
    # real 190MB build. Both a top-level and a nested copy (for the dir-probe test).
    tmpdir = tempfile.mkdtemp(prefix="fp_test_")
    fake_exe = Path(tmpdir) / "chrome.exe"
    fake_exe.write_text("stub")
    nested = Path(tmpdir) / "nested"
    nested.mkdir()
    (nested / "chrome.exe").write_text("stub")

    print("[1] fingerprint_seed: deterministic, distinct, 31-bit")
    check(fp.fingerprint_seed("primary") == fp.fingerprint_seed("primary"), "same account -> same seed")
    seeds = {fp.fingerprint_seed(a) for a in ("primary", "business", "alt-1")}
    check(len(seeds) == 3, "distinct accounts -> distinct seeds")
    check(all(1 <= fp.fingerprint_seed(a) < 2 ** 31 for a in ("primary", "x", "")), "seed in [1, 2^31)")

    print("[2] build_args content")
    seed = fp.fingerprint_seed("primary")
    args = fp.build_args("primary", "America/Chicago")
    check(f"--fingerprint={seed}" in args, "carries --fingerprint=<seed>")
    check("--fingerprint-platform=windows" in args, "platform=windows")
    check("--fingerprint-brand=Chrome" in args, "brand=Chrome")
    check("--accept-lang=en-US" in args, "accept-lang=en-US")
    check("--timezone=America/Chicago" in args, "timezone flag when tz given")
    check(all(not a.startswith("--timezone=") for a in fp.build_args("primary", None)),
          "no timezone flag when tz is None")

    print("[3] OFF path is a strict no-op")
    _clear_env()
    check(fp.is_enabled() is False, "is_enabled False when env unset")
    check(fp.launch_overrides("primary", "America/Chicago") == (None, []), "launch_overrides -> (None, [])")
    check(fp.profile_dir("nodriver-profile") == "nodriver-profile", "profile_dir unchanged")

    print("[4] enabled but NO binary -> safe fallback (still a no-op)")
    _clear_env()
    os.environ["TARGET_FP_CHROMIUM"] = "1"
    os.environ["TARGET_FP_CHROMIUM_PATH"] = str(Path(tmpdir) / "missing" / "chrome.exe")
    fp.reset_cache()
    check(fp.is_enabled() is True, "is_enabled True")
    check(fp.executable_path() is None, "executable_path None for bogus path")
    check(fp.launch_overrides("primary") == (None, []), "launch_overrides falls back to (None, [])")
    check(fp.profile_dir("nodriver-profile") == "nodriver-profile", "profile_dir unchanged without a binary")

    print("[5] enabled + explicit binary -> overrides + -fp profile")
    _clear_env()
    os.environ["TARGET_FP_CHROMIUM"] = "1"
    os.environ["TARGET_FP_CHROMIUM_PATH"] = str(fake_exe)
    fp.reset_cache()
    exe, largs = fp.launch_overrides("business", "America/Chicago")
    check(exe == str(fake_exe.resolve()), "launch exe resolves to the binary")
    check(f"--fingerprint={fp.fingerprint_seed('business')}" in largs, "launch args carry the business seed")
    check(fp.profile_dir("nodriver-profile-2") == "nodriver-profile-2-fp", "profile gets -fp suffix")
    check(fp.profile_dir("nodriver-profile-2-fp") == "nodriver-profile-2-fp", "profile remap is idempotent")

    print("[6] enabled + binary via DIRECTORY (rglob finds chrome.exe)")
    _clear_env()
    os.environ["TARGET_FP_CHROMIUM"] = "1"
    os.environ["TARGET_FP_CHROMIUM_PATH"] = str(tmpdir)
    fp.reset_cache()
    resolved = fp.executable_path()
    check(bool(resolved) and resolved.endswith("chrome.exe"), "dir path resolves to a chrome.exe")

    print("[7] truthy / falsy env variants")
    for v in ("1", "true", "TRUE", "yes", "on"):
        _clear_env(); os.environ["TARGET_FP_CHROMIUM"] = v; fp.reset_cache()
        check(fp.is_enabled() is True, f"is_enabled True for {v!r}")
    for v in ("0", "false", "no", "off", ""):
        _clear_env(); os.environ["TARGET_FP_CHROMIUM"] = v; fp.reset_cache()
        check(fp.is_enabled() is False, f"is_enabled False for {v!r}")

    _clear_env()
    shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
