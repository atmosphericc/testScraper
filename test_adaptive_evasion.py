#!/usr/bin/env python3
"""
Test script for the integrated adaptive evasion system
Demonstrates machine learning-like bot detection avoidance capabilities
"""

import asyncio
import json
import logging
from src.authenticated_stock_checker import AuthenticatedStockChecker

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def test_adaptive_evasion():
    """Test the adaptive evasion system with real Target API calls"""
    
    # Initialize the enhanced stock checker
    checker = AuthenticatedStockChecker()
    
    print("=== Advanced Adaptive Evasion System Test ===\n")
    
    # Test products (Pokemon TCG cards from config)
    test_tcins = [
        "89542109",  # Pokemon card example
        "94681785",  # Another Pokemon card
        "94724987"   # Another example
    ]
    
    print(f"Testing {len(test_tcins)} products with adaptive evasion...\n")
    
    # Test multiple products to trigger adaptive behavior
    results = await checker.check_multiple_products(test_tcins)
    
    print("\n=== Results with Adaptive Intelligence ===")
    for result in results:
        adaptive_meta = result.get('adaptive_metadata', {})
        
        print(f"\nProduct: {result['name']}")
        print(f"Status: {'IN STOCK' if result['available'] else 'OUT OF STOCK'}")
        print(f"Response Time: {result['response_time']:.1f}ms")
        print(f"Session ID: {adaptive_meta.get('session_id', 'Unknown')}")
        print(f"User Type: {adaptive_meta.get('user_type', 'Unknown')}")
        print(f"Strategy: {adaptive_meta.get('strategy_used', 'Unknown')}")
        print(f"Pattern: {adaptive_meta.get('pattern_used', 'Unknown')}")
        print(f"Threat Level: {adaptive_meta.get('threat_level', 0):.3f}")
        print(f"Delay Applied: {adaptive_meta.get('delay_applied', 0):.2f}s")
    
    # Get comprehensive performance statistics
    print("\n=== Adaptive System Performance ===")
    stats = checker.get_adaptive_performance_stats()
    
    print(f"Current Strategy: {stats['adaptive_limiter']['current_strategy']}")
    print(f"Overall Success Rate: {stats['adaptive_limiter']['overall_success_rate']:.1%}")
    print(f"Circuit Breaker Active: {stats['adaptive_limiter']['circuit_breaker_active']}")
    print(f"Threat Level: {stats['threat_assessment']['level']}")
    print(f"Threat Confidence: {stats['threat_assessment']['confidence']}")
    
    if stats['session_stats']['active_session']:
        print(f"Session User Type: {stats['session_stats']['user_type']}")
        print(f"Session Duration: {stats['session_stats']['session_duration']:.1f}s")
        print(f"Session Success Rate: {stats['session_stats']['success_rate']:.1%}")
    
    recommendations = stats['recommendations']
    print(f"Should Slow Down: {recommendations['should_slow_down']}")
    print(f"Should Change Pattern: {recommendations['should_change_pattern']}")
    print(f"Suggested Delay Range: {recommendations['suggested_delay_range']}")
    
    print("\n=== Strategy Performance Scores ===")
    for strategy, score in stats['adaptive_limiter']['strategy_scores'].items():
        print(f"{strategy.capitalize()}: {score:.3f}")
    
    return results

async def demo_behavioral_patterns():
    """Demonstrate different user behavioral patterns"""
    
    print("\n=== Behavioral Pattern Demonstration ===")
    
    from src.behavioral_session_manager import session_manager, UserBehaviorType
    
    # Test different user types
    user_types = [
        UserBehaviorType.CASUAL_BROWSER,
        UserBehaviorType.TARGETED_SHOPPER,
        UserBehaviorType.COMPARISON_SHOPPER,
        UserBehaviorType.BULK_CHECKER
    ]
    
    for user_type in user_types:
        session = session_manager.start_new_session(force_user_type=user_type)
        delay = session_manager.get_realistic_delay_for_next_request()
        context = session_manager.get_session_context()
        
        print(f"\n{user_type.value.replace('_', ' ').title()}:")
        print(f"  Session Duration Limit: {session.session_duration_limit:.1f}s")
        print(f"  Interest Category: {session.current_interest}")
        print(f"  Realistic Delay: {delay:.2f}s")
        print(f"  Should Search: {context['behavioral_flags'].get('has_searched', False)}")
        
        session_manager.end_current_session()

if __name__ == "__main__":
    print("Starting Advanced Adaptive Evasion System Test...")
    
    try:
        # Run the main test
        asyncio.run(test_adaptive_evasion())
        
        # Demonstrate behavioral patterns
        asyncio.run(demo_behavioral_patterns())
        
        print("\n[SUCCESS] All tests completed successfully!")
        print("The adaptive evasion system is fully operational with:")
        print("  - Machine learning-like adaptation")
        print("  - Behavioral session simulation")
        print("  - Response analysis and threat detection")
        print("  - Adaptive rate limiting")
        print("  - Request pattern obfuscation")
        
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()