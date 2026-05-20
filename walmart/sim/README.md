# walmart/sim — Stateful Walmart Simulator

A mitmproxy-based simulator that lets the Walmart bot exercise its full
queue-handler, hybrid-checkout, APQ fallback, and PIE.js code paths against
deterministic, scripted responses — **without** making any real Walmart
network calls.

## Why this exists

Prior CDP-Fetch-interception mocks bypassed Chrome's Network domain, which
meant the `Network.responseReceived` listener path in `walmart/queue_handler.py`
was never actually exercised end-to-end. A real TCP/TLS proxy fixes that
categorically: every request and response flows through Chrome's normal
network stack, and CDP events fire as they would against real Walmart.

This unblocks:
- Queue handler E2E (Day 2)
- APQ fallback validation (Day 3)
- Hybrid checkout pipeline (Day 3-4)
- PIE.js CVV encryption (Day 4)
- Statistical antibot validators (Day 5)

## Quick start

```bash
# 1. Run the smoke test (validates everything below works):
python tests/test_mitm_sim_smoke.py

# 2. Run the sim manually:
python -m walmart.sim.server
# Sim is now listening on http://127.0.0.1:8089

# 3. Test a route via curl (-k accepts self-signed mitmproxy cert):
curl -k --proxy http://127.0.0.1:8089 https://www.walmart.com/ip/12345
curl -k --proxy http://127.0.0.1:8089 \
  "https://api.waiting-room.walmart.com/checkTicket?queue=qa484c0ebd7014"
```

## Architecture

```
                 ┌─────────────────────┐
   Chrome  ───►  │  mitmdump subproc   │  ───► sim addon decides route
  (or curl)     │  on 127.0.0.1:8089  │       (no real Walmart hit)
                 └─────────────────────┘
                       │
                       ▼
       walmart/sim/mitm_addon.py — URL pattern matchers
                       │
                       ▼
       walmart/sim/state.py — per-test state machine
```

**Three Python modules:**
- `state.py` — `SimState`, `QueueScenario`, `HashScenario` dataclasses;
  the brain that decides "what should this endpoint return right now?"
- `mitm_addon.py` — mitmproxy hook (`def request(flow)`) that dispatches
  by URL pattern and produces responses
- `server.py` — `SimServer` class; spawns `mitmdump` as a subprocess and
  exposes a clean start/stop API for tests

**Subprocess isolation:** mitmdump runs in a child process. Its addon
holds state in a module-level singleton (`SIM_STATE`), but that singleton
lives in the SUBPROCESS, not the test process. For Day 1, all tests rely
on default state (items default to OOS, queues auto-create with defaults).
Day 2+ will add a `/__sim__/` control endpoint so tests can script
behavior in flight.

## Fixture files

All in `walmart/sim/fixtures/`:

| File | Purpose |
|---|---|
| `ip_in_stock.html` | PDP HTML with IN_STOCK availabilityStatus + walmart_direct seller |
| `ip_oos.html` | PDP HTML with OUT_OF_STOCK + showAtc=false |
| `ip_third_party.html` | PDP HTML with showAtc=true but sellerId is NOT WALMART_SELLER_ID — parser should treat as OOS |
| `qp_pending.html` | Queue interstitial; embedded JS polls checkTicket every 2s |
| `ticket_pending.json` | Real-shape ticket response (verbatim from matthew7j2014/walmart-queue-tracker README) |
| `ticket_valid.json` | Admitted state |
| `ticket_expired.json` | Evicted state |
| `ticket_unlikely.json` | Pending with admissionLikelihood=unlikely (used for early-bail tests) |
| `graphql_apq_miss.json` | `extensions.code == "PERSISTED_QUERY_NOT_FOUND"` (Apollo Client #10253 sentinel) |
| `getkey.js` | Minimal PIE.js public-key blob (Day 4 will replace with real test keypair) |

## Routes the addon handles

| Pattern | Action |
|---|---|
| `https://www.walmart.com/ip/<id>` | Serve HTML based on `SIM_STATE.get_item(id)` — IN_STOCK / OOS / 3P, or 302 → `/qp` if QUEUED |
| `https://www.walmart.com/qp*` | Serve `qp_pending.html` (queue interstitial with polling JS) |
| `https://api.waiting-room.walmart.com/{issueTicket,checkTicket,refreshTicket,validateTickets}` | Build real-shape JSON via `build_ticket_response(scenario)`; `validateTickets` wraps in `{tickets: [...]}` array |
| `https://www.walmart.com/orchestra/<svc>/graphql/<op>/<hash>` | If hash known OR request has full `query` body → canned success; otherwise APQ miss (`PERSISTED_QUERY_NOT_FOUND`) |
| `https://securedataweb.walmart.com/pie/v1/<bundle>/getkey.js` | Serve PIE pubkey JS |
| `/api/checkout-customer/*` | Accept PIE-encrypted CVV submission, return `{cardToken: "test"}` |
| Anything else | Pass through (logged) |

## Using the sim from tests

For Day 1 (default-state-only):

```python
from walmart.sim.server import SimServer
import requests

sim = SimServer(port=8089)
sim.start()
try:
    r = requests.get(
        "https://www.walmart.com/ip/12345",
        proxies={"http": sim.proxy_url, "https": sim.proxy_url},
        verify=False,
    )
    assert r.status_code == 200
finally:
    sim.stop()
```

For Day 2+ (Chrome + CDP listener):

```python
from walmart.sim.server import SimServer
import zendriver as uc

sim = SimServer()
sim.start()
config = uc.Config(
    browser_args=[
        f"--proxy-server={sim.proxy_url}",
        "--ignore-certificate-errors",   # accept mitmproxy self-signed cert
    ],
)
browser = await uc.start(config)
tab = await browser.get("https://www.walmart.com/ip/12345")
# ... your test code; QueueHandler's CDP listeners now fire for real ...
await browser.stop()
sim.stop()
```

## Trusting the mitmproxy CA cert (production-grade tests)

`--ignore-certificate-errors` is fine for local tests. For tests that
specifically validate certificate-related behavior (rare), trust the cert:

```bash
# Generate the cert (mitmproxy does this on first run):
mitmdump -s walmart/sim/mitm_addon.py --listen-port 8089 &
sleep 2
kill %1

# Cert lives at ~/.mitmproxy/mitmproxy-ca-cert.pem
# Add to macOS system trust:
sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain \
    ~/.mitmproxy/mitmproxy-ca-cert.pem
```

## Adding a new fixture from a real Walmart capture

When you opportunistically capture real Walmart traffic via
`walmart_live_queue_capture.py` (one-shot diagnostic that runs through a
real BD proxy + bootstrapped session), the resulting JSONL contains real
response bodies. To turn one into a fixture:

```bash
# 1. Find the JSONL from your most recent live capture:
ls -lt walmart/logs/live_capture_*.jsonl | head -1

# 2. Extract the response body of interest:
python3 -c "
import json
for line in open('walmart/logs/live_capture_19922854775_20260520_134500.jsonl'):
    rec = json.loads(line)
    if rec.get('_kind') == 'response_body' and 'checkTicket' in rec.get('url',''):
        print(rec['body'][:2000])
        break
" | jq . > walmart/sim/fixtures/ticket_real_capture.json

# 3. Wire into state.py if it's a new shape variant.
```

## Adding a new route

1. Add a regex to `mitm_addon.py` near the existing `_RE_*` patterns
2. Add a `_route_xxx(flow)` handler that returns an `http.Response`
3. Dispatch from the main `request(flow)` hook
4. Add at least one assertion to `tests/test_mitm_sim_smoke.py`
5. Run the smoke test — expect green

## Limitations (known)

- **Subprocess state isolation**: tests can't write to `SIM_STATE` directly
  because it lives in the mitmdump child. Day 2 will add a `/__sim__/state`
  HTTP endpoint that the addon serves so tests can POST scenario configs.
- **No HTTPS upstream validation**: mitmproxy uses `--ssl-insecure` to avoid
  cert validation against fake upstream. This is fine for laptop testing,
  not appropriate for production traffic.
- **No HTTP/2 timing fidelity**: Chrome will negotiate HTTP/2 with mitmproxy
  but the multiplexing characteristics differ from real Walmart's CDN.
  Fine for functional tests; not appropriate for performance benchmarks.

## Day-by-day usage in the plan

| Day | What uses the sim |
|---|---|
| Day 1 | Build it; smoke test (this README + `test_mitm_sim_smoke.py`) |
| Day 2 | `test_walmart_queue_e2e_mitm.py` — drive QueueHandler against sim |
| Day 3 | `test_apq_fallback.py` — force hash rotation, validate retry path |
| Day 4 | `test_e2e_hybrid_checkout.py` — full pipeline incl. PIE encryption |
| Day 5 | `fingerprint_probe.html` route — assert Chrome fingerprint clean |
| Day 6 | Indirectly via `test_self_healing_agent.py` failure injection |
| Day 7 | `walmart_live_queue_capture.py --dry-run` validates capture via sim |
