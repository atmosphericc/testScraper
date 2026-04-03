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

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_manager: WalmartPurchaseManager = None
_manager_loop: asyncio.AbstractEventLoop = None
_test_mode = False

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
    """Build a single product table row for the dashboard."""
    item_id = p["item_id"]
    name = p.get("name", "Unknown")
    state = state_by_id.get(item_id, {}).get("state", "MONITORING")
    if state == "SUCCESS":
        badge = "badge-green"
    elif state in ("PURCHASING", "IN_QUEUE"):
        badge = "badge-yellow"
    else:
        badge = "badge-blue"
    # Escape all user-controlled strings before embedding in HTML/JS
    safe_id = _he(item_id)
    safe_name = _he(name)
    safe_state = _he(state)
    # JSON-encode item_id for the onclick JS string to handle any special chars
    import json as _json
    js_id = _json.dumps(item_id)
    return (
        f'<tr>'
        f'<td>{safe_id}</td>'
        f'<td>{safe_name}</td>'
        f'<td><span class="badge {badge}">{safe_state}</span></td>'
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

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Walmart Bot Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: monospace; background: #0a0a0a; color: #e0e0e0; margin: 0; padding: 20px; }}
    h1 {{ color: #0071ce; border-bottom: 1px solid #333; padding-bottom: 10px; }}
    .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 16px; margin-bottom: 16px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; }}
    .badge-green {{ background: #0a3d0a; color: #4caf50; border: 1px solid #4caf50; }}
    .badge-red {{ background: #3d0a0a; color: #f44336; border: 1px solid #f44336; }}
    .badge-yellow {{ background: #3d2e00; color: #ffc107; border: 1px solid #ffc107; }}
    .badge-blue {{ background: #003d52; color: #29b6f6; border: 1px solid #29b6f6; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #2a2a2a; }}
    th {{ color: #888; font-size: 12px; text-transform: uppercase; }}
    input[type=text] {{ background: #111; border: 1px solid #444; color: #eee; padding: 6px 10px; border-radius: 4px; width: 250px; }}
    button {{ background: #0071ce; color: white; border: none; padding: 7px 16px; border-radius: 4px; cursor: pointer; margin-left: 6px; }}
    button:hover {{ background: #0056a3; }}
    #log {{ height: 300px; overflow-y: auto; background: #111; padding: 10px; border-radius: 4px; font-size: 12px; line-height: 1.6; }}
    .log-line {{ color: #aaa; }}
    .log-line.success {{ color: #4caf50; }}
    .log-line.error {{ color: #f44336; }}
    .status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
    .dot-green {{ background: #4caf50; }}
    .dot-red {{ background: #f44336; }}
    .dot-yellow {{ background: #ffc107; animation: pulse 1s infinite; }}
    @keyframes pulse {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:0.4 }} }}
  </style>
</head>
<body>
  <h1>&#x1F6D2; Walmart Bot Dashboard</h1>

  <div class="card">
    <b>Status:</b>
    <span class="status-dot {'dot-green' if status.get('running') else 'dot-red'}"></span>
    {'Running' if status.get('running') else 'Stopped'}
    &nbsp;&nbsp;
    {'<span class="badge badge-red">CIRCUIT OPEN (' + str(status.get("circuit_open_until", 0)) + 's)</span>' if status.get('circuit_open') else ''}
    &nbsp;
    <span class="badge {'badge-yellow' if _test_mode else 'badge-green'}">{'TEST MODE' if _test_mode else 'LIVE'}</span>
    <br><br>
    <button onclick="fetch('/api/test/enable',{{method:'POST'}}).then(()=>location.reload())">Enable Test Mode</button>
    <button onclick="fetch('/api/test/disable',{{method:'POST'}}).then(()=>location.reload())">Disable Test Mode</button>
  </div>

  <div class="card">
    <b>Products</b>
    <table>
      <tr><th>Item ID</th><th>Name</th><th>State</th><th>Action</th></tr>
      {''.join(_product_row(p, state_by_id) for p in products)}
    </table>
    <br>
    <input type="text" id="new-item-id" placeholder="Walmart Item ID">
    <input type="text" id="new-item-name" placeholder="Product name">
    <input type="number" id="new-max-price" placeholder="Max price" step="0.01" style="width:100px">
    <button onclick="addProduct()">Add Product</button>
  </div>

  <div class="card">
    <b>Live Activity</b>
    <div id="log">
      {''.join(f'<div class="log-line">[{_he(e["time"])}] {_he(e["message"])}</div>' for e in reversed(activity))}
    </div>
  </div>

  <script>
    const log = document.getElementById('log');

    // SSE live updates
    const evtSource = new EventSource('/api/stream');
    evtSource.onmessage = (e) => {{
      const msg = JSON.parse(e.data);
      if (msg.type === 'activity') {{
        const line = document.createElement('div');
        line.className = 'log-line' + (msg.data.message.includes('SUCCESS') ? ' success' : msg.data.message.includes('error') || msg.data.message.includes('failed') ? ' error' : '');
        line.textContent = `[${{msg.data.time}}] ${{msg.data.message}}`;
        log.insertBefore(line, log.firstChild);
        if (log.children.length > 200) log.removeChild(log.lastChild);
      }}
    }};

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
