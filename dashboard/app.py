from flask import Flask, render_template, jsonify
import json
import aiohttp
import asyncio
from pathlib import Path
from datetime import datetime
import logging
import random

app = Flask(__name__)
app.secret_key = 'target-monitor-2025'

# Disable Flask logging in production
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

class DashboardData:
    @staticmethod
    def get_config():
        """Load current configuration"""
        config_path = Path('../config/product_config.json')
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
        log_path = Path('../logs/monitor.log')
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
        log_path = Path('../logs/purchases.log')
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
        
        # Look through logs chronologically to track stock status changes
        import re
        for i, line in enumerate(logs):
            if 'IN STOCK:' in line:
                # Look for the next line that has "Initiating purchase for TCIN"
                if i + 1 < len(logs):
                    next_line = logs[i + 1]
                    tcin_match = re.search(r'Initiating purchase for (\d+)', next_line)
                    if tcin_match:
                        tcin = tcin_match.group(1)
                        status['stock_by_tcin'][tcin] = 'IN_STOCK'
                        
            elif 'OUT OF STOCK:' in line:
                # Extract product name and try to match it back to TCIN
                # Look back through recent IN STOCK events to find the TCIN
                product_match = re.search(r'OUT OF STOCK: (.+)$', line)
                if product_match:
                    product_name = product_match.group(1).strip()
                    # Look backwards through recent logs to find matching IN STOCK entry
                    for j in range(max(0, i-20), i):
                        if 'IN STOCK:' in logs[j] and product_name in logs[j]:
                            # Found matching IN STOCK entry, get TCIN from next line
                            if j + 1 < len(logs):
                                purchase_line = logs[j + 1]
                                tcin_match = re.search(r'Initiating purchase for (\d+)', purchase_line)
                                if tcin_match:
                                    tcin = tcin_match.group(1)
                                    status['stock_by_tcin'][tcin] = 'OUT_OF_STOCK'
                                    break
        
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

if __name__ == '__main__':
    print("="*60)
    print("TARGET MONITOR DASHBOARD")
    print("Access at: http://localhost:5000")
    print("="*60)
    app.run(debug=False, host='0.0.0.0', port=5000)