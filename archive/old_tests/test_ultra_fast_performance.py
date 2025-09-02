#!/usr/bin/env python3
"""
Test Ultra-Fast Stock Checker Performance
Validates sub-3-second performance for 50+ SKUs with zero missed opportunities
"""
import asyncio
import time
import json
import sys
from pathlib import Path

sys.path.insert(0, 'src')
from ultra_fast_stock_checker import UltraFastStockChecker, StockResult

async def performance_test():
    """Test performance with various product counts"""
    
    print("ULTRA-FAST STOCK CHECKER PERFORMANCE TEST")
    print("=" * 60)
    
    # Mock purchase callback for testing
    purchase_calls = []
    
    async def test_purchase_callback(stock_result: StockResult):
        purchase_calls.append({
            'tcin': stock_result.tcin,
            'method': stock_result.method,
            'confidence': stock_result.confidence,
            'timestamp': time.time()
        })
        print(f"🚀 MOCK PURCHASE: {stock_result.tcin} ({stock_result.method})")
    
    # Test product sets of different sizes
    test_products = {
        'small': [
            "89542109",  # Known in stock
            "94724987",  # Known out of stock
            "94681785",  # Known out of stock
        ],
        'medium': [
            "89542109", "94724987", "94681785", "12345678", "87654321",
            "11111111", "22222222", "33333333", "44444444", "55555555",
            "66666666", "77777777", "88888888", "99999999", "10101010"
        ],
        'large': []  # Will generate 50+ products
    }
    
    # Generate 50+ test products for large test
    base_products = ["89542109", "94724987", "94681785"]  # Include known products
    for i in range(50):
        test_products['large'].append(f"{10000000 + i:08d}")
    
    # Initialize ultra-fast checker
    checker = UltraFastStockChecker(
        purchase_callback=test_purchase_callback,
        num_background_sessions=6,  # More sessions for better performance
        enable_caching=True
    )
    
    try:
        print("Initializing background sessions...")
        init_start = time.time()
        await checker.initialize()
        init_time = time.time() - init_start
        print(f"✅ Initialization complete in {init_time:.2f}s")
        
        # Test different product set sizes
        for test_name, tcins in test_products.items():
            if not tcins:
                continue
                
            print(f"\n{'='*20} {test_name.upper()} TEST ({len(tcins)} products) {'='*20}")
            
            purchase_calls.clear()  # Reset for this test
            
            start_time = time.time()
            results = await checker.check_multiple_products(tcins)
            total_time = time.time() - start_time
            
            # Analyze results
            available_count = sum(1 for r in results.values() if r.available)
            verified_count = sum(1 for r in results.values() if r.browser_confirmed)
            avg_time_per_product = total_time / len(tcins)
            
            print(f"\n📊 PERFORMANCE RESULTS:")
            print(f"   Total time: {total_time:.3f}s")
            print(f"   Products checked: {len(tcins)}")
            print(f"   Available products: {available_count}")
            print(f"   Browser verified: {verified_count}")
            print(f"   Avg time per product: {avg_time_per_product:.3f}s")
            print(f"   Purchase callbacks triggered: {len(purchase_calls)}")
            
            # Performance assessment
            if test_name == 'large' and len(tcins) >= 50:
                target_met = total_time <= 3.0
                status = "✅ TARGET MET" if target_met else "❌ TARGET MISSED"
                print(f"\n🎯 50+ SKU TARGET: {status}")
                print(f"   Target: ≤ 3.0s, Actual: {total_time:.3f}s")
                
                if not target_met:
                    print(f"   Performance gap: +{total_time - 3.0:.3f}s")
                    print("   Recommend: More background sessions or API-only mode")
            
            # Show sample results
            print(f"\n📋 SAMPLE RESULTS:")
            for i, (tcin, result) in enumerate(list(results.items())[:5]):
                status = "✅ AVAILABLE" if result.available else "❌ OUT OF STOCK"
                print(f"   {tcin}: {status} ({result.confidence}) - {result.method} [{result.check_time:.3f}s]")
            
            if len(results) > 5:
                print(f"   ... and {len(results) - 5} more products")
        
        # Overall performance report
        print(f"\n{'='*60}")
        print("OVERALL PERFORMANCE REPORT")
        print("=" * 60)
        
        report = await checker.get_performance_report()
        print(f"Total checks performed: {report['total_checks_performed']}")
        print(f"System accuracy rate: {report['accuracy_rate']*100:.1f}%")
        print(f"Missed opportunities: {report['missed_opportunities']}")
        print(f"False positives: {report['false_positives']}")
        print(f"Background sessions healthy: {report['health_status']}")
        
        bg_stats = report.get('background_session_stats', {})
        if bg_stats:
            print(f"\nBackground Session Performance:")
            print(f"   Average check time: {bg_stats.get('avg_check_time', 0):.3f}s")
            print(f"   Fastest check: {bg_stats.get('fastest_check', 0):.3f}s")
            print(f"   Cache hits: {bg_stats.get('cache_hits', 0)}")
            print(f"   Active sessions: {bg_stats.get('active_sessions', 0)}")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        print(f"\n🧹 Cleaning up resources...")
        await checker.cleanup()
        print("✅ Cleanup complete")

async def stress_test():
    """Stress test with 100+ products to find limits"""
    print("\n" + "=" * 60)
    print("STRESS TEST - 100+ PRODUCTS")
    print("=" * 60)
    
    # Generate 100+ test products
    stress_tcins = []
    stress_tcins.extend(["89542109", "94724987", "94681785"])  # Known products first
    
    for i in range(100):
        stress_tcins.append(f"{20000000 + i:08d}")
    
    checker = UltraFastStockChecker(num_background_sessions=8)
    
    try:
        await checker.initialize()
        
        print(f"Testing {len(stress_tcins)} products...")
        start_time = time.time()
        
        results = await checker.check_multiple_products(stress_tcins, max_concurrent=12)
        
        total_time = time.time() - start_time
        print(f"\n🚀 STRESS TEST RESULTS:")
        print(f"   Products: {len(stress_tcins)}")
        print(f"   Total time: {total_time:.2f}s")
        print(f"   Rate: {len(stress_tcins) / total_time:.1f} products/second")
        print(f"   Available: {sum(1 for r in results.values() if r.available)}")
        
        # Theoretical maximum for Target's rate limits
        theoretical_max = len(stress_tcins) * 0.05  # 50ms per product theoretical minimum
        efficiency = theoretical_max / total_time * 100
        print(f"   Efficiency: {efficiency:.1f}% of theoretical maximum")
        
    finally:
        await checker.cleanup()

if __name__ == "__main__":
    print("Starting Ultra-Fast Stock Checker Performance Tests...")
    print("This will test the system's ability to check 50+ SKUs in under 3 seconds")
    print("with zero missed opportunities using background browser sessions.\n")
    
    # Run performance test
    asyncio.run(performance_test())
    
    # Ask user if they want stress test
    print(f"\n{'='*60}")
    stress_input = input("Run stress test with 100+ products? (y/N): ")
    if stress_input.lower().startswith('y'):
        asyncio.run(stress_test())
    
    print("\n✅ All tests complete!")
    print("\nNext steps:")
    print("1. If performance targets are met, integrate with your main monitor")
    print("2. If targets are missed, consider:")
    print("   - More background sessions (increase num_background_sessions)")
    print("   - API-only mode for initial pass, hybrid for uncertain results")
    print("   - Reduce max_concurrent to prevent overwhelming Target's servers")