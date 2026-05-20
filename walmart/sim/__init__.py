"""
walmart/sim — stateful Walmart simulator (mitmproxy-based).

Replaces real Walmart traffic with deterministic, scripted responses so the
queue handler, hybrid checkout, APQ fallback, and PIE.js paths can be tested
end-to-end on a laptop with NO real Walmart calls.

Key design choices (vs prior CDP Fetch interception attempts):
  - mitmproxy serves real TCP/TLS → Chrome's `Network.responseReceived`
    events fire normally. The QueueHandler's CDP listener (which previously
    couldn't be exercised through Fetch interception) is observable here.
  - State machine in `state.py` advances ticket states deterministically
    after N polls so admission/eviction scenarios are reproducible.
  - All routes match what walmart_live_queue_capture.py logs against real
    Walmart, so the harness is reusable for both sim and live diagnostics.

Routes:
  /ip/<id>                                        → IN_STOCK / OOS / third-party HTML
  /qp*                                            → queue interstitial
  api.waiting-room.walmart.com/{issueTicket,
      checkTicket, refreshTicket, validateTickets} → ticket API JSON
  /orchestra/cartxo/graphql/<op>/<hash>           → checkout mutations + APQ
  securedataweb.walmart.com/pie/v1/.../getkey.js  → PIE key JS
  /api/checkout-customer/encrypted-pan            → PIE-encrypted CVV submit

Usage:
  # From a test:
  from walmart.sim.server import SimServer
  sim = SimServer(port=8089)
  await sim.start()
  # ... configure state, run bot against 127.0.0.1:8089 ...
  await sim.stop()

See walmart/sim/README.md for fixture format + Chrome CA trust steps.
"""
