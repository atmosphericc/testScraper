#!/usr/bin/env python3
"""
Background Target.com Session Manager for Ultra-Fast Stock Checking
Maintains persistent authenticated browser sessions for instant hybrid verification
"""
import asyncio
import json
import time
from typing import Dict, List, Optional, Set
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import aiohttp
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import logging

@dataclass
class SessionContext:
    """Represents a background browser session context"""
    context: BrowserContext
    page: Page
    last_used: float
    in_use: bool = False
    session_id: str = ""
    
class BackgroundSessionManager:
    """
    Ultra-fast stock checking using persistent background Target.com sessions
    - Maintains 3-5 parallel browser contexts
    - Each context stays logged in and ready
    - Instant page loads for hybrid verification
    - Zero missed opportunities with parallel processing
    """
    
    def __init__(self, num_sessions: int = 4, session_storage_path: str = "sessions/target_storage.json"):
        self.num_sessions = num_sessions
        self.session_storage_path = Path(session_storage_path)
        self.sessions: List[SessionContext] = []
        self.browser: Optional[Browser] = None
        self.playwright = None
        self.logger = logging.getLogger(__name__)
        
        # Performance tracking
        self.check_times = []
        self.api_cache = {}  # Simple cache for API responses
        
    async def initialize(self):
        """Initialize persistent browser sessions"""
        if not self.session_storage_path.exists():
            raise FileNotFoundError("No Target session found! Run: python setup.py login")
            
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        
        # Create multiple persistent contexts
        for i in range(self.num_sessions):
            context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
                storage_state=str(self.session_storage_path)
            )
            
            page = await context.new_page()
            
            session_ctx = SessionContext(
                context=context,
                page=page,
                last_used=0,
                session_id=f"session_{i}"
            )
            
            self.sessions.append(session_ctx)
            
        self.logger.info(f"Initialized {self.num_sessions} background Target sessions")
        
    async def get_available_session(self) -> Optional[SessionContext]:
        """Get next available session context"""
        # Find least recently used session that's not in use
        available = [s for s in self.sessions if not s.in_use]
        if not available:
            return None
            
        return min(available, key=lambda x: x.last_used)
        
    async def ultra_fast_stock_check(self, tcin: str, use_hybrid: bool = True) -> Dict:
        """
        Ultra-fast stock check with optional hybrid verification
        - API first (sub-100ms)
        - Background browser verification if needed (sub-2 seconds)
        """
        start_time = time.time()
        
        # Step 1: API Check (fastest)
        api_result = await self._api_check(tcin)
        
        if not use_hybrid:
            self.check_times.append(time.time() - start_time)
            return api_result
            
        # Step 2: Hybrid verification only if API is uncertain
        if api_result.get('confidence') == 'high':
            self.check_times.append(time.time() - start_time)
            return api_result
            
        # Use background session for instant verification
        session = await self.get_available_session()
        if not session:
            # All sessions busy, return API result
            self.check_times.append(time.time() - start_time)
            return api_result
            
        try:
            session.in_use = True
            hybrid_result = await self._background_browser_check(session, tcin)
            
            # Combine results with browser as authority
            final_result = {
                **api_result,
                'available': hybrid_result.get('available', api_result['available']),
                'confidence': 'verified',
                'method': 'hybrid_verified',
                'browser_confirmed': True,
                'check_time': time.time() - start_time
            }
            
        except Exception as e:
            self.logger.error(f"Background browser check failed for {tcin}: {e}")
            final_result = api_result
            
        finally:
            session.in_use = False
            session.last_used = time.time()
            
        self.check_times.append(time.time() - start_time)
        return final_result
        
    async def _api_check(self, tcin: str) -> Dict:
        """Fast API-only check with caching"""
        cache_key = f"{tcin}_{int(time.time() / 30)}"  # 30-second cache
        if cache_key in self.api_cache:
            return self.api_cache[cache_key]
            
        # Import stock checker for API logic
        import sys
        sys.path.insert(0, 'src')
        from stock_checker import StockChecker
        
        checker = StockChecker(use_website_checking=False)
        
        try:
            result = await checker.check_stock_async(tcin)
            self.api_cache[cache_key] = result
            return result
        except Exception as e:
            return {
                'tcin': tcin,
                'available': False,
                'confidence': 'error',
                'reason': f'API error: {str(e)}',
                'method': 'api_error'
            }
            
    async def _background_browser_check(self, session: SessionContext, tcin: str) -> Dict:
        """Ultra-fast browser verification using persistent session"""
        url = f"https://www.target.com/p/-/A-{tcin}"
        
        try:
            # Navigate to page (should be instant with persistent session)
            await session.page.goto(url, wait_until='domcontentloaded', timeout=5000)
            
            # Quick check for add to cart button
            add_to_cart = session.page.locator('button:has-text("Add to cart")')
            
            # Wait briefly for dynamic content
            try:
                await add_to_cart.wait_for(timeout=2000)
                is_disabled = await add_to_cart.is_disabled()
                is_visible = await add_to_cart.is_visible()
                
                available = is_visible and not is_disabled
                
            except:
                # No add to cart button or timeout
                available = False
                
            return {
                'available': available,
                'method': 'background_browser',
                'session_id': session.session_id
            }
            
        except Exception as e:
            raise Exception(f"Browser check failed: {str(e)}")
            
    async def parallel_stock_check(self, tcins: List[str], max_concurrent: int = None) -> Dict[str, Dict]:
        """
        Parallel stock checking for 50+ SKUs with zero missed opportunities
        - Intelligent batching
        - All sessions utilized
        - Sub-3-second total time
        """
        if max_concurrent is None:
            max_concurrent = min(len(tcins), self.num_sessions * 2)  # Allow some overlap
            
        start_time = time.time()
        results = {}
        
        # Create semaphore to limit concurrent operations
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def check_with_semaphore(tcin: str):
            async with semaphore:
                return await self.ultra_fast_stock_check(tcin, use_hybrid=True)
                
        # Execute all checks in parallel
        tasks = [check_with_semaphore(tcin) for tcin in tcins]
        check_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for tcin, result in zip(tcins, check_results):
            if isinstance(result, Exception):
                results[tcin] = {
                    'tcin': tcin,
                    'available': False,
                    'confidence': 'error',
                    'reason': f'Check failed: {str(result)}',
                    'method': 'error'
                }
            else:
                results[tcin] = result
                
        total_time = time.time() - start_time
        
        # Summary
        available_count = sum(1 for r in results.values() if r.get('available'))
        verified_count = sum(1 for r in results.values() if r.get('confidence') == 'verified')
        
        self.logger.info(f"Parallel check: {len(tcins)} products in {total_time:.2f}s")
        self.logger.info(f"Available: {available_count}, Browser verified: {verified_count}")
        
        return {
            'results': results,
            'summary': {
                'total_products': len(tcins),
                'total_time': total_time,
                'avg_time_per_product': total_time / len(tcins),
                'available_count': available_count,
                'verified_count': verified_count,
                'sessions_used': len([s for s in self.sessions if s.last_used > start_time])
            }
        }
        
    async def health_check(self):
        """Check health of all background sessions"""
        healthy = 0
        for session in self.sessions:
            try:
                # Simple navigation test
                await session.page.goto("https://www.target.com/", timeout=5000)
                healthy += 1
            except:
                self.logger.warning(f"Session {session.session_id} appears unhealthy")
                
        self.logger.info(f"Session health: {healthy}/{len(self.sessions)} healthy")
        return healthy == len(self.sessions)
        
    async def get_performance_stats(self) -> Dict:
        """Get performance statistics"""
        if not self.check_times:
            return {}
            
        return {
            'total_checks': len(self.check_times),
            'avg_check_time': sum(self.check_times) / len(self.check_times),
            'fastest_check': min(self.check_times),
            'slowest_check': max(self.check_times),
            'cache_hits': len(self.api_cache),
            'active_sessions': len(self.sessions)
        }
        
    async def cleanup(self):
        """Clean up resources"""
        for session in self.sessions:
            try:
                await session.context.close()
            except:
                pass
                
        if self.browser:
            await self.browser.close()
            
        if self.playwright:
            await self.playwright.stop()