#!/usr/bin/env python3
"""
Fetch and analyze the complete GitHub Gist content about Target's Redsky API
"""

import requests

def fetch_and_analyze_gist():
    """Fetch the complete gist content and analyze it"""
    
    raw_url = "https://gist.githubusercontent.com/LumaDevelopment/f2a34a202fed6ab5a7f3a31282834943/raw"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(raw_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            content = response.text
            print("="*80)
            print("COMPLETE GITHUB GIST CONTENT - TARGET REDSKY API RESEARCH")
            print("="*80)
            print(content)
            print("="*80)
            print(f"Total content length: {len(content)} characters")
            print("="*80)
            
            return content
        else:
            print(f"Failed to fetch content: HTTP {response.status_code}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None

if __name__ == '__main__':
    content = fetch_and_analyze_gist()
    if content:
        print("\n✅ Successfully fetched complete gist content")
    else:
        print("\n❌ Failed to fetch gist content")