#!/usr/bin/env python3
"""
Standalone Product Management Dashboard
Lightweight Flask app for managing product_config.json and product_catalog.json
without starting a browser, stock monitor, or purchase automation.

Port: 5002 (distinct from app.py:5000 and test_app.py:5001)
"""

import json
import os
from datetime import datetime
from flask import Flask, jsonify, request

app = Flask(__name__)
app.secret_key = 'configure-products-2025'

# ========== CONFIG HELPERS (ported verbatim from app.py) ==========

def get_catalog_config():
    """Load catalog configuration"""
    catalog_file = "config/product_catalog.json"
    try:
        if os.path.exists(catalog_file):
            with open(catalog_file, 'r') as f:
                return json.load(f)
        else:
            return {"catalog": []}
    except Exception as e:
        print(f"[CATALOG] Failed to load catalog: {e}")
        return {"catalog": []}


def save_catalog_config(catalog_data):
    """Save catalog configuration (atomic write)"""
    catalog_file = "config/product_catalog.json"
    try:
        os.makedirs('config', exist_ok=True)
        temp_file = f"{catalog_file}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(catalog_data, f, indent=2)
        if os.path.exists(catalog_file):
            os.remove(catalog_file)
        os.rename(temp_file, catalog_file)
        return True
    except Exception as e:
        print(f"[CATALOG] Failed to save catalog: {e}")
        return False


def get_product_config():
    """Load active product configuration via StockMonitor (same as app.py)"""
    try:
        from src.monitoring import StockMonitor
        stock_monitor = StockMonitor()
        return stock_monitor.get_config(), stock_monitor
    except Exception as e:
        print(f"[CONFIG] Failed to load via StockMonitor: {e}")
        # Fallback: read directly
        config_file = "config/product_config.json"
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return json.load(f), None
        return {"products": []}, None


def save_product_config(config):
    """Save active product configuration (atomic write, same as app.py lines 2026-2034)"""
    config_path = "config/product_config.json"
    temp_config_path = f"{config_path}.tmp.{os.getpid()}"
    try:
        os.makedirs('config', exist_ok=True)
        with open(temp_config_path, 'w') as f:
            json.dump(config, f, indent=2)
        if os.path.exists(config_path):
            os.remove(config_path)
        os.rename(temp_config_path, config_path)
        return True
    except Exception as e:
        print(f"[CONFIG] Failed to save config: {e}")
        return False


def fetch_product_name(tcin, stock_monitor=None):
    """Fetch product name from Target API. Falls back to 'Product {tcin}'."""
    try:
        if stock_monitor is None:
            from src.monitoring import StockMonitor
            stock_monitor = StockMonitor()
        temp_stock_data = stock_monitor.check_stock()
        name = temp_stock_data.get(tcin, {}).get('title', f'Product {tcin}')
        return name
    except Exception as e:
        print(f"[CONFIG] Failed to fetch product name for {tcin}: {e}")
        return f'Product {tcin}'


# ========== ROUTES ==========

@app.route('/')
def index():
    """Serve the standalone product management dashboard."""
    config, _ = get_product_config()
    catalog_config = get_catalog_config()
    products = config.get('products', [])
    catalog = catalog_config.get('catalog', [])
    active_tcins = [p['tcin'] for p in products]
    for item in catalog:
        item['is_actively_monitored'] = item['tcin'] in active_tcins

    HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Product Manager</title>
    <style>
        :root {
            --primary-bg: #0f1117;
            --secondary-bg: #1a1d2e;
            --accent-color: #6c63ff;
            --success-color: #4caf50;
            --danger-color: #f44336;
            --warning-color: #ff9800;
            --text-primary: #e8eaf6;
            --text-secondary: #9e9e9e;
            --text-muted: #616161;
            --border-color: #2a2d3e;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--primary-bg);
            color: var(--text-primary);
            font-family: 'Segoe UI', system-ui, sans-serif;
            font-size: 17px;
            min-height: 100vh;
            padding: 2.5rem;
        }
        h1 {
            font-size: 2rem;
            font-weight: 600;
            margin-bottom: 0.35rem;
            color: var(--text-primary);
        }
        .subtitle {
            color: var(--text-muted);
            font-size: 1rem;
            margin-bottom: 2.5rem;
        }
        .card {
            background: var(--secondary-bg);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 2.25rem;
            max-width: 1300px;
        }
        .tab-navigation {
            display: flex;
            gap: 0.75rem;
            margin-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.25rem;
        }
        .tab-button {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 0.7rem 1.4rem;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.2s ease;
        }
        .tab-button.active, .tab-button:hover {
            background: var(--accent-color);
            border-color: var(--accent-color);
            color: white;
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .add-form {
            display: flex;
            gap: 1rem;
            align-items: center;
            max-width: 640px;
            margin-bottom: 1.75rem;
        }
        .add-form input {
            flex: 1;
            padding: 0.9rem 1.2rem;
            background: var(--primary-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-primary);
            font-size: 1rem;
            font-family: 'JetBrains Mono', monospace;
            outline: none;
        }
        .add-form input:focus { border-color: var(--accent-color); }
        .btn {
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.95rem;
            font-weight: 500;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .btn-primary { background: var(--accent-color); color: white; }
        .btn-primary:hover { opacity: 0.85; }
        .btn-secondary { background: #2a2d3e; color: var(--text-primary); border: 1px solid var(--border-color); }
        .btn-secondary:hover { background: #3a3d4e; }
        .btn-danger { background: transparent; border: 1px solid var(--danger-color); color: var(--danger-color); }
        .btn-danger:hover { background: var(--danger-color); color: white; }
        .btn-disabled { opacity: 0.5; cursor: not-allowed; background: #2a2d3e; color: var(--text-muted); }
        .product-list {
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            max-height: 520px;
            overflow-y: auto;
        }
        .product-row {
            display: flex;
            align-items: center;
            padding: 1.1rem 1.4rem;
            border-bottom: 1px solid var(--border-color);
            background: var(--primary-bg);
            transition: background 0.15s;
        }
        .product-row:last-child { border-bottom: none; }
        .product-row:hover { background: #161821; }
        .product-info { flex: 1; min-width: 0; }
        .product-name {
            font-weight: 500;
            font-size: 1rem;
            color: var(--text-primary);
            margin-bottom: 0.3rem;
            word-break: break-word;
        }
        .product-tcin {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            color: var(--text-muted);
        }
        .product-actions { display: flex; gap: 0.75rem; align-items: center; }
        .empty-state {
            text-align: center;
            padding: 3rem;
            color: var(--text-muted);
            font-style: italic;
            font-size: 1rem;
        }
        .section-label {
            font-size: 0.95rem;
            color: var(--text-secondary);
            font-weight: 500;
            margin-bottom: 1rem;
        }
        /* Toast */
        #toast {
            position: fixed;
            bottom: 1.5rem;
            right: 1.5rem;
            padding: 0.75rem 1.25rem;
            border-radius: 8px;
            color: white;
            font-size: 0.875rem;
            font-weight: 500;
            opacity: 0;
            transition: opacity 0.3s ease;
            z-index: 1000;
            pointer-events: none;
        }
        #toast.show { opacity: 1; }
        #toast.success { background: var(--success-color); }
        #toast.error { background: var(--danger-color); }
        #toast.warning { background: var(--warning-color); }
    </style>
</head>
<body>
    <h1>Product Manager</h1>
    <p class="subtitle">Port 5002 &mdash; No browser, no automation. Edit config files directly.</p>

    <div class="card">
        <div class="tab-navigation">
            <button class="tab-button active" onclick="switchTab('active')" id="active-tab">
                📊 Active Products (<span id="active-count">''' + str(len(products)) + '''</span>)
            </button>
            <button class="tab-button" onclick="switchTab('catalog')" id="catalog-tab">
                📚 Product Catalog (<span id="catalog-count">''' + str(len(catalog)) + '''</span>)
            </button>
        </div>

        <!-- ===== ACTIVE PRODUCTS TAB ===== -->
        <div class="tab-content active" id="active-content">
            <div class="add-form">
                <input type="text" id="active-tcin-input" placeholder="Enter TCIN (8-10 digits)..."
                    maxlength="10" onkeydown="if(event.key==='Enter') addProduct()">
                <button class="btn btn-primary" onclick="addProduct()">➕ Add Product</button>
            </div>
            <p style="margin-top:-1rem; margin-bottom:1.25rem; font-size:0.72rem; color:var(--text-muted);">
                Product name fetched automatically from Target API
            </p>

            <div class="section-label">Active Products:</div>
            <div class="product-list" id="active-list">''' + _render_active_products(products) + '''</div>
        </div>

        <!-- ===== PRODUCT CATALOG TAB ===== -->
        <div class="tab-content" id="catalog-content">
            <div class="add-form">
                <input type="text" id="catalog-tcin-input" placeholder="Enter TCIN (8-10 digits)..."
                    maxlength="10" onkeydown="if(event.key==='Enter') addToCatalog()">
                <button class="btn btn-secondary" onclick="addToCatalog()">📚 Save to Catalog</button>
            </div>

            <div class="section-label">Catalog Products:</div>
            <div class="product-list" id="catalog-list">''' + _render_catalog_products(catalog) + '''</div>
        </div>
    </div>

    <div id="toast"></div>

    <script>
        function switchTab(name) {
            document.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(name + '-tab').classList.add('active');
            document.getElementById(name + '-content').classList.add('active');
        }

        function showToast(msg, type) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.className = 'show ' + type;
            clearTimeout(t._timer);
            t._timer = setTimeout(() => t.className = '', 3500);
        }

        function updateCounters() {
            document.getElementById('active-count').textContent =
                document.getElementById('active-list').querySelectorAll('.product-row').length;
            document.getElementById('catalog-count').textContent =
                document.getElementById('catalog-list').querySelectorAll('.product-row').length;
        }

        // ---- Active Products ----

        async function addProduct() {
            const input = document.getElementById('active-tcin-input');
            const tcin = input.value.trim();
            if (!tcin) return;

            const btn = document.querySelector('#active-content .btn-primary');
            btn.textContent = 'Adding...';
            btn.disabled = true;

            try {
                const res = await fetch('/add-product', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({tcin})
                });
                const data = await res.json();
                if (data.success) {
                    input.value = '';
                    appendActiveProduct(data.product);
                    updateCatalogRow(data.product.tcin, true);
                    updateCounters();
                    showToast('Added: ' + data.product.name, 'success');
                } else {
                    showToast('Error: ' + data.error, 'error');
                }
            } catch(e) {
                showToast('Network error: ' + e.message, 'error');
            } finally {
                btn.textContent = '➕ Add Product';
                btn.disabled = false;
            }
        }

        async function removeProduct(tcin) {
            try {
                const res = await fetch('/remove-product/' + tcin, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                const data = await res.json();
                if (data.success) {
                    const row = document.querySelector(`[data-tcin="${tcin}"]`);
                    if (row) { row.style.opacity='0'; setTimeout(() => row.remove(), 250); }
                    updateCatalogRow(tcin, false);
                    setTimeout(updateCounters, 300);
                    showToast('Removed from active monitoring', 'success');
                } else {
                    showToast('Error: ' + data.error, 'error');
                }
            } catch(e) {
                showToast('Network error: ' + e.message, 'error');
            }
        }

        function appendActiveProduct(p) {
            const list = document.getElementById('active-list');
            const empty = list.querySelector('.empty-state');
            if (empty) empty.remove();
            const div = document.createElement('div');
            div.className = 'product-row';
            div.setAttribute('data-tcin', p.tcin);
            div.innerHTML = `
                <div class="product-info">
                    <div class="product-name">${escHtml(p.name)}</div>
                    <div class="product-tcin">TCIN: ${p.tcin}</div>
                </div>
                <div class="product-actions">
                    <button class="btn btn-danger" onclick="removeProduct('${p.tcin}')">🗑️ Remove</button>
                </div>`;
            list.appendChild(div);
        }

        // ---- Catalog ----

        async function addToCatalog() {
            const input = document.getElementById('catalog-tcin-input');
            const tcin = input.value.trim();
            if (!tcin) return;

            const btn = document.querySelector('#catalog-content .btn-secondary');
            btn.textContent = 'Saving...';
            btn.disabled = true;

            try {
                const res = await fetch('/catalog/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({tcin})
                });
                const data = await res.json();
                if (data.success) {
                    input.value = '';
                    appendCatalogProduct(data.product, false);
                    updateCounters();
                    showToast('Saved to catalog: ' + data.product.name, 'success');
                } else {
                    showToast('Error: ' + data.error, 'error');
                }
            } catch(e) {
                showToast('Network error: ' + e.message, 'error');
            } finally {
                btn.textContent = '📚 Save to Catalog';
                btn.disabled = false;
            }
        }

        async function removeFromCatalog(tcin) {
            try {
                const res = await fetch('/catalog/remove/' + tcin, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                const data = await res.json();
                if (data.success) {
                    const row = document.querySelector(`[data-catalog-tcin="${tcin}"]`);
                    if (row) { row.style.opacity='0'; setTimeout(() => row.remove(), 250); }
                    setTimeout(updateCounters, 300);
                    showToast('Removed from catalog', 'success');
                } else {
                    showToast('Error: ' + data.error, 'error');
                }
            } catch(e) {
                showToast('Network error: ' + e.message, 'error');
            }
        }

        async function activateFromCatalog(tcin) {
            try {
                const res = await fetch('/catalog/activate/' + tcin, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                const data = await res.json();
                if (data.success) {
                    appendActiveProduct(data.product);
                    updateCatalogRow(tcin, true);
                    updateCounters();
                    showToast('Activated: ' + data.product.name, 'success');
                } else {
                    showToast('Error: ' + data.error, 'error');
                }
            } catch(e) {
                showToast('Network error: ' + e.message, 'error');
            }
        }

        function appendCatalogProduct(p, isActive) {
            const list = document.getElementById('catalog-list');
            const empty = list.querySelector('.empty-state');
            if (empty) empty.remove();
            const div = document.createElement('div');
            div.className = 'product-row';
            div.setAttribute('data-catalog-tcin', p.tcin);
            const added = p.date_added ? p.date_added.substring(0, 10) : '';
            div.innerHTML = `
                <div class="product-info">
                    <div class="product-name">${escHtml(p.name)}</div>
                    <div class="product-tcin">TCIN: ${p.tcin}${added ? ' • Added: ' + added : ''}</div>
                </div>
                <div class="product-actions">
                    <button class="btn ${isActive ? 'btn-disabled' : 'btn-primary'}"
                        onclick="activateFromCatalog('${p.tcin}')"
                        ${isActive ? 'disabled' : ''}>
                        ${isActive ? '✅ Already Active' : '🚀 Add to Monitor'}
                    </button>
                    <button class="btn btn-danger" onclick="removeFromCatalog('${p.tcin}')">🗑️ Remove</button>
                </div>`;
            list.appendChild(div);
        }

        function updateCatalogRow(tcin, isActive) {
            const row = document.querySelector(`[data-catalog-tcin="${tcin}"]`);
            if (!row) return;
            const btn = row.querySelector('button[onclick*="activateFromCatalog"]');
            if (!btn) return;
            btn.disabled = isActive;
            btn.className = 'btn ' + (isActive ? 'btn-disabled' : 'btn-primary');
            btn.textContent = isActive ? '✅ Already Active' : '🚀 Add to Monitor';
        }

        function escHtml(str) {
            const d = document.createElement('div');
            d.appendChild(document.createTextNode(str));
            return d.innerHTML;
        }
    </script>
</body>
</html>'''
    return HTML


def _render_active_products(products):
    if not products:
        return '<div class="empty-state">No products configured. Add your first product above.</div>'
    rows = []
    for p in products:
        name = p.get('name') or f"Product {p['tcin']}"
        tcin = p['tcin']
        rows.append(f'''
        <div class="product-row" data-tcin="{tcin}">
            <div class="product-info">
                <div class="product-name">{name}</div>
                <div class="product-tcin">TCIN: {tcin}</div>
            </div>
            <div class="product-actions">
                <button class="btn btn-danger" onclick="removeProduct('{tcin}')">🗑️ Remove</button>
            </div>
        </div>''')
    return ''.join(rows)


def _render_catalog_products(catalog):
    if not catalog:
        return '<div class="empty-state">No products in catalog. Save your first product above.</div>'
    rows = []
    for p in catalog:
        name = p.get('name') or f"Product {p['tcin']}"
        tcin = p['tcin']
        added = (p.get('date_added') or '')[:10]
        is_active = p.get('is_actively_monitored', False)
        activate_cls = 'btn-disabled' if is_active else 'btn-primary'
        activate_disabled = 'disabled' if is_active else ''
        activate_label = '✅ Already Active' if is_active else '🚀 Add to Monitor'
        rows.append(f'''
        <div class="product-row" data-catalog-tcin="{tcin}">
            <div class="product-info">
                <div class="product-name">{name}</div>
                <div class="product-tcin">TCIN: {tcin}{(" • Added: " + added) if added else ""}</div>
            </div>
            <div class="product-actions">
                <button class="btn {activate_cls}" onclick="activateFromCatalog('{tcin}')" {activate_disabled}>
                    {activate_label}
                </button>
                <button class="btn btn-danger" onclick="removeFromCatalog('{tcin}')">🗑️ Remove</button>
            </div>
        </div>''')
    return ''.join(rows)


# ========== API ENDPOINTS ==========

@app.route('/api/products', methods=['GET'])
def api_products():
    """List active products."""
    config, _ = get_product_config()
    return jsonify(config)


@app.route('/add-product', methods=['POST'])
def add_product():
    """Add product to active monitoring + auto-add to catalog."""
    try:
        data = request.get_json()
        tcin = data.get('tcin', '').strip()

        if not tcin or not tcin.isdigit() or len(tcin) < 8 or len(tcin) > 10:
            return jsonify({'success': False, 'error': 'Invalid TCIN format (must be 8-10 digits)'})

        config, stock_monitor = get_product_config()
        existing_tcins = [p['tcin'] for p in config.get('products', [])]
        if tcin in existing_tcins:
            return jsonify({'success': False, 'error': 'Product already exists'})

        product_name = fetch_product_name(tcin, stock_monitor)

        new_product = {
            'tcin': tcin,
            'name': product_name,
            'enabled': True
        }
        config['products'].append(new_product)

        if not save_product_config(config):
            return jsonify({'success': False, 'error': 'Failed to save configuration'})

        print(f"[CONFIG] Added product: {product_name} (TCIN: {tcin})")

        # COHESIVE SYSTEM: Auto-add to catalog when adding to active monitoring
        try:
            catalog_config = get_catalog_config()
            existing_catalog_tcins = [p['tcin'] for p in catalog_config.get('catalog', [])]
            if tcin not in existing_catalog_tcins:
                new_catalog_item = {
                    'tcin': tcin,
                    'name': product_name,
                    'date_added': datetime.now().isoformat(),
                    'url': f"https://www.target.com/p/-/A-{tcin}"
                }
                catalog_config['catalog'].append(new_catalog_item)
                if save_catalog_config(catalog_config):
                    print(f"[COHESIVE] Auto-added {tcin} to catalog")
                else:
                    print(f"[COHESIVE] Warning: Failed to auto-add {tcin} to catalog")
            else:
                print(f"[COHESIVE] {tcin} already exists in catalog")
        except Exception as e:
            print(f"[COHESIVE] Warning: Failed to auto-add to catalog: {e}")

        return jsonify({
            'success': True,
            'product': {
                'tcin': tcin,
                'name': product_name,
                'url': f"https://www.target.com/p/-/A-{tcin}"
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'})


@app.route('/remove-product/<tcin>', methods=['POST'])
def remove_product(tcin):
    """Remove product from active monitoring (catalog unchanged)."""
    try:
        config, _ = get_product_config()
        original_count = len(config.get('products', []))
        config['products'] = [p for p in config.get('products', []) if p.get('tcin') != tcin]

        if len(config['products']) < original_count:
            if not save_product_config(config):
                return jsonify({'success': False, 'error': 'Failed to save configuration'})
            print(f"[CONFIG] Removed product TCIN: {tcin}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Product not found'})

    except Exception as e:
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'})


@app.route('/catalog/list', methods=['GET'])
def get_catalog():
    """Get all catalog items with their active monitoring status."""
    try:
        catalog_config = get_catalog_config()
        config, _ = get_product_config()
        active_tcins = [p['tcin'] for p in config.get('products', [])]
        for item in catalog_config.get('catalog', []):
            item['is_actively_monitored'] = item['tcin'] in active_tcins
        return jsonify(catalog_config)
    except Exception as e:
        return jsonify({'catalog': [], 'error': str(e)})


@app.route('/catalog/add', methods=['POST'])
def add_to_catalog():
    """Add TCIN to catalog without activating monitoring."""
    try:
        data = request.get_json()
        tcin = data.get('tcin', '').strip()

        if not tcin or not tcin.isdigit() or len(tcin) < 8 or len(tcin) > 10:
            return jsonify({'success': False, 'error': 'Invalid TCIN format (must be 8-10 digits)'})

        catalog_config = get_catalog_config()
        existing_catalog_tcins = [p['tcin'] for p in catalog_config.get('catalog', [])]
        if tcin in existing_catalog_tcins:
            return jsonify({'success': False, 'error': 'Product already in catalog'})

        _, stock_monitor = get_product_config()
        product_name = fetch_product_name(tcin, stock_monitor)

        new_catalog_item = {
            'tcin': tcin,
            'name': product_name,
            'date_added': datetime.now().isoformat(),
            'url': f"https://www.target.com/p/-/A-{tcin}"
        }
        catalog_config['catalog'].append(new_catalog_item)

        if save_catalog_config(catalog_config):
            print(f"[CATALOG] Added {product_name} (TCIN: {tcin})")
            return jsonify({'success': True, 'product': new_catalog_item})
        else:
            return jsonify({'success': False, 'error': 'Failed to save catalog'})

    except Exception as e:
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'})


@app.route('/catalog/remove/<tcin>', methods=['POST'])
def remove_from_catalog(tcin):
    """Remove TCIN from catalog."""
    try:
        catalog_config = get_catalog_config()
        original_length = len(catalog_config.get('catalog', []))
        catalog_config['catalog'] = [p for p in catalog_config.get('catalog', []) if p['tcin'] != tcin]

        if len(catalog_config['catalog']) < original_length:
            if save_catalog_config(catalog_config):
                print(f"[CATALOG] Removed TCIN: {tcin}")
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Failed to save catalog'})
        else:
            return jsonify({'success': False, 'error': 'Product not found in catalog'})

    except Exception as e:
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'})


@app.route('/catalog/activate/<tcin>', methods=['POST'])
def activate_from_catalog(tcin):
    """Move TCIN from catalog into active monitoring."""
    try:
        catalog_config = get_catalog_config()
        catalog_product = next(
            (p for p in catalog_config.get('catalog', []) if p['tcin'] == tcin), None
        )
        if not catalog_product:
            return jsonify({'success': False, 'error': 'Product not found in catalog'})

        config, _ = get_product_config()
        existing_tcins = [p['tcin'] for p in config.get('products', [])]
        if tcin in existing_tcins:
            return jsonify({'success': False, 'error': 'Product already in active monitoring'})

        new_product = {
            'tcin': tcin,
            'name': catalog_product['name'],
            'enabled': True,
            'url': f"https://www.target.com/p/-/A-{tcin}"
        }
        config['products'].append(new_product)

        # Atomic write (same as app.py lines 2026-2034)
        config_path = "config/product_config.json"
        temp_config_path = f"{config_path}.tmp.{os.getpid()}"
        with open(temp_config_path, 'w') as f:
            json.dump(config, f, indent=2)
        if os.path.exists(config_path):
            os.remove(config_path)
        os.rename(temp_config_path, config_path)

        print(f"[CONFIG] Activated from catalog: {catalog_product['name']} (TCIN: {tcin})")
        return jsonify({'success': True, 'product': new_product})

    except Exception as e:
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'})


# ========== MAIN ==========

if __name__ == '__main__':
    print("=" * 60)
    print("  Product Manager — Standalone Config Dashboard")
    print("  Port: 5002  (no browser, no automation)")
    print("  Files: config/product_config.json")
    print("         config/product_catalog.json")
    print("=" * 60)
    print("  Open: http://127.0.0.1:5002")
    print("=" * 60)

    from waitress import serve
    serve(app, host='127.0.0.1', port=5002, threads=4, channel_timeout=60)
