#!/usr/bin/env python3
"""
Test file to check if we can access and read GitHub Gist content
Does not modify existing code - just tests access
"""

import requests

def test_gist_access():
    """Test access to the GitHub Gist"""
    
    gist_url = "https://gist.github.com/LumaDevelopment/f2a34a202fed6ab5a7f3a31282834943"
    raw_url = "https://gist.githubusercontent.com/LumaDevelopment/f2a34a202fed6ab5a7f3a31282834943/raw"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    print("Testing GitHub Gist access...")
    print(f"URL: {gist_url}")
    print("="*60)
    
    try:
        # Try the main gist page
        print("1. Testing main gist page...")
        response = requests.get(gist_url, headers=headers, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Content Length: {len(response.text)} characters")
        print(f"Content Type: {response.headers.get('content-type', 'Unknown')}")
        
        if response.status_code == 200:
            print("✅ Successfully accessed gist page")
            # Show first 500 characters
            print("\nFirst 500 characters of content:")
            print("-" * 40)
            print(response.text[:500])
            print("-" * 40)
        else:
            print(f"❌ Failed to access gist page: HTTP {response.status_code}")
            
        print("\n" + "="*60)
        
        # Try the raw content URL
        print("2. Testing raw content URL...")
        raw_response = requests.get(raw_url, headers=headers, timeout=10)
        print(f"Status Code: {raw_response.status_code}")
        print(f"Content Length: {len(raw_response.text)} characters")
        print(f"Content Type: {raw_response.headers.get('content-type', 'Unknown')}")
        
        if raw_response.status_code == 200:
            print("✅ Successfully accessed raw content")
            # Show first 500 characters of raw content
            print("\nFirst 500 characters of raw content:")
            print("-" * 40)
            print(raw_response.text[:500])
            print("-" * 40)
        else:
            print(f"❌ Failed to access raw content: HTTP {raw_response.status_code}")
            
        return response.status_code == 200 or raw_response.status_code == 200
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False

if __name__ == '__main__':
    success = test_gist_access()
    print(f"\nOverall result: {'✅ SUCCESS' if success else '❌ FAILED'}")