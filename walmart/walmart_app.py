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
import threading
import time
from pathlib import Path

from html import escape as _he
from flask import Flask, Response, jsonify, request, stream_with_context

from walmart.config import get_config, save_config, get_enabled_products
from walmart.purchase_manager import WalmartPurchaseManager

# Setup logging with both console and file output
log_dir = Path(__file__).parent.parent / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"walmart_app_{time.strftime('%Y%m%d_%H%M%S')}.log"

formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.addHandler(file_handler)

# Also add file handler to all other loggers
for name in ["MANAGER", "PURCHASE", "SESSION", "MONITOR", "PROXY"]:
    log = logging.getLogger(name)
    log.addHandler(file_handler)

# Ensure CHECKOUT_MODE env var matches the default _test_mode=False (LIVE)
os.environ.setdefault("CHECKOUT_MODE", "LIVE")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_manager: WalmartPurchaseManager = None
_manager_loop: asyncio.AbstractEventLoop = None
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
    """Launch the WalmartPurchaseManager in a background thread."""
    global _manager, _manager_loop

    email = os.environ.get("WALMART_EMAIL", "")
    password = os.environ.get("WALMART_PASSWORD", "")

    if not email or not password:
        logger.warning(
            "[APP] WALMART_EMAIL or WALMART_PASSWORD not set — bot will start "
            "but purchases will fail. Set env vars and restart."
        )

    _manager = WalmartPurchaseManager(status_callback=_status_callback)
    _manager.set_stock_update_callback(_stock_update_callback)
    _manager_loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(_manager_loop)
        try:
            _manager_loop.run_until_complete(
                _manager.start()
            )
        except Exception:
            logger.exception("[APP] Manager start() failed — bot will not run")
            return
        _manager_loop.run_forever()

    t = threading.Thread(target=_run, daemon=True, name="WalmartManagerThread")
    t.start()
    logger.info("[APP] Walmart purchase manager started in background")


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
    status = _manager.get_status() if _manager else {"running": False}
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
    return jsonify({
        "status": "ok",
        "running": _manager.get_status().get("running", False) if _manager else False,
        "test_mode": _test_mode,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_dashboard():
    """Start the Walmart dashboard on port 5001."""
    from waitress import serve

    logger.info("[APP] Starting Walmart dashboard on http://localhost:5001")
    _start_manager()
    serve(app, host="0.0.0.0", port=5001, threads=4)


if __name__ == "__main__":
    run_dashboard()
