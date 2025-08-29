"""
Test script to verify the complete stock-to-purchase flow
Run this to test your bot end-to-end
"""
import asyncio
import sys
import os
sys.path.append('src')

from monitor import TargetMonitor

async def test_complete_flow():
    print("🚀 Testing Complete Stock-to-Purchase Flow")
    print("=" * 50)
    
    # Initialize monitor
    monitor = TargetMonitor("config/product_config.json")
    
    print(f"✅ Monitor initialized")
    print(f"✅ Products loaded: {len(monitor.products)}")
    print(f"✅ Mode: {monitor.config['settings']['mode']}")
    
    # Test session
    try:
        session_valid = await monitor.session_manager.is_session_valid()
        print(f"✅ Session valid: {session_valid}")
    except Exception as e:
        print(f"❌ Session error: {e}")
    
    # Test one product manually
    if monitor.products:
        test_product = monitor.products[0]
        print(f"\n🧪 Testing product: {test_product['name']} ({test_product['tcin']})")
        
        # This tests your complete flow
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                # This will trigger your async purchase if in stock
                await monitor.check_and_buy(session, test_product)
                print("✅ check_and_buy completed")
        except Exception as e:
            print(f"❌ Flow error: {e}")
    
    print("\n📊 Current Status:")
    print(f"In stock items: {len(monitor.in_stock_items)}")
    print(f"Active purchases: {len(monitor.active_purchases)}")
    print(f"Total checks: {monitor.total_checks}")

if __name__ == "__main__":
    asyncio.run(test_complete_flow())