"""
Walmart bot dashboard — standalone Flask app on port 5001.

Does NOT import anything from app.py or src/ — fully self-contained.
Runs alongside the Target dashboard (port 5000) without conflict.

Endpoints:
  GET  /                     Dashboard UI
  GET  /api/stream           SSE real-time updates
  GET  /api/status           Current state JSON
  POST /add-product          Add item ID to walmart_config.json
  POST /remove-product/<id>  Remove product
  POST /api/test/enable      Enable test mode
  POST /api/test/disable     Disable test mode
  GET  /health               Health check
"""

import asyncio
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from html import escape as _he
from flask import Flask, Response, jsonify, request, stream_with_context

from walmart.config import get_config, save_config, get_enabled_products
from walmart.purchase_manager import WalmartPurchaseManager

# Resilient stack imports are lazy — only loaded when WALMART_USE_RESILIENT=1.
# Importing them eagerly would pull `src.stack.*` and adapter machinery into
# the default single-Chrome path for no benefit.

# Setup logging with both console and file output
log_dir = Path(__file__).parent.parent / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"walmart_app_{time.strftime('%Y%m%d_%H%M%S')}.log"

formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
# WALMART_LOG_LEVEL=DEBUG surfaces _clear_cart removal counts, cookie deltas,
# and other normally-suppressed diagnostics from the walmart package. Defaults
# to INFO. Note: the ROOT logger is always pinned at INFO so third-party libs
# (zendriver, urllib, asyncio, hpack, hyper) don't flood the console with
# DEBUG noise — only `walmart.*` loggers respect WALMART_LOG_LEVEL.
_log_level_name = os.environ.get("WALMART_LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Silence specific noisy third-party loggers that emit DEBUG/INFO spam during
# normal operation. zendriver's chrome-path discovery alone produces ~100 lines.
for noisy in ("zendriver", "urllib3", "asyncio", "websockets", "hpack", "hyper"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# delay=True so the file is only created when the first log line is written —
# prevents 0-byte log files when the process is killed during import / startup
# before any logger.info() has fired.
file_handler = logging.FileHandler(log_file, delay=True)
file_handler.setFormatter(formatter)
file_handler.setLevel(_log_level)

# Attach to the `walmart` package root so every submodule logger
# (walmart.purchase_manager, walmart.purchase_executor, walmart.session_manager,
# walmart.stock_monitor, etc.) writes to the log file via propagation.
walmart_pkg_logger = logging.getLogger("walmart")
walmart_pkg_logger.addHandler(file_handler)
walmart_pkg_logger.setLevel(_log_level)

logger = logging.getLogger(__name__)

# Named loggers used by legacy modules that don't follow the walmart.* hierarchy
for name in ["MANAGER", "PURCHASE", "SESSION", "MONITOR", "PROXY"]:
    log = logging.getLogger(name)
    log.addHandler(file_handler)
    log.setLevel(_log_level)

# Ensure CHECKOUT_MODE env var matches the default _test_mode=False (LIVE).
# Executor checks `CHECKOUT_MODE == "PRODUCTION"` to gate Place Order — "LIVE"
# is just the UI label; the executor's non-test branch is named PRODUCTION.
os.environ.setdefault("CHECKOUT_MODE", "PRODUCTION")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_manager: WalmartPurchaseManager = None
_manager_loop: asyncio.AbstractEventLoop = None
_resilient_checker = None  # ResilientChecker | None — set when WALMART_USE_RESILIENT=1
_test_mode = False  # default to LIVE

# Per-client SSE queues
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _broadcast(event_type: str, data: dict):
    """Push an SSE event to all connected dashboard clients."""
    payload = json.dumps({"type": event_type, "data": data, "ts": time.time()})
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(f"data: {payload}\n\n")
            except queue.Full:
                dead.append(q)
        for q in dead:
            if q in _sse_clients:
                _sse_clients.remove(q)


def _status_callback(message: str):
    """Called by purchase manager for every status update — broadcasts to SSE."""
    logger.info(message)
    _broadcast("activity", {"message": message, "time": time.strftime("%H:%M:%S")})


def _stock_update_callback(item_id: str, in_stock: bool, price):
    """Called by stock monitor on every stock state change — broadcasts structured SSE event."""
    _broadcast("stock_update", {
        "item_id": item_id,
        "in_stock": in_stock,
        "price": price,
        "time": time.strftime("%H:%M:%S"),
    })


# ---------------------------------------------------------------------------
# Manager startup
# ---------------------------------------------------------------------------

def _start_manager():
    """Launch the WalmartPurchaseManager in a background thread.

    `WALMART_USE_RESILIENT=1` swaps the manager's internal single-Chrome
    stock monitor for the multi-session ResilientChecker (Phase 2 cutover).
    The manager still owns login, warmup, session, purchase, and circuit-
    breaker; only the stock-detection layer is replaced.
    """
    global _manager, _manager_loop, _resilient_checker

    email = os.environ.get("WALMART_EMAIL", "")
    password = os.environ.get("WALMART_PASSWORD", "")

    if not email or not password:
        logger.warning(
            "[APP] WALMART_EMAIL or WALMART_PASSWORD not set — bot will start "
            "but purchases will fail. Set env vars and restart."
        )

    use_resilient = os.environ.get("WALMART_USE_RESILIENT", "").strip() in ("1", "true", "yes")

    _manager = WalmartPurchaseManager(
        status_callback=_status_callback,
        external_stock_monitor=use_resilient,
    )
    _manager.set_stock_update_callback(_stock_update_callback)
    _manager_loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(_manager_loop)
        try:
            _manager_loop.run_until_complete(_manager.start())
        except Exception:
            logger.exception("[APP] Manager start() failed — bot will not run")
            return

        if use_resilient and _manager._login_ok:
            # Login + warmup completed inside _manager.start(). Now bring up
            # the resilient checker on the same loop and bridge its
            # ItemStatus signals into the manager's purchase pipeline.
            try:
                _manager_loop.run_until_complete(_start_resilient_checker())
                logger.info("[APP] Resilient stock checker started — single-Chrome monitor disabled")
            except Exception:
                logger.exception("[APP] Resilient checker start() failed — bot has no monitor")
                return

        _manager_loop.run_forever()

    t = threading.Thread(target=_run, daemon=True, name="WalmartManagerThread")
    t.start()
    mode = "resilient stack" if use_resilient else "single Chrome"
    logger.info("[APP] Walmart purchase manager started in background (%s)", mode)


async def _start_resilient_checker():
    """Build + start the resilient stack and bridge its on_in_stock signal
    into the existing manager's purchase pipeline.

    Runs on _manager_loop so the checker, manager, browser session, and
    purchase coroutines all share one loop — same constraint as the
    single-Chrome path.
    """
    global _resilient_checker

    from walmart.walmart_stock_resilient import build_walmart_checker, DEFAULT_RPS
    from walmart import queue_race

    items = [p["item_id"] for p in get_enabled_products()]
    if not items:
        logger.warning("[APP] No enabled items — resilient checker will idle")
        items = ["320424995"]  # throwaway notebook, matches walmart_stock_resilient fallback

    rps = float(os.environ.get("WALMART_RESILIENT_RPS", DEFAULT_RPS))
    num_str = os.environ.get("WALMART_RESILIENT_NUM_CHROMES")
    num_chromes = int(num_str) if num_str else None
    first_port = int(os.environ.get("WALMART_RESILIENT_FIRST_PORT", 25000))

    def _bridge_in_stock(status):
        # Route queue events through the multi-session race coordinator;
        # all other status types follow the single-session path.
        if queue_race.is_queued_status(status):
            queue_race.dispatch_queue_race(_resilient_checker, _manager, status)
            return
        # ItemStatus → manager._on_in_stock_signal signature.
        # ItemStatus has no offer_id/order_limit fields; the manager already
        # handles None for both. Title falls back to item_id if missing.
        _manager._on_in_stock_signal(
            item_id=status.item_id,
            offer_id=None,
            name=status.title or status.item_id,
            price=status.price,
            order_limit=None,
        )

    _resilient_checker = build_walmart_checker(
        items=items,
        on_in_stock=_bridge_in_stock,
        target_rps=rps,
        num_chromes=num_chromes,
        first_local_port=first_port,
    )
    await _resilient_checker.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _product_row(p: dict, state_by_id: dict) -> str:
    """Build a single product table row for the dashboard (server-rendered initial state)."""
    import json as _json
    item_id = p["item_id"]
    name = p.get("name", "Unknown")
    priority = p.get("priority", "—")
    ps = state_by_id.get(item_id, {})
    state = ps.get("state", "MONITORING")
    in_stock = ps.get("in_stock", False)
    safe_id = _he(item_id)
    safe_name = _he(name)
    js_id = _json.dumps(item_id)
    return (
        f'<tr data-id="{safe_id}">'
        f'<td style="white-space:nowrap">'
        f'<button onclick="movePriority(this,-1)" title="Move Up" class="prio-btn">&#x25B2;</button>'
        f'<button onclick="movePriority(this,1)" title="Move Down" class="prio-btn">&#x25BC;</button>'
        f'</td>'
        f'<td><div class="product-name">{safe_name}</div>'
        f'<div class="product-id">{safe_id}</div></td>'
        f'<td class="cell-lastcheck" style="color:var(--text-muted);font-size:12px">—</td>'
        f'<td class="cell-stock"><span class="badge badge-gray">&#9675; OUT</span></td>'
        f'<td class="cell-state"><span class="badge badge-gray">{_he(state)}</span></td>'
        f'<td><button class="btn btn-sm btn-danger" onclick="removeProduct({js_id})">Remove</button></td>'
        f'</tr>\n'
    )


@app.route("/")
def index():
    products = get_enabled_products()
    status = _manager.get_status() if _manager else {}
    activity = _manager.get_activity_log()[-50:] if _manager else []
    # Build a state lookup by item_id so dashboard rows always match the right product
    state_by_id = {ps["item_id"]: ps for ps in status.get("products", [])}

    running = status.get("running", False)
    circuit_open = status.get("circuit_open", False)
    circuit_secs = status.get("circuit_open_until", 0)
    instock_count = sum(1 for p in products if state_by_id.get(p["item_id"], {}).get("in_stock", False))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Walmart Bot Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg-primary:   #0a0e14;
      --bg-secondary: #151a21;
      --bg-elevated:  #1c2128;
      --bg-card:      #1f2937;
      --bg-hover:     #252d3a;
      --border:       #2d3748;
      --border-active:#4b5563;
      --accent:       #60a5fa;
      --accent-hover: #3b82f6;
      --success:      #10b981;
      --success-bg:   rgba(16,185,129,0.12);
      --success-bdr:  rgba(16,185,129,0.35);
      --warning:      #f59e0b;
      --warning-bg:   rgba(245,158,11,0.12);
      --warning-bdr:  rgba(245,158,11,0.35);
      --danger:       #ef4444;
      --danger-bg:    rgba(239,68,68,0.12);
      --danger-bdr:   rgba(239,68,68,0.35);
      --info:         #06b6d4;
      --info-bg:      rgba(6,182,212,0.12);
      --text-primary: #f9fafb;
      --text-secondary:#d1d5db;
      --text-muted:   #6b7280;
      --font:         'Inter', -apple-system, sans-serif;
      --mono:         'JetBrains Mono', Consolas, monospace;
    }}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family: var(--font); background: var(--bg-primary); color: var(--text-primary); min-height: 100vh; font-size: 14px; }}
    .header {{ background: var(--bg-secondary); border-bottom: 1px solid var(--border); padding: 0 24px; display: flex; align-items: center; justify-content: space-between; height: 56px; position: sticky; top: 0; z-index: 100; }}
    .header-brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 16px; color: var(--text-primary); }}
    .header-brand span {{ color: var(--accent); }}
    .header-meta {{ display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--text-muted); }}
    .content {{ padding: 24px; max-width: 1400px; margin: 0 auto; }}
    .dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; }}
    .dot-green {{ background: var(--success); animation: pulse 2s infinite; }}
    .dot-red {{ background: var(--danger); }}
    .dot-yellow {{ background: var(--warning); animation: pulse 1s infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.4}} }}
    .status-bar {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }}
    .status-bar-left {{ display: flex; align-items: center; gap: 12px; flex: 1; }}
    .status-bar-right {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .badge {{ display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }}
    .badge-green {{ background: var(--success-bg); color: var(--success); border: 1px solid var(--success-bdr); }}
    .badge-yellow {{ background: var(--warning-bg); color: var(--warning); border: 1px solid var(--warning-bdr); }}
    .badge-red {{ background: var(--danger-bg); color: var(--danger); border: 1px solid var(--danger-bdr); }}
    .badge-blue {{ background: var(--info-bg); color: var(--info); border: 1px solid rgba(6,182,212,0.35); }}
    .badge-gray {{ background: var(--bg-elevated); color: var(--text-muted); border: 1px solid var(--border); }}
    .btn {{ background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-secondary); padding: 6px 14px; border-radius: 5px; cursor: pointer; font-family: var(--font); font-size: 12px; font-weight: 500; transition: background 0.15s, border-color 0.15s; }}
    .btn:hover {{ background: var(--bg-hover); border-color: var(--border-active); color: var(--text-primary); }}
    .btn-primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    .btn-primary:hover {{ background: var(--accent-hover); border-color: var(--accent-hover); color: #fff; }}
    .btn-danger {{ background: var(--danger-bg); border-color: var(--danger-bdr); color: var(--danger); }}
    .btn-danger:hover {{ background: rgba(239,68,68,0.2); }}
    .btn-sm {{ padding: 4px 10px; font-size: 11px; }}
    .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px; }}
    .card-header {{ padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 13px; color: var(--text-secondary); display: flex; align-items: center; justify-content: space-between; }}
    .product-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .product-table th {{ padding: 9px 14px; text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); border-bottom: 1px solid var(--border); background: var(--bg-elevated); }}
    .product-table td {{ padding: 10px 14px; border-bottom: 1px solid rgba(45,55,72,0.5); vertical-align: middle; }}
    .product-table tr:last-child td {{ border-bottom: none; }}
    .product-table tr:hover td {{ background: var(--bg-hover); }}
    .product-name {{ font-weight: 500; color: var(--text-primary); }}
    .product-id {{ font-family: var(--mono); font-size: 11px; color: var(--text-muted); }}
    .prio-btn {{ background: none; border: 1px solid var(--border); color: var(--text-secondary); border-radius: 4px; padding: 2px 7px; cursor: pointer; margin-right: 2px; font-size: 10px; }}
    .prio-btn:hover {{ border-color: var(--border-active); color: var(--text-primary); }}
    .add-form {{ padding: 14px 16px; border-top: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .add-form input {{ background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-primary); padding: 7px 11px; border-radius: 5px; font-family: var(--font); font-size: 13px; outline: none; transition: border-color 0.15s; }}
    .add-form input:focus {{ border-color: var(--accent); }}
    .add-form input::placeholder {{ color: var(--text-muted); }}
    .log-box {{ height: 280px; overflow-y: auto; padding: 10px 14px; font-family: var(--mono); font-size: 12px; line-height: 1.7; }}
    .log-line {{ color: var(--text-muted); }}
    .log-line.log-success {{ color: var(--success); }}
    .log-line.log-error {{ color: var(--danger); }}
    .log-line.log-warning {{ color: var(--warning); }}
    .log-time {{ color: var(--text-muted); margin-right: 6px; }}
    .stats-row {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
    .stat-card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 14px 18px; flex: 1; min-width: 130px; }}
    .stat-label {{ font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
    .stat-value {{ font-size: 22px; font-weight: 700; color: var(--text-primary); }}
    .stat-value.green {{ color: var(--success); }}
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--border-active); }}
    .conn-indicator {{ display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--text-muted); }}
  </style>
</head>
<body>

<div class="header">
  <div class="header-brand">
    <span>&#x1F6D2;</span> Walmart Bot
  </div>
  <div class="header-meta">
    <div class="conn-indicator" id="conn-status">
      <span class="dot dot-yellow"></span> Connecting&hellip;
    </div>
  </div>
</div>

<div class="content">
  <div class="status-bar">
    <div class="status-bar-left">
      <span id="running-dot" class="dot dot-yellow"></span>
      <strong id="running-text">Connecting&hellip;</strong>
      <span id="mode-badge" class="badge badge-gray">TEST</span>
      <span id="circuit-badge" style="display:none" class="badge badge-red">CIRCUIT OPEN</span>
    </div>
    <div class="status-bar-right">
      <button class="btn btn-sm" onclick="setTestMode(true)">Enable Test Mode</button>
      <button class="btn btn-sm btn-danger" onclick="setTestMode(false)">Live Mode</button>
    </div>
  </div>

  <div class="stats-row">
    <div class="stat-card">
      <div class="stat-label">Products</div>
      <div class="stat-value" id="stat-total">—</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">In Stock</div>
      <div class="stat-value green" id="stat-instock">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Last Cycle</div>
      <div class="stat-value" id="stat-lastcycle" style="font-size:14px;margin-top:4px">—</div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      Products
      <span id="monitoring-badge" class="badge badge-gray" style="font-size:11px">STOPPED</span>
    </div>
    <table class="product-table">
      <thead>
        <tr><th>Priority</th><th>Product</th><th>Last Check</th><th>Stock</th><th>State</th><th></th></tr>
      </thead>
      <tbody id="product-tbody">
        {''.join(_product_row(p, state_by_id) for p in products)}
      </tbody>
    </table>
    <div class="add-form">
      <input id="new-item-id" placeholder="Item ID (e.g. 15042474261)" style="width:180px">
      <input id="new-item-name" placeholder="Product name" style="width:200px">
      <input type="number" id="new-max-price" placeholder="Max price" step="0.01" style="width:110px">
      <button class="btn btn-primary" onclick="addProduct()">Add Product</button>
    </div>
  </div>

  <div class="card">
    <div class="card-header">Live Activity</div>
    <div class="log-box" id="log">
      {''.join(f'<div class="log-line"><span class="log-time">[{_he(e["time"])}]</span>{_he(e["message"])}</div>' for e in reversed(activity))}
    </div>
  </div>
</div>

<script>
  // ── Helpers ──
  function esc(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }}
  function stockBadge(inStock) {{
    return inStock
      ? '<span class="badge badge-green">&#9679; IN STOCK</span>'
      : '<span class="badge badge-gray">&#9675; OUT</span>';
  }}
  function stateBadge(state) {{
    const s = (state || '').toUpperCase();
    if (s === 'SUCCESS' || s === 'PURCHASED') return '<span class="badge badge-green">' + s + '</span>';
    if (s === 'PURCHASING' || s === 'IN_QUEUE' || s === 'ATTEMPTING') return '<span class="badge badge-yellow">' + s + '</span>';
    if (s === 'FAILED' || s === 'CIRCUIT_OPEN') return '<span class="badge badge-red">' + s + '</span>';
    return '<span class="badge badge-gray">' + (s || 'MONITORING') + '</span>';
  }}

  // ── Products state ──
  let products = {{}};
  let testModeOn = false;

  function buildRow(id) {{
    const p = products[id];
    const lastCheck = p.last_checked ? new Date(p.last_checked * 1000).toLocaleTimeString() : '—';
    const tr = document.createElement('tr');
    tr.setAttribute('data-id', id);
    tr.innerHTML = `
      <td style="white-space:nowrap">
        <button onclick="movePriority(this,-1)" title="Move Up" class="prio-btn">&#x25B2;</button>
        <button onclick="movePriority(this,1)" title="Move Down" class="prio-btn">&#x25BC;</button>
      </td>
      <td>
        <div class="product-name">${{esc(p.name || id)}}</div>
        <div class="product-id">${{esc(id)}}</div>
      </td>
      <td class="cell-lastcheck" style="color:var(--text-muted);font-size:12px">${{esc(lastCheck)}}</td>
      <td class="cell-stock">${{stockBadge(p.in_stock)}}</td>
      <td class="cell-state">${{stateBadge(p.state)}}</td>
      <td><button class="btn btn-sm btn-danger" onclick="removeProduct('${{esc(id)}}')">Remove</button></td>`;
    return tr;
  }}

  function renderTable() {{
    const tbody = document.getElementById('product-tbody');
    const ids = Object.keys(products).sort((a,b) =>
      (products[a].priority || 999) - (products[b].priority || 999)
    );
    if (!ids.length) {{
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">No products configured</td></tr>';
      document.getElementById('stat-total').textContent = '0';
      document.getElementById('stat-instock').textContent = '0';
      return;
    }}
    tbody.querySelectorAll('tr:not([data-id])').forEach(r => r.remove());
    const existing = {{}};
    tbody.querySelectorAll('tr[data-id]').forEach(r => {{ existing[r.getAttribute('data-id')] = r; }});
    Object.keys(existing).filter(id => !products[id]).forEach(id => existing[id].remove());
    ids.filter(id => !existing[id]).forEach(id => tbody.appendChild(buildRow(id)));
    ids.filter(id => existing[id]).forEach(id => {{
      const row = existing[id];
      const p = products[id];
      const lastCheck = p.last_checked ? new Date(p.last_checked * 1000).toLocaleTimeString() : '—';
      row.querySelector('.cell-lastcheck').textContent = lastCheck;
      row.querySelector('.cell-stock').innerHTML = stockBadge(p.in_stock);
      row.querySelector('.cell-state').innerHTML = stateBadge(p.state);
      const nameEl = row.querySelector('.product-name');
      if (nameEl.textContent.trim() === id && p.name && p.name !== id) nameEl.textContent = p.name;
    }});
    ids.forEach(id => {{ const r = tbody.querySelector(`tr[data-id="${{id}}"]`); if (r) tbody.appendChild(r); }});
    document.getElementById('stat-total').textContent = ids.length;
    document.getElementById('stat-instock').textContent = ids.filter(id => products[id].in_stock).length;
  }}

  function updateStatusBar(running, testMode, circuitOpen) {{
    const dot = document.getElementById('running-dot');
    const text = document.getElementById('running-text');
    const badge = document.getElementById('mode-badge');
    const cbadge = document.getElementById('circuit-badge');
    const monBadge = document.getElementById('monitoring-badge');
    dot.className = 'dot ' + (running ? 'dot-green' : 'dot-red');
    text.textContent = running ? 'Running' : 'Stopped';
    badge.textContent = testMode ? 'TEST MODE' : 'LIVE';
    badge.className = 'badge ' + (testMode ? 'badge-yellow' : 'badge-green');
    if (cbadge) cbadge.style.display = circuitOpen ? 'inline-flex' : 'none';
    if (monBadge) {{
      monBadge.textContent = running ? 'MONITORING' : 'STOPPED';
      monBadge.className = 'badge ' + (running ? 'badge-blue' : 'badge-gray');
    }}
    const conn = document.getElementById('conn-status');
    if (conn) conn.innerHTML = '<span class="dot ' + (running ? 'dot-green' : 'dot-red') + '"></span> ' + (running ? 'Live' : 'Stopped');
  }}

  function prependLog(time, message) {{
    const box = document.getElementById('log');
    if (!box) return;
    const isSuccess = message.includes('SUCCESS') || message.includes('ORDER PLACED') || message.includes('confirmed');
    const isError = /error|fail|failed/i.test(message);
    const isWarning = /warn|circuit/i.test(message);
    const cls = isSuccess ? 'log-success' : isError ? 'log-error' : isWarning ? 'log-warning' : '';
    const line = document.createElement('div');
    line.className = 'log-line ' + cls;
    line.innerHTML = '<span class="log-time">[' + esc(time) + ']</span>' + esc(message);
    box.insertBefore(line, box.firstChild);
    while (box.children.length > 300) box.removeChild(box.lastChild);
  }}

  // ── API ──
  async function loadStatus() {{
    try {{
      const d = await fetch('/api/status').then(r => r.json());
      const prods = d.products || [];
      const incoming = new Set(prods.map(p => p.item_id));
      Object.keys(products).forEach(id => {{ if (!incoming.has(id)) delete products[id]; }});
      prods.forEach(p => {{
        const id = p.item_id;
        if (!products[id]) products[id] = {{}};
        if (p.name && p.name !== id && !/^\\d+$/.test(p.name.trim())) {{
          products[id].name = p.name;
        }} else if (!products[id].name) {{
          products[id].name = p.name || id;
        }}
        products[id].state = p.state || 'MONITORING';
        products[id].priority = p.priority || products[id].priority || 999;
        products[id].in_stock = p.in_stock || false;
        products[id].last_checked = p.last_checked || null;
      }});
      testModeOn = d.test_mode || false;
      const running = d.running || false;
      updateStatusBar(running, testModeOn, d.circuit_open || false);
      renderTable();
      const lc = document.getElementById('stat-lastcycle');
      if (lc && running) lc.textContent = new Date().toLocaleTimeString();
    }} catch(e) {{ console.warn('Status load failed', e); }}
  }}

  function movePriority(btn, direction) {{
    const row = btn.closest('tr');
    const tbody = row.closest('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr[data-id]'));
    const idx = rows.indexOf(row);
    const tgt = idx + direction;
    if (tgt < 0 || tgt >= rows.length) return;
    if (direction === -1) tbody.insertBefore(row, rows[tgt]);
    else tbody.insertBefore(rows[tgt], row);
    savePriorityOrder();
  }}

  async function savePriorityOrder() {{
    const tbody = document.getElementById('product-tbody');
    if (!tbody) return;
    const order = Array.from(tbody.querySelectorAll('tr[data-id]')).map(r => r.getAttribute('data-id'));
    order.forEach((id, i) => {{ if (products[id]) products[id].priority = i + 1; }});
    try {{
      await fetch('/reorder-products', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{order}})
      }});
    }} catch(e) {{ console.error('Reorder error:', e); }}
  }}

  function addProduct() {{
    const item_id = document.getElementById('new-item-id').value.trim();
    const name = document.getElementById('new-item-name').value.trim() || 'Unknown';
    const max_price = parseFloat(document.getElementById('new-max-price').value) || null;
    if (!item_id) return;
    fetch('/add-product', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{item_id, name, max_price}})
    }}).then(() => loadStatus());
  }}

  function removeProduct(item_id) {{
    if (!confirm('Remove ' + item_id + '?')) return;
    fetch('/remove-product/' + item_id, {{method: 'POST'}}).then(() => {{
      delete products[item_id];
      renderTable();
    }});
  }}

  async function setTestMode(enable) {{
    await fetch(enable ? '/api/test/enable' : '/api/test/disable', {{method:'POST'}});
    testModeOn = enable;
    updateStatusBar(true, enable, false);
  }}

  // ── SSE ──
  let sse = null;
  function connectSSE() {{
    if (sse) sse.close();
    sse = new EventSource('/api/stream');
    sse.onopen = () => {{
      const conn = document.getElementById('conn-status');
      if (conn) conn.innerHTML = '<span class="dot dot-green"></span> Live';
    }};
    sse.onmessage = (e) => {{
      try {{
        const msg = JSON.parse(e.data);
        if (msg.type === 'activity' && msg.data) {{
          prependLog(msg.data.time || '', msg.data.message || '');
          if (/purchased|success|order|state|PURCHASING|MONITORING|FAILED/i.test(msg.data.message || '')) {{
            loadStatus();
          }}
        }}
        if (msg.type === 'stock_update' && msg.data) {{
          const id = msg.data.item_id;
          if (id && products[id] !== undefined) {{
            products[id].in_stock = msg.data.in_stock;
            products[id].last_checked = Date.now() / 1000;
            renderTable();
            const lc = document.getElementById('stat-lastcycle');
            if (lc) lc.textContent = msg.data.time || new Date().toLocaleTimeString();
          }}
        }}
      }} catch(err) {{ console.warn('SSE parse error:', err); }}
    }};
    sse.onerror = () => {{
      const conn = document.getElementById('conn-status');
      if (conn) conn.innerHTML = '<span class="dot dot-yellow"></span> Reconnecting&hellip;';
      setTimeout(connectSSE, 5000);
    }};
  }}

  // ── Init ──
  (async function init() {{
    await loadStatus();
    connectSSE();
    setInterval(loadStatus, 15000);
  }})();
</script>
</body>
</html>"""
    return html


@app.route("/api/stream")
def stream():
    """SSE endpoint — pushes real-time activity to connected dashboard clients."""
    client_queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_clients.append(client_queue)

    def generate():
        try:
            # Send initial heartbeat
            yield "data: {\"type\":\"connected\"}\n\n"
            while True:
                try:
                    data = client_queue.get(timeout=1.0)
                    yield data
                except queue.Empty:
                    yield ": heartbeat\n\n"  # keep-alive comment
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if client_queue in _sse_clients:
                    _sse_clients.remove(client_queue)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/status")
def api_status():
    # Guard against get_status() raising mid-shutdown or during a manager
    # state-corruption hiccup — the dashboard's 15s polling should never crash
    # the route and break the live feed.
    try:
        status = _manager.get_status() if _manager else {"running": False}
    except Exception:
        logger.exception("[APP] /api/status get_status failed")
        status = {"running": False, "error": "status_unavailable"}
    status["test_mode"] = _test_mode
    return jsonify(status)


@app.route("/add-product", methods=["POST"])
def add_product():
    try:
        data = request.get_json(force=True) or {}
        item_id = str(data.get("item_id", "")).strip()
        name = str(data.get("name", "Unknown")).strip()
        max_price = data.get("max_price")

        if not item_id:
            return jsonify({"error": "item_id required"}), 400

        # Validate max_price if provided
        parsed_price = None
        if max_price is not None:
            try:
                parsed_price = float(max_price)
            except (TypeError, ValueError):
                return jsonify({"error": "max_price must be a number"}), 400

        config = get_config()
        # Avoid duplicates
        for p in config["products"]:
            if p["item_id"] == item_id:
                return jsonify({"message": "already exists"}), 200

        config["products"].append({
            "item_id": item_id,
            "name": name,
            "enabled": True,
            "max_price": parsed_price,
            "url": f"https://www.walmart.com/ip/x/{item_id}",
        })
        save_config(config)
        _status_callback(f"Product added: {name} ({item_id})")
        return jsonify({"message": "added", "item_id": item_id})
    except Exception as e:
        logger.exception("[APP] /add-product error")
        return jsonify({"error": str(e)}), 500


@app.route("/remove-product/<item_id>", methods=["POST"])
def remove_product(item_id: str):
    try:
        config = get_config()
        before = len(config["products"])
        config["products"] = [p for p in config["products"] if p["item_id"] != item_id]
        if len(config["products"]) < before:
            save_config(config)
            _status_callback(f"Product removed: {item_id}")
            return jsonify({"message": "removed"})
        return jsonify({"error": "not found"}), 404
    except Exception as e:
        logger.exception("[APP] /remove-product error")
        return jsonify({"error": str(e)}), 500


@app.route("/reorder-products", methods=["POST"])
def reorder_products():
    """Persist the priority order set by the dashboard's up/down buttons."""
    try:
        data = request.get_json(force=True) or {}
        order = data.get("order")
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            return jsonify({"error": "order must be a list of item_id strings"}), 400

        config = get_config()
        by_id = {p["item_id"]: p for p in config.get("products", [])}

        # Assign new priorities by position in the submitted order.
        # Products not in the submitted order keep their existing priority but
        # get pushed below the reordered ones.
        reordered = []
        for i, item_id in enumerate(order, start=1):
            p = by_id.pop(item_id, None)
            if p is None:
                continue
            p["priority"] = i
            reordered.append(p)

        # Append remaining (unranked) products after the reordered ones.
        offset = len(reordered)
        for j, p in enumerate(by_id.values(), start=1):
            p["priority"] = offset + j
            reordered.append(p)

        config["products"] = reordered
        save_config(config)
        return jsonify({"message": "reordered", "count": len(reordered)})
    except Exception as e:
        logger.exception("[APP] /reorder-products error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/test/enable", methods=["POST"])
def test_enable():
    global _test_mode
    _test_mode = True
    os.environ["CHECKOUT_MODE"] = "TEST"
    _status_callback("[TEST] Test mode enabled")
    return jsonify({"test_mode": True})


@app.route("/api/test/disable", methods=["POST"])
def test_disable():
    global _test_mode
    _test_mode = False
    os.environ["CHECKOUT_MODE"] = "PRODUCTION"
    _status_callback("[TEST] Test mode disabled — LIVE mode")
    return jsonify({"test_mode": False})


@app.route("/health")
def health():
    try:
        running = _manager.get_status().get("running", False) if _manager else False
    except Exception:
        running = False
    return jsonify({
        "status": "ok",
        "running": running,
        "test_mode": _test_mode,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_shutdown_in_progress = False


def _graceful_shutdown(signum=None, frame=None):
    """
    SIGINT/SIGTERM handler — schedules WalmartPurchaseManager.stop() on the
    manager loop, waits briefly, then exits. Without this, Ctrl+C kills the
    daemon thread mid-purchase and the browser, harvester, and monitor never
    clean up — leaving a hung Chrome + a 0-byte log file behind.
    """
    global _shutdown_in_progress
    if _shutdown_in_progress:
        logger.warning("[APP] Second shutdown signal — forcing exit")
        os._exit(1)
    _shutdown_in_progress = True

    sig_name = signal.Signals(signum).name if signum is not None else "shutdown"
    logger.info("[APP] Received %s — shutting down gracefully (5s budget)", sig_name)

    if _manager is not None and _manager_loop is not None and _manager_loop.is_running():
        try:
            fut = asyncio.run_coroutine_threadsafe(_manager.stop(), _manager_loop)
            try:
                fut.result(timeout=5.0)
                logger.info("[APP] Manager stopped cleanly")
            except Exception as e:
                logger.warning("[APP] Manager stop did not complete in 5s: %s", e)
        except Exception as e:
            logger.warning("[APP] Could not schedule manager stop: %s", e)

    # Flush all logging handlers so the file isn't truncated mid-write
    for handler in logging.getLogger().handlers + logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass

    sys.exit(0)


def run_dashboard():
    """Start the Walmart dashboard on port 5001."""
    from waitress import serve

    # Register graceful shutdown — only in the main thread (signal handlers
    # cannot be registered from worker threads).
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)

    logger.info("[APP] Starting Walmart dashboard on http://localhost:5001")
    _start_manager()
    serve(app, host="0.0.0.0", port=5001, threads=4)


if __name__ == "__main__":
    run_dashboard()
