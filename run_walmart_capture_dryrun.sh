#!/usr/bin/env bash
# ===========================================================================
#  run_walmart_capture_dryrun.sh
# ---------------------------------------------------------------------------
#  Wednesday CAPTURE DRY-RUN launcher. Runs the full Walmart flow against the
#  real drop — monitor, enter the real queue, ATC, build checkout — with ALL
#  capture on, but NEVER places an order. The goal is DATA, not a purchase:
#  come back with the real queue ticket shape, the live GraphQL persisted-query
#  hashes, the real queue timing, and exactly where the flow breaks.
#
#  Why a dry-run and not a buy: you're new and have no real-drop data yet. The
#  checkout path has never been validated against real Walmart, so a buy attempt
#  would almost certainly 0-for anyway. This run turns Wednesday into the
#  capture that MAKES the next attempt real. (See docs/DROP_DAY_PLAYBOOK.md.)
#
#  SAFETY: FINAL_PURCHASE=NO — the executor runs everything up to the place-order
#  gate, then screenshots + clears the cart and stops (purchase_executor.py:301).
#  It cannot place an order in this mode. Passive checkout capture still records
#  the GraphQL hashes from Walmart's own page traffic.
#
#  Usage:
#     ./run_walmart_capture_dryrun.sh
#  Stop: Ctrl+C.
#  Windows equivalent (set the same 5 vars, then `python -m walmart.walmart_app`):
#     set CHECKOUT_MODE=PRODUCTION & set FINAL_PURCHASE=NO
#     set WALMART_QUEUE_CAPTURE=1 & set WALMART_CAPTURE_CHECKOUT=1
#     set WALMART_CHECKOUT_API=1  & python -m walmart.walmart_app
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

# Pick the venv python that has the deps (this box uses venv/, no dot).
PY="./venv/bin/python"
[ -x "$PY" ] || PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"

# --- Pre-flight gate: refuse to launch with a hard blocker unfixed ----------
echo "Running pre-flight check first..."
if ! "$PY" walmart_preflight.py; then
    echo
    echo "Pre-flight found STOP blocker(s) above. Fix them, then re-run."
    echo "(Most common on a fresh setup: put creds in .env + run walmart_relogin.py.)"
    exit 1
fi

# --- Capture dry-run environment -------------------------------------------
# CHECKOUT_MODE=PRODUCTION  -> run past the TEST-mode early stop, into checkout
# FINAL_PURCHASE=NO         -> but STOP at the place-order gate (never buys)
export CHECKOUT_MODE=PRODUCTION
export FINAL_PURCHASE=NO

# Resilient multi-session stack (races all seeded sessions through the queue).
export WALMART_USE_RESILIENT=1

# Capture everything:
#   queue layer (/qp redirect + api.waiting-room ticket bodies + timeline)
export WALMART_QUEUE_CAPTURE=1
export WALMART_QUEUE_EVENTLOG=1
#   checkout HTTP layer (the GraphQL /orchestra calls that carry the live hashes)
export WALMART_CAPTURE_CHECKOUT=1
#   use the hybrid API checkout path so our own updateItems/getSlots/reserveSlot
#   POSTs fire (and get captured) up to — but not including — place-order.
export WALMART_CHECKOUT_API=1

# Match the seeded profile count (only s1/s2 are seeded; see preflight).
export WALMART_RESILIENT_NUM_CHROMES="${WALMART_RESILIENT_NUM_CHROMES:-2}"
export WALMART_RESILIENT_RPS="${WALMART_RESILIENT_RPS:-1.0}"

TS="$(date +%Y%m%d_%H%M%S)"
echo
echo "==========================================================================="
echo "  WALMART CAPTURE DRY-RUN  —  $TS"
echo "  CHECKOUT_MODE=PRODUCTION  FINAL_PURCHASE=NO  (WILL NOT BUY)"
echo "  capturing: queue_events + checkout_capture  ->  logs/"
echo "  Chromes=$WALMART_RESILIENT_NUM_CHROMES  rps=$WALMART_RESILIENT_RPS"
echo "==========================================================================="
echo "  After the drop, analyze what was captured with:"
echo "     $PY analyze_queue_capture.py"
echo "==========================================================================="
echo

exec "$PY" -m walmart.walmart_app
