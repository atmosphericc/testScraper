import requests
import json
import time
import os
import random
from typing import List, Dict, Any
from flask import Flask, render_template, redirect, url_for, flash
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import html

# Import request manager for smart retry logic
from request_manager import TargetMonitor

print("🚀 TARGET DASHBOARD - PRODUCTION VERSION 1.0 (CONCURRENT)")

app = Flask(__name__)
app.secret_key = 'target-dashboard-secret-key-2025'

# Configuration
last_refresh_time = None
config = {}

# Initialize the monitor with request manager
monitor = TargetMonitor(use_proxies=False)
print("📡 Request Manager initialized with concurrent support")


def load_product_config():
    """Load product configuration from JSON file"""
    try:
        with open('product_config.json', 'r') as f:
            config = json.load(f)
            return config
    except FileNotFoundError:
        print("⚠️ product_config.json not found, using defaults")
        return {
            "products": [
                {"tcin": "1001304528", "max_price": 999.99, "quantity": 1, "enabled": True},
                {"tcin": "94300069", "max_price": 999.99, "quantity": 1, "enabled": True},
            ],
            "settings": {"only_target_direct": False, "auto_buy_enabled": False}
        }


def process_target_product(tcin: str, api_response: Dict[str, Any], max_price: float = 999.99, only_target_direct: bool = False) -> Dict[str, Any]:
    """
    Process API response with bulletproof stock detection algorithm
    """
    try:
        # Extract core product data
        product_data = api_response['data']['product']
        raw_name = product_data['item']['product_description']['title']
        
        # Clean up HTML entities
        name = html.unescape(raw_name)
        
        # Robust price extraction
        price_data = product_data.get('price', {})
        current_price = price_data.get('current_retail')
        if current_price is None:
            current_price = price_data.get('formatted_current_price', '$0.00')
            if isinstance(current_price, str):
                import re
                price_match = re.search(r'[\d.]+', current_price)
                current_price = float(price_match.group()) if price_match else 0.0
        
        # Extract API signals
        fulfillment = product_data['item']['fulfillment']
        eligibility_rules = product_data['item'].get('eligibility_rules', {})
        
        is_marketplace = fulfillment.get('is_marketplace', False)
        purchase_limit = fulfillment.get('purchase_limit', 0)
        ship_to_guest_active = eligibility_rules.get('ship_to_guest', {}).get('is_active', False)
        has_eligibility_rules = len(eligibility_rules) > 0
        
        # Check for pre-order indicators
        purchase_date_display_active = eligibility_rules.get('available_to_purchase_date_display', {}).get('is_active', False)
        has_purchase_date_field = 'available_to_purchase_date_display' in eligibility_rules
        
        # Initialize pre-order flag
        is_preorder = False
        preorder_reason = ""
        
        # BULLETPROOF STOCK DETECTION
        if is_marketplace:
            seller_type = "third-party"
            available = purchase_limit > 0
        else:
            seller_type = "target"
            
            if not ship_to_guest_active:
                # Special case for pre-order bundles
                if current_price >= 400 and purchase_limit == 1 and not has_eligibility_rules:
                    available = True
                    is_preorder = True
                    preorder_reason = "Console Bundle"
                else:
                    available = False
                    
            elif purchase_limit <= 1:
                available = False
                
            elif ship_to_guest_active and purchase_limit >= 2:
                available = True
                
                # Check if this might be a pre-order
                if has_purchase_date_field and not purchase_date_display_active:
                    if "Nintendo Switch 2" in name or "Switch 2" in name or "Donkey Kong" in name:
                        is_preorder = True
                        preorder_reason = "Upcoming Console"
            else:
                available = False
        
        # Extract numeric price for comparison
        if isinstance(current_price, (int, float)):
            numeric_price = float(current_price)
        else:
            import re
            price_match = re.search(r'[\d.]+', str(current_price))
            numeric_price = float(price_match.group()) if price_match else 999.99
        
        # Determine if we want to buy
        want_to_buy = False
        buy_decision_reason = ""
        
        if not available:
            buy_decision_reason = "Out of stock"
        elif numeric_price > max_price:
            buy_decision_reason = f"Price ${numeric_price:.2f} exceeds max ${max_price:.2f}"
        elif seller_type == "third-party" and only_target_direct:
            buy_decision_reason = "Third-party seller (Target direct only)"
        else:
            want_to_buy = True
            buy_decision_reason = f"✅ BUY - In stock at ${numeric_price:.2f} (max: ${max_price:.2f})"
        
        # Format results
        stock_status = "In Stock" if available else "Out of Stock"
        price_formatted = f"${numeric_price:.2f}" if isinstance(numeric_price, (int, float)) else str(current_price)
        product_url = f"https://www.target.com/p/-/A-{tcin}"
        
        return {
            "tcin": tcin,
            "name": name,
            "price": price_formatted,
            "numeric_price": numeric_price,
            "seller": seller_type,
            "stock": stock_status,
            "link": product_url,
            "want_to_buy": want_to_buy,
            "buy_decision": buy_decision_reason,
            "purchase_limit": purchase_limit,
            "preorder": preorder_reason if is_preorder else "",
            "status": "success"
        }
        
    except Exception as e:
        print(f"❌ Processing Error for TCIN {tcin}: {e}")
        return {
            "tcin": tcin,
            "name": f"Error processing {tcin}",
            "price": "$0.00",
            "numeric_price": 0,
            "seller": "error",
            "stock": "Error",
            "link": f"https://www.target.com/p/-/A-{tcin}",
            "want_to_buy": False,
            "buy_decision": "Processing Error",
            "purchase_limit": 0,
            "preorder": "",
            "status": "error"
        }


def fetch_single_product(product_config: Dict, only_target_direct: bool) -> Dict[str, Any]:
    """
    Fetch a single product with jitter delay
    """
    tcin = product_config['tcin']
    max_price = product_config.get('max_price', 999.99)
    
    # Add small random delay to avoid pattern detection
    time.sleep(random.uniform(0, 0.5))
    
    # Use the monitor to get product info
    api_response = monitor.get_product_info(tcin)
    
    if api_response:
        # Process with price threshold
        product_info = process_target_product(tcin, api_response, max_price, only_target_direct)
        product_info['config_name'] = product_config.get('name', 'Unknown')
        product_info['max_price'] = max_price
    else:
        # API failure fallback
        product_info = {
            "tcin": tcin,
            "name": product_config.get('name', f"API Error - TCIN {tcin}"),
            "price": "$0.00",
            "numeric_price": 0,
            "seller": "error",
            "stock": "API Error",
            "link": f"https://www.target.com/p/-/A-{tcin}",
            "want_to_buy": False,
            "buy_decision": "API Error",
            "purchase_limit": 0,
            "preorder": "",
            "status": "api_error",
            "max_price": max_price
        }
    
    return product_info


def fetch_product_data_concurrent(product_list: List[Dict], only_target_direct: bool = False, max_workers: int = 3) -> List[Dict[str, Any]]:
    """
    Fetch multiple products concurrently for much faster performance
    
    Args:
        product_list: List of product configurations
        only_target_direct: Whether to only buy from Target directly
        max_workers: Number of concurrent requests (3 is safe, 5 is usually OK)
    """
    products = []
    
    # Filter to only enabled products
    enabled_products = [p for p in product_list if p.get('enabled', True)]
    
    print(f"📊 Fetching {len(enabled_products)} products concurrently (max {max_workers} workers)...")
    start_time = time.time()
    
    completed = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all fetch tasks
        future_to_product = {
            executor.submit(fetch_single_product, product_config, only_target_direct): product_config
            for product_config in enabled_products
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_product):
            completed += 1
            product_config = future_to_product[future]
            
            try:
                product_info = future.result()
                products.append(product_info)
                
                # Progress indicator
                status_emoji = "✅" if product_info.get('want_to_buy', False) else "❌"
                print(f"[{completed}/{len(enabled_products)}] {status_emoji} {product_info['tcin']}: {product_info['name'][:30]}... - {product_info['stock']}")
                
            except Exception as e:
                print(f"Error processing {product_config['tcin']}: {e}")
                products.append({
                    "tcin": product_config['tcin'],
                    "name": f"Error - {product_config.get('name', 'Unknown')}",
                    "price": "$0.00",
                    "numeric_price": 0,
                    "seller": "error",
                    "stock": "Error",
                    "link": f"https://www.target.com/p/-/A-{product_config['tcin']}",
                    "want_to_buy": False,
                    "buy_decision": "Processing Error",
                    "purchase_limit": 0,
                    "preorder": "",
                    "status": "error",
                    "max_price": product_config.get('max_price', 999.99)
                })
    
    elapsed = time.time() - start_time
    print(f"\n⚡ Completed in {elapsed:.2f} seconds ({elapsed/len(enabled_products):.2f}s per product)")
    
    # Print buy summary
    buy_ready = [p for p in products if p.get('want_to_buy', False)]
    if buy_ready:
        print(f"\n🎯 READY TO BUY: {len(buy_ready)} products")
        for p in buy_ready:
            print(f"  ✅ {p['tcin']}: {p['name'][:30]} at {p['price']}")
    else:
        print(f"\n❌ No products meet buy criteria")
    
    # Show stats
    stats = monitor.request_manager.get_stats()
    print(f"\n📈 Batch Complete - Stats:")
    print(f"  Total Requests: {stats['total_requests']}")
    print(f"  Success Rate: {stats['success_rate']:.1%}")
    print(f"  Timing Pattern: {stats['current_pattern']}")
    
    return products


def get_dashboard_data(concurrent: bool = True) -> List[Dict[str, Any]]:
    """
    Get dashboard data using either concurrent or sequential fetching
    """
    global last_refresh_time, config
    
    # Load configuration
    config = load_product_config()
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Loading config...")
    print(f"  Found {len(config['products'])} products")
    print(f"  {sum(1 for p in config['products'] if p.get('enabled', True))} enabled")
    
    # Get settings
    only_target_direct = config.get('settings', {}).get('only_target_direct', False)
    
    # Fetch products (concurrent by default for speed)
    if concurrent:
        # Fixed at 5 workers for optimal speed
        max_workers = 5
            
        products = fetch_product_data_concurrent(config['products'], only_target_direct, max_workers)
    else:
        # Fallback to sequential if needed (original method)
        from preserved.request_manager import TargetMonitor
        monitor_seq = TargetMonitor(use_proxies=False)
        products = []
        for product_config in config['products']:
            if not product_config.get('enabled', True):
                continue
            tcin = product_config['tcin']
            api_response = monitor_seq.get_product_info(tcin)
            if api_response:
                product_info = process_target_product(tcin, api_response, 
                                                     product_config.get('max_price', 999.99), 
                                                     only_target_direct)
                products.append(product_info)
    
    last_refresh_time = datetime.now()
    
    # Save data for audit trail
    with open('target_products.json', 'w') as f:
        json.dump(products, f, indent=2)
    
    # Business intelligence logging
    success_count = sum(1 for p in products if p['status'] == 'success')
    error_count = len(products) - success_count
    buy_ready_count = sum(1 for p in products if p.get('want_to_buy', False))
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Update complete:")
    print(f"  {success_count} success, {error_count} errors")
    print(f"  {buy_ready_count} ready to purchase")
    
    return products


# Flask Routes
@app.route('/')
def dashboard():
    """Main dashboard route - Always fetch fresh inventory data"""
    try:
        # Use concurrent fetching for speed
        products = get_dashboard_data(concurrent=True)
        
        # Add request statistics to dashboard
        stats = monitor.request_manager.get_stats()
        
        refresh_info = {
            'last_refresh': last_refresh_time.strftime('%Y-%m-%d %H:%M:%S') if last_refresh_time else 'Never',
            'total_products': len(products),
            'in_stock_count': sum(1 for p in products if p['stock'] == 'In Stock'),
            'out_of_stock_count': sum(1 for p in products if p['stock'] == 'Out of Stock'),
            'total_requests': stats['total_requests'],
            'success_rate': f"{stats['success_rate']:.1%}" if stats['success_rate'] else "N/A",
            'timing_pattern': stats['current_pattern']
        }
        
        return render_template('dashboard.html', products=products, refresh_info=refresh_info)
        
    except Exception as e:
        print(f"❌ Dashboard Error: {e}")
        return render_template('dashboard.html', products=[], error=f"System Error: {e}")


@app.route('/refresh')
def refresh_data():
    """Manual refresh endpoint"""
    try:
        products = get_dashboard_data(concurrent=True)
        flash(f"Inventory refreshed at {datetime.now().strftime('%H:%M:%S')} - {len(products)} products updated", "success")
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        flash(f"Refresh failed: {e}", "error")
        return redirect(url_for('dashboard'))


@app.route('/api/status')
def api_status():
    """API status endpoint for monitoring"""
    stats = monitor.request_manager.get_stats()
    return {
        'status': 'operational',
        'last_refresh': last_refresh_time.isoformat() if last_refresh_time else None,
        'timestamp': datetime.now().isoformat(),
        'version': '1.0-concurrent',
        'request_stats': stats
    }


@app.route('/set_speed/<pattern>')
def set_speed(pattern):
    """Control monitoring speed"""
    valid_patterns = ['aggressive', 'normal', 'conservative', 'human']
    if pattern in valid_patterns:
        monitor.request_manager.set_timing_pattern(pattern)
        flash(f"Speed set to {pattern} mode", "success")
    else:
        flash(f"Invalid pattern. Use: {', '.join(valid_patterns)}", "error")
    return redirect(url_for('dashboard'))


if __name__ == '__main__':
    # Production startup
    if not os.path.exists('templates'):
        os.makedirs('templates')
        print("Created templates directory")
    
    template_path = 'templates/dashboard.html'
    if not os.path.exists(template_path):
        print(f"⚠️ WARNING: {template_path} not found")
    
    print("\n" + "="*60)
    print("🎯 TARGET INVENTORY DASHBOARD - CONCURRENT VERSION")
    print("⚡ 3-4x FASTER with concurrent requests")
    print("🔬 BULLETPROOF ALGORITHM - NO ARBITRARY DATES!")
    print("Real-time inventory monitoring system")
    print("Dashboard: http://localhost:5000")
    print("API Status: http://localhost:5000/api/status")
    print("Speed Control: http://localhost:5000/set_speed/[pattern]")
    print("  Patterns: aggressive, normal, conservative, human")
    print("="*60 + "\n")
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Dashboard shutdown complete")