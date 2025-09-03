"""
Advanced API-only stock checker with machine learning-like adaptive evasion
Zero web scraping - pure API with intelligent bot detection avoidance
"""
import asyncio
import aiohttp
import json
from pathlib import Path
import logging
import random
import time
import ssl
import html

# Import advanced evasion systems
try:
    from .behavioral_session_manager import session_manager
    from .adaptive_rate_limiter import adaptive_limiter
    from .response_analyzer import response_analyzer
    from .request_pattern_obfuscator import request_obfuscator
except ImportError:
    # Fallback for direct execution
    from behavioral_session_manager import session_manager
    from adaptive_rate_limiter import adaptive_limiter
    from response_analyzer import response_analyzer
    from request_pattern_obfuscator import request_obfuscator

class AuthenticatedStockChecker:
    """Fast API-only stock checker using fulfillment endpoint for maximum speed"""
    
    def __init__(self, session_path: str = "sessions/target_storage.json"):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1"
        self.product_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        
        # Load session cookies for authenticated requests
        self.cookies = self._load_session_cookies()
    
    def _load_session_cookies(self) -> dict:
        """Load cookies from target_storage.json for authenticated API calls"""
        try:
            if not self.session_path.exists():
                self.logger.warning(f"Session file not found: {self.session_path}")
                return {}
            
            with open(self.session_path, 'r') as f:
                session_data = json.load(f)
            
            # Extract cookies from session storage
            cookies = {}
            if 'cookies' in session_data:
                for cookie in session_data['cookies']:
                    cookies[cookie['name']] = cookie['value']
            
            return cookies
        except Exception as e:
            self.logger.error(f"Error loading session cookies: {e}")
            return {}
    
    def _parse_fulfillment_response(self, fulfillment_data: dict, product_data: dict, tcin: str, start_time: float) -> dict:
        """Enhanced parser for combined fulfillment + product API data"""
        try:
            response_time = (time.time() - start_time) * 1000
            
            # Parse fulfillment data (stock availability)
            fulfillment_product = fulfillment_data.get('data', {}).get('product', {})
            fulfillment = fulfillment_product.get('fulfillment', {})
            
            # Key stock indicators from fulfillment API
            sold_out = fulfillment.get('sold_out', True)
            out_of_stock_all_stores = fulfillment.get('is_out_of_stock_in_all_store_locations', True)
            
            # Shipping availability (most important for online purchases)
            shipping_options = fulfillment.get('shipping_options', {})
            shipping_status = shipping_options.get('availability_status', 'UNAVAILABLE')
            shipping_quantity = shipping_options.get('available_to_promise_quantity', 0)
            loyalty_status = shipping_options.get('loyalty_availability_status', 'UNAVAILABLE')
            
            # Store pickup availability
            store_options = fulfillment.get('store_options', [])
            pickup_available = False
            if store_options:
                for store in store_options:
                    pickup_status = store.get('order_pickup', {}).get('availability_status', 'UNAVAILABLE')
                    if pickup_status not in ['UNAVAILABLE', 'NOT_SOLD_IN_STORE']:
                        pickup_available = True
                        break
            
            # Parse product data (name, price, details)
            product_name = f'Product {tcin}'  # Default
            current_price = 0.0
            formatted_price = "$0.00"
            
            if product_data:
                product_info = product_data.get('data', {}).get('product', {})
                
                # Extract product name
                item = product_info.get('item', {})
                if 'product_description' in item:
                    desc = item['product_description']
                    product_name = desc.get('title', f'Product {tcin}')
                    # Clean up the name
                    if product_name:
                        product_name = product_name.replace(' : Target', '').strip()
                        # Remove HTML entities
                        import html
                        product_name = html.unescape(product_name)
                
                # Extract price information  
                price_info = product_info.get('price', {})
                current_price = price_info.get('current_retail', 0.0) or 0.0
                formatted_price = price_info.get('formatted_current_price', f'${current_price:.2f}')
            
            # ENHANCED AVAILABILITY LOGIC
            available = False
            confidence = 'high'
            availability_details = []
            
            # Primary check: Shipping availability (most important for online orders)
            if shipping_status == 'IN_STOCK' and not sold_out and shipping_quantity > 0:
                available = True
                availability_details.append(f"Shipping: {shipping_quantity} available")
                if loyalty_status == 'IN_STOCK':
                    availability_details.append("Target Circle eligible")
            
            # Secondary check: Store pickup as alternative
            elif pickup_available and not sold_out:
                available = True
                availability_details.append("Store pickup available")
                confidence = 'medium'  # Less reliable than shipping
            
            # Tertiary check: Handle edge cases
            elif shipping_status in ['LIMITED', 'BACKORDER'] and not sold_out:
                available = True
                availability_details.append(f"Limited availability: {shipping_status.lower()}")
                confidence = 'medium'
            
            # Determine final availability text
            if available:
                base_text = "In stock"
                if availability_details:
                    availability_text = f"{base_text} - {', '.join(availability_details)}"
                else:
                    availability_text = f"{base_text} - Available for purchase"
            else:
                # Detailed out-of-stock reasons
                reasons = []
                if sold_out:
                    reasons.append("sold out")
                if shipping_status == 'UNAVAILABLE':
                    reasons.append("shipping unavailable")
                if out_of_stock_all_stores:
                    reasons.append("out of stock in all stores")
                
                if reasons:
                    availability_text = f"Out of stock - {', '.join(reasons)}"
                else:
                    availability_text = f"Unavailable - Status: {shipping_status}"
            
            # Log detailed info for debugging
            self.logger.debug(f"TCIN {tcin}: sold_out={sold_out}, shipping={shipping_status}, qty={shipping_quantity}")
            self.logger.info(f"API Check {tcin}: {'IN STOCK' if available else 'OUT OF STOCK'} - {availability_text}")
            
            return {
                'available': available,
                'availability_text': availability_text,
                'confidence': confidence,
                'method': 'dual_api',  # Using both fulfillment + product APIs
                'response_time': response_time,
                'price': current_price,
                'formatted_price': formatted_price,
                'tcin': tcin,
                'name': product_name[:60],  # Allow longer names
                
                # Detailed fulfillment data for advanced use
                'fulfillment_details': {
                    'sold_out': sold_out,
                    'shipping_status': shipping_status,
                    'shipping_quantity': shipping_quantity,
                    'pickup_available': pickup_available,
                    'out_of_stock_all_stores': out_of_stock_all_stores,
                    'loyalty_status': loyalty_status
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error parsing API responses for {tcin}: {e}")
            return {
                'available': False,
                'availability_text': f'Parse Error: {str(e)}',
                'confidence': 'error',
                'method': 'dual_api',
                'response_time': (time.time() - start_time) * 1000,
                'price': 0.0,
                'tcin': tcin,
                'name': f'Product {tcin}'
            }
    
    async def check_authenticated_stock(self, tcin: str) -> dict:
        """Enhanced stock checking using DUAL API approach - fulfillment + product data"""
        start_time = time.time()
        
        # Dynamic parameters with evasion techniques
        visitor_id = ''.join(random.choices('0123456789ABCDEF', k=32))
        store_id = random.choice(['1176', '865', '1847', '2542'])  # Rotate store IDs
        
        # Dynamic user agent rotation
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15'
        ]
        
        current_ua = random.choice(user_agents)
        chrome_version = random.randint(138, 140)
        
        # Get current session context for behavioral adaptation first
        if not session_manager.current_session:
            session_manager.start_new_session()
        
        session_context = session_manager.get_session_context()

        base_headers = {
            'accept': 'application/json',
            'accept-language': random.choice(['en-US,en;q=0.9', 'en-US,en;q=0.8,es;q=0.7', 'en-US,en;q=0.9,fr;q=0.8']),
            'accept-encoding': 'gzip, deflate, br',
            'origin': 'https://www.target.com',
            'referer': request_obfuscator.get_realistic_referer(tcin),  # Dynamic referer
            'sec-ch-ua': f'"Not;A=Brand";v="99", "Google Chrome";v="{chrome_version}", "Chromium";v="{chrome_version}"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': random.choice(['"Windows"', '"macOS"']),
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': current_ua,
            'cache-control': 'no-cache',
            'pragma': 'no-cache'
        }
        
        # Apply browsing behavior headers with session context
        headers = request_obfuscator.get_browsing_headers(base_headers)
        
        # Add session-specific headers based on user behavior type
        if session_context:
            if session_context['user_type'] == 'comparison_shopper':
                headers['sec-ch-ua-prefers-color-scheme'] = 'light'
            elif session_context['user_type'] == 'targeted_shopper':
                headers['sec-ch-prefers-reduced-motion'] = 'no-preference'
        
        # Add referer based on browsing history
        if session_context.get('has_browsing_history'):
            headers['referer'] = request_obfuscator.get_realistic_referer(tcin)
        else:
            headers['referer'] = 'https://www.target.com/'
        
        # Use adaptive rate limiter for intelligent delay calculation
        threat_assessment = response_analyzer.get_threat_assessment()
        threat_level = threat_assessment.get('avg_threat_score', 0)
        
        # Check if we're in test mode for faster checking
        max_delay = None
        try:
            config_path = Path('config/product_config.json')
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    if config.get('settings', {}).get('mode') == 'test':
                        max_delay = 45.0  # Max 45 seconds in test mode (much more conservative)
                        self.logger.debug("Test mode detected - limiting delays to 45s max")
        except Exception as e:
            self.logger.debug(f"Could not check config for test mode: {e}")
        
        delay, delay_metadata = await adaptive_limiter.get_next_delay(threat_level=threat_level, max_delay=max_delay)
        
        # Log adaptive behavior
        self.logger.debug(f"Adaptive delay: {delay:.2f}s, strategy: {delay_metadata['strategy']}, pattern: {delay_metadata['pattern']}")
        
        # Apply behavioral session delay
        behavioral_delay = session_manager.get_realistic_delay_for_next_request()
        final_delay = max(delay, behavioral_delay)
        
        await asyncio.sleep(final_delay)
        
        try:
            # Proxy system placeholder (to be integrated with existing proxy manager)
            proxy = None  # Will be integrated with existing proxy system
            
            # Use different connector for each request to vary network fingerprint
            import ssl
            
            # Create custom SSL context to vary TLS fingerprint
            ssl_context = ssl.create_default_context()
            ssl_context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
            ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1  # Disable old TLS
            
            connector = aiohttp.TCPConnector(
                limit=random.randint(1, 3),
                limit_per_host=random.randint(1, 3),  # Vary connection limits
                enable_cleanup_closed=True,
                ttl_dns_cache=random.randint(60, 300),  # Random DNS cache TTL
                use_dns_cache=random.choice([True, False]),  # Sometimes skip DNS cache
                ssl_context=ssl_context,
                keepalive_timeout=random.randint(30, 60)  # Vary keepalive
            )
            
            # Enhanced cookie management with loaded session cookies
            enhanced_cookies = self.cookies.copy()
            # Add behavioral session cookies
            if session_context:
                enhanced_cookies.update({
                    '_session_id': session_context.get('session_id', ''),
                    '_behavior_type': session_context.get('user_type', 'casual_browser')
                })
            
            # Create session with enhanced settings
            session_kwargs = {
                'cookies': enhanced_cookies,
                'connector': connector,
                'timeout': aiohttp.ClientTimeout(total=30, connect=10),  # Increased timeouts to prevent hanging
                'skip_auto_headers': ['User-Agent'],  # We set our own
                'trust_env': True,  # Use system proxy settings if available
            }
            
            if proxy:
                session_kwargs['proxy'] = proxy.get('http')
                self.logger.debug(f"Using proxy for {tcin}: {proxy.get('type', 'unknown')}")
            
            async with aiohttp.ClientSession(**session_kwargs) as session:
                # Call both APIs concurrently for maximum speed
                # Rotate geographic locations to appear like different users
                locations = [
                    {'zip': '60056', 'state': 'IL', 'lat': '42.056656', 'lng': '-87.968300'},  # Chicago area
                    {'zip': '90210', 'state': 'CA', 'lat': '34.0736', 'lng': '-118.4004'},     # Beverly Hills
                    {'zip': '10001', 'state': 'NY', 'lat': '40.7505', 'lng': '-73.9934'},      # NYC
                    {'zip': '33101', 'state': 'FL', 'lat': '25.7743', 'lng': '-80.1937'},      # Miami
                    {'zip': '75201', 'state': 'TX', 'lat': '32.7767', 'lng': '-96.7970'}       # Dallas
                ]
                
                location = random.choice(locations)
                
                base_fulfillment_params = {
                    'key': self.api_key,
                    'is_bot': 'false',
                    'tcin': tcin,
                    'store_id': store_id,
                    'zip': location['zip'],
                    'state': location['state'],
                    'latitude': location['lat'],
                    'longitude': location['lng'],
                    'scheduled_delivery_store_id': store_id,
                    'paid_membership': 'false',
                    'base_membership': 'false',
                    'card_membership': 'false',
                    'required_store_id': store_id,
                    'visitor_id': visitor_id,
                    'channel': 'WEB',
                    'page': f'/p/A-{tcin}'
                }
                
                base_product_params = {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': store_id,
                    'pricing_store_id': store_id,
                    'has_pricing_store_id': 'true',
                    'visitor_id': visitor_id
                }
                
                # Apply human-like parameter variations
                fulfillment_params = request_obfuscator.get_human_like_parameters(base_fulfillment_params)
                product_params = request_obfuscator.get_human_like_parameters(base_product_params)
                
                # Randomize API call order to avoid patterns
                tasks = [
                    ('fulfillment', session.get(self.fulfillment_url, params=fulfillment_params, headers=headers, timeout=aiohttp.ClientTimeout(total=20))),
                    ('product', session.get(self.product_url, params=product_params, headers=headers, timeout=aiohttp.ClientTimeout(total=20)))
                ]
                
                # Sometimes add a small delay between calls to seem more human
                if random.random() < 0.3:  # 30% chance
                    # Call them sequentially with small delay
                    random.shuffle(tasks)
                    responses = {}
                    for name, task in tasks:
                        responses[name] = await task
                        if name == tasks[0][0]:  # Add delay after first call
                            await asyncio.sleep(random.uniform(0.1, 0.5))
                    fulfillment_response = responses['fulfillment']
                    product_response = responses['product']
                else:
                    # Call them concurrently (faster)
                    fulfillment_task = tasks[0][1] if tasks[0][0] == 'fulfillment' else tasks[1][1]
                    product_task = tasks[0][1] if tasks[0][0] == 'product' else tasks[1][1]
                    fulfillment_response, product_response = await asyncio.gather(fulfillment_task, product_task, return_exceptions=True)
                
                # Parse fulfillment response (primary - for stock status)
                fulfillment_data = None
                if not isinstance(fulfillment_response, Exception) and fulfillment_response.status == 200:
                    fulfillment_data = await fulfillment_response.json()
                else:
                    self.logger.warning(f"Fulfillment API failed for {tcin}: {fulfillment_response}")
                
                # Parse product response (secondary - for name/price)
                product_data = None
                if not isinstance(product_response, Exception) and product_response.status == 200:
                    product_data = await product_response.json()
                else:
                    self.logger.debug(f"Product API failed for {tcin}: {product_response}")
                
                # Close responses
                if not isinstance(fulfillment_response, Exception):
                    fulfillment_response.close()
                if not isinstance(product_response, Exception):
                    product_response.close()
                
                # Must have fulfillment data to proceed
                if not fulfillment_data:
                    return {
                        'available': False,
                        'availability_text': 'Fulfillment API failed',
                        'confidence': 'error',
                        'method': 'dual_api',
                        'response_time': (time.time() - start_time) * 1000,
                        'price': 0.0,
                        'tcin': tcin,
                        'name': f'Product {tcin}'
                    }
                
                # Record response for learning systems
                response_time = time.time() - start_time
                
                # Analyze responses for threat patterns and learning
                request_metadata = {
                    'response_time': response_time,
                    'delay_used': final_delay,
                    'session_context': session_context,
                    'headers_used': list(headers.keys()),
                    'user_type': session_context.get('user_type', 'unknown') if session_context else 'unknown'
                }
                
                # Analyze fulfillment response for threats
                if fulfillment_data:
                    analysis = response_analyzer.analyze_response(fulfillment_data, request_metadata)
                    
                    # Record results with adaptive rate limiter
                    success = analysis['success_type'] == 'api_success'
                    adaptive_limiter.record_request_result(success, response_time, analysis['threat_level'])
                    
                    # Record session interaction
                    session_manager.record_product_interaction(tcin, success, response_time)
                    
                    # Log threat intelligence
                    if analysis['threat_level'] > 0.3:
                        self.logger.warning(f"High threat level detected for {tcin}: {analysis['threat_level']:.2f}")
                        for rec in analysis.get('recommendations', []):
                            self.logger.warning(f"Threat recommendation: {rec}")
                
                # Parse combined response
                result = self._parse_fulfillment_response(fulfillment_data, product_data, tcin, start_time)
                
                # Add adaptive intelligence to result
                result['adaptive_metadata'] = {
                    'session_id': session_context.get('session_id') if session_context else None,
                    'user_type': session_context.get('user_type') if session_context else None,
                    'threat_level': analysis.get('threat_level', 0) if fulfillment_data else 0,
                    'strategy_used': delay_metadata.get('strategy', 'unknown'),
                    'pattern_used': delay_metadata.get('pattern', 'unknown'),
                    'delay_applied': final_delay
                }
                
                return result
                
        except Exception as e:
            # Record failure with learning systems
            response_time = time.time() - start_time
            
            # Record failure with adaptive rate limiter
            adaptive_limiter.record_request_result(False, response_time, threat_level=0.5)
            
            # Record failed session interaction
            if session_manager.current_session:
                session_manager.record_product_interaction(tcin, False, response_time)
            
            self.logger.error(f"API calls failed for {tcin}: {e}")
            
            return {
                'available': False,
                'availability_text': f'API Error: {str(e)}',
                'confidence': 'error',
                'method': 'dual_api',
                'response_time': response_time * 1000,
                'price': 0.0,
                'tcin': tcin,
                'name': f'Product {tcin}',
                'adaptive_metadata': {
                    'session_id': session_manager.current_session.session_id if session_manager.current_session else None,
                    'error_type': type(e).__name__,
                    'adaptive_failure': True
                }
            }

    async def check_multiple_products(self, tcins: list) -> list:
        """Check multiple products with intelligent adaptive behavior"""
        results = []
        
        # Start new session for batch checking
        session_manager.start_new_session()
        
        for i, tcin in enumerate(tcins):
            # Check if we should end current session and start new one
            if session_manager.should_end_session():
                session_manager.end_current_session()
                session_manager.start_new_session()
                self.logger.info("Started new behavioral session for continued checking")
            
            result = await self.check_authenticated_stock(tcin)
            results.append(result)
            
            # Use adaptive delay between products
            if i < len(tcins) - 1:
                # Get adaptive recommendations
                recommendations = response_analyzer.get_adaptive_recommendations()
                
                if recommendations['should_slow_down']:
                    delay_range = recommendations['suggested_delay_range']
                    delay = random.uniform(*delay_range)
                    self.logger.info(f"Adaptive slow-down: {delay:.1f}s delay before next product")
                    await asyncio.sleep(delay)
                elif response_analyzer.should_trigger_evasion_mode():
                    # Enhanced evasion mode - longer delays and session rotation
                    evasion_delay = random.uniform(15, 45)
                    self.logger.warning(f"Evasion mode triggered: {evasion_delay:.1f}s delay")
                    await asyncio.sleep(evasion_delay)
                    
                    # Force session rotation in evasion mode
                    session_manager.end_current_session()
                    session_manager.start_new_session()
                else:
                    # Normal adaptive delay
                    behavioral_delay = session_manager.get_realistic_delay_for_next_request()
                    await asyncio.sleep(behavioral_delay)
        
        # End session after batch completion
        session_manager.end_current_session()
        
        # Log batch statistics
        success_count = sum(1 for r in results if r.get('available', False))
        adaptive_stats = adaptive_limiter.get_performance_stats()
        
        self.logger.info(f"Batch complete: {success_count}/{len(tcins)} in stock, strategy: {adaptive_stats['current_strategy']}")
        
        return results
    
    def get_adaptive_performance_stats(self) -> dict:
        """Get comprehensive adaptive system performance statistics"""
        return {
            'adaptive_limiter': adaptive_limiter.get_performance_stats(),
            'threat_assessment': response_analyzer.get_threat_assessment(),
            'session_stats': session_manager.get_session_statistics(),
            'recommendations': response_analyzer.get_adaptive_recommendations()
        }