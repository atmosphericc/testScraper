#!/usr/bin/env python3
"""
Test file to get raw RedSky API response for TCIN 89542109
Returns the response exactly as-is without modifications
"""

import requests
import json

def get_raw_redsky_response(tcin="89542109"):
    """Get raw RedSky API response for specific TCIN"""
    
    # Target RedSky API endpoint
    url = f"https://redsky.target.com/redsky_aggregations/v1/redsky/case_study_v1?key=ff457966e64d5e877fdbad070f276d18ecec4a01&tcin={tcin}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"Raw Response:")
        print(json.dumps(response.json(), indent=2))
        
        return response.json()
        
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON decode failed: {e}")
        print(f"Raw text response: {response.text}")
        return None

if __name__ == '__main__':
    # Get raw response for TCIN 89542109
    get_raw_redsky_response("89542109")