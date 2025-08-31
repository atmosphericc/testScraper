#!/usr/bin/env python3
"""
Test all products from config to ensure API connectivity
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from stealth_requester import StealthRequester

async def test_config_products():
    """Test all products from the config file"""
    tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    stealth = StealthRequester()
    
    print('🔍 Testing all 5 products from config...\n')
    
    working_count = 0
    
    for i, tcin in enumerate(tcins, 1):
        print(f'[{i}/5] Testing TCIN {tcin}:')
        result = stealth.check_stock_stealth(tcin)
        
        status = result.get('status', 'unknown')
        available = result.get('available', False)
        name = result.get('name', 'Unknown')
        price = result.get('price', 0)
        
        print(f'  Status: {status}')
        print(f'  Available: {"🟢 YES" if available else "🔴 NO"}')
        print(f'  Name: {name}')
        print(f'  Price: ${price:.2f}')
        
        if result.get('error'):
            print(f'  ❌ Error: {result["error"]}')
        else:
            working_count += 1
            print(f'  ✅ API Response OK')
            
        print('-' * 60)
        
        # Small delay between requests
        if i < len(tcins):
            await asyncio.sleep(2)
    
    print(f'\n📊 Results: {working_count}/{len(tcins)} products responding correctly')
    
    if working_count == len(tcins):
        print('🎉 All products are working! API connectivity restored.')
    else:
        print(f'⚠️  {len(tcins) - working_count} products still having issues.')

if __name__ == "__main__":
    asyncio.run(test_config_products())