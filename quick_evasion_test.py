#!/usr/bin/env python3
"""
Quick test of the adaptive evasion system with one product
"""

import asyncio
import logging
from src.authenticated_stock_checker import AuthenticatedStockChecker

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def quick_test():
    """Quick test with one product"""
    
    # Initialize the enhanced stock checker
    checker = AuthenticatedStockChecker()
    
    print("=== Quick Adaptive Evasion Test ===\n")
    
    # Test one product
    tcin = "89542109"  # Pokemon card
    
    print(f"Testing product {tcin} with adaptive evasion...")
    
    result = await checker.check_authenticated_stock(tcin)
    
    print(f"\nProduct: {result['name']}")
    print(f"Status: {'IN STOCK' if result['available'] else 'OUT OF STOCK'}")
    print(f"Details: {result['availability_text']}")
    print(f"Response Time: {result['response_time']:.1f}ms")
    print(f"Price: {result.get('formatted_price', 'N/A')}")
    
    # Show adaptive intelligence
    adaptive_meta = result.get('adaptive_metadata', {})
    print(f"\n=== Adaptive Intelligence ===")
    print(f"Session ID: {adaptive_meta.get('session_id', 'Unknown')}")
    print(f"User Type: {adaptive_meta.get('user_type', 'Unknown')}")
    print(f"Strategy: {adaptive_meta.get('strategy_used', 'Unknown')}")
    print(f"Pattern: {adaptive_meta.get('pattern_used', 'Unknown')}")
    print(f"Threat Level: {adaptive_meta.get('threat_level', 0):.3f}")
    print(f"Delay Applied: {adaptive_meta.get('delay_applied', 0):.2f}s")
    
    # Get system stats
    stats = checker.get_adaptive_performance_stats()
    print(f"\n=== System Status ===")
    print(f"Current Strategy: {stats['adaptive_limiter']['current_strategy']}")
    print(f"Threat Assessment: {stats['threat_assessment']['level']}")
    print(f"Active Session: {stats['session_stats']['active_session']}")
    
    return result

if __name__ == "__main__":
    print("Starting Quick Adaptive Evasion Test...")
    
    try:
        result = asyncio.run(quick_test())
        print(f"\n[SUCCESS] Test completed! Product availability: {'IN STOCK' if result['available'] else 'OUT OF STOCK'}")
        
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()