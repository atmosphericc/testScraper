#!/usr/bin/env python3
"""
Test the enhanced stealth system with fingerprint rotation and intelligent rate limiting
This should prevent early IP bans by using different API keys, headers, and timing
"""
import asyncio
import sys
import json
from datetime import datetime

# Add src to path
sys.path.append('src')

from src.enhanced_stealth_requester import enhanced_stealth_requester

async def test_enhanced_stealth_system():
    """Test the enhanced stealth system"""
    
    print("🚀 ENHANCED STEALTH SYSTEM TEST")
    print("=" * 60)
    print("Testing with fingerprint rotation and intelligent rate limiting")
    print("This should prevent early IP bans by:")
    print("• Rotating API keys, store IDs, and headers")
    print("• Intelligent rate limiting based on response patterns")
    print("• Adaptive delays that respond to server behavior")
    print()
    
    # Test products from your config
    test_tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    print("📊 INITIAL SYSTEM STATUS")
    print("-" * 40)
    
    # Get initial statistics
    stats = enhanced_stealth_requester.get_statistics()
    print(f"Available fingerprints: {stats['fingerprint_rotation']['total_fingerprints']}")
    print(f"Rate limiter strategy: {stats['intelligent_rate_limiting'].get('strategy', 'initializing')}")
    print()
    
    print("🔄 RUNNING STOCK CHECKS")
    print("-" * 40)
    
    results = []
    
    for i, tcin in enumerate(test_tcins, 1):
        print(f"\n📡 Request {i}/5: Checking TCIN {tcin}")
        print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
        
        try:
            result = await enhanced_stealth_requester.check_stock_stealth(tcin)
            results.append(result)
            
            # Display result
            status_emoji = "✅" if result.get('status') == 'success' else "❌"
            available_text = "IN STOCK" if result.get('available') else "OUT OF STOCK"
            
            print(f"{status_emoji} Status: {result.get('status', 'unknown')}")
            print(f"   Stock: {available_text}")
            print(f"   Name: {result.get('name', 'Unknown')[:50]}...")
            print(f"   Price: ${result.get('price', 0)}")
            print(f"   Response Time: {result.get('response_time', 0):.2f}s")
            print(f"   Fingerprint: {result.get('fingerprint_used', 'unknown')}")
            print(f"   Store: {result.get('store_used', 'unknown')}")
            print(f"   Delay Strategy: {result.get('delay_strategy', 'unknown')}")
            
            if result.get('status') in ['rate_limited', 'blocked_or_not_found']:
                print(f"⚠️  Warning: {result.get('error', 'Unknown issue')}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            results.append({'tcin': tcin, 'status': 'exception', 'error': str(e)})
    
    print("\n" + "=" * 60)
    print("📊 FINAL STATISTICS")
    print("-" * 40)
    
    # Get final statistics
    final_stats = enhanced_stealth_requester.get_statistics()
    
    print("Request Statistics:")
    req_stats = final_stats['requests']
    print(f"  Total Requests: {req_stats['total']}")
    print(f"  Successful: {req_stats['successful']}")
    print(f"  Success Rate: {req_stats['success_rate']:.1%}")
    
    print("\nFingerprint Rotation:")
    fp_stats = final_stats['fingerprint_rotation']
    print(f"  Total Fingerprints: {fp_stats['total_fingerprints']}")
    print(f"  Available: {fp_stats['available_fingerprints']}")
    print(f"  Overall Success Rate: {fp_stats['overall_success_rate']:.1%}")
    print(f"  Best Fingerprint: {fp_stats['best_fingerprint_success_rate']:.1%}")
    print(f"  Current Key: {fp_stats['current_fingerprint_key']}")
    
    print("\nIntelligent Rate Limiting:")
    rl_stats = final_stats['intelligent_rate_limiting']
    if rl_stats:
        print(f"  Current Strategy: {rl_stats.get('strategy', 'unknown')}")
        print(f"  Current Delay: {rl_stats.get('current_delay', 0):.1f}s")
        print(f"  Baseline Response Time: {rl_stats.get('baseline_response_time', 0):.2f}s")
        print(f"  Success Rate: {rl_stats.get('success_rate', 0):.1%}")
        print(f"  Circuit Breaker: {'ACTIVE' if rl_stats.get('circuit_breaker_active') else 'inactive'}")
    
    print("\n" + "=" * 60)
    print("📋 SUMMARY")
    print("-" * 40)
    
    successful_checks = len([r for r in results if r.get('status') == 'success'])
    rate_limited = len([r for r in results if r.get('status') == 'rate_limited'])
    blocked = len([r for r in results if r.get('status') == 'blocked_or_not_found'])
    errors = len([r for r in results if r.get('status') in ['error', 'exception', 'parse_error']])
    
    print(f"Successful Checks: {successful_checks}/5")
    print(f"Rate Limited: {rate_limited}/5")
    print(f"Blocked/Not Found: {blocked}/5")
    print(f"Errors: {errors}/5")
    
    if successful_checks >= 3:
        print("\n🎉 SUCCESS: System is working well!")
        print("   The enhanced stealth system is preventing early IP bans.")
    elif rate_limited > 0:
        print("\n⚠️  RATE LIMITED: System detected rate limiting")
        print("   The intelligent rate limiter should adapt for future requests.")
    elif blocked > 0:
        print("\n🚨 BLOCKED: Possible IP blocking detected")
        print("   Consider using VPN or different network.")
    else:
        print("\n❓ MIXED RESULTS: System needs adjustment")
    
    print("\n💡 RECOMMENDATIONS:")
    if rate_limited > 0 or blocked > 0:
        print("• The system will automatically increase delays")
        print("• Different fingerprints will be used for future requests")
        print("• Consider running tests with longer intervals")
    else:
        print("• System is working optimally")
        print("• You can increase request frequency if needed")
    
    print(f"\n⏰ Test completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(test_enhanced_stealth_system())