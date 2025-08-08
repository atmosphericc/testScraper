import requests
import json
import time
import os
from typing import List, Dict, Any
from flask import Flask, render_template, redirect, url_for, flash
from datetime import datetime

# NEW IMPORT - Add this line
from request_manager import TargetMonitor

print("🚀 TARGET DASHBOARD - PRODUCTION VERSION 1.0")

app = Flask(__name__)
app.secret_key = 'target-dashboard-secret-key-2025'

# Configuration
last_refresh_time = None
config = {}  # ADD THIS - Initialize config globally

# INITIALIZE THE MONITOR - Add these lines
monitor = TargetMonitor(use_proxies=False)
print("📡 Request Manager initialized - No proxies (direct connection)")


def load_product_config():
    """
    Load product configuration from JSON file
    """
    try:
        with open('product_config.json', 'r') as f:
            config = json.load(f)
            return config
    except FileNotFoundError:
        print("⚠️ product_config.json not found, using defaults")
        # Fallback to your original hardcoded list
        return {
            "products": [
                {"tcin": "1001304528", "max_price": 999.99, "quantity": 1, "enabled": True},
                {"tcin": "94300069", "max_price": 999.99, "quantity": 1, "enabled": True},
                # ... etc
            ],
            "settings": {"only_target_direct": False, "auto_buy_enabled": False}
        }


def process_target_product(tcin: str, api_response: Dict[str, Any], max_price: float = 999.99, only_target_direct: bool = False) -> Dict[str, Any]:
    """
    BULLETPROOF: Stock detection based on actual API patterns - NO ARBITRARY DATES!
    MODIFIED: Now includes max_price parameter for buy decisions
    
    Based on comprehensive API analysis:
    - Uses only reliable API signals that Target controls
    - No hardcoded dates that can break over time
    - Pattern-based logic derived from real data
    """
    try:
        # Extract core product data
        product_data = api_response['data']['product']
        raw_name = product_data['item']['product_description']['title']
        
        # Clean up HTML entities and special characters in product names
        import html
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
        
        # Check for pre-order indicators (NO DATES!)
        purchase_date_display_active = eligibility_rules.get('available_to_purchase_date_display', {}).get('is_active', False)
        has_purchase_date_field = 'available_to_purchase_date_display' in eligibility_rules
        
        # Initialize pre-order flag
        is_preorder = False
        preorder_reason = ""
        
        # BULLETPROOF STOCK DETECTION - Based on Real API Patterns
        
        if is_marketplace:
            # MARKETPLACE LOGIC: Simple and reliable
            seller_type = "third-party"
            # Marketplace items with purchase limits are available
            available = purchase_limit > 0
            
        else:
            # TARGET DIRECT LOGIC: Multi-signal approach
            seller_type = "target"
            
            print(f"  🔍 LOGIC FLOW: ship_to_guest_active={ship_to_guest_active}")
            print(f"  🔍 LOGIC FLOW: not ship_to_guest_active = {not ship_to_guest_active}")
            
            # Pattern discovered from analysis:
            # IN-STOCK: ship_to_guest=True + reasonable purchase_limit
            # OUT-OF-STOCK: ship_to_guest=False (regardless of other factors)
            # SPECIAL CASE: ship_to_guest=True + purchase_limit=1 = OUT-OF-STOCK
            
            if not ship_to_guest_active:
                # Special case for pre-order bundles: Nintendo Switch 2 bundles with high prices
                if current_price >= 400 and purchase_limit == 1 and not has_eligibility_rules:
                    available = True
                    is_preorder = True
                    preorder_reason = "Console Bundle"
                    reason = "High-value pre-order item (likely console bundle)"
                else:
                    # No shipping = definitely out of stock
                    available = False
                    reason = "No ship-to-guest capability"
                
            elif purchase_limit <= 1:
                # Very low purchase limit = likely out of stock or restricted
                # (94693225: ship_guest=True but purchase_limit=1 = OUT OF STOCK)
                available = False
                reason = "Purchase limit too restrictive"
                
            elif ship_to_guest_active and purchase_limit >= 2:
                # Can ship + reasonable purchase limit = in stock
                # (94694203: ship_guest=True + purchase_limit=2 = IN STOCK)
                available = True
                reason = "Ship-to-guest active with good purchase limit"
                
                # Check if this might be a pre-order too
                if has_purchase_date_field and not purchase_date_display_active:
                    # Has purchase date field but it's inactive = available now, but might be upcoming release
                    if "Nintendo Switch 2" in name or "Switch 2" in name or "Donkey Kong" in name:
                        is_preorder = True
                        preorder_reason = "Upcoming Console"
                
            else:
                # Fallback case
                available = False
                reason = "Default fallback"
        
        # Extract numeric price for comparison
        if isinstance(current_price, (int, float)):
            numeric_price = float(current_price)
        else:
            # Try to extract number from string like "$49.99"
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
        
        # Enhanced debug for specific TCINs
        if tcin in ["93859727", "94693225", "94300069", "94694203", "94881750"]:
            print(f"\n🎯 BULLETPROOF ALGORITHM FOR {tcin}:")
            print(f"  Product: {name[:50]}...")
            print(f"  Seller: {seller_type}")
            print(f"  Purchase limit: {purchase_limit}")
            print(f"  Ship to guest: {ship_to_guest_active}")
            print(f"  Has eligibility rules: {has_eligibility_rules}")
            print(f"  Purchase date display active: {purchase_date_display_active}")
            print(f"  Has purchase date field: {has_purchase_date_field}")
            print(f"  Current price: ${numeric_price:.2f}")
            print(f"  Max price: ${max_price:.2f}")
            print(f"  💰 Buy Decision: {buy_decision_reason}")
            print(f"  🔍 Pre-order check - is_preorder: {is_preorder}, reason: '{preorder_reason}'")
            if is_preorder:
                print(f"  🚀 PRE-ORDER DETECTED: {preorder_reason}")
            else:
                print(f"  ❌ NO PRE-ORDER DETECTED")
            
            if not is_marketplace:
                print(f"  Logic reasoning: {reason}")
                
                # Show the decision tree
                if not ship_to_guest_active:
                    if current_price >= 400 and purchase_limit == 1 and not has_eligibility_rules:
                        print(f"  Decision: High-value pre-order (${current_price}, limit={purchase_limit}, no rules) → IN STOCK")
                        print(f"  🚀 SETTING PREORDER FLAG: Pre-order")
                    else:
                        print(f"  Decision: No shipping → OUT OF STOCK")
                elif purchase_limit <= 1:
                    print(f"  Decision: Purchase limit {purchase_limit} ≤ 1 → OUT OF STOCK")
                elif ship_to_guest_active and purchase_limit >= 2:
                    print(f"  Decision: Shipping + limit {purchase_limit} ≥ 2 → IN STOCK")
                else:
                    print(f"  Decision: Fallback → OUT OF STOCK")
            else:
                print(f"  Decision: Marketplace with limit {purchase_limit} → {'IN STOCK' if available else 'OUT OF STOCK'}")
            
            print(f"  FINAL RESULT: {'AVAILABLE' if available else 'NOT AVAILABLE'}")
            print(f"  WANT TO BUY: {want_to_buy}")
            print(f"  PREORDER STATUS: '{preorder_reason}' (is_preorder: {is_preorder})")
            print("="*60)
        
        # Format results
        stock_status = "In Stock" if available else "Out of Stock"
        price_formatted = f"${numeric_price:.2f}" if isinstance(numeric_price, (int, float)) else str(current_price)
        product_url = f"https://www.target.com/p/-/A-{tcin}"
        
        return {
            "tcin": tcin,
            "name": name,
            "price": price_formatted,
            "numeric_price": numeric_price,  # NEW: Add numeric price
            "seller": seller_type,
            "stock": stock_status,
            "link": product_url,
            "want_to_buy": want_to_buy,
            "buy_decision": buy_decision_reason,  # NEW: Add decision reason
            "purchase_limit": purchase_limit,
            "preorder": preorder_reason if is_preorder else "",
            "status": "success"
        }
        
    except Exception as e:
        print(f"❌ Processing Error for TCIN {tcin}: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
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


def fetch_product_data(product_list: List[Dict], only_target_direct: bool = False) -> List[Dict[str, Any]]:
    """
    MODIFIED: Now accepts product config list instead of just TCINs
    """
    products = []
    
    # Filter to only enabled products
    enabled_products = [p for p in product_list if p.get('enabled', True)]
    
    print(f"📊 Fetching data for {len(enabled_products)} enabled products...")
    
    for i, product_config in enumerate(enabled_products, 1):
        tcin = product_config['tcin']
        max_price = product_config.get('max_price', 999.99)
        
        print(f"Processing {i}/{len(enabled_products)}: {tcin} (max: ${max_price:.2f})")
        
        # Use the monitor to get product info
        api_response = monitor.get_product_info(tcin)
        
        if api_response:
            # Process with price threshold and pass only_target_direct setting
            product_info = process_target_product(tcin, api_response, max_price, only_target_direct)
            # Add config info to result
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
        
        products.append(product_info)
    
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

def get_dashboard_data() -> List[Dict[str, Any]]:
    """
    MODIFIED: Now loads from config file
    """
    global last_refresh_time, config
    
    # Load configuration
    config = load_product_config()
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Loading config...")
    print(f"  Found {len(config['products'])} products")
    print(f"  {sum(1 for p in config['products'] if p.get('enabled', True))} enabled")
    
    # Pass the only_target_direct setting from config
    only_target_direct = config.get('settings', {}).get('only_target_direct', False)
    products = fetch_product_data(config['products'], only_target_direct)
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
    """
    PRODUCTION: Main dashboard route - Always fetch fresh inventory data
    """
    try:
        products = get_dashboard_data()
        
        # NEW: Add request statistics to dashboard
        stats = monitor.request_manager.get_stats()
        
        refresh_info = {
            'last_refresh': last_refresh_time.strftime('%Y-%m-%d %H:%M:%S') if last_refresh_time else 'Never',
            'total_products': len(products),
            'in_stock_count': sum(1 for p in products if p['stock'] == 'In Stock'),
            'out_of_stock_count': sum(1 for p in products if p['stock'] == 'Out of Stock'),
            # NEW: Add request stats
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
    """
    PRODUCTION: Manual refresh endpoint
    """
    try:
        products = get_dashboard_data()
        flash(f"Inventory refreshed at {datetime.now().strftime('%H:%M:%S')} - {len(products)} products updated", "success")
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        flash(f"Refresh failed: {e}", "error")
        return redirect(url_for('dashboard'))

@app.route('/api/status')
def api_status():
    """
    PRODUCTION: API status endpoint for monitoring
    MODIFIED: Now includes request manager stats
    """
    stats = monitor.request_manager.get_stats()
    return {
        'status': 'operational',
        'last_refresh': last_refresh_time.isoformat() if last_refresh_time else None,
        'timestamp': datetime.now().isoformat(),
        'version': '1.0',
        # NEW: Include request stats
        'request_stats': stats
    }

# NEW ROUTE: Speed control endpoint
@app.route('/set_speed/<pattern>')
def set_speed(pattern):
    """
    NEW: Control monitoring speed
    Patterns: 'aggressive', 'normal', 'conservative', 'human'
    """
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
        print(f"⚠️  WARNING: {template_path} not found")
    
    print("\n" + "="*60)
    print("🎯 TARGET INVENTORY DASHBOARD - PRODUCTION MODE")
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