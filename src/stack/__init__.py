"""
src/stack — retailer-agnostic resilient stock-monitoring framework.

The framework provides the infrastructure pieces (multi-session Chrome pool,
local CONNECT forwarder, per-IP state tracking, dispatcher, sweep orchestrator)
that any retailer adapter can plug into.

Retailer-specific behavior (endpoints, response parsing, anti-bot tolerances,
cookie freshness rules, checkout flow) is provided by a RetailerAdapter
implementation — see `retailer_adapter.py`.

Walmart's adapter lives in `walmart/walmart_adapter.py`.
Target continues to use `src/session/`, `src/proxy/`, `src/monitoring/` as
its existing legacy implementation; the framework here is a parallel,
generalized version that Walmart (and future retailers) will use.

When Target eventually migrates onto this framework, its existing modules
can be removed and a `TargetAdapter` plugged in alongside Walmart's.
"""
