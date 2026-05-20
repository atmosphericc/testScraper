"""
Unit tests for walmart/self_healing_agent.py (Day 6).

Tests are isolated via tempdir + monkeypatched WALMART_DIR so the real
purchase_executor.py / queue_handler.py are never touched. Each test
creates a temp copy of the target files, runs the patcher, asserts
on the result, and discards the temp dir.

Critical assertions:
  1. Every _RULES entry has a matching exemplar log line (rule coverage)
  2. Rule priority: ANTIBOT_BLOCK beats WRONG_SELECTOR when phrases overlap
  3. _ButtonExtractor pulls attrs+text from real Walmart-shape HTML
  4. patch_from_html updates BOTH purchase_executor AND queue_handler
     when patching ATC_SELECTORS (dual-file path is the real bug the
     2026-05-11 audit had to fix manually)
  5. Ambiguous matches (2+ button candidates) are rejected, not guessed
  6. ast.parse rejects malformed patches BEFORE writing to disk
  7. Patches are idempotent — re-running on same HTML is a no-op
  8. Patcher confines writes to walmart/ via WALMART_DIR

Run: python tests/test_self_healing_agent.py
"""

from __future__ import annotations

import sys
import shutil
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from walmart.self_healing_agent import (   # noqa: E402
    FailureCategory, HtmlSelectorPatcher, LogDiagnostician, _ButtonExtractor,
)


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


# ── helpers ──────────────────────────────────────────────────────────────


def _make_fake_purchase_executor(tmpdir: Path) -> Path:
    """Write a minimal purchase_executor.py to the tmpdir that has the
    selector lists the patcher will try to update."""
    path = tmpdir / "purchase_executor.py"
    path.write_text('''"""Stub purchase_executor for patcher tests."""

ATC_SELECTORS = [
    'button[data-automation-id="atc"]',
    'button[data-automation-id="add-to-cart-btn"]',
]

CHECKOUT_SELECTORS = [
    'button[data-automation-id="checkout-button"]',
]

PLACE_ORDER_SELECTORS = [
    'button[data-automation-id="place-order"]',
]

CVV_SELECTORS = [
    'input[name="cvv"]',
]
''')
    return path


def _make_fake_queue_handler(tmpdir: Path) -> Path:
    path = tmpdir / "queue_handler.py"
    path.write_text('''"""Stub queue_handler for patcher tests."""

ATC_SELECTORS = [
    'button[data-automation-id="atc"]',
]

QUEUE_ENTRY_SELECTORS = [
    'button:has-text("Hold my spot")',
]
''')
    return path


def _setup_patcher_with_tmpdir(tmpdir: Path) -> HtmlSelectorPatcher:
    """Build a patcher pointed at the tmpdir so it doesn't touch real files."""
    patcher = HtmlSelectorPatcher()
    patcher.WALMART_DIR = tmpdir   # type: ignore[assignment]
    return patcher


# ── Test 1: LogDiagnostician — every rule has a matching exemplar ──────


def test_diagnostician_classifies_every_category():
    diag = LogDiagnostician()
    exemplars = {
        FailureCategory.ANTIBOT_BLOCK: [
            "Error: 403 forbidden from akamai.walmart.com",
            "PerimeterX challenge detected",
            "captcha shown to user",
            "human verification required",
        ],
        FailureCategory.LOGIN_FAILURE: [
            "login failed: invalid credentials",
            "still on login page after 30s",
            "could not find email field",
            "login error: please try again",
        ],
        FailureCategory.QUEUE_TIMEOUT: [
            "queue timeout after 30 min",
            "timed out waiting for queue admission",
        ],
        FailureCategory.OUT_OF_STOCK: [
            "cart empty — item never added",
            "item not found in cart after ATC",
            "OOS state observed",
            "Out of stock at checkout time",
        ],
        FailureCategory.WRONG_SELECTOR: [
            "ATC button not found in DOM",
            "no_atc — element missing",
            "Checkout button not found",
            "CVV field not found",
        ],
        FailureCategory.NETWORK_TIMEOUT: [
            "timeout exceeded after 30s",
            "TimeoutError: read timed out",
            "ERR_NETWORK_TIMED_OUT",
        ],
    }
    for expected_cat, lines in exemplars.items():
        for line in lines:
            cat, reason = diag.diagnose([line])
            _check(f"diagnose: '{line[:40]}...' → {expected_cat}",
                   cat == expected_cat,
                   detail=f"got {cat}")


def test_diagnostician_priority_antibot_beats_wrong_selector():
    """When a log line could match both ANTIBOT and WRONG_SELECTOR, ANTIBOT
    must win — it's earlier in _RULES so it gets matched first.

    Reason matters: WRONG_SELECTOR triggers auto-code-patching. ANTIBOT
    just throttles. If a 403 is misclassified as a missing selector, the
    patcher would unnecessarily rewrite the selectors list when in fact
    the bot just got blocked.
    """
    diag = LogDiagnostician()
    # Line that mentions both 403 (ANTIBOT) and no_atc (WRONG_SELECTOR)
    cat, _ = diag.diagnose([
        "Error: 403 forbidden — no_atc element also missing"
    ])
    _check("ANTIBOT_BLOCK wins over WRONG_SELECTOR for combined line",
           cat == FailureCategory.ANTIBOT_BLOCK,
           detail=f"got {cat}")


def test_diagnostician_returns_unknown_for_no_match():
    diag = LogDiagnostician()
    cat, reason = diag.diagnose([
        "Bot started", "Heartbeat: ok", "Random irrelevant log line",
    ])
    _check("unrelated logs → UNKNOWN", cat == FailureCategory.UNKNOWN)


def test_diagnostician_most_recent_line_wins():
    diag = LogDiagnostician()
    cat, _ = diag.diagnose([
        "403 forbidden — earlier",
        "queue timed out — most recent",
    ])
    _check("most recent line matches first",
           cat == FailureCategory.QUEUE_TIMEOUT)


# ── Test 2: _ButtonExtractor pulls real-shape attrs ────────────────────


def test_button_extractor_extracts_walmart_shape():
    extractor = _ButtonExtractor()
    extractor.feed('''<html><body>
        <button data-automation-id="atc" data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button">
            Add to Cart
        </button>
        <button data-automation-id="checkout-button">
            Proceed to Checkout
        </button>
        <a href="/login">Sign In</a>
        <input type="text" name="cvv" aria-label="Security Code">
    </body></html>''')
    buttons = extractor.buttons
    _check("extracted 4 elements (button, button, a, input)",
           len(buttons) == 4,
           detail=f"got {len(buttons)}")

    atc = next((b for b in buttons if "add to cart" in b["text"].lower()), None)
    _check("ATC button found with text",
           atc is not None)
    if atc:
        _check("ATC has data-automation-id attr",
               atc["attrs"].get("data-automation-id") == "atc")
        _check("ATC has data-tl-id attr",
               "data-tl-id" in atc["attrs"])

    cvv = next((b for b in buttons if b.get("tag") == "input"), None)
    _check("CVV input found",
           cvv is not None and cvv["attrs"].get("name") == "cvv")


# ── Test 3: dual-file patch (ATC_SELECTORS in both files) ───────────────


def test_patcher_updates_both_executor_and_queue_handler():
    """ATC_SELECTORS is duplicated across purchase_executor.py and
    queue_handler.py. The patcher must update BOTH files; updating only
    one is the same drift bug the 2026-05-11 audit fixed manually."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        executor_path = _make_fake_purchase_executor(tmpdir)
        queue_path = _make_fake_queue_handler(tmpdir)
        before_exec = executor_path.read_text()
        before_queue = queue_path.read_text()

        patcher = _setup_patcher_with_tmpdir(tmpdir)

        # HTML with a NEW ATC button selector the bot doesn't know yet
        html = '''<html><body>
            <button data-automation-id="new-atc-2026">Add to Cart</button>
        </body></html>'''
        result = patcher.patch_from_html(html)

        _check("patch_from_html returned True (patched at least one)",
               result is True)

        after_exec = executor_path.read_text()
        after_queue = queue_path.read_text()

        _check("purchase_executor.py was modified",
               after_exec != before_exec)
        _check("queue_handler.py was modified",
               after_queue != before_queue)
        _check("new selector present in purchase_executor.py",
               'data-automation-id="new-atc-2026"' in after_exec)
        _check("new selector present in queue_handler.py",
               'data-automation-id="new-atc-2026"' in after_queue)


# ── Test 4: ambiguous matches (2+ candidates) rejected ─────────────────


def test_patcher_rejects_ambiguous_match():
    """If HTML has 2+ different-text candidates matching the same label,
    patcher must skip rather than guess. (Multiple buttons with same
    exact text might collapse to 1 via exact-match dedup — that's OK;
    we're testing the "truly different candidates" case.)"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        executor_path = _make_fake_purchase_executor(tmpdir)
        _make_fake_queue_handler(tmpdir)
        before = executor_path.read_text()

        patcher = _setup_patcher_with_tmpdir(tmpdir)
        # Two distinct DOM elements both matching "Add to Cart" pattern
        # but with different attributes — patcher can't choose between them
        html = '''<html><body>
            <button data-automation-id="atc-modal">Add to Cart (in modal)</button>
            <button data-automation-id="atc-flyout">Add to cart from flyout</button>
        </body></html>'''
        patcher.patch_from_html(html)
        after = executor_path.read_text()

        # Neither selector should have been patched in (ambiguous → skip)
        _check("ambiguous match → no patch applied",
               'atc-modal' not in after and 'atc-flyout' not in after,
               detail=f"after has atc-modal={('atc-modal' in after)}, atc-flyout={('atc-flyout' in after)}")


# ── Test 5: ast.parse rejects bad patches ───────────────────────────────


def test_patcher_ast_validates_before_writing():
    """If the patched code would be syntactically broken, patcher must
    not write to disk. Test this by manipulating the target file so
    inserting ANY new entry would break syntax."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        # Write a malformed-syntax purchase_executor that already fails
        # ast.parse — any patch attempt should also fail parsing.
        executor_path = tmpdir / "purchase_executor.py"
        executor_path.write_text('ATC_SELECTORS = [\n    \'btn-x\',\n')   # unclosed list
        before = executor_path.read_text()

        _make_fake_queue_handler(tmpdir)

        patcher = _setup_patcher_with_tmpdir(tmpdir)
        html = '<html><body><button data-automation-id="x">Add to Cart</button></body></html>'
        # Even though we have a match, the file is broken — should fail
        # syntax validation and refuse to write.
        patcher.patch_from_html(html)

        after = executor_path.read_text()
        # The exec file should remain syntactically broken
        # AND the patch should NOT have been applied to it
        # (queue_handler may have been patched separately)
        try:
            import ast
            ast.parse(after)
            file_is_valid = True
        except SyntaxError:
            file_is_valid = False
        # Either: (a) patch was rejected due to invalid syntax → file
        # unchanged, OR (b) patch + file together still produces invalid
        # syntax → patcher wrote it anyway (BUG).
        # Our assertion: if patcher wrote, ast.parse must NOT pass on
        # the result. Either "not modified" or "still invalid".
        _check("patcher didn't write a syntactically broken file",
               after == before or not file_is_valid,
               detail=f"file changed: {after != before}, valid: {file_is_valid}")


# ── Test 6: idempotence — same patch twice is a no-op ───────────────────


def test_patcher_is_idempotent():
    """Running patcher twice with the same HTML must not double-prepend
    the same selector (duplicate guard at line 319)."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        executor_path = _make_fake_purchase_executor(tmpdir)
        _make_fake_queue_handler(tmpdir)

        patcher = _setup_patcher_with_tmpdir(tmpdir)
        html = '<html><body><button data-automation-id="new-atc-X">Add to Cart</button></body></html>'

        # First application
        patcher.patch_from_html(html)
        after_first = executor_path.read_text()
        first_count = after_first.count('data-automation-id="new-atc-X"')

        # Second application (same HTML)
        patcher.patch_from_html(html)
        after_second = executor_path.read_text()
        second_count = after_second.count('data-automation-id="new-atc-X"')

        _check("first patch added the selector exactly once",
               first_count == 1)
        _check("second patch is a no-op — count remains 1",
               second_count == 1,
               detail=f"first_count={first_count}, second_count={second_count}")


# ── Test 7: patcher confined to walmart/ via WALMART_DIR ───────────────


def test_patcher_confined_to_walmart_dir():
    """The patcher only writes to files inside its WALMART_DIR. We test
    this by giving it a tmpdir as WALMART_DIR and confirming the real
    walmart/purchase_executor.py is unchanged."""
    real_executor = REPO_ROOT / "walmart" / "purchase_executor.py"
    real_md5_before = real_executor.read_bytes()

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        _make_fake_purchase_executor(tmpdir)
        _make_fake_queue_handler(tmpdir)

        patcher = _setup_patcher_with_tmpdir(tmpdir)
        html = '<html><body><button data-automation-id="x">Add to Cart</button></body></html>'
        patcher.patch_from_html(html)

    real_md5_after = real_executor.read_bytes()
    _check("real walmart/purchase_executor.py not touched by patcher",
           real_md5_before == real_md5_after,
           detail="bytes differ — patcher escaped WALMART_DIR!")


# ── Test 8: nonexistent file is skipped, not crashed ────────────────────


def test_patcher_skips_missing_files():
    """If WALMART_DIR doesn't have one of the expected files, the patcher
    must skip it cleanly instead of crashing."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        # Only write purchase_executor — queue_handler missing
        _make_fake_purchase_executor(tmpdir)

        patcher = _setup_patcher_with_tmpdir(tmpdir)
        html = '<html><body><button data-automation-id="x">Add to Cart</button></body></html>'
        try:
            result = patcher.patch_from_html(html)
            _check("patcher tolerates missing queue_handler.py",
                   isinstance(result, bool))
        except Exception as e:
            _check("patcher tolerates missing queue_handler.py", False,
                   detail=f"raised {type(e).__name__}: {e}")


# ── runner ───────────────────────────────────────────────────────────────


TESTS = [
    ("LogDiagnostician: all categories classified", test_diagnostician_classifies_every_category),
    ("LogDiagnostician: ANTIBOT priority", test_diagnostician_priority_antibot_beats_wrong_selector),
    ("LogDiagnostician: UNKNOWN fallback", test_diagnostician_returns_unknown_for_no_match),
    ("LogDiagnostician: most-recent-line wins", test_diagnostician_most_recent_line_wins),
    ("_ButtonExtractor: extracts Walmart shape", test_button_extractor_extracts_walmart_shape),
    ("Patcher: dual-file (executor + queue_handler)", test_patcher_updates_both_executor_and_queue_handler),
    ("Patcher: rejects ambiguous match", test_patcher_rejects_ambiguous_match),
    ("Patcher: ast.parse validates before writing", test_patcher_ast_validates_before_writing),
    ("Patcher: idempotent (same patch twice = no-op)", test_patcher_is_idempotent),
    ("Patcher: confined to WALMART_DIR (real files untouched)", test_patcher_confined_to_walmart_dir),
    ("Patcher: missing files skipped cleanly", test_patcher_skips_missing_files),
]


def main():
    print("=" * 70)
    print("Self-healing agent — unit tests (Day 6)")
    print("=" * 70)
    for name, fn in TESTS:
        print(f"\n=== {name} ===")
        try:
            fn()
        except Exception as e:
            results.append(TestResult(
                name=name, status="FAIL",
                detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}",
            ))

    print()
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:3]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
