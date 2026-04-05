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

from .config import get_config, save_config, get_enabled_products
from .purchase_manager import WalmartPurchaseManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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

def _stock_badge(in_stock: bool) -> str:
    if in_stock:
        return '<span class="badge badge-green">&#9679; IN STOCK</span>'
    return '<span class="badge badge-gray">&#9675; OUT OF STOCK</span>'


def _state_badge(state: str) -> str:
    s = state.upper()
    if s == "SUCCESS":
        return f'<span class="badge badge-green">{s}</span>'
    if s in ("PURCHASING", "IN_QUEUE"):
        return f'<span class="badge badge-yellow">{s}</span>'
    if s in ("FAILED", "CIRCUIT_OPEN"):
        return f'<span class="badge badge-red">{s}</span>'
    return f'<span class="badge badge-gray">{_he(state) or "MONITORING"}</span>'


def _product_row(p: dict, state_by_id: dict) -> str:
    """Build a single product table row for the dashboard."""
    import json as _json
    item_id = p["item_id"]
    name = p.get("name", "Unknown")
    ps = state_by_id.get(item_id, {})
    state = ps.get("state", "MONITORING")
    in_stock = ps.get("in_stock", False)
    last_checked = ps.get("last_checked")
    last_check_str = ""
    if last_checked:
        import datetime
        last_check_str = datetime.datetime.fromtimestamp(last_checked).strftime("%H:%M:%S")
    safe_id = _he(item_id)
    safe_name = _he(name)
    js_id = _json.dumps(item_id)
    return (
        f'<tr data-id="{safe_id}">'
        f'<td><div class="product-name">{safe_name}</div>'
        f'<div class="product-id">{safe_id}</div></td>'
        f'<td class="cell-lastcheck" style="color:#666;font-size:12px">{_he(last_check_str)}</td>'
        f'<td class="cell-stock">{_stock_badge(in_stock)}</td>'
        f'<td class="cell-state">{_state_badge(state)}</td>'
        f'<td><button onclick="removeProduct({js_id})">Remove</button></td>'
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
  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',monospace;background:#0d0d0d;color:#e0e0e0;margin:0;padding:20px;}}
    h1{{color:#0071ce;border-bottom:1px solid #2a2a2a;padding-bottom:12px;margin-bottom:20px;font-size:20px;}}
    .card{{background:#141414;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:16px;}}
    .card-header{{font-size:12px;text-transform:uppercase;color:#666;font-weight:600;letter-spacing:.05em;margin-bottom:12px;display:flex;align-items:center;gap:8px;}}
    .badge{{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:700;letter-spacing:.03em;}}
    .badge-green{{background:#0a2a0a;color:#4caf50;border:1px solid #4caf50;}}
    .badge-red{{background:#2a0a0a;color:#f44336;border:1px solid #f44336;}}
    .badge-yellow{{background:#2a1e00;color:#ffc107;border:1px solid #ffc107;}}
    .badge-gray{{background:#1a1a1a;color:#666;border:1px solid #333;}}
    .status-bar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:16px;}}
    .status-bar-left{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}}
    .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;flex-shrink:0;}}
    .dot-green{{background:#4caf50;}}
    .dot-red{{background:#f44336;}}
    .dot-yellow{{background:#ffc107;animation:pulse 1s infinite;}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
    table{{width:100%;border-collapse:collapse;}}
    th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #1e1e1e;vertical-align:middle;}}
    th{{color:#555;font-size:11px;text-transform:uppercase;letter-spacing:.05em;}}
    .product-name{{font-size:13px;font-weight:500;}}
    .product-id{{font-size:11px;color:#555;margin-top:2px;}}
    input{{background:#111;border:1px solid #333;color:#eee;padding:6px 10px;border-radius:4px;font-size:13px;}}
    input::placeholder{{color:#444;}}
    .add-form{{display:flex;gap:8px;flex-wrap:wrap;padding-top:12px;border-top:1px solid #1e1e1e;margin-top:12px;}}
    button{{background:#0071ce;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px;}}
    button:hover{{background:#0056a3;}}
    .btn-danger{{background:#c62828;}}
    .btn-danger:hover{{background:#b71c1c;}}
    .btn-sm{{padding:4px 10px;font-size:12px;}}
    #log{{height:300px;overflow-y:auto;background:#0a0a0a;padding:10px;border-radius:4px;font-size:12px;line-height:1.7;}}
    .log-line{{color:#555;}}
    .log-line .log-time{{color:#333;margin-right:6px;}}
    .log-success{{color:#4caf50;}}
    .log-error{{color:#f44336;}}
    .log-warning{{color:#ffc107;}}
    .stats-row{{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;}}
    .stat-card{{background:#141414;border:1px solid #2a2a2a;border-radius:6px;padding:10px 16px;flex:1;min-width:100px;}}
    .stat-label{{font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.05em;}}
    .stat-value{{font-size:22px;font-weight:700;margin-top:4px;}}
    .stat-value.green{{color:#4caf50;}}
  </style>
</head>
<body>
  <h1>&#x1F6D2; Walmart Bot Dashboard</h1>

  <div class="status-bar">
    <div class="status-bar-left">
      <span class="dot {'dot-green' if running else 'dot-red'}"></span>
      <strong>{'Running' if running else 'Stopped'}</strong>
      <span class="badge {'badge-yellow' if _test_mode else 'badge-green'}">{'TEST MODE' if _test_mode else 'LIVE'}</span>
      {'<span class="badge badge-red">CIRCUIT OPEN (' + str(circuit_secs) + 's)</span>' if circuit_open else ''}
    </div>
    <div style="display:flex;gap:8px;">
      <button class="btn-sm" onclick="fetch('/api/test/enable',{{method:'POST'}}).then(()=>location.reload())">Test Mode</button>
      <button class="btn-sm btn-danger" onclick="fetch('/api/test/disable',{{method:'POST'}}).then(()=>location.reload())">Live Mode</button>
    </div>
  </div>

  <div class="stats-row">
    <div class="stat-card">
      <div class="stat-label">Products</div>
      <div class="stat-value" id="stat-total">{len(products)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">In Stock</div>
      <div class="stat-value green" id="stat-instock">{instock_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Last Update</div>
      <div class="stat-value" id="stat-lastupdate" style="font-size:14px;margin-top:6px">—</div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">Products</div>
    <table>
      <thead><tr><th>Product</th><th>Last Check</th><th>Stock</th><th>State</th><th></th></tr></thead>
      <tbody id="product-tbody">
        {''.join(_product_row(p, state_by_id) for p in products)}
      </tbody>
    </table>
    <div class="add-form">
      <input id="new-item-id" placeholder="Walmart Item ID" style="width:180px">
      <input id="new-item-name" placeholder="Product name" style="width:200px">
      <input type="number" id="new-max-price" placeholder="Max price" step="0.01" style="width:110px">
      <button onclick="addProduct()">Add Product</button>
    </div>
  </div>

  <div class="card">
    <div class="card-header">Live Activity</div>
    <div id="log">
      {''.join(f'<div class="log-line"><span class="log-time">[{_he(e["time"])}]</span>{_he(e["message"])}</div>' for e in reversed(activity))}
    </div>
  </div>

  <script>
    const evtSource = new EventSource('/api/stream');

    evtSource.onmessage = (e) => {{
      const msg = JSON.parse(e.data);
      if (msg.type === 'activity' && msg.data) {{
        prependLog(msg.data.time, msg.data.message);
        // Refresh table state on purchase transitions
        if (/PURCHASING|SUCCESS|FAILED|MONITORING|order/i.test(msg.data.message)) {{
          loadStatus();
        }}
      }}
      if (msg.type === 'stock_update' && msg.data) {{
        updateRowStock(msg.data.item_id, msg.data.in_stock);
        document.getElementById('stat-lastupdate').textContent = msg.data.time;
        refreshInStockCount();
      }}
    }};

    function prependLog(time, message) {{
      const log = document.getElementById('log');
      const isSuccess = /SUCCESS|ORDER PLACED/i.test(message);
      const isError   = /error|fail/i.test(message);
      const isWarning = /warn|circuit/i.test(message);
      const cls = isSuccess ? 'log-success' : isError ? 'log-error' : isWarning ? 'log-warning' : '';
      const line = document.createElement('div');
      line.className = 'log-line ' + cls;
      line.innerHTML = '<span class="log-time">[' + esc(time) + ']</span>' + esc(message);
      log.insertBefore(line, log.firstChild);
      while (log.children.length > 200) log.removeChild(log.lastChild);
    }}

    function esc(s) {{
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }}

    function stockBadge(inStock) {{
      return inStock
        ? '<span class="badge badge-green">&#9679; IN STOCK</span>'
        : '<span class="badge badge-gray">&#9675; OUT OF STOCK</span>';
    }}

    function stateBadge(state) {{
      const s = (state || '').toUpperCase();
      if (s === 'SUCCESS') return '<span class="badge badge-green">' + s + '</span>';
      if (s === 'PURCHASING' || s === 'IN_QUEUE') return '<span class="badge badge-yellow">' + s + '</span>';
      if (s === 'FAILED' || s === 'CIRCUIT_OPEN') return '<span class="badge badge-red">' + s + '</span>';
      return '<span class="badge badge-gray">' + (s || 'MONITORING') + '</span>';
    }}

    function updateRowStock(item_id, in_stock) {{
      const row = document.querySelector(`tr[data-id="${{CSS.escape(item_id)}}"]`);
      if (!row) return;
      const cell = row.querySelector('.cell-stock');
      if (cell) cell.innerHTML = stockBadge(in_stock);
      row.dataset.instock = in_stock ? '1' : '0';
    }}

    function refreshInStockCount() {{
      const rows = document.querySelectorAll('#product-tbody tr[data-id]');
      let count = 0;
      rows.forEach(r => {{ if (r.dataset.instock === '1') count++; }});
      document.getElementById('stat-instock').textContent = count;
    }}

    async function loadStatus() {{
      try {{
        const d = await fetch('/api/status').then(r => r.json());
        const products = d.products || [];
        products.forEach(p => {{
          const row = document.querySelector(`tr[data-id="${{CSS.escape(p.item_id)}}"]`);
          if (!row) return;
          const stateCell = row.querySelector('.cell-state');
          const stockCell = row.querySelector('.cell-stock');
          const lastCell  = row.querySelector('.cell-lastcheck');
          if (stateCell) stateCell.innerHTML = stateBadge(p.state);
          if (stockCell) {{ stockCell.innerHTML = stockBadge(p.in_stock || false); row.dataset.instock = p.in_stock ? '1' : '0'; }}
          if (lastCell && p.last_checked) {{
            lastCell.textContent = new Date(p.last_checked * 1000).toLocaleTimeString();
          }}
        }});
        refreshInStockCount();
      }} catch(e) {{ console.warn('Status load failed', e); }}
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
      }}).then(() => location.reload());
    }}

    function removeProduct(item_id) {{
      if (!confirm('Remove ' + item_id + '?')) return;
      fetch('/remove-product/' + item_id, {{method: 'POST'}}).then(() => location.reload());
    }}

    // Poll state every 15s as a fallback
    setInterval(loadStatus, 15000);
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
