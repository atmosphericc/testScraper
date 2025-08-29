import sys
sys.path.append('src')

from stock_checker import StockChecker
import asyncio
import aiohttp

async def test_stock_checker():
    checker = StockChecker()
    
    # Test with the products from your config that are showing as "in stock"
    test_tcins = ["94724987", "94681770", "89542109"]
    
    async with aiohttp.ClientSession() as session:
        for tcin in test_tcins:
            result = await checker.check_stock(session, tcin)
            print(f"\nTCIN {tcin}:")
            print(f"Full result: {result}")
            print(f"Available: {result.get('available')}")
            print(f"Status: {result.get('status')}")
            
            # Test the monitor logic
            if result.get('available'):
                print(f"❌ Monitor would mark as IN STOCK")
            else:
                print(f"✅ Monitor would mark as OUT OF STOCK")

if __name__ == "__main__":
    asyncio.run(test_stock_checker())