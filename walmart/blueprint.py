"""
Walmart bot — Flask Blueprint for use in unified_app.py.

All routes are mounted under /walmart prefix.
The module-level globals (_manager, _sse_clients, etc.) are independent of
app.py's Target globals, so both retailers run safely in one process.
"""

import asyncio
import atexit
import json
import logging
import os
import queue
import threading
import time
from html import escape as _he

from flask import Blueprint, Response, jsonify, request, stream_with_context

from .config import get_config, save_config, get_enabled_products
from .purchase_manager import WalmartPurchaseManager
from .self_healing_agent import SelfHealingAgent
from .logging_manager import get_walmart_logger, log_activity

logger = logging.getLogger(__name__)
walmart_logger = get_walmart_logger()

# ---------------------------------------------------------------------------
# Blueprint definition
# ---------------------------------------------------------------------------

walmart_bp = Blueprint("walmart", __name__, url_prefix="/walmart")

# Ensure CHECKOUT_MODE env var matches the default _test_mode=False (LIVE).
# Executor branches on CHECKOUT_MODE == "PRODUCTION" — "LIVE" is just the UI
# label; without PRODUCTION the non-test branch never runs and orders silently
# fall through to TEST mode. Mirrors the walmart_app.py fix from 2026-05-11.
os.environ.setdefault("CHECKOUT_MODE", "PRODUCTION")

# ---------------------------------------------------------------------------
# Global state (scoped to this module, not shared with Target)
# ---------------------------------------------------------------------------

# Note: type annotation says WalmartPurchaseManager but start_manager()
# instantiates SelfHealingAgent — both expose the same get_status / get_activity_log
# / start / stop API surface used by the routes below.
_manager = None
_manager_loop: asyncio.AbstractEventLoop = None
_test_mode = False  # default to LIVE

_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _broadcast(event_type: str, data: dict):
    """Push an SSE event to all connected Walmart dashboard clients."""
    payload = json.dumps({
        "type": event_type,
        "retailer": "walmart",
        "data": data,
        "ts": time.time(),
    })
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
# Manager startup (called from unified_app.py at server start)
# ---------------------------------------------------------------------------

def start_manager():
    """Launch the Walmart SelfHealingAgent in a background thread."""
    global _manager, _manager_loop

    _manager = SelfHealingAgent(status_callback=_status_callback)
    _manager_loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(_manager_loop)
        try:
            _manager_loop.run_until_complete(
                _manager.start()
            )
        except Exception:
            logger.exception("[WALMART] Manager start() failed — bot will not run")
            return
        _manager_loop.run_forever()

    t = threading.Thread(target=_run, daemon=True, name="WalmartManagerThread")
    t.start()
    # Register an atexit fallback so a clean Python exit (e.g. Waitress shutdown
    # or a sys.exit not preceded by os._exit) still tries to stop the manager.
    # NOTE: This does NOT run if the process exits via os._exit() — which is
    # what app.py's Target shutdown_handler does on SIGINT/SIGTERM. Under
    # unified_app.py, the most reliable way to stop the Walmart manager on
    # Ctrl+C is for the operator to call stop_manager() from app.py's
    # shutdown_handler. That edit is out of Walmart-only scope here.
    atexit.register(stop_manager)
    logger.info("[WALMART] Purchase manager started in background thread")


def stop_manager(timeout: float = 5.0) -> bool:
    """Stop the Walmart manager and its event loop. Safe to call multiple times.

    Returns True if the manager was stopped cleanly, False on timeout/error.
    Designed to be called from the operator's signal handler or atexit. Idempotent.
    """
    global _manager, _manager_loop
    if _manager is None or _manager_loop is None:
        return True
    mgr = _manager
    loop = _manager_loop
    # Null out the globals first so a second call short-circuits cleanly even
    # if the stop coroutine takes a while to finish.
    _manager = None
    _manager_loop = None
    try:
        if not loop.is_closed() and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(mgr.stop(), loop)
                fut.result(timeout=timeout)
            except Exception:
                logger.exception("[WALMART] stop_manager: manager.stop() raised")
                return False
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass  # loop already stopping
        return True
    except Exception:
        logger.exception("[WALMART] stop_manager: unexpected error")
        return False


# ---------------------------------------------------------------------------
# Helper: product table row
# ---------------------------------------------------------------------------

def _product_row(p: dict, state_by_id: dict) -> str:
    item_id = p["item_id"]
    name = p.get("name", "Unknown")
    priority = p.get("priority", "—")
    state = state_by_id.get(item_id, {}).get("state", "MONITORING")
    if state == "SUCCESS":
        badge = "badge-green"
    elif state in ("PURCHASING", "IN_QUEUE"):
        badge = "badge-yellow"
    else:
        badge = "badge-blue"
    safe_id = _he(item_id)
    safe_name = _he(name)
    safe_state = _he(state)
    import json as _json
    js_id = _json.dumps(item_id)
    return (
        f'<tr>'
        f'<td>{_he(str(priority))}</td>'
        f'<td>{safe_id}</td>'
        f'<td>{safe_name}</td>'
        f'<td><span class="badge {badge}">{safe_state}</span></td>'
        f'<td><button onclick="removeProduct({js_id})">Remove</button></td>'
        f'</tr>\n'
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@walmart_bp.route("/")
def index():
    products = get_enabled_products()
    try:
        status = _manager.get_status() if _manager else {}
        activity = _manager.get_activity_log()[-50:] if _manager else []
    except Exception:
        logger.exception("[WALMART] index: get_status/get_activity_log failed")
        status, activity = {}, []
    state_by_id = {ps["item_id"]: ps for ps in status.get("products", [])}

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Walmart Bot</title>
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
    input[type=text], input[type=number] {{ background: #111; border: 1px solid #444; color: #eee; padding: 6px 10px; border-radius: 4px; width: 200px; }}
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
    .nav {{ margin-bottom: 20px; }}
    .nav a {{ color: #29b6f6; margin-right: 16px; text-decoration: none; font-weight: bold; }}
    .nav a.active {{ color: #0071ce; border-bottom: 2px solid #0071ce; padding-bottom: 4px; }}
  </style>
</head>
<body>
  <div class="nav">
    <a href="/">&#127919; Target</a>
    <a href="/walmart/" class="active">&#x1F6D2; Walmart</a>
  </div>
  <h1>&#x1F6D2; Walmart Bot</h1>

  <div class="card">
    <b>Status:</b>
    <span class="status-dot {'dot-green' if status.get('running') else 'dot-red'}"></span>
    {'Running' if status.get('running') else 'Stopped'}
    &nbsp;&nbsp;
    {'<span class="badge badge-red">CIRCUIT OPEN</span>' if status.get('circuit_open') else ''}
    &nbsp;
    <span class="badge {'badge-yellow' if _test_mode else 'badge-green'}">{'TEST MODE' if _test_mode else 'LIVE'}</span>
    <br><br>
    <button onclick="fetch('/walmart/api/test/enable',{{method:'POST'}}).then(()=>location.reload())">Enable Test Mode</button>
    <button onclick="fetch('/walmart/api/test/disable',{{method:'POST'}}).then(()=>location.reload())">Disable Test Mode</button>
  </div>

  <div class="card">
    <b>Products</b>
    <table>
      <tr><th>Priority</th><th>Item ID</th><th>Name</th><th>State</th><th>Action</th></tr>
      {''.join(_product_row(p, state_by_id) for p in products)}
    </table>
    <br>
    <input type="text" id="new-item-id" placeholder="Walmart Item ID">
    <input type="text" id="new-item-name" placeholder="Product name">
    <input type="number" id="new-max-price" placeholder="Max price" step="0.01" style="width:100px">
    <input type="number" id="new-priority" placeholder="Priority" style="width:80px" value="1">
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
    const evtSource = new EventSource('/walmart/api/stream');
    evtSource.onmessage = (e) => {{
      const msg = JSON.parse(e.data);
      if (msg.type === 'activity') {{
        const line = document.createElement('div');
        const m = msg.data.message;
        line.className = 'log-line' + (m.includes('SUCCESS') ? ' success' : (m.toLowerCase().includes('error') || m.toLowerCase().includes('failed')) ? ' error' : '');
        line.textContent = `[${{msg.data.time}}] ${{m}}`;
        log.insertBefore(line, log.firstChild);
        if (log.children.length > 200) log.removeChild(log.lastChild);
      }}
    }};

    function addProduct() {{
      const item_id = document.getElementById('new-item-id').value.trim();
      const name = document.getElementById('new-item-name').value.trim() || 'Unknown';
      const max_price = parseFloat(document.getElementById('new-max-price').value) || null;
      const priority = parseInt(document.getElementById('new-priority').value) || 999;
      if (!item_id) return;
      fetch('/walmart/add-product', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{item_id, name, max_price, priority}})
      }}).then(() => location.reload());
    }}

    function removeProduct(item_id) {{
      if (!confirm('Remove ' + item_id + '?')) return;
      fetch('/walmart/remove-product/' + item_id, {{method: 'POST'}}).then(() => location.reload());
    }}
  </script>
</body>
</html>"""
    return html


@walmart_bp.route("/api/stream")
def stream():
    client_queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_clients.append(client_queue)

    def generate():
        try:
            yield 'data: {"type":"connected","retailer":"walmart"}\n\n'
            while True:
                try:
                    data = client_queue.get(timeout=1.0)
                    yield data
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if client_queue in _sse_clients:
                    _sse_clients.remove(client_queue)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@walmart_bp.route("/api/status")
def api_status():
    # Wrap manager.get_status() in try/except — without it, a transient state
    # corruption mid-shutdown crashes the 15s dashboard polling and the SSE
    # reconnect storm follows. Mirrors the walmart_app.py PM2 fix.
    try:
        if _manager:
            status = _manager.get_status()
            status["activity_log"] = _manager.get_activity_log()[-50:]
        else:
            status = {
                "running": False,
                "products": [
                    {"item_id": p["item_id"], "name": p.get("name", "Unknown"),
                     "priority": p.get("priority", 999), "state": "MONITORING"}
                    for p in get_enabled_products()
                ],
                "activity_log": [],
            }
    except Exception:
        logger.exception("[WALMART] /api/status failed")
        status = {"running": False, "error": "status_unavailable",
                  "products": [], "activity_log": []}
    status["test_mode"] = _test_mode
    return jsonify(status)


@walmart_bp.route("/add-product", methods=["POST"])
def add_product():
    try:
        data = request.get_json(force=True) or {}
        item_id = str(data.get("item_id", "")).strip()
        name = str(data.get("name", "Unknown")).strip()
        max_price = data.get("max_price")
        priority = data.get("priority")

        if not item_id:
            return jsonify({"error": "item_id required"}), 400

        parsed_price = None
        if max_price is not None:
            try:
                parsed_price = float(max_price)
            except (TypeError, ValueError):
                return jsonify({"error": "max_price must be a number"}), 400

        parsed_priority = 999
        if priority is not None:
            try:
                parsed_priority = int(priority)
            except (TypeError, ValueError):
                pass

        config = get_config()
        for p in config["products"]:
            if p["item_id"] == item_id:
                return jsonify({"message": "already exists"}), 200

        if parsed_priority == 999:
            parsed_priority = len(config["products"]) + 1

        config["products"].append({
            "item_id": item_id,
            "name": name,
            "enabled": True,
            "max_price": parsed_price,
            "priority": parsed_priority,
            "url": f"https://www.walmart.com/ip/x/{item_id}",
        })
        save_config(config)
        _status_callback(f"Product added: {name} ({item_id}) priority={parsed_priority}")
        return jsonify({"message": "added", "item_id": item_id})
    except Exception as e:
        logger.exception("[WALMART] /add-product error")
        return jsonify({"error": str(e)}), 500


@walmart_bp.route("/remove-product/<item_id>", methods=["POST"])
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
        logger.exception("[WALMART] /remove-product error")
        return jsonify({"error": str(e)}), 500


@walmart_bp.route("/reorder-products", methods=["POST"])
def reorder_products():
    try:
        data = request.get_json(force=True) or {}
        order = data.get("order", [])
        if not isinstance(order, list):
            return jsonify({"error": "order must be a list"}), 400
        config = get_config()
        id_to_product = {p["item_id"]: p for p in config["products"]}
        reordered = [id_to_product[iid] for iid in order if iid in id_to_product]
        # Append any products not in the order list at the end
        included = set(order)
        for p in config["products"]:
            if p["item_id"] not in included:
                reordered.append(p)
        # Update priority field to match new order
        for i, p in enumerate(reordered):
            p["priority"] = i + 1
        config["products"] = reordered
        save_config(config)
        return jsonify({"success": True})
    except Exception as e:
        logger.exception("[WALMART] /reorder-products error")
        return jsonify({"error": str(e)}), 500


@walmart_bp.route("/api/test/enable", methods=["POST"])
def test_enable():
    global _test_mode
    _test_mode = True
    os.environ["CHECKOUT_MODE"] = "TEST"
    _status_callback("[TEST] Test mode enabled")
    return jsonify({"test_mode": True})


@walmart_bp.route("/api/test/disable", methods=["POST"])
def test_disable():
    global _test_mode
    _test_mode = False
    os.environ["CHECKOUT_MODE"] = "PRODUCTION"
    _status_callback("[TEST] Test mode disabled — LIVE mode")
    return jsonify({"test_mode": False})


@walmart_bp.route("/health")
def health():
    running = False
    if _manager:
        try:
            running = _manager.get_status().get("running", False)
        except Exception:
            logger.exception("[WALMART] /health get_status failed")
    return jsonify({
        "status": "ok",
        "running": running,
        "test_mode": _test_mode,
    })
