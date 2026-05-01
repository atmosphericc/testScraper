# Speed Optimization Checklist for testScraper
**Date**: 2026-04-30  
**Priority**: Phased implementation (Phase 1 = critical, Phase 2 = speed, Phase 3 = behavioral)

---

## PHASE 1: Critical Improvements (2-3 hours)
**Estimated Speed Gain**: +2-3 seconds | **Reliability Gain**: +5-10%

### Task 1.1: Auto-Detect & Refresh Stale GraphQL Hash

**File**: `walmart/stock_monitor.py`

**Changes**:
```python
# Line ~100 (in check_stock() method)
# Add 400-error detection:

response = fetch_graphql_query(query, hash=self.graphql_hash)
if response.status == 400 and "invalid" in response.text.lower():
    log.warning(f"GraphQL 400 error - hash likely stale. Attempting refresh...")
    await self._discover_graphql_hash()  # Already exists
    response = fetch_graphql_query(query, hash=self.graphql_hash)
    log.info(f"GraphQL hash refreshed to {self.graphql_hash}")
```

**Test**:
- Force stale hash in config
- Monitor should detect 400 and auto-refresh
- Verify log output shows "GraphQL hash refreshed"

**Success Metric**: Zero silent 400 errors; hash auto-updates within 1 request

---

### Task 1.2: Monitor `_abck` Cookie Age

**File**: `walmart/session_manager.py`

**Changes**:
```python
# In __init__ or new method _check_abck_freshness():
import base64
import time

def _is_abck_stale(self, max_age_seconds=600):  # 10 minutes
    """Check if _abck cookie is older than max_age_seconds."""
    try:
        cookies = self.browser.cookies
        abck_cookie = next((c for c in cookies if c.name == '_abck'), None)
        if not abck_cookie:
            log.warn("_abck cookie not found")
            return True
        
        # _abck format: base64_data~timestamp
        parts = abck_cookie.value.split('~')
        if len(parts) < 2:
            return True
        
        created_time = int(parts[-1]) // 1000  # Convert to seconds
        age = time.time() - created_time
        
        if age > max_age_seconds:
            log.warn(f"_abck is stale: {age}s old (max {max_age_seconds}s)")
            return True
        else:
            log.debug(f"_abck age: {age}s, OK")
            return False
    except Exception as e:
        log.error(f"Error checking _abck age: {e}")
        return False

# Call before checkout:
# In walmart/purchase_executor.py, before _confirm_shipping():
if self.session_manager._is_abck_stale():
    log.info("Forcing re-login due to stale _abck")
    await self.session_manager._relogin()
```

**Test**:
- Create test case: set _abck to 15+ minutes old
- System should detect and force re-login
- Verify logs show "Forcing re-login"

**Success Metric**: Zero 403 Akamai blocks due to stale `_abck`

---

### Task 1.3: Reduce `_wait_for_page_ready()` Timeout

**File**: `walmart/purchase_executor.py`

**Changes**:
```python
# Line 268 (in _navigate method)
# Change from:
await self._wait_for_page_ready(timeout=13000)
# To:
await self._wait_for_page_ready(timeout=8000)

# Reason: Buybox hydration cap is 7-10s observed; 13s is over-protective
# Validate: Update FAILURES.md 2026-04-10 note to reflect new timeout
```

**Test**:
- Monitor `_wait_for_page_ready` exit times across 10 navigation attempts
- Should exit at 2-5s (React hydration complete)
- Occasional 7-8s (slow network) acceptable
- Log output: "Page ready in X.Xs — ATC button found via: button:has-text(...)"

**Success Metric**: Average exit time 3-4s; no timeout events

---

## PHASE 2: Speed Optimizations (4-6 hours)
**Estimated Speed Gain**: +2-3 seconds | **Reliability Impact**: +2-5% (minor improvement)

### Task 2.1: Implement Tab Pre-warming Pool

**File**: `walmart/session_manager.py`

**Design**:
```
TabPool:
  - 3 Active Tabs: Tab 1 (auth/session), Tab 2A, Tab 2B, Tab 2C (pre-warmed to product page)
  - 2 Standby Tabs: Tab 2D, Tab 2E (being initialized in background)
  - Rotation: On purchase, close Tab 2A, spin new Tab 2D → Tab 2E → Tab 2D
  - Warm Path: Home → Search Product → Product Page (Stop: don't go to cart/checkout)
```

**Implementation Steps**:

1. **Add TabPool class**:
```python
class TabPool:
    def __init__(self, size=3, standby_size=2, browser=None):
        self.active_tabs = []
        self.standby_tabs = []
        self.size = size
        self.standby_size = standby_size
        self.browser = browser
    
    async def initialize_pool(self):
        """Spin up all active + standby tabs."""
        for _ in range(self.size + self.standby_size):
            tab = await self._create_and_warmup_tab()
            if len(self.active_tabs) < self.size:
                self.active_tabs.append(tab)
            else:
                self.standby_tabs.append(tab)
    
    async def get_tab(self):
        """Pop from active, add standby to active, spin new standby."""
        if not self.active_tabs:
            await self.initialize_pool()
        tab = self.active_tabs.pop(0)
        if self.standby_tabs:
            self.active_tabs.append(self.standby_tabs.pop(0))
        asyncio.create_task(self._spin_standby())
        return tab
    
    async def _create_and_warmup_tab(self):
        """Create tab and warm to product page."""
        tab = await self.browser.new_tab()
        await tab.goto(walmart_home_url)
        # Simulate search for first product
        await tab.fill("input[placeholder='Search']", "pokemon card")
        await tab.press("input[placeholder='Search']", "Enter")
        await tab.wait_for_selector("a[data-automation-id='search-result']")
        first_result = await tab.query_selector("a[data-automation-id='search-result']")
        await first_result.click()
        await tab.wait_for_load_state("networkidle")
        return tab
    
    async def _spin_standby(self):
        """Create new standby tab in background."""
        new_tab = await self._create_and_warmup_tab()
        self.standby_tabs.append(new_tab)
```

2. **Integrate into WalmartSessionManager**:
```python
class WalmartSessionManager:
    def __init__(self, ...):
        ...
        self.tab_pool = TabPool(size=3, standby_size=2, browser=self.browser)
    
    async def initialize(self):
        ...
        await self.tab_pool.initialize_pool()
    
    async def get_checkout_tab(self):
        """Return pre-warmed tab 2."""
        return await self.tab_pool.get_tab()
```

3. **Update WalmartPurchaseExecutor**:
```python
# In _navigate() or execute():
# Replace: tab = self.session_manager.browser_tab_2
# With: tab = await self.session_manager.get_checkout_tab()
```

**Test**:
- Monitor tab startup latency: should be ~200-300ms (already hydrated) vs 3-5s (cold start)
- Verify no "stale session" errors on reused tabs
- Confirm tab pool maintains 3 active, 2 standby at all times

**Success Metric**: 60-70% faster tab startup (3-5s → 200-300ms); zero tab reuse errors

---

### Task 2.2: GraphQL Query Batching

**File**: `walmart/stock_monitor.py`

**Design**:
- Batch 5-10 product stock checks into single GraphQL POST
- Accumulate checks over small time window (5-10ms)
- Batch on timeout or when batch size reached

**Implementation**:
```python
class BatchedStockMonitor:
    def __init__(self, batch_size=5, batch_timeout_ms=10):
        self.pending_checks = []
        self.batch_size = batch_size
        self.batch_timeout_ms = batch_timeout_ms
        self.batch_timer = None
    
    async def check_stock_batched(self, product_id):
        """Queue a stock check for batching."""
        self.pending_checks.append(product_id)
        
        if len(self.pending_checks) >= self.batch_size:
            await self._flush_batch()
        elif not self.batch_timer:
            self.batch_timer = asyncio.create_task(
                asyncio.sleep(self.batch_timeout_ms / 1000),
                self._flush_batch()
            )
    
    async def _flush_batch(self):
        """Execute batched query."""
        if not self.pending_checks:
            return
        
        batch = self.pending_checks[:self.batch_size]
        self.pending_checks = self.pending_checks[self.batch_size:]
        
        # Build single GraphQL query for all products
        batch_query = self._build_batch_graphql(batch)
        results = await self._fetch_batch(batch_query)
        
        # Dispatch results to listeners
        for product_id, status in results.items():
            await self._notify_stock_change(product_id, status)
        
        self.batch_timer = None
        if self.pending_checks:
            await self._flush_batch()
    
    def _build_batch_graphql(self, product_ids):
        """Build multi-product GraphQL query."""
        products_arg = ",".join([f'{{id:"{pid}"}}' for pid in product_ids])
        return f"""
        query {{
            productDetails(products: [{products_arg}]) {{
                id
                inventory {{ available }}
            }}
        }}
        """
```

**Test**:
- Queue 10 checks, verify they batch into 2 queries (not 10)
- Measure latency: batch should be 2x faster than individual
- Verify no checks lost during batching

**Success Metric**: 200-300ms savings per monitor cycle; 1x-2x faster stock check

---

### Task 2.3: Session Reuse (If Time Permits)

**File**: `walmart/session_manager.py`

**Quick Win**: Don't log out at end of purchase; save session to disk
- Next purchase: reload cookies, skip login (saves ~3-5s)
- Risk: Account gets locked if same device logs in too quickly
- Mitigation: Force re-login every 10th purchase or after 30 min idle

---

## PHASE 3: Behavioral Realism (3-4 hours)
**Estimated Speed Gain**: 0s (pure behavioral noise) | **Detection Rate Reduction**: -5-15%

### Task 3.1: Randomize Stock Monitor Timing

**File**: `walmart/stock_monitor.py`

**Changes**:
```python
# Line 32 (CHECK_INTERVAL definition)
# From:
CHECK_INTERVAL = 1.0

# To:
CHECK_INTERVAL = 1.0 * random.uniform(0.85, 1.15)  # ±15% variation

# Also randomize per-product dispatch stagger (lines 258-266)
# From:
offsets = [0.0, 0.333, 0.666, 1.0, ...]

# To:
base_offsets = [0.0, 0.333, 0.666, 1.0, ...]
offsets = [offset + random.uniform(-0.05, 0.05) for offset in base_offsets]
```

**Reason**: Akamai detects machine-perfect timing (1.0s interval every time = bot signal)

**Test**:
- Run monitor for 30 checks, graph actual intervals
- Should see ±15% variance (0.85-1.15s range)
- No visual pattern (not linear, not sinusoidal)

**Success Metric**: Interval variance >10%; no detectable pattern

---

### Task 3.2: Randomize Warmup Tab Site Order

**File**: `walmart/session_manager.py` (in `_initialize_warmup_tab()`)

**Changes**:
```python
# From fixed list:
warmup_sites = [
    'https://google.com',
    'https://amazon.com',
    'https://reddit.com',
    'https://youtube.com',
    'https://ebay.com'
]
for site in warmup_sites:
    await tab.goto(site)

# To shuffled + subset:
all_sites = [
    'https://google.com', 'https://amazon.com', 'https://reddit.com',
    'https://youtube.com', 'https://ebay.com', 'https://github.com',
    'https://twitter.com', 'https://instagram.com'
]
warmup_sites = random.sample(all_sites, k=random.randint(3, 5))
random.shuffle(warmup_sites)

for site in warmup_sites:
    await tab.goto(site)
    await tab.wait_for_load_state("networkidle")
```

**Reason**: Same site order every session = machine-generated pattern

**Test**:
- Run 10 sessions, log warmup site sequence
- All 10 should be different
- Order should be random, not just shuffled

**Success Metric**: All warmup sequences unique; no pattern

---

### Task 3.3: Randomize Modal Dismiss Coordinates

**File**: `walmart/purchase_executor.py`

**Changes**:
```python
# Line 1263 (in _handle_delivery_day_modal or similar)
# From:
await tab.mouse_move(512, 400)

# To:
random_x = random.randint(400, 700)
random_y = random.randint(300, 500)
await tab.mouse_move(random_x, random_y)
```

**Reason**: Fixed coordinates = bot fingerprint

**Test**:
- Dismiss 10 modals, log all coordinates
- All should be different
- All should be within (400-700, 300-500) range

**Success Metric**: No coordinate repeats in 10 trials

---

### Task 3.4: Add Pre-Click Mouse Movement (Optional, +500ms Cost)

**File**: `walmart/purchase_executor.py` (all click operations)

**Setup**:
```bash
pip install human-mouse
```

**Usage**:
```python
from human_mouse import bezier_mouse_move

# Before ATC click:
await bezier_mouse_move(
    start_x=current_mouse_x,
    end_x=atc_button_x,
    start_y=current_mouse_y,
    end_y=atc_button_y,
    duration=random.uniform(0.3, 0.6),
    jitter=True
)
await atc_button.click()
```

**Impact**: +500ms per click (not recommended unless detection spike observed)

**Test**: Only if detection rate increases >15%

---

## ROLLOUT PLAN

### Week 1: Phase 1 (Critical)
- Monday-Tuesday: Implement tasks 1.1-1.3
- Wednesday: Integration testing
- Thursday-Friday: Staging deployment

### Week 2: Phase 2 (Speed)
- Monday-Wednesday: Implement tab pool (2.1) + batching (2.2)
- Thursday: Integration testing
- Friday: Staging deployment

### Week 3: Phase 3 (Behavioral)
- Monday-Tuesday: Implement randomization tasks (3.1-3.3)
- Wednesday: Testing + tuning
- Thursday-Friday: Deploy + monitor

---

## SUCCESS METRICS

### Speed
- Baseline: 15-20s per purchase
- Target: 10-15s per purchase (Phase 1 + 2)
- Stretch: 8-12s per purchase (Phase 1 + 2 + optimizations)

### Reliability
- Baseline: 5-10% detection rate
- Target: 2-5% detection rate (Phase 1 + 3)
- Stretch: <2% detection rate (all phases + residential proxies)

### Latency (per component)
- Stock detection: 1.0s ± 15% (instead of fixed 1.0s)
- Page load: 2-3s (unavoidable browser startup)
- React hydration: 3-5s (normal for Next.js)
- ATC click: 0.5-1.0s (with modal handling)
- Checkout flow: 4-6s (with delivery modal, payment form)
- **Total**: 11-17s realistic

---

## MONITORING CHECKLIST

After each phase, collect metrics:

```python
# Log checkout metrics
log_metrics = {
    "stock_detected": time.time(),
    "browser_ready": time.time(),
    "atc_clicked": time.time(),
    "cart_verified": time.time(),
    "checkout_page_loaded": time.time(),
    "payment_filled": time.time(),
    "order_submitted": time.time(),
    "confirmed": time.time(),
    
    "total_time": confirmed - stock_detected,
    "success": True/False,
    "detection_signal": None/str (403, modal_timeout, etc)
}

# Aggregate across 100 purchases:
# - P50 latency, P95, P99
# - Success rate by phase
# - Detection errors breakdown
```

---

**End of Checklist**
