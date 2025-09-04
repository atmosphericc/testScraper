#!/usr/bin/env python3
"""
DASHBOARD INTEGRATION - Connect Advanced Evasion System to Dashboard
Fixes the 500 error and provides proper integration with the new system
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from flask import Flask, jsonify
import asyncio
import json
from pathlib import Path
from datetime import datetime
import time

from ultra_stealth_bypass import UltraStealthBypass
from advanced_evasion_engine import AdvancedEvasionEngine

app = Flask(__name__)

class AdvancedDashboardIntegration:
    """Integration layer for advanced evasion system with dashboard"""
    
    def __init__(self):
        self.ultra_stealth = UltraStealthBypass()
        self.advanced_evasion = AdvancedEvasionEngine()
        self.last_check_time = {}
        self.check_results = {}
        
    def get_config(self):
        """Load product configuration"""
        config_path = Path('config/product_config.json')
        if config_path.exists():
            with open(config_path, 'r') as f:
                return json.load(f)
        return {"products": []}
    
    async def check_stock_with_evasion(self, tcin: str, method: str = 'ultra_stealth') -> dict:
        """Check stock using advanced evasion system"""
        try:
            if method == 'ultra_stealth':
                result = await self.ultra_stealth.check_stock_ultra_stealth(tcin, warm_proxy=False)
            else:
                result = await self.advanced_evasion.check_stock_advanced(tcin, warm_session=False)
            
            # Normalize the result for dashboard consumption
            normalized = {
                'tcin': tcin,
                'available': result.get('available', False),
                'status': 'IN_STOCK' if result.get('available') else 'OUT_OF_STOCK',
                'details': result.get('reason', result.get('error', 'Unknown')),
                'price': result.get('price', 0),
                'confidence': result.get('confidence', 'unknown'),
                'response_time': result.get('response_time', 0),
                'last_checked': datetime.now().isoformat(),
                'method_used': method,
                'http_code': result.get('http_code'),
                'seller_type': result.get('seller_type', 'unknown')
            }
            
            # Handle errors
            if result.get('status') in ['blocked_or_not_found', 'rate_limited', 'request_exception']:
                normalized['status'] = 'ERROR'
                normalized['details'] = result.get('error', 'Check failed')
            
            return normalized
            
        except Exception as e:
            return {
                'tcin': tcin,
                'available': False,
                'status': 'ERROR',
                'details': str(e),
                'price': 0,
                'last_checked': datetime.now().isoformat(),
                'method_used': method
            }
    
    async def check_all_configured_products(self, method: str = 'ultra_stealth') -> dict:
        """Check all configured products"""
        config = self.get_config()
        products = config.get('products', [])
        
        if not products:
            return {'error': 'No products configured'}
        
        results = {}
        
        for product in products[:5]:  # Limit to 5 to avoid overload
            if product.get('enabled', True):
                tcin = product['tcin']
                
                # Check if we've checked this recently (avoid spam)
                last_check = self.last_check_time.get(tcin, 0)
                if time.time() - last_check < 30:  # 30 second minimum between checks
                    # Use cached result
                    if tcin in self.check_results:
                        results[tcin] = self.check_results[tcin]
                        continue
                
                result = await self.check_stock_with_evasion(tcin, method)
                results[tcin] = result
                
                # Cache the result
                self.check_results[tcin] = result
                self.last_check_time[tcin] = time.time()
                
                # Add delay to avoid hitting API too fast
                await asyncio.sleep(2)
        
        return results

# Global integration instance
integration = AdvancedDashboardIntegration()

@app.route('/api/live-stock-status')
def api_live_stock_status():
    """Fixed API endpoint for live stock status"""
    try:
        # Run async function properly
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        stock_results = loop.run_until_complete(
            integration.check_all_configured_products('ultra_stealth')
        )
        
        return jsonify(stock_results)
        
    except Exception as e:
        return jsonify({
            'error': f'Stock check failed: {str(e)}',
            'timestamp': datetime.now().isoformat(),
            'suggestion': 'Try using the advanced stock monitor directly: python advanced_stock_monitor.py --tcin XXXXXX'
        }), 500

@app.route('/api/advanced-status')
def api_advanced_status():
    """Get status of advanced evasion system"""
    try:
        # Get evasion stats
        ultra_stats = integration.ultra_stealth.get_evasion_stats() if hasattr(integration.ultra_stealth, 'get_evasion_stats') else {}
        advanced_stats = integration.advanced_evasion.get_evasion_stats() if hasattr(integration.advanced_evasion, 'get_evasion_stats') else {}
        
        return jsonify({
            'ultra_stealth_system': {
                'status': 'active',
                'profile': f"{integration.ultra_stealth.current_profile.browser} {integration.ultra_stealth.current_profile.version}",
                'stats': ultra_stats
            },
            'advanced_evasion_system': {
                'status': 'active', 
                'fingerprint': integration.advanced_evasion.fingerprint.browser_type,
                'behavioral_pattern': integration.advanced_evasion.behavioral_pattern,
                'stats': advanced_stats
            },
            'recent_checks': len(integration.check_results),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test-single/<tcin>')
def api_test_single(tcin):
    """Test single TCIN with advanced evasion"""
    try:
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(
            integration.check_stock_with_evasion(tcin, 'ultra_stealth')
        )
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("="*60)
    print("ADVANCED EVASION DASHBOARD INTEGRATION")
    print("Fixed API endpoints running on: http://localhost:5002")
    print("="*60)
    print("Endpoints:")
    print("  /api/live-stock-status - Check all configured products")
    print("  /api/advanced-status - System status")
    print("  /api/test-single/<tcin> - Test single TCIN")
    print("="*60)
    
    app.run(debug=True, host='127.0.0.1', port=5002)