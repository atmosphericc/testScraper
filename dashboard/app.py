from flask import Flask, render_template, jsonify, request
import json
import aiohttp
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
import logging
import random
import sqlite3
import os
from typing import Dict, List, Any
import time

app = Flask(__name__)
app.secret_key = 'target-monitor-2025'

# Initialize enhanced data storage
class DataStore:
    def __init__(self):
        self.db_path = Path('dashboard/analytics.db')
        self.db_path.parent.mkdir(exist_ok=True)
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for analytics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Stock history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tcin TEXT NOT NULL,
                product_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                in_stock BOOLEAN,
                price REAL,
                availability_text TEXT,
                response_time_ms INTEGER
            )
        ''')
        
        # Purchase attempts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS purchase_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tcin TEXT NOT NULL,
                product_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN,
                reason TEXT,
                price REAL,
                order_number TEXT
            )
        ''')
        
        # Proxy performance table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS proxy_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_host TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN,
                response_time_ms INTEGER,
                error_message TEXT
            )
        ''')
        
        # System stats table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_checks INTEGER,
                in_stock_items INTEGER,
                active_purchases INTEGER,
                cycle_count INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def record_stock_check(self, tcin: str, product_name: str, in_stock: bool, price: float = None, availability: str = None, response_time: int = None):
        """Record a stock check result"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO stock_history (tcin, product_name, in_stock, price, availability_text, response_time_ms)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (tcin, product_name, in_stock, price, availability, response_time))
        
        conn.commit()
        conn.close()
    
    def record_purchase_attempt(self, tcin: str, product_name: str, success: bool, reason: str = None, price: float = None, order_number: str = None):
        """Record a purchase attempt"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO purchase_attempts (tcin, product_name, success, reason, price, order_number)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (tcin, product_name, success, reason, price, order_number))
        
        conn.commit()
        conn.close()
    
    def record_proxy_performance(self, proxy_host: str, success: bool, response_time: int = None, error: str = None):
        """Record proxy performance"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO proxy_performance (proxy_host, success, response_time_ms, error_message)
            VALUES (?, ?, ?, ?)
        ''', (proxy_host, success, response_time, error))
        
        conn.commit()
        conn.close()
    
    def record_system_stats(self, total_checks: int, in_stock_items: int, active_purchases: int, cycle_count: int):
        """Record system statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO system_stats (total_checks, in_stock_items, active_purchases, cycle_count)
            VALUES (?, ?, ?, ?)
        ''', (total_checks, in_stock_items, active_purchases, cycle_count))
        
        conn.commit()
        conn.close()
    
    def get_stock_history(self, tcin: str = None, hours: int = 24) -> List[Dict]:
        """Get stock history for analysis"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = '''
            SELECT tcin, product_name, timestamp, in_stock, price, availability_text, response_time_ms
            FROM stock_history
            WHERE timestamp > datetime('now', '-{} hours')
        '''.format(hours)
        
        if tcin:
            query += ' AND tcin = ?'
            cursor.execute(query, (tcin,))
        else:
            cursor.execute(query)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'tcin': row[0],
                'product_name': row[1],
                'timestamp': row[2],
                'in_stock': bool(row[3]),
                'price': row[4],
                'availability': row[5],
                'response_time': row[6]
            })
        
        conn.close()
        return results
    
    def get_purchase_history(self, hours: int = 168) -> List[Dict]:  # 7 days default
        """Get purchase attempt history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT tcin, product_name, timestamp, success, reason, price, order_number
            FROM purchase_attempts
            WHERE timestamp > datetime('now', '-{} hours')
            ORDER BY timestamp DESC
        '''.format(hours))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'tcin': row[0],
                'product_name': row[1],
                'timestamp': row[2],
                'success': bool(row[3]),
                'reason': row[4],
                'price': row[5],
                'order_number': row[6]
            })
        
        conn.close()
        return results
    
    def get_proxy_stats(self, hours: int = 24) -> Dict:
        """Get proxy performance statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                proxy_host,
                COUNT(*) as total_requests,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_requests,
                AVG(response_time_ms) as avg_response_time,
                MAX(timestamp) as last_used
            FROM proxy_performance
            WHERE timestamp > datetime('now', '-{} hours')
            GROUP BY proxy_host
        '''.format(hours))
        
        stats = {}
        for row in cursor.fetchall():
            host = row[0]
            total = row[1]
            success = row[2]
            avg_time = row[3]
            last_used = row[4]
            
            stats[host] = {
                'total_requests': total,
                'successful_requests': success,
                'success_rate': (success / total * 100) if total > 0 else 0,
                'avg_response_time': round(avg_time, 2) if avg_time else 0,
                'last_used': last_used
            }
        
        conn.close()
        return stats
    
    def get_analytics_summary(self) -> Dict:
        """Get comprehensive analytics summary"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Stock check analytics
        cursor.execute('''
            SELECT 
                COUNT(*) as total_checks,
                SUM(CASE WHEN in_stock = 1 THEN 1 ELSE 0 END) as in_stock_count,
                AVG(response_time_ms) as avg_response_time
            FROM stock_history
            WHERE timestamp > datetime('now', '-24 hours')
        ''')
        
        stock_stats = cursor.fetchone()
        
        # Purchase analytics
        cursor.execute('''
            SELECT 
                COUNT(*) as total_attempts,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_purchases
            FROM purchase_attempts
            WHERE timestamp > datetime('now', '-168 hours')  -- 7 days
        ''')
        
        purchase_stats = cursor.fetchone()
        
        # Recent activity
        cursor.execute('''
            SELECT tcin, product_name, timestamp, in_stock, price
            FROM stock_history
            WHERE in_stock = 1 AND timestamp > datetime('now', '-24 hours')
            ORDER BY timestamp DESC
            LIMIT 10
        ''')
        
        recent_stock = []
        for row in cursor.fetchall():
            recent_stock.append({
                'tcin': row[0],
                'product_name': row[1],
                'timestamp': row[2],
                'price': row[4]
            })
        
        conn.close()
        
        return {
            'stock_analytics': {
                'total_checks_24h': stock_stats[0] or 0,
                'in_stock_found_24h': stock_stats[1] or 0,
                'avg_response_time': round(stock_stats[2], 2) if stock_stats[2] else 0,
                'availability_rate': round((stock_stats[1] or 0) / (stock_stats[0] or 1) * 100, 2)
            },
            'purchase_analytics': {
                'total_attempts_7d': purchase_stats[0] or 0,
                'successful_purchases_7d': purchase_stats[1] or 0,
                'success_rate': round((purchase_stats[1] or 0) / (purchase_stats[0] or 1) * 100, 2)
            },
            'recent_stock_alerts': recent_stock
        }

# Global data store instance
data_store = DataStore()

# Disable Flask logging in production
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

class DashboardData:
    @staticmethod
    def get_config():
        """Load current configuration"""
        config_path = Path('config/product_config.json')
        if config_path.exists():
            with open(config_path, 'r') as f:
                return json.load(f)
        return {"products": [], "settings": {}}
    
    @staticmethod
    async def fetch_product_name(tcin: str):
        """Fetch product name from Target API"""
        base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        store_id = "865"
        
        chrome_version = random.randint(120, 125)
        headers = {
            'accept': 'application/json',
            'accept-language': 'en-US,en;q=0.9',
            'origin': 'https://www.target.com',
            'user-agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36',
            'sec-ch-ua': f'"Google Chrome";v="{chrome_version}", "Chromium";v="{chrome_version}", "Not?A_Brand";v="24"',
            'sec-fetch-site': 'same-site',
            'sec-fetch-mode': 'cors'
        }
        
        params = {
            'key': api_key,
            'tcin': tcin,
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_store_id': 'true',
            'visitor_id': ''.join(random.choices('0123456789ABCDEF', k=32))
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(base_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        product = data['data']['product']
                        item = product['item']
                        name = item.get('product_description', {}).get('title', f'Product {tcin}')
                        # Clean up HTML entities and encoding issues
                        import html
                        name = html.unescape(name)  # Convert HTML entities like &#233; to é
                        name = name.encode('ascii', 'ignore').decode('ascii')  # Remove non-ASCII
                        return name[:60].strip()  # Truncate long names and strip whitespace
                    else:
                        return f'Product {tcin}'
        except Exception as e:
            return f'Product {tcin}'
    
    @staticmethod
    def get_enriched_config():
        """Get configuration with enriched product names"""
        config = DashboardData.get_config()
        
        # Run async function to fetch product names
        async def enrich_products():
            enriched_products = []
            for product in config.get('products', []):
                tcin = product['tcin']
                # Try to get cached name first, otherwise fetch from API
                dynamic_name = await DashboardData.fetch_product_name(tcin)
                
                enriched_product = product.copy()
                enriched_product['dynamic_name'] = dynamic_name
                enriched_product['url'] = f"https://www.target.com/p/-/A-{tcin}"
                enriched_products.append(enriched_product)
            
            config['products'] = enriched_products
            return config
        
        # Run the async function
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(enrich_products())
            loop.close()
            return result
        except Exception as e:
            # Fallback to regular config if API fails
            for product in config.get('products', []):
                product['dynamic_name'] = product.get('name', f"Product {product['tcin']}")
                product['url'] = f"https://www.target.com/p/-/A-{product['tcin']}"
            return config
    
    @staticmethod
    def get_recent_logs(lines=50):
        """Get recent log entries"""
        log_path = Path('logs/monitor.log')
        if not log_path.exists():
            return []
        
        try:
            with open(log_path, 'r') as f:
                return f.readlines()[-lines:]
        except:
            return []
    
    @staticmethod
    def get_purchase_logs(lines=20):
        """Get recent purchase attempts"""
        log_path = Path('logs/purchases.log')
        if not log_path.exists():
            return []
        
        try:
            with open(log_path, 'r') as f:
                return f.readlines()[-lines:]
        except:
            return []
    
    @staticmethod
    def parse_status():
        """Parse current status from logs"""
        logs = DashboardData.get_recent_logs(100)
        status = {
            'monitoring': False,
            'total_checks': 0,
            'in_stock_count': 0,
            'last_update': None,
            'recent_stock': [],
            'recent_purchases': []
        }
        
        # Find most recent status line for monitoring state, total checks, and in stock count
        for line in reversed(logs):
            if 'Status - Cycle:' in line:
                try:
                    parts = line.split('|')
                    for part in parts:
                        if 'Checks:' in part:
                            status['total_checks'] = int(part.split(':')[1].strip())
                        elif 'In Stock:' in part:
                            status['in_stock_count'] = int(part.split(':')[1].strip())
                    status['monitoring'] = True
                    status['last_update'] = line.split(' - ')[0]
                except:
                    pass
                break
        
        # Track which specific products are currently in stock by TCIN
        status['stock_by_tcin'] = {}
        
        # Look through logs in reverse chronological order to get most recent status
        import re
        recent_status = {}
        
        # Go through logs backwards to find the most recent status for each TCIN
        for i in reversed(range(len(logs))):
            line = logs[i]
            
            if 'IN STOCK:' in line:
                # Look for the next line that has "Initiating purchase for TCIN"
                if i + 1 < len(logs):
                    next_line = logs[i + 1]
                    tcin_match = re.search(r'Initiating purchase for (\d+)', next_line)
                    if tcin_match:
                        tcin = tcin_match.group(1)
                        # Only set if we haven't seen this TCIN yet (most recent wins)
                        if tcin not in recent_status:
                            recent_status[tcin] = 'IN_STOCK'
                        
            elif 'OUT OF STOCK:' in line:
                # Extract product name and try to match it back to TCIN
                product_match = re.search(r'OUT OF STOCK: (.+)$', line)
                if product_match:
                    product_name = product_match.group(1).strip()
                    # Look forward through logs to find matching IN STOCK entry
                    for j in range(max(0, i-20), min(len(logs), i+20)):
                        if 'IN STOCK:' in logs[j] and product_name in logs[j]:
                            # Found matching IN STOCK entry, get TCIN from next line
                            if j + 1 < len(logs):
                                purchase_line = logs[j + 1]
                                tcin_match = re.search(r'Initiating purchase for (\d+)', purchase_line)
                                if tcin_match:
                                    tcin = tcin_match.group(1)
                                    # Only set if we haven't seen this TCIN yet (most recent wins)
                                    if tcin not in recent_status:
                                        recent_status[tcin] = 'OUT_OF_STOCK'
                                    break
        
        status['stock_by_tcin'] = recent_status
        
        # Collect recent stock alerts and purchase attempts
        for line in reversed(logs):
            if 'IN STOCK:' in line:
                status['recent_stock'].append(line.strip())
                if len(status['recent_stock']) >= 5:
                    break
            
            if 'PURCHASE SUCCESS' in line or 'PURCHASE FAILED' in line:
                status['recent_purchases'].append(line.strip())
                if len(status['recent_purchases']) >= 10:
                    break
        
        return status

@app.route('/')
def dashboard():
    """Main dashboard view"""
    config = DashboardData.get_enriched_config()
    status = DashboardData.parse_status()
    
    return render_template('dashboard.html', 
                         config=config, 
                         status=status,
                         timestamp=datetime.now())

@app.route('/api/status')
def api_status():
    """API endpoint for live status"""
    return jsonify(DashboardData.parse_status())

@app.route('/api/logs')
def api_logs():
    """API endpoint for recent logs"""
    return jsonify({
        'monitor': DashboardData.get_recent_logs(50),
        'purchases': DashboardData.get_purchase_logs(20)
    })

@app.route('/api/analytics')
def api_analytics():
    """API endpoint for enhanced analytics"""
    return jsonify(data_store.get_analytics_summary())

@app.route('/api/stock-history/<tcin>')
def api_stock_history(tcin):
    """API endpoint for individual product stock history"""
    hours = request.args.get('hours', 24, type=int)
    return jsonify(data_store.get_stock_history(tcin=tcin, hours=hours))

@app.route('/api/purchase-history')
def api_purchase_history():
    """API endpoint for purchase attempt history"""
    hours = request.args.get('hours', 168, type=int)  # 7 days default
    return jsonify(data_store.get_purchase_history(hours=hours))

@app.route('/api/proxy-stats')
def api_proxy_stats():
    """API endpoint for proxy performance statistics"""
    hours = request.args.get('hours', 24, type=int)
    return jsonify(data_store.get_proxy_stats(hours=hours))

@app.route('/api/record-stock')
def api_record_stock():
    """API endpoint for recording stock checks (used by monitor)"""
    tcin = request.args.get('tcin')
    product_name = request.args.get('name', 'Unknown Product')
    in_stock = request.args.get('in_stock', 'false').lower() == 'true'
    price = request.args.get('price', type=float)
    availability = request.args.get('availability')
    response_time = request.args.get('response_time', type=int)
    
    if tcin:
        data_store.record_stock_check(tcin, product_name, in_stock, price, availability, response_time)
        return jsonify({'status': 'recorded'})
    
    return jsonify({'error': 'missing tcin'}), 400

@app.route('/api/record-purchase')
def api_record_purchase():
    """API endpoint for recording purchase attempts (used by monitor)"""
    tcin = request.args.get('tcin')
    product_name = request.args.get('name', 'Unknown Product')
    success = request.args.get('success', 'false').lower() == 'true'
    reason = request.args.get('reason')
    price = request.args.get('price', type=float)
    order_number = request.args.get('order_number')
    
    if tcin:
        data_store.record_purchase_attempt(tcin, product_name, success, reason, price, order_number)
        return jsonify({'status': 'recorded'})
    
    return jsonify({'error': 'missing tcin'}), 400

@app.route('/api/record-proxy')
def api_record_proxy():
    """API endpoint for recording proxy performance (used by monitor)"""
    proxy_host = request.args.get('proxy_host')
    success = request.args.get('success', 'false').lower() == 'true'
    response_time = request.args.get('response_time', type=int)
    error = request.args.get('error', '')
    
    if proxy_host:
        data_store.record_proxy_performance(proxy_host, success, response_time, error)
        return jsonify({'status': 'recorded'})
    
    return jsonify({'error': 'missing proxy_host'}), 400

if __name__ == '__main__':
    print("="*60)
    print("TARGET MONITOR DASHBOARD")
    print("Access at: http://localhost:5000")
    print("="*60)
    app.run(debug=False, host='0.0.0.0', port=5000)