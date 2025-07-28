from flask import Flask, render_template
import requests
import time
import random
import html
import json
import os
from datetime import datetime

app = Flask(__name__)

# Product cache file
PRODUCT_CACHE_FILE = "product_cache.json"
# Cache expiration time (in seconds) - 24 hours
CACHE_EXPIRATION = 86400

def load_product_cache():
    """Load the cached product data from file"""
    if os.path.exists(PRODUCT_CACHE_FILE):
        try:
            with open(PRODUCT_CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading cache: {e}")
            return {}
    return {}

def save_product_cache(cache):
    """Save the product cache to file"""
    try:
        with open(PRODUCT_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"Error saving cache: {e}")

def is_cache_valid(cache_entry):
    """Check if a cache entry is still valid based on timestamp"""
    if not cache_entry or "timestamp" not in cache_entry:
        return False
    
    cached_time = datetime.fromisoformat(cache_entry["timestamp"])
    current_time = datetime.now()
    age = (current_time - cached_time).total_seconds()
    
    return age < CACHE_EXPIRATION

def generate_random_visitor_id():
    """Generate a random visitor ID to avoid tracking"""
    hex_chars = "0123456789ABCDEF"
    return ''.join(random.choice(hex_chars) for _ in range(32))

def generate_random_ip():
    """Generate a random IP address for X-Forwarded-For header"""
    return f"{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"

def generate_random_cookies():
    """Generate some plausible-looking cookies"""
    session_id = ''.join(random.choice("0123456789abcdef") for _ in range(32))
    visitor_id = generate_random_visitor_id()
    return f"visitorId={visitor_id}; sessionId={session_id}; TealeafAkaSid=hpSCOmJD"

def get_product_data(tcin, force_refresh=False):
    """
    Get product data for a TCIN.
    First checks cache, then falls back to API if needed.
    """
    # First check cache
    cache = load_product_cache()
    if tcin in cache and not force_refresh and is_cache_valid(cache[tcin]):
        print(f"Using cached data for TCIN {tcin}")
        return cache[tcin]["data"]
    
    # If not in cache or forced refresh, get from API
    print(f"Getting fresh data for TCIN {tcin}")
    product_data = fetch_product_data(tcin)
    
    # Store successful result in cache
    if product_data and "name" in product_data and product_data["name"] != f"Product {tcin}":
        if tcin not in cache:
            cache[tcin] = {}
        
        cache[tcin]["data"] = product_data
        cache[tcin]["timestamp"] = datetime.now().isoformat()
        save_product_cache(cache)
    
    return product_data

def fetch_product_data(tcin):
    """Fetch product data from Target API with improved request randomization"""
    # URLs to try
    urls = [
        # Original client_v1 endpoint with random visitor ID
        f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}&is_bot=false&store_id=3229&pricing_store_id=3229&has_pricing_store_id=true&has_financing_options=true&include_obsolete=true&visitor_id={generate_random_visitor_id()}&skip_personalized=true&skip_variation_hierarchy=true&channel=WEB&page=%2Fp%2FA-{tcin}",
        
        # Alternative combined endpoint
        f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_combined?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}&excludes=taxonomy,bulk_ship,rating_and_review_reviews,rating_and_review_statistics,question_answer_statistics",
        
        # Try with minimal parameters
        f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}"
    ]
    
    # User agents to rotate through
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.82 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.53 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36 Edg/113.0.1774.57"
    ]
    
    # Try each URL
    for i, url in enumerate(urls):
        print(f"Trying URL {i+1} for TCIN {tcin}...")
        
        # Headers with more randomization to avoid pattern detection
        headers = {
            "User-Agent": random.choice(user_agents),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": random.choice(["document", "empty"]),
            "Sec-Fetch-Mode": random.choice(["navigate", "cors"]),
            "Sec-Fetch-Site": random.choice(["none", "same-origin"]),
            "Referer": "https://www.target.com/",
            "Cookie": generate_random_cookies(),
            "X-Forwarded-For": generate_random_ip()
        }
        
        try:
            # Random delay between 1-2 seconds
            delay = 1 + random.random()
            time.sleep(delay)
            
            # Send the request
            response = requests.get(url, headers=headers, timeout=15)
            
            # Check if successful
            if response.status_code == 200:
                json_data = response.json()
                
                # Extract product data
                try:
                    product_data = json_data['data']['product']
                    
                    # Get name and decode HTML entities
                    name = product_data['item']['product_description']['title']
                    name = html.unescape(name)
                    
                    price = product_data['price']['formatted_current_price']
                    is_marketplace = product_data['item']['fulfillment'].get('is_marketplace', False)
                    seller = "Target" if not is_marketplace else "Third-Party Seller"
                    
                    # Try to get stock status from main product response
                    stock_status = extract_stock_status(product_data, tcin)
                    
                    return {
                        "tcin": tcin,
                        "name": name,
                        "price": price,
                        "seller": seller,
                        "stock": stock_status,
                        "link": f"https://www.target.com/p/-/A-{tcin}"
                    }
                except KeyError as e:
                    print(f"KeyError for TCIN {tcin}: {e}")
                    # Continue to next URL if data extraction fails
            else:
                print(f"Failed with status code: {response.status_code}")
        
        except Exception as e:
            print(f"Error for TCIN {tcin}: {str(e)}")
            
        # Add delay before trying next URL
        wait_time = 2 + random.random()
        time.sleep(wait_time)
    
    # If all URLs fail, return placeholder data
    return {
        "tcin": tcin,
        "name": f"Product {tcin}",
        "price": "Price checking...",
        "seller": "Checking...",
        "stock": "Unknown",
        "link": f"https://www.target.com/p/-/A-{tcin}"
    }

def extract_stock_status(product_data, tcin):
    """Extract stock status directly from product data"""
    try:
        # Method 1: Check for online_purchasable flag
        if 'item' in product_data and 'online_purchasable' in product_data['item']:
            if product_data['item']['online_purchasable']:
                return "IN_STOCK"
            else:
                return "OUT_OF_STOCK"
        
        # Method 2: Check availability in fulfillment
        if 'fulfillment' in product_data:
            # Check for direct availability status
            if 'availability_status' in product_data['fulfillment']:
                return product_data['fulfillment']['availability_status']
            
            # Check availability in shipping_options
            shipping_options = product_data['fulfillment'].get('shipping_options', {})
            if shipping_options and 'availability_status' in shipping_options:
                return shipping_options['availability_status']
                
            # Check is_available flag
            if 'is_available' in product_data['fulfillment']:
                return "IN_STOCK" if product_data['fulfillment']['is_available'] else "OUT_OF_STOCK"
        
        # Method 3: Check purchasability flags
        if 'item' in product_data:
            if product_data['item'].get('purchasable', False):
                return "IN_STOCK"
            if product_data['item'].get('is_out_of_stock', False):
                return "OUT_OF_STOCK"
        
        # Default to a simple check for pickup eligibility
        if 'fulfillment' in product_data and 'is_eligible_for_pickup' in product_data['fulfillment']:
            return "IN_STOCK" if product_data['fulfillment']['is_eligible_for_pickup'] else "OUT_OF_STOCK"
        
    except Exception as e:
        print(f"Error extracting stock status for TCIN {tcin}: {e}")
    
    # If all methods fail or an error occurs, use a generic status
    return "IN_STOCK"  # Default to showing as in stock 

def get_tcins_from_file():
    """Get TCINs from skus.csv file if it exists, otherwise use defaults"""
    default_tcins = ["1001304528", "94300069", "93859727", "94694203"]
    
    if os.path.exists("skus.csv"):
        try:
            tcins = []
            with open("skus.csv", "r") as file:
                for line in file:
                    tcin = line.strip()
                    if tcin:  # Only add non-empty lines
                        tcins.append(tcin)
            return tcins if tcins else default_tcins
        except Exception as e:
            print(f"Error reading skus.csv: {e}")
            return default_tcins
    else:
        return default_tcins

@app.route("/")
def dashboard():
    """Display the dashboard with product information"""
    # Get TCINs from file
    tcins = get_tcins_from_file()
    
    # Check if we should force refresh (every 10th request)
    force_refresh = random.random() < 0.1  # 10% chance of refreshing all data
    
    products = []
    for tcin in tcins:
        print(f"Processing TCIN: {tcin}")
        
        # Get product data, using cache when possible
        product = get_product_data(tcin, force_refresh)
        products.append(product)
        
        # Add spacing between processing different TCINs
        time.sleep(1)
    
    return render_template("dashboard.html", products=products)

@app.route("/refresh")
def refresh_all():
    """Force refresh all product data"""
    # Get TCINs from file
    tcins = get_tcins_from_file()
    
    for tcin in tcins:
        print(f"Force refreshing TCIN: {tcin}")
        get_product_data(tcin, force_refresh=True)
        time.sleep(2)  # Add delay between refreshes
    
    return "All products refreshed. <a href='/'>Return to dashboard</a>"

if __name__ == "__main__":
    app.run(debug=True)