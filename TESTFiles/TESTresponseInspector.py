import requests
import json
import time
from typing import Dict, Any, List
from datetime import datetime

def get_target_product_info(tcin: str) -> Dict[str, Any]:
    """Fetch product information from Target's Redsky API"""
    url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}&is_bot=false&store_id=865&pricing_store_id=865&has_pricing_store_id=true&has_financing_options=true&include_obsolete=true&visitor_id=0198538661860201B9F1AD74ED8A1AE4&skip_personalized=true&skip_variation_hierarchy=true&channel=WEB&page=%2Fp%2FA-{tcin}"
    
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.target.com",
        "referer": f"https://www.target.com/p/A-{tcin}",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"❌ API Error for TCIN {tcin}: {e}")
        return None

def analyze_api_response(tcin: str, api_response: Dict[str, Any], expected_status: str) -> Dict[str, Any]:
    """
    Deep analysis of Target API response to understand stock indicators
    
    Args:
        tcin: Product TCIN
        api_response: Raw API response
        expected_status: What the status should be ("in_stock" or "out_of_stock")
    """
    try:
        product_data = api_response['data']['product']
        item = product_data['item']
        
        # Extract all potentially relevant fields
        analysis = {
            'tcin': tcin,
            'expected_status': expected_status,
            'product_name': item['product_description']['title'][:100],
            
            # Core availability signals
            'fulfillment': item.get('fulfillment', {}),
            'eligibility_rules': item.get('eligibility_rules', {}),
            'mmbv_content': item.get('mmbv_content', {}),
            
            # Price signals
            'price_data': product_data.get('price', {}),
            
            # Additional fields that might indicate stock
            'enrichment': item.get('enrichment', {}),
            'compliance': item.get('compliance', {}),
            'wellness': item.get('wellness', {}),
            'product_vendors': item.get('product_vendors', {}),
            'available_to_promise_network': item.get('available_to_promise_network', {}),
            
            # Look for any fields with "available", "stock", "inventory" in the name
            'availability_fields': {},
            'stock_fields': {},
            'inventory_fields': {}
        }
        
        # Recursively search for availability-related fields
        def find_availability_fields(obj, prefix=""):
            """Recursively find fields related to availability/stock/inventory"""
            if isinstance(obj, dict):
                for key, value in obj.items():
                    full_key = f"{prefix}.{key}" if prefix else key
                    
                    # Check if key contains availability indicators
                    key_lower = key.lower()
                    if any(indicator in key_lower for indicator in ['available', 'avail']):
                        analysis['availability_fields'][full_key] = value
                    elif any(indicator in key_lower for indicator in ['stock', 'inventory', 'quantity', 'qty']):
                        analysis['stock_fields'][full_key] = value
                    elif any(indicator in key_lower for indicator in ['purchase', 'buy', 'cart', 'order']):
                        analysis['inventory_fields'][full_key] = value
                    
                    # Recurse into nested objects (but limit depth to avoid infinite loops)
                    if isinstance(value, dict) and len(prefix.split('.')) < 3:
                        find_availability_fields(value, full_key)
                    elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                        find_availability_fields(value[0], f"{full_key}[0]")
        
        # Analyze the entire product data structure
        find_availability_fields(product_data)
        
        # Extract specific key metrics for comparison
        fulfillment = analysis['fulfillment']
        eligibility = analysis['eligibility_rules']
        
        analysis['key_signals'] = {
            'is_marketplace': fulfillment.get('is_marketplace', False),
            'purchase_limit': fulfillment.get('purchase_limit', 0),
            'ship_to_guest_active': eligibility.get('ship_to_guest', {}).get('is_active', False),
            'purchase_date_display_active': eligibility.get('available_to_purchase_date_display', {}).get('is_active', False),
            'has_purchase_date_field': 'available_to_purchase_date_display' in eligibility,
            'has_eligibility_rules': len(eligibility) > 0,
            'street_date': analysis['mmbv_content'].get('street_date', ''),
            'current_price': analysis['price_data'].get('current_retail', 0)
        }
        
        return analysis
        
    except Exception as e:
        return {
            'tcin': tcin,
            'error': str(e),
            'expected_status': expected_status
        }

def run_comprehensive_analysis():
    """
    Run analysis on your known test cases to find the perfect algorithm
    """
    
    # Test cases with known correct statuses
    test_cases = [
        ("94694203", "in_stock"),      # Should be In Stock
        ("94300069", "out_of_stock"),  # Should be Out of Stock  
        ("93859727", "out_of_stock"),  # Should be Out of Stock
        ("94693225", "out_of_stock"),  # Should be Out of Stock
        ("1001304528", "in_stock"),    # Third-party, should be In Stock
        ("1004021929", "unknown"),     # Pokemon - let's see what this shows
        ("94881750", "unknown"),       # Let's analyze this one
        ("14777416", "unknown")        # And this one
    ]
    
    print("🔍 COMPREHENSIVE TARGET API ANALYSIS")
    print("="*80)
    print("Analyzing API responses to find bulletproof stock detection patterns...")
    print()
    
    all_analyses = []
    
    for tcin, expected in test_cases:
        print(f"📊 Analyzing TCIN {tcin} (expected: {expected})...")
        
        # Fetch API response
        api_response = get_target_product_info(tcin)
        if not api_response:
            print(f"❌ Failed to fetch {tcin}")
            continue
            
        # Analyze the response
        analysis = analyze_api_response(tcin, api_response, expected)
        all_analyses.append(analysis)
        
        # Print key findings
        if 'key_signals' in analysis:
            signals = analysis['key_signals']
            print(f"  Product: {analysis['product_name']}")
            print(f"  Marketplace: {signals['is_marketplace']}")
            print(f"  Purchase Limit: {signals['purchase_limit']}")
            print(f"  Ship to Guest: {signals['ship_to_guest_active']}")
            print(f"  Purchase Date Active: {signals['purchase_date_display_active']}")
            print(f"  Has Purchase Date Field: {signals['has_purchase_date_field']}")
            print(f"  Has Eligibility Rules: {signals['has_eligibility_rules']}")
            print(f"  Street Date: {signals['street_date']}")
            print(f"  Price: ${signals['current_price']}")
            
            # Show availability fields found
            if analysis['availability_fields']:
                print(f"  🔍 Availability Fields Found:")
                for field, value in list(analysis['availability_fields'].items())[:5]:
                    print(f"    {field}: {value}")
            
            if analysis['stock_fields']:
                print(f"  📦 Stock/Inventory Fields Found:")
                for field, value in list(analysis['stock_fields'].items())[:5]:
                    print(f"    {field}: {value}")
        
        print("  " + "-"*60)
        
        # Rate limiting
        time.sleep(1.5)
    
    # Save detailed analysis to file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'target_api_analysis_{timestamp}.json'
    
    with open(filename, 'w') as f:
        json.dump(all_analyses, f, indent=2, default=str)
    
    print(f"\n💾 Detailed analysis saved to: {filename}")
    print("\n🧠 PATTERN ANALYSIS:")
    print("="*50)
    
    # Analyze patterns between in-stock and out-of-stock items
    in_stock_items = [a for a in all_analyses if a.get('expected_status') == 'in_stock']
    out_of_stock_items = [a for a in all_analyses if a.get('expected_status') == 'out_of_stock']
    
    print("\n📈 IN-STOCK PATTERNS:")
    for item in in_stock_items:
        if 'key_signals' in item:
            s = item['key_signals']
            print(f"  {item['tcin']}: purchase_limit={s['purchase_limit']}, "
                  f"ship_guest={s['ship_to_guest_active']}, "
                  f"purchase_date={s['purchase_date_display_active']}, "
                  f"marketplace={s['is_marketplace']}")
    
    print("\n📉 OUT-OF-STOCK PATTERNS:")
    for item in out_of_stock_items:
        if 'key_signals' in item:
            s = item['key_signals']
            print(f"  {item['tcin']}: purchase_limit={s['purchase_limit']}, "
                  f"ship_guest={s['ship_to_guest_active']}, "
                  f"purchase_date={s['purchase_date_display_active']}, "
                  f"marketplace={s['is_marketplace']}")
    
    print(f"\n✅ Analysis complete! Check {filename} for full details.")
    print("Use this data to build a bulletproof algorithm with NO arbitrary dates!")
    
    return all_analyses

if __name__ == "__main__":
    # Run the comprehensive analysis
    analyses = run_comprehensive_analysis()
    
    print("\n🎯 NEXT STEPS:")
    print("1. Review the analysis file for patterns")
    print("2. Look for fields that reliably distinguish in-stock vs out-of-stock")
    print("3. Build algorithm based on actual API signals, not dates")
    print("4. Test the new algorithm on these known cases")