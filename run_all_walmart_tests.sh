#!/usr/bin/env bash
#
# run_all_walmart_tests.sh — single-command sweep across every Walmart test suite.
#
# Run before every commit + before every drop. Fails fast on any non-zero
# exit; reports per-suite pass counts and total runtime.
#
# Total runtime budget: under 5 minutes. If this exceeds, profile + slim
# down the longest suite (likely the E2E sim-based ones).
#
# Usage:
#   bash run_all_walmart_tests.sh
#   bash run_all_walmart_tests.sh --quick     # skip the slower E2E suites
#

set -uo pipefail

# Resolve to repo root so the script works from any cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-$SCRIPT_DIR/venv/bin/python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    PYTHON="python3"
fi

# ── color codes (turn off if not a tty) ──────────────────────────────────
if [ -t 1 ]; then
    GREEN=$'\033[0;32m'
    RED=$'\033[0;31m'
    YELLOW=$'\033[0;33m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    GREEN='' RED='' YELLOW='' BOLD='' RESET=''
fi

QUICK=0
if [ "${1:-}" = "--quick" ]; then
    QUICK=1
fi

# ── test suites in order ─────────────────────────────────────────────────
# fast pure-Python first (fail-fast on logic regressions before paying
# the multi-second cost of spawning sim + Chrome)
FAST_SUITES=(
    "test_walmart_framework_unit.py"
    "test_walmart_queue_handler.py"
    "tests/test_apq_fallback.py"
    "tests/test_pie_encryption.py"
    "tests/test_stock_edge_cases.py"
    "tests/test_self_healing_agent.py"
    "tests/test_session_concurrency.py"
    "tests/test_mouse_trajectory_stats.py"
    "tests/test_keystroke_stats.py"
)

# Slow suites: spawn mitmproxy subprocess + (sometimes) real Chrome
SLOW_SUITES=(
    "tests/test_mitm_sim_smoke.py"
    "tests/test_walmart_queue_e2e_mitm.py"
    "tests/test_checkout_api_against_sim.py"
    "tests/test_e2e_hybrid_checkout.py"
    "tests/test_fingerprint_probe.py"
    "tests/test_walmart_e2e_purchase_with_queue.py"
)

# ── runner ───────────────────────────────────────────────────────────────
TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SUITES=()
START_TIME=$(date +%s)

run_suite() {
    local suite="$1"
    local name
    name="$(basename "$suite")"

    if [ ! -f "$suite" ]; then
        echo "${YELLOW}[skip]${RESET} $name (file not found)"
        return
    fi

    local t0
    t0=$(date +%s)
    echo "${BOLD}── $name ──${RESET}"

    # Capture output; pull the "Results: X passed, Y failed" line
    local output
    output=$("$PYTHON" "$suite" 2>&1)
    local rc=$?

    # Parse the final results line
    local results_line
    results_line=$(echo "$output" | grep -E "^Results: " | tail -1)

    local elapsed=$(( $(date +%s) - t0 ))

    if [ $rc -eq 0 ]; then
        local pass_count
        pass_count=$(echo "$results_line" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | head -1)
        pass_count="${pass_count:-0}"
        TOTAL_PASS=$((TOTAL_PASS + pass_count))
        echo "${GREEN}  ✓ ${name}${RESET}  (${pass_count} passed, ${elapsed}s)"
    else
        local fail_count
        fail_count=$(echo "$results_line" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | head -1)
        fail_count="${fail_count:-1}"
        TOTAL_FAIL=$((TOTAL_FAIL + fail_count))
        FAILED_SUITES+=("$name")
        echo "${RED}  ✗ ${name}${RESET}  (${fail_count} failed, ${elapsed}s, rc=$rc)"
        # Show last 15 lines of output for diagnosis
        echo "${YELLOW}--- last 15 lines ---${RESET}"
        echo "$output" | tail -15 | sed 's/^/    /'
        echo "${YELLOW}--- end ---${RESET}"
    fi
}

echo "${BOLD}Walmart bot — full test sweep${RESET}"
echo "Python: $($PYTHON --version)"
echo "Repo:   $SCRIPT_DIR"
echo ""
echo "${BOLD}== Fast suites (pure Python) ==${RESET}"
for s in "${FAST_SUITES[@]}"; do
    run_suite "$s"
done

if [ $QUICK -eq 0 ]; then
    echo ""
    echo "${BOLD}== Slow suites (sim + Chrome) ==${RESET}"
    for s in "${SLOW_SUITES[@]}"; do
        run_suite "$s"
    done
else
    echo ""
    echo "${YELLOW}== Slow suites SKIPPED (--quick) ==${RESET}"
fi

# ── summary ──────────────────────────────────────────────────────────────
END_TIME=$(date +%s)
TOTAL_ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "${BOLD}═════════════════════════════════════════════════════════${RESET}"
echo "${BOLD}Summary${RESET}"
echo "${BOLD}═════════════════════════════════════════════════════════${RESET}"
echo "Total passed:  ${GREEN}${TOTAL_PASS}${RESET}"
if [ ${#FAILED_SUITES[@]} -eq 0 ]; then
    echo "Total failed:  ${GREEN}0${RESET}"
else
    echo "Total failed:  ${RED}${TOTAL_FAIL}${RESET}"
    echo "Failed suites:"
    for s in "${FAILED_SUITES[@]}"; do
        echo "  ${RED}✗${RESET} $s"
    done
fi
echo "Total runtime: ${TOTAL_ELAPSED}s"

if [ $TOTAL_ELAPSED -gt 300 ]; then
    echo "${YELLOW}WARNING: runtime exceeded 5-minute budget${RESET}"
fi

if [ ${#FAILED_SUITES[@]} -eq 0 ]; then
    exit 0
else
    exit 1
fi
