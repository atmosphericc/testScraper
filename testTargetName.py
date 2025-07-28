import requests
import json

def get_stock_status():
    """Get stock status using the exact URL string"""
    # Using the exact, complete URL you provided
    url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&is_bot=false&tcin=1001304528&store_id=3229&zip=10271&state=NY&latitude=40.710&longitude=-74.010&scheduled_delivery_store_id=3229&paid_membership=false&base_membership=false&card_membership=false&required_store_id=3229&visitor_id=0196876157D70201B4E9788E945A25B2&channel=WEB&page=%2Fp%2FA-1001304528"
    
    # Include a proper User-Agent and other headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.target.com/"
    }
    
    # Make the request
    response = requests.get(url, headers=headers)
    
    # Print status code and response
    print(f"Status code: {response.status_code}")
    if response.status_code == 200:
        # Pretty print the JSON response
        pretty_json = json.dumps(response.json(), indent=2)
        print("Response:")
        print(pretty_json)
    else:
        print(f"Request failed with status: {response.status_code}")
        print("Response content:")
        print(response.text)

if __name__ == "__main__":
    print("Getting stock status...")
    get_stock_status()