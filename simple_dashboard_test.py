#!/usr/bin/env python3
"""
Simple test to verify dashboard works with enhanced adaptive evasion system
"""

import asyncio
import json
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.authenticated_stock_checker import AuthenticatedStockChecker

async def test_dashboard_compatibility():
    """Test that the enhanced system works with expected dashboard format"""
    
    # Load products from config
    config_path = Path('config/product_config.json')
    if not config_path.exists():
        print("ERROR: No product config found")
        return
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    products = config.get('products', [])
    enabled_products = [p for p in products if p.get('enabled', True)]
    
    if not enabled_products:
        print("ERROR: No enabled products")
        return
    
    print(f"Testing {len(enabled_products)} products with enhanced adaptive system...")
    
    # Test the enhanced checker
    checker = AuthenticatedStockChecker()
    
    tcins = [p['tcin'] for p in enabled_products]
    
    # Check all products
    results = await checker.check_multiple_products(tcins)
    
    # Format results for dashboard compatibility
    dashboard_results = []
    for result in results:
        dashboard_result = {
            'tcin': result.get('tcin'),
            'name': result.get('name', 'Unknown Product'),
            'available': result.get('available', False),
            'availability_text': result.get('availability_text', 'Unknown'),
            'price': result.get('formatted_price', 'N/A'),
            'response_time': f"{result.get('response_time', 0):.1f}ms",
            'method': result.get('method', 'enhanced_adaptive'),
            'confidence': result.get('confidence', 'unknown')
        }
        dashboard_results.append(dashboard_result)
    
    # Print results in dashboard-friendly format
    print("\n=== Dashboard-Compatible Results ===")
    in_stock_count = 0
    total_response_time = 0
    
    for result in dashboard_results:
        status = "IN STOCK" if result['available'] else "OUT OF STOCK"
        print(f"{result['name'][:50]}: {status} - {result['price']} ({result['response_time']})")
        
        if result['available']:
            in_stock_count += 1
        
        # Parse response time for aggregation
        try:
            rt = float(result['response_time'].replace('ms', ''))
            total_response_time += rt
        except:
            pass
    
    avg_response_time = total_response_time / len(dashboard_results) if dashboard_results else 0
    
    print(f"\n=== Dashboard Summary ===")
    print(f"Total Products: {len(dashboard_results)}")
    print(f"In Stock: {in_stock_count}")
    print(f"Out of Stock: {len(dashboard_results) - in_stock_count}")
    print(f"Average Response Time: {avg_response_time:.1f}ms")
    print(f"System: Enhanced Adaptive Evasion")
    
    # Test adaptive system stats
    try:
        stats = checker.get_adaptive_performance_stats()
        print(f"\n=== Adaptive Intelligence ===")
        print(f"Strategy: {stats['adaptive_limiter']['current_strategy']}")
        print(f"Threat Level: {stats['threat_assessment']['level']}")
        print(f"Success Rate: {stats['adaptive_limiter']['overall_success_rate']:.1%}")
        print(f"Circuit Breaker: {'Active' if stats['adaptive_limiter']['circuit_breaker_active'] else 'Normal'}")
        
    except Exception as e:
        print(f"Adaptive stats error: {e}")
    
    return dashboard_results

if __name__ == "__main__":
    print("Starting Dashboard Compatibility Test...")
    try:
        results = asyncio.run(test_dashboard_compatibility())
        print(f"\n[SUCCESS] Dashboard test completed with {len(results)} products!")
        print("The enhanced system is ready for dashboard integration.")
        
    except Exception as e:
        print(f"\n[ERROR] Dashboard test failed: {e}")
        import traceback
        traceback.print_exc()