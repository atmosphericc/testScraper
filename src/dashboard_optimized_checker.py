#!/usr/bin/env python3
"""
Dashboard-optimized stock checker with faster initial response
Balances speed for UI responsiveness with evasion capabilities
"""

try:
    from .authenticated_stock_checker import AuthenticatedStockChecker
except ImportError:
    from authenticated_stock_checker import AuthenticatedStockChecker
import asyncio
import logging
from pathlib import Path
import json

class DashboardOptimizedChecker(AuthenticatedStockChecker):
    """Stock checker optimized for dashboard display with fast initial checking"""
    
    def __init__(self, session_path: str = "sessions/target_storage.json"):
        super().__init__(session_path)
        self.first_run = True
        
    async def check_multiple_products_fast(self, tcins: list, max_delay_override: float = 3.0) -> list:
        """Optimized for dashboard - faster initial checking, then normal adaptive behavior"""
        results = []
        
        # Start behavioral session
        try:
            from .behavioral_session_manager import session_manager
        except ImportError:
            from behavioral_session_manager import session_manager
        session_manager.start_new_session()
        
        self.logger.info(f"Dashboard mode: Checking {len(tcins)} products with {max_delay_override}s max delay")
        
        for i, tcin in enumerate(tcins):
            # For dashboard responsiveness, override long delays
            result = await self.check_authenticated_stock_with_override(tcin, max_delay_override)
            results.append(result)
            
            # Shorter delays between products for dashboard
            if i < len(tcins) - 1:
                await asyncio.sleep(1.0)  # Fixed 1 second between products
        
        # End session after batch
        session_manager.end_current_session()
        
        # Log dashboard performance
        success_count = sum(1 for r in results if r.get('available', False))
        total_time = sum(r.get('response_time', 0) for r in results) / 1000.0
        self.logger.info(f"Dashboard check complete: {success_count}/{len(tcins)} in stock, total time: {total_time:.1f}s")
        
        return results
    
    async def check_authenticated_stock_with_override(self, tcin: str, max_delay: float = 3.0) -> dict:
        """Stock check with dashboard-friendly delay limits"""
        
        # Import required modules
        try:
            from .behavioral_session_manager import session_manager
            from .adaptive_rate_limiter import adaptive_limiter
            from .response_analyzer import response_analyzer
        except ImportError:
            from behavioral_session_manager import session_manager
            from adaptive_rate_limiter import adaptive_limiter
            from response_analyzer import response_analyzer
        
        # Get session context
        if not session_manager.current_session:
            session_manager.start_new_session()
        
        session_context = session_manager.get_session_context()
        
        # Use faster delays for dashboard responsiveness
        threat_assessment = response_analyzer.get_threat_assessment()
        threat_level = threat_assessment.get('avg_threat_score', 0)
        
        # Dashboard mode: limit delays to max_delay
        delay, delay_metadata = await adaptive_limiter.get_next_delay(
            threat_level=threat_level, 
            max_delay=max_delay
        )
        
        # Apply behavioral delay but cap it
        behavioral_delay = session_manager.get_realistic_delay_for_next_request()
        final_delay = min(max(delay, behavioral_delay), max_delay)
        
        self.logger.debug(f"Dashboard delay: {final_delay:.2f}s (capped at {max_delay}s)")
        
        await asyncio.sleep(final_delay)
        
        # Continue with normal stock checking logic but capture the metadata
        try:
            # Call the parent method to get the result
            result = await super().check_authenticated_stock(tcin)
            
            # Override the delay metadata to show actual dashboard delay
            if 'adaptive_metadata' in result:
                result['adaptive_metadata']['delay_applied'] = final_delay
                result['adaptive_metadata']['dashboard_optimized'] = True
                result['adaptive_metadata']['max_delay_cap'] = max_delay
            
            return result
            
        except Exception as e:
            # Record failure with learning systems
            adaptive_limiter.record_request_result(False, final_delay, threat_level=0.5)
            session_manager.record_product_interaction(tcin, False, final_delay)
            
            return {
                'available': False,
                'availability_text': f'Dashboard Error: {str(e)}',
                'confidence': 'error',
                'method': 'dashboard_optimized',
                'response_time': final_delay * 1000,
                'price': 0.0,
                'tcin': tcin,
                'name': f'Product {tcin}',
                'adaptive_metadata': {
                    'dashboard_optimized': True,
                    'error_type': type(e).__name__,
                    'max_delay_cap': max_delay
                }
            }

# Global dashboard-optimized checker instance
dashboard_checker = DashboardOptimizedChecker()