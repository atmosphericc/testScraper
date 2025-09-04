#!/usr/bin/env python3
"""
FIXED ULTRA-FAST DASHBOARD - Port 5001
Properly integrates with advanced evasion system and fixes all issues
"""

import json
import time
import asyncio
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template_string, jsonify, request
from flask_cors import CORS
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

app = Flask(__name__)
CORS(app)

# Fixed HTML template with proper status display and controlled checking
FIXED_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Target Monitor - Ultra Fast Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: #333;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        
        .header {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
        }
        .header h1 { color: #cc0000; font-size: 2em; margin-bottom: 10px; }
        .header .subtitle { color: #666; font-size: 1.1em; }
        
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .card {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            transition: transform 0.3s ease;
        }
        .card:hover { transform: translateY(-5px); }
        .card h3 { color: #333; margin-bottom: 15px; font-size: 1.3em; }
        
        .metric {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid #eee;
        }
        .metric:last-child { border-bottom: none; }
        .metric-label { color: #666; }
        .metric-value {
            font-weight: bold;
            font-size: 1.1em;
        }
        .metric-value.good { color: #28a745; }
        .metric-value.bad { color: #dc3545; }
        .metric-value.warning { color: #ffc107; }
        
        .product-list { max-height: 400px; overflow-y: auto; }
        .product-item {
            padding: 15px;
            margin: 10px 0;
            border-radius: 10px;
            border-left: 4px solid #ccc;
            transition: all 0.3s ease;
        }
        .product-item.available {
            background: #d4edda;
            border-left-color: #28a745;
            animation: pulse 2s infinite;
        }
        .product-item.unavailable {
            background: #f8d7da;
            border-left-color: #dc3545;
        }
        .product-item.checking {
            background: #fff3cd;
            border-left-color: #ffc107;
            animation: checking 1s infinite;
        }
        .product-item.error {
            background: #f8d7da;
            border-left-color: #fd7e14;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        @keyframes checking {
            0%, 100% { transform: translateX(0); }
            50% { transform: translateX(5px); }
        }
        
        .controls {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            margin: 20px 0;
        }
        
        button {
            background: linear-gradient(45deg, #007bff, #0056b3);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0, 123, 255, 0.3);
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 123, 255, 0.4);
        }
        button:disabled {
            background: #6c757d;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-indicator.online { background: #28a745; animation: pulse 2s infinite; }
        .status-indicator.offline { background: #dc3545; }
        .status-indicator.checking { background: #ffc107; animation: checking 1s infinite; }
        
        .countdown {
            font-family: monospace;
            font-size: 1.2em;
            color: #007bff;
            font-weight: bold;
        }
        
        .activity-log {
            max-height: 200px;
            overflow-y: auto;
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            font-family: monospace;
            font-size: 0.9em;
        }
        .log-entry {
            padding: 2px 0;
            border-bottom: 1px dotted #dee2e6;
        }
        .log-entry.success { color: #28a745; }
        .log-entry.error { color: #dc3545; }
        .log-entry.info { color: #17a2b8; }
        
        .stats-summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }
        .stat-box {
            text-align: center;
            padding: 20px;
            background: rgba(255, 255, 255, 0.8);
            border-radius: 10px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: #007bff;
        }
        .stat-label {
            color: #666;
            margin-top: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎯 Target Monitor - Ultra Fast Dashboard</h1>
            <div class="subtitle">Advanced Evasion System • Real-time Stock Monitoring • Port 5001</div>
        </div>
        
        <div class="stats-summary">
            <div class="stat-box">
                <div class="stat-value" id="total-products">{{ config.products|length }}</div>
                <div class="stat-label">Products Monitored</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="in-stock-count">-</div>
                <div class="stat-label">Currently In Stock</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="response-time">-</div>
                <div class="stat-label">Avg Response Time (ms)</div>
            </div>
            <div class="stat-box">
                <div class="stat-value countdown" id="next-check">-</div>
                <div class="stat-label">Next Check</div>
            </div>
        </div>
        
        <div class="status-grid">
            <div class="card">
                <h3>System Status</h3>
                <div class="metric">
                    <span class="metric-label">Ultra-Fast System</span>
                    <span class="metric-value good">
                        <span class="status-indicator online"></span>
                        Active
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Advanced Evasion</span>
                    <span class="metric-value good">
                        <span class="status-indicator online"></span>
                        Enabled
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Real-time Monitoring</span>
                    <span class="metric-value good" id="monitoring-status">
                        <span class="status-indicator online"></span>
                        Ready
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Last Update</span>
                    <span class="metric-value" id="last-update">{{ timestamp.strftime('%H:%M:%S') }}</span>
                </div>
                
                <div class="controls">
                    <button onclick="checkNow()" id="check-btn">🔍 Check Now</button>
                    <button onclick="refreshDashboard()">🔄 Refresh</button>
                    <button onclick="toggleAutoCheck()" id="auto-toggle">⏸️ Pause Auto-Check</button>
                </div>
            </div>
            
            <div class="card">
                <h3>Product Status</h3>
                <div class="product-list" id="product-list">
                    <div class="product-item checking">
                        <strong>Loading products...</strong><br>
                        <small>Initializing advanced evasion system...</small>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h3>Recent Activity</h3>
            <div class="activity-log" id="activity-log">
                <div class="log-entry info">{{ timestamp.strftime('%H:%M:%S') }} - Dashboard initialized</div>
                <div class="log-entry info">{{ timestamp.strftime('%H:%M:%S') }} - Advanced evasion system loaded</div>
                <div class="log-entry info">{{ timestamp.strftime('%H:%M:%S') }} - Ready for stock monitoring</div>
            </div>
        </div>
    </div>

    <script>
        let autoCheckEnabled = true;
        let checkInterval = null;
        let countdownInterval = null;
        let checkInProgress = false;
        let nextCheckTime = 180; // 3 minutes between checks
        
        // Initialize dashboard
        document.addEventListener('DOMContentLoaded', function() {
            addLogEntry('Dashboard ready', 'info');
            updateSystemTime();
            setInterval(updateSystemTime, 1000);
            
            // Check immediately on startup
            setTimeout(checkStock, 2000);
            
            // Start auto-check cycle
            startAutoCheck();
        });
        
        function startAutoCheck() {
            if (checkInterval) clearInterval(checkInterval);
            if (countdownInterval) clearInterval(countdownInterval);
            
            // Set up 3-minute check interval
            checkInterval = setInterval(function() {
                if (autoCheckEnabled && !checkInProgress) {
                    checkStock();
                }
            }, nextCheckTime * 1000);
            
            // Countdown timer
            let countdown = nextCheckTime;
            countdownInterval = setInterval(function() {
                countdown--;
                if (countdown <= 0) {
                    countdown = nextCheckTime;
                }
                document.getElementById('next-check').textContent = countdown + 's';
            }, 1000);
        }
        
        function checkStock() {
            if (checkInProgress) return;
            
            checkInProgress = true;
            const btn = document.getElementById('check-btn');
            const monitoringStatus = document.getElementById('monitoring-status');
            
            btn.disabled = true;
            btn.textContent = '🔄 Checking...';
            monitoringStatus.innerHTML = '<span class="status-indicator checking"></span>Checking';
            
            // Update products to show checking state
            const products = document.querySelectorAll('.product-item');
            products.forEach(product => {
                product.className = 'product-item checking';
            });
            
            addLogEntry('Starting stock check with advanced evasion...', 'info');
            
            fetch('/api/live-stock-status')
                .then(response => response.json())
                .then(data => {
                    displayResults(data);
                    updateStats(data);
                    addLogEntry('Stock check completed', 'success');
                    
                    monitoringStatus.innerHTML = '<span class="status-indicator online"></span>Active';
                })
                .catch(error => {
                    addLogEntry('Stock check failed: ' + error.message, 'error');
                    monitoringStatus.innerHTML = '<span class="status-indicator offline"></span>Error';
                    
                    // Show error in product list
                    document.getElementById('product-list').innerHTML = 
                        '<div class="product-item error"><strong>❌ Check Failed</strong><br>' +
                        '<small>' + error.message + '</small></div>';
                })
                .finally(() => {
                    checkInProgress = false;
                    btn.disabled = false;
                    btn.textContent = '🔍 Check Now';
                    updateLastUpdate();
                });
        }
        
        function displayResults(data) {
            const productList = document.getElementById('product-list');
            
            if (data.error) {
                productList.innerHTML = 
                    '<div class="product-item error">' +
                    '<strong>❌ Error: ' + data.error + '</strong><br>' +
                    (data.suggestion ? '<small>' + data.suggestion + '</small>' : '') +
                    '</div>';
                return;
            }
            
            let html = '';
            let totalProducts = 0;
            let availableCount = 0;
            let totalResponseTime = 0;
            
            for (const [tcin, result] of Object.entries(data)) {
                totalProducts++;
                const available = result.available || false;
                const status = result.status || 'UNKNOWN';
                const price = result.price ? '$' + result.price : 'N/A';
                const details = result.details || 'No details';
                const responseTime = result.response_time || 0;
                
                if (available) availableCount++;
                totalResponseTime += responseTime;
                
                const cssClass = available ? 'available' : (status === 'ERROR' ? 'error' : 'unavailable');
                const icon = available ? '✅' : (status === 'ERROR' ? '⚠️' : '❌');
                const statusText = available ? 'IN STOCK' : (status === 'ERROR' ? 'CHECK FAILED' : 'OUT OF STOCK');
                
                html += `
                    <div class="product-item ${cssClass}">
                        <strong>${icon} ${statusText}</strong><br>
                        <small>TCIN: ${tcin} | Price: ${price}</small><br>
                        <small>${details}</small>
                        ${responseTime > 0 ? '<br><small>Response: ' + Math.round(responseTime * 1000) + 'ms</small>' : ''}
                        ${result.confidence ? '<br><small>Confidence: ' + result.confidence + '</small>' : ''}
                    </div>
                `;
                
                // Log each product result
                addLogEntry(`${statusText}: TCIN ${tcin} (${Math.round(responseTime * 1000)}ms)`, 
                           available ? 'success' : (status === 'ERROR' ? 'error' : 'info'));
            }
            
            if (totalProducts === 0) {
                html = '<div class="product-item error"><strong>No products configured</strong><br><small>Add products to config/product_config.json</small></div>';
            }
            
            productList.innerHTML = html;
            
            // Update summary stats
            document.getElementById('in-stock-count').textContent = availableCount;
            if (totalProducts > 0) {
                const avgTime = Math.round((totalResponseTime / totalProducts) * 1000);
                document.getElementById('response-time').textContent = avgTime;
            }
        }
        
        function updateStats(data) {
            // Stats are updated in displayResults
        }
        
        function addLogEntry(message, type = 'info') {
            const log = document.getElementById('activity-log');
            const time = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = 'log-entry ' + type;
            entry.textContent = time + ' - ' + message;
            
            log.insertBefore(entry, log.firstChild);
            
            // Keep only last 20 entries
            const entries = log.querySelectorAll('.log-entry');
            if (entries.length > 20) {
                entries[entries.length - 1].remove();
            }
        }
        
        function updateLastUpdate() {
            document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
        }
        
        function updateSystemTime() {
            // Update is handled by updateLastUpdate when needed
        }
        
        function checkNow() {
            checkStock();
        }
        
        function refreshDashboard() {
            location.reload();
        }
        
        function toggleAutoCheck() {
            autoCheckEnabled = !autoCheckEnabled;
            const btn = document.getElementById('auto-toggle');
            const status = document.getElementById('monitoring-status');
            
            if (autoCheckEnabled) {
                btn.textContent = '⏸️ Pause Auto-Check';
                status.innerHTML = '<span class="status-indicator online"></span>Active';
                startAutoCheck();
                addLogEntry('Auto-check resumed', 'info');
            } else {
                btn.textContent = '▶️ Resume Auto-Check';
                status.innerHTML = '<span class="status-indicator offline"></span>Paused';
                if (checkInterval) clearInterval(checkInterval);
                if (countdownInterval) clearInterval(countdownInterval);
                document.getElementById('next-check').textContent = 'Paused';
                addLogEntry('Auto-check paused', 'info');
            }
        }
        
        // Pause when tab is not visible
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                if (checkInterval) clearInterval(checkInterval);
                if (countdownInterval) clearInterval(countdownInterval);
            } else {
                if (autoCheckEnabled) {
                    startAutoCheck();
                }
            }
        });
    </script>
</body>
</html>
"""

def get_config():
    """Load configuration"""
    config_path = Path('config/product_config.json')
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"products": []}
    return {"products": []}

@app.route('/')
def dashboard():
    """Main dashboard with proper status display"""
    config = get_config()
    timestamp = datetime.now()
    
    return render_template_string(FIXED_DASHBOARD_HTML, 
                                config=config, 
                                timestamp=timestamp)

@app.route('/api/live-stock-status')
def api_live_stock_status():
    """Fixed live stock status API with advanced evasion integration"""
    try:
        from ultra_stealth_bypass import UltraStealthBypass
        
        config = get_config()
        products = config.get('products', [])
        
        if not products:
            return jsonify({
                'error': 'No products configured in config/product_config.json',
                'suggestion': 'Add products to the configuration file'
            })
        
        # Check products using advanced evasion system
        async def check_products():
            bypass = UltraStealthBypass()
            results = {}
            
            # Limit to first 5 products to prevent overload
            products_to_check = [p for p in products if p.get('enabled', True)][:5]
            
            for product in products_to_check:
                tcin = product['tcin']
                try:
                    print(f"[DASHBOARD] Checking TCIN {tcin} with ultra stealth...")
                    result = await bypass.check_stock_ultra_stealth(tcin, warm_proxy=False)
                    
                    results[tcin] = {
                        'available': result.get('available', False),
                        'status': 'IN_STOCK' if result.get('available') else 'OUT_OF_STOCK',
                        'details': result.get('reason', result.get('error', 'Unknown')),
                        'price': result.get('price', 0),
                        'response_time': result.get('response_time', 0),
                        'confidence': result.get('confidence', 'unknown'),
                        'last_checked': datetime.now().isoformat(),
                        'method': 'ultra_stealth_advanced'
                    }
                    
                    # Handle error states
                    if result.get('status') in ['blocked_or_not_found', 'rate_limited', 'request_exception']:
                        results[tcin]['status'] = 'ERROR'
                        results[tcin]['details'] = f"Evasion needed: {result.get('error', 'Unknown error')}"
                    
                    print(f"[DASHBOARD] TCIN {tcin}: {results[tcin]['status']}")
                    
                except Exception as e:
                    print(f"[DASHBOARD] Error checking {tcin}: {e}")
                    results[tcin] = {
                        'available': False,
                        'status': 'ERROR',
                        'details': str(e),
                        'price': 0,
                        'response_time': 0,
                        'last_checked': datetime.now().isoformat()
                    }
                
                # Delay between checks
                await asyncio.sleep(2)
            
            return results
        
        # Run async check
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            stock_results = loop.run_until_complete(check_products())
            print(f"[DASHBOARD] Completed check of {len(stock_results)} products")
            return jsonify(stock_results)
        finally:
            loop.close()
            
    except ImportError as e:
        return jsonify({
            'error': f'Advanced evasion system not available: {str(e)}',
            'suggestion': 'Run: python setup_advanced_evasion.py'
        }), 500
    except Exception as e:
        return jsonify({
            'error': f'Stock check failed: {str(e)}',
            'suggestion': 'Check logs for details'
        }), 500

@app.route('/api/status')
def api_status():
    """System status API"""
    return jsonify({
        'status': 'running',
        'monitoring': True,
        'system_type': 'ultra-fast-advanced',
        'features': [
            'Advanced TLS fingerprint spoofing',
            'Anti-bot parameter injection',
            'Intelligent request timing',
            'Real-time stock checking'
        ],
        'timestamp': datetime.now().isoformat(),
        'uptime': 'Active'
    })

@app.route('/api/config')
def api_config():
    """Configuration API"""
    return jsonify(get_config())

if __name__ == '__main__':
    print("=" * 60)
    print("TARGET MONITOR - ULTRA FAST DASHBOARD (FIXED)")
    print("Access at: http://localhost:5001")
    print("=" * 60)
    print("✅ Fixed Issues:")
    print("  • 500 API errors resolved")
    print("  • Perpetual checking controlled (3-minute intervals)")
    print("  • Invalid date issue fixed")
    print("  • Startup status properly displayed")
    print("  • Advanced evasion system integrated")
    print("=" * 60)
    
    app.run(debug=False, host='127.0.0.1', port=5001)