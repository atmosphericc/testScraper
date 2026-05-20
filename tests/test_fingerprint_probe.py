"""
Fingerprint probe — Day 5.

Launches zendriver Chrome through the sim, loads the fingerprint probe
page, lets it run all the standard bot-detection checks, and asserts
the launch flags + zendriver patches are tight enough that PerimeterX
wouldn't immediately flag us.

The probe page (walmart/sim/fixtures/fingerprint_probe.html) runs:
  navigator.webdriver, plugins, languages, userAgent, WebGL renderer,
  chrome.runtime, cdc_* leaks, Notification.permission, screen dims,
  hardwareConcurrency, deviceMemory, Function.prototype.toString tampering,
  performance.now precision, iframe contentWindow.navigator.webdriver

Critical asserts (catch-anything-PerimeterX-would-catch):
  1. navigator.webdriver !== true
  2. navigator.plugins.length > 0
  3. navigator.languages non-empty
  4. WebGL renderer is NOT SwiftShader
  5. No cdc_* property leaks
  6. iframe contentWindow.navigator.webdriver !== true

Non-critical (record but don't fail): chrome.runtime presence, screen
dims, perf precision — these vary legitimately by environment.

Run: python tests/test_fingerprint_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import urllib3   # noqa: E402
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from walmart.sim.test_harness import WalmartSimHarness   # noqa: E402


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


# ── scenario ─────────────────────────────────────────────────────────────


async def run_probe_and_assert(h: WalmartSimHarness):
    """Load the probe page through real Chrome via sim; wait for the POST
    back to /__sim__/fp_report; read the report; assert."""
    h.ctl.reset()

    # Navigate to the probe URL — sim serves the HTML, embedded JS runs
    # in Chrome, POSTs results back to /__sim__/fp_report.
    await h.tab.get("https://www.walmart.com/__fp_probe__/")

    # Poll the sim for up to 5s waiting for the report to arrive
    deadline = time.monotonic() + 5.0
    reports = []
    while time.monotonic() < deadline:
        reports = h.ctl.get_fp_reports()
        if reports:
            break
        await asyncio.sleep(0.2)

    _check("probe POSTed report back to sim", len(reports) > 0,
           detail=f"sim got {len(reports)} reports")
    if not reports:
        return

    fp = reports[-1]
    print(f"\n--- raw FP report ---\n{json.dumps(fp, indent=2)[:1500]}\n---\n")

    # ── CRITICAL CHECKS ──────────────────────────────────────────────────
    # These would IMMEDIATELY fail us at PerimeterX scoring even with
    # a perfect queue handler.

    _check("navigator.webdriver !== true",
           fp.get("webdriver") is not True,
           detail=f"got {fp.get('webdriver')!r}")

    _check("navigator.plugins.length > 0 (headless leaks 0)",
           (fp.get("plugins_length") or 0) > 0,
           detail=f"got {fp.get('plugins_length')}")

    _check("navigator.languages non-empty",
           (fp.get("languages_count") or 0) > 0,
           detail=f"languages={fp.get('languages')}")

    _check("userAgent doesn't contain 'headless'",
           "headless" not in (fp.get("ua") or "").lower(),
           detail=f"ua first 80: {(fp.get('ua') or '')[:80]}")

    # WebGL: SwiftShader = software renderer = headless without GPU.
    # Real Chrome on macOS reports "Apple GPU", "Intel Iris", etc.
    renderer = fp.get("webgl_renderer") or ""
    _check("WebGL renderer is NOT SwiftShader",
           "SwiftShader" not in renderer,
           detail=f"renderer={renderer!r}")

    _check("no cdc_* property leaks (ChromeDriver fingerprint)",
           len(fp.get("cdc_leak_keys") or []) == 0,
           detail=f"cdc keys: {fp.get('cdc_leak_keys')}")

    _check("iframe contentWindow.navigator.webdriver !== true",
           fp.get("iframe_webdriver") is not True,
           detail=f"got {fp.get('iframe_webdriver')!r}")

    _check("documentElement has no webdriver attribute",
           not fp.get("document_has_chrome_flag"),
           detail=f"got {fp.get('document_has_chrome_flag')!r}")

    # ── INFORMATIONAL — don't fail, but log to surface drift ────────────
    print(f"\n--- Informational ---")
    print(f"  chrome.runtime defined: {fp.get('has_chrome_runtime')}")
    print(f"  screen: {fp.get('screen_w')}x{fp.get('screen_h')}")
    print(f"  inner: {fp.get('inner_w')}x{fp.get('inner_h')}")
    print(f"  hardwareConcurrency: {fp.get('hardware_concurrency')}")
    print(f"  deviceMemory: {fp.get('device_memory')}")
    print(f"  performance.now() precision: {fp.get('performance_now_precision')}ms")
    print(f"  Function.toString native: {fp.get('fn_tostring_native')}")
    print(f"  notification permission: {fp.get('notification_permission')}")

    # Aggregate failure list from the probe's own assessment
    failures = fp.get("_failures") or []
    print(f"\n  Probe self-assessment: {len(failures)} failure(s): {failures}")

    # CRITICAL: zero failures expected from the probe's own list
    _check("probe self-assessment: no critical failures",
           len(failures) == 0,
           detail=f"failures: {failures}")


# ── runner ───────────────────────────────────────────────────────────────


async def run_test():
    async with WalmartSimHarness(headless=True) as h:
        await run_probe_and_assert(h)


def main():
    print("=" * 70)
    print("Fingerprint probe — Day 5")
    print("=" * 70)
    try:
        asyncio.run(run_test())
    except Exception as e:
        results.append(TestResult(
            name="harness", status="FAIL",
            detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:600]}",
        ))

    print()
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:4]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
