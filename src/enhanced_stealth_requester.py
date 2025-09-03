#!/usr/bin/env python3
"""
Enhanced stealth requester with fingerprint rotation and intelligent rate limiting
Prevents early IP bans by rotating API keys, headers, and request patterns
"""
try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False

import random
import time
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime

try:
    from .request_fingerprint_rotator import fingerprint_rotator, RequestFingerprint
    from .intelligent_rate_limiter import intelligent_limiter, ResponseMetrics
except ImportError:
    # Fallback for direct execution
    from request_fingerprint_rotator import fingerprint_rotator, RequestFingerprint
    from intelligent_rate_limiter import intelligent_limiter, ResponseMetrics

class EnhancedStealthRequester:
    """Ultra-stealth HTTP client with fingerprint rotation and intelligent rate limiting"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Browser profiles for curl_cffi
        self.browser_profiles = [
            "chrome110", "chrome116", "chrome119", "chrome120", "chrome124",
            "edge99", "edge101", "safari15_3", "safari15_5"
        ] if CURL_CFFI_AVAILABLE else ["chrome"]
        
        # Request statistics
        self.total_requests = 0
        self.successful_requests = 0
        
    async def check_stock_stealth(self, tcin: str) -> Dict:
        """Check stock using rotated fingerprints and intelligent rate limiting"""
        
        # Get next delay from intelligent rate limiter
        delay, delay_metadata = await intelligent_limiter.get_next_delay()
        
        self.logger.info(f"Waiting {delay:.1f}s before request (strategy: {delay_metadata['strategy']})")
        await asyncio.sleep(delay)
        
        # Get rotated request parameters
        url, params, headers = fingerprint_rotator.get_request_params(tcin)
        
        self.logger.debug(f"Using API key ending in ...{params['key'][-8:]} with store {params['store_id']}")
        
        start_time = time.time()
        response_metrics = None
        success = False
        result = None
        
        try:
            if CURL_CFFI_AVAILABLE:
                # Use curl_cffi for perfect browser impersonation
                browser_profile = random.choice(self.browser_profiles)
                
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=15,
                    impersonate=browser_profile,
                    http2=True,
                )
            else:
                # Fallback to regular requests
                import requests as fallback_requests
                response = fallback_requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=15
                )
            
            # Record response metrics
            response_time = time.time() - start_time
            content_length = len(response.content) if hasattr(response, 'content') else 0
            
            response_metrics = ResponseMetrics(
                response_time=response_time,
                status_code=response.status_code,
                timestamp=time.time(),
                content_length=content_length,
                headers=dict(response.headers)
            )
            
            # Get response body for analysis
            try:
                response_body = response.text
            except:
                response_body = ""
            
            # Record response for rate limiting analysis
            intelligent_limiter.record_response(response_metrics, response_body)
            
            self.total_requests += 1
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result = self.parse_availability(tcin, data)
                    success = True
                    self.successful_requests += 1
                except Exception as parse_error:
                    self.logger.error(f"Failed to parse response for {tcin}: {parse_error}")
                    result = {
                        'tcin': tcin,
                        'available': False,
                        'status': 'parse_error',
                        'error': f'Parse error: {parse_error}',
                        'response_time': response_time
                    }
            
            elif response.status_code == 429:
                self.logger.warning(f"Rate limited for {tcin} - switching fingerprint")
                result = {
                    'tcin': tcin,
                    'available': False,
                    'status': 'rate_limited',
                    'error': 'Rate limited - will back off',
                    'response_time': response_time
                }
                
                # Force fingerprint cooldown on rate limiting
                if fingerprint_rotator.current_fingerprint:
                    fingerprint_rotator.current_fingerprint.failure_count += 10
            
            elif response.status_code in [403, 404]:
                self.logger.warning(f"HTTP {response.status_code} for {tcin} - possible IP blocking")
                result = {
                    'tcin': tcin,
                    'available': False,
                    'status': 'blocked_or_not_found',
                    'error': f'HTTP {response.status_code} - IP may be blocked',
                    'response_time': response_time
                }
            
            else:
                self.logger.warning(f"HTTP {response.status_code} for {tcin}")
                result = {
                    'tcin': tcin,
                    'available': False,
                    'status': 'error',
                    'error': f'HTTP {response.status_code}',
                    'response_time': response_time
                }
                
        except Exception as e:
            response_time = time.time() - start_time
            self.logger.error(f"Stealth request failed for {tcin}: {e}")
            
            # Create error response metrics
            response_metrics = ResponseMetrics(
                response_time=response_time,
                status_code=0,
                timestamp=time.time(),
                content_length=0,
                headers={}
            )
            intelligent_limiter.record_response(response_metrics, str(e))
            
            result = {
                'tcin': tcin,
                'available': False,
                'status': 'request_exception',
                'error': str(e),
                'response_time': response_time
            }
            self.total_requests += 1
        
        # Record fingerprint result
        if fingerprint_rotator.current_fingerprint:
            fingerprint_rotator.record_fingerprint_result(
                fingerprint_rotator.current_fingerprint,
                success
            )
        
        # Add metadata to result
        if result:
            result.update({
                'fingerprint_used': params['key'][-8:] + "...",
                'store_used': params['store_id'],
                'delay_strategy': delay_metadata['strategy'],
                'delay_applied': delay
            })
        
        return result
    
    def parse_availability(self, tcin: str, data: Dict) -> Dict:
        """Parse Target API response"""
        try:
            product = data['data']['product']
            item = product['item']
            
            name = item.get('product_description', {}).get('title', 'Unknown Product')
            price = product.get('price', {}).get('current_retail', 0)
            
            fulfillment = item.get('fulfillment', {})
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            # Stock detection logic
            if is_marketplace:
                available = purchase_limit > 0
                seller_type = "third-party"
            else:
                has_eligibility_rules = bool(item.get('eligibility_rules'))
                available = has_eligibility_rules and ship_to_guest and purchase_limit >= 1
                seller_type = "target"
            
            return {
                'tcin': tcin,
                'name': name,
                'available': available,
                'price': price,
                'purchase_limit': purchase_limit,
                'seller_type': seller_type,
                'is_marketplace': is_marketplace,
                'status': 'success',
                'eligibility_active': ship_to_guest,
                'has_eligibility_rules': bool(eligibility)
            }
            
        except KeyError as e:
            self.logger.error(f"Missing key in API response for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': f'Missing key: {e}'
            }
        except Exception as e:
            self.logger.error(f"Parse error for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': str(e)
            }
    
    def get_statistics(self) -> Dict:
        """Get comprehensive statistics"""
        success_rate = self.successful_requests / self.total_requests if self.total_requests > 0 else 0
        
        return {
            'requests': {
                'total': self.total_requests,
                'successful': self.successful_requests,
                'success_rate': success_rate
            },
            'fingerprint_rotation': fingerprint_rotator.get_statistics(),
            'intelligent_rate_limiting': intelligent_limiter.get_statistics()
        }
    
    def reset_statistics(self):
        """Reset all statistics"""
        self.total_requests = 0
        self.successful_requests = 0
        fingerprint_rotator.reset_fingerprint_stats()
        intelligent_limiter.reset()
        self.logger.info("All statistics reset")

# Global enhanced stealth requester
enhanced_stealth_requester = EnhancedStealthRequester()