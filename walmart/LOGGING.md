# Walmart Logging System

## Overview
Walmart now has a comprehensive logging infrastructure matching Target's structure, enabling the failure-forensics agent to diagnose and fix issues with full visibility into what happened during each purchase attempt.

## Log Files

### 1. **Error Log** — `walmart/logs/error_log.txt`
- **Purpose**: Central error/exception log
- **Format**: `[YYYY-MM-DD HH:MM:SS] [CATEGORY] error message` + full traceback
- **Used for**: Tracking exceptions, assertion failures, API errors
- **Example**:
  ```
  [2026-04-08 14:32:15] [purchase_executor] Unexpected error during purchase for 15042474261: Timeout waiting for Place Order button
  Traceback (most recent call last):
    File "walmart/purchase_executor.py", line 295, in purchase
      ...
  ```

### 2. **Activity Log** — `walmart/logs/activity_log.pkl`
- **Purpose**: Serialized activity history (Python pickle format)
- **Structure**: List of dicts with keys:
  - `timestamp`: ISO format timestamp
  - `message`: Activity description
  - `level`: `INFO`, `WARNING`, `ERROR`
  - `category`: `PURCHASE`, `MANAGER`, `MONITOR`, `SESSION`, etc.
  - `time_str`: Human-readable time (HH:MM:SS)
  - `date_str`: Human-readable date (YYYY-MM-DD)
  - `full_time`: Combined timestamp
- **Used for**: Dashboard activity feed, timeline reconstruction
- **Example**:
  ```python
  {
    'timestamp': '2026-04-08T14:32:15.123456',
    'message': 'Purchase attempt started for item 15042474261',
    'level': 'INFO',
    'category': 'PURCHASE',
    'time_str': '14:32:15',
    'date_str': '2026-04-08',
    'full_time': '2026-04-08 14:32:15'
  }
  ```

### 3. **Purchase States** — `walmart/logs/purchase_states.json`
- **Purpose**: Current state of each item in the purchase pipeline
- **Structure**: JSON dict mapping item_id → state object
- **State fields**:
  - `status`: `ready` | `queued` | `attempting` | `success` | `failure`
  - `timestamp`: ISO timestamp of last state change
  - `attempt_count`: Number of attempts made
  - `final_outcome`: `purchased` | `oos` | `timeout` | `error` | `unknown` | `test_mode` | `dry_run` | etc.
  - `last_error`: Error message if failed, `null` if success
  - `order_id`: Walmart order ID if purchased, `null` otherwise
- **Used for**: Preventing double-purchases, race condition detection, state machine debugging
- **Example**:
  ```json
  {
    "15042474261": {
      "status": "success",
      "timestamp": "2026-04-08T14:35:22.456789",
      "attempt_count": 1,
      "final_outcome": "purchased",
      "last_error": null,
      "order_id": "WM-12345678-001"
    },
    "15052637492": {
      "status": "failure",
      "timestamp": "2026-04-08T14:33:45.123456",
      "attempt_count": 1,
      "final_outcome": "timeout",
      "last_error": "Queue timeout",
      "order_id": null
    }
  }
  ```

### 4. **Per-Purchase Logs** — `walmart/logs/purchases/purchase_*.log`
- **Purpose**: Detailed execution trace for a single purchase attempt
- **Naming**: `purchase_{item_id}_{YYYYMMDD_HHMMSS}.log`
- **Content**: All stdout from purchase_executor (mirrors Target's approach using log tee)
- **Used for**: Step-by-step debugging, understanding where a purchase failed
- **Example filename**: `purchase_15042474261_20260408_143215.log`
- **Example content**:
  ```
  [2026-04-08 14:32:15] [PURCHASE] Starting purchase attempt for 15042474261
  [PURCHASE] Navigating to product page...
  [NAVIGATE] Loaded https://www.walmart.com/ip/Pokemon-Bundle/15042474261
  [PURCHASE] Checking for virtual queue...
  [QUEUE] No queue detected
  [PURCHASE] Attempting Add to Cart...
  [ATC] Clicked button: button[data-automation-id="add-to-cart-btn"]
  [ATC] Confirmed in cart
  [PURCHASE] Proceeding to checkout...
  ...
  ```

## Logging API

### Core Functions (in `walmart/logging_manager.py`)

```python
from walmart.logging_manager import (
    get_walmart_logger,
    log_error,
    log_activity,
    log_purchase_state,
    get_purchase_state,
)

# Get global logger instance
logger = get_walmart_logger()

# Log an error
log_error(category="purchase_executor", message="CVV entry failed", exception=e)

# Log an activity
log_activity(message="Item added to cart", category="PURCHASE")

# Update purchase state
log_purchase_state(item_id="15042474261", state={
    'status': 'success',
    'timestamp': datetime.now().isoformat(),
    'attempt_count': 1,
    'final_outcome': 'purchased',
    'last_error': None,
    'order_id': 'WM-12345678-001'
})

# Retrieve current state
state = get_purchase_state(item_id="15042474261")
print(state['status'])  # 'success'
```

### WalmartLogger Class Methods

```python
logger = get_walmart_logger()

# Log error with full traceback
logger.log_error(
    category="session_manager",
    message="Login failed",
    exception=e  # optional, auto-traceback if provided
)

# Log activity
logger.log_activity(
    message="Session initialized",
    category="SESSION",
    level="INFO"  # default: "INFO"
)

# Manage purchase states
logger.log_purchase_state(item_id, state_dict)
state = logger.get_purchase_state(item_id)

# Create per-purchase log file
log_path = logger.create_purchase_log(item_id, timestamp_str)
# Use with _WalmartPurchaseLogTee to capture stdout

# Get recent activity
recent = logger.get_activity_log(limit=50)

# Clear activity (use with caution)
logger.clear_activity_log()
```

## Modules with Logging

Each Walmart module has been updated to import and use the logging system:

| Module | Logger Usage | Key Events Logged |
|--------|--------------|-------------------|
| `purchase_executor.py` | Per-purchase log tee + activity + state | ATC, checkout, CVV, Place Order, successes/failures |
| `purchase_manager.py` | Activity + error | Manager events, state transitions, circuit breaker |
| `session_manager.py` | Error + activity | Session init, login, warmup, validation |
| `stock_monitor.py` | Activity + error | Stock checks, in-stock signals, API errors |
| `blueprint.py` | Activity | Flask route events, dashboard interactions |
| `queue_handler.py` | Activity | Queue detection, entry, pass-through |
| `self_healing_agent.py` | Error + activity + patch log | Failure diagnosis, selector patching, restarts |
| `proxy_manager.py` | Error + activity | Proxy rotations, health checks |

## Failure-Forensics Integration

When a purchase fails, the `failure-forensics` agent can:

1. **Read the error log** for exceptions and root causes
2. **Check purchase states** for race conditions or stuck states
3. **Review per-purchase logs** for step-by-step trace
4. **Query activity history** for timeline of events
5. **Identify patterns** across multiple failures

### Example Troubleshooting Workflow

```python
# Agent reads error log
with open('walmart/logs/error_log.txt') as f:
    errors = f.readlines()

# Agent checks state for stuck items
import json
with open('walmart/logs/purchase_states.json') as f:
    states = json.load(f)
    stuck = [id for id, s in states.items() if s['status'] == 'attempting']

# Agent reads per-purchase log to see exact failure
with open('walmart/logs/purchases/purchase_15042474261_20260408_143215.log') as f:
    trace = f.read()
    # Find: "CVV entry failed: Timeout waiting for input element"

# Agent makes targeted fix:
# - If selector issue: update CVV_SELECTORS in purchase_executor.py
# - If timing issue: increase timeout in config
# - If antibot issue: recommend proxy rotation
```

## Log Rotation & Cleanup

**Current behavior** (manual):
- `error_log.txt` grows unbounded (append mode)
- `activity_log.pkl` is rewritten on every activity
- Purchase state file is rewritten on every purchase
- Per-purchase logs accumulate in `purchases/` directory

**Recommendation for future**:
- Implement log rotation (max 1MB per file)
- Archive old logs to `walmart/logs/archive/`
- Add a cleanup task to remove purchase logs > 7 days old

## Testing the Logging System

Run a test purchase and verify logs are created:

```bash
# Ensure test directories exist
ls -la walmart/logs/

# Run a purchase (test mode)
CHECKOUT_MODE=TEST python walmart/purchase_executor.py

# Check logs
tail -20 walmart/logs/error_log.txt
python -c "import pickle; data = pickle.load(open('walmart/logs/activity_log.pkl', 'rb')); print(f'Latest: {data[-1][\"message\"]}')"
cat walmart/logs/purchase_states.json | jq .
ls -la walmart/logs/purchases/
```

## Log Retention Policy

| Log | Retention | Auto-Cleanup |
|-----|-----------|--------------|
| error_log.txt | Indefinite | Manual archive needed |
| activity_log.pkl | Indefinite | Manual cleanup needed |
| purchase_states.json | Indefinite | Needed after successful purchase |
| purchase_*.log | 30 days | Manual or cron job |

## Integration with Failure Agent

When invoking `failure-forensics` agent:

```
User: "Check walmart/logs/ and fix the issue that caused the last purchase to fail"

Agent will:
1. Read walmart/logs/error_log.txt for exceptions
2. Check walmart/logs/purchase_states.json for stuck items
3. Find most recent purchase_*.log in walmart/logs/purchases/
4. Diagnose root cause
5. Write fix (selector patch, timeout increase, etc.) to walmart/ files
6. Update walmart/LOGGING.md with what was fixed
```

## Fields in Purchase State

Each item in `purchase_states.json` should track:

```python
{
    'status': 'ready|queued|attempting|success|failure',
    'timestamp': '2026-04-08T14:35:22.456789',  # ISO format
    'attempt_count': 1,  # Number of purchase attempts made
    'final_outcome': (
        'purchased'      # Successfully placed order
        | 'oos'          # Out of stock
        | 'timeout'      # Exceeded timeout (queue/navigation)
        | 'atc_failed'   # Add to cart failed
        | 'checkout_nav_failed'  # Could not navigate to checkout
        | 'cart_verify_failed'   # Item not confirmed in cart
        | 'antibot'      # Anti-bot block detected
        | 'error'        # Unexpected exception
        | 'unknown'      # Unknown failure
        | 'test_mode'    # Stopped before Place Order (test mode)
        | 'dry_run'      # Stopped before Place Order (dry run)
    ),
    'last_error': 'Error message or null if success',
    'order_id': 'WM-12345678-001 or null'
}
```

## Summary

The Walmart logging system is now at **feature parity** with Target's logging:

✅ Error log — `walmart/logs/error_log.txt`
✅ Activity log — `walmart/logs/activity_log.pkl`
✅ Purchase states — `walmart/logs/purchase_states.json`
✅ Per-purchase logs — `walmart/logs/purchases/purchase_*.log`
✅ Structured logging API — `walmart/logging_manager.py`
✅ Integration across all modules
✅ Ready for failure-forensics agent diagnosis

Tell the failure-forensics agent to look in `walmart/logs/` and it will know exactly where to find what it needs to troubleshoot and fix issues.
