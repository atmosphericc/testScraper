"""
Target API client for product data retrieval
"""
import requests
from typing import Dict, Any, Optional
from config import (
    TARGET_API_BASE_URL, TARGET_API_KEY, STORE_ID, PRICING_STORE_ID,
    REQUEST_TIMEOUT, get_target_headers
)


class TargetAPIClient:
    """Client for interacting with Target's Redsky API"""
    
    def __init__(self):
        self.base_url = TARGET_API_BASE_URL
        self.api_key = TARGET_API_KEY
        self.store_id = STORE_ID
        self.pricing_store_id = PRICING_STORE_ID
        self.timeout = REQUEST_TIMEOUT
    
    def get_product_info(self, tcin: str) -> Optional[Dict[str, Any]]:
        """
        Fetch product information from Target's Redsky API
        
        Args:
            tcin (str): Target product ID
            
        Returns:
            Dict containing product data or None if error
        """
        url = self._build_api_url(tcin)
        headers = get_target_headers(tcin)
        
        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"❌ API Error for TCIN {tcin}: {e}")
            return None
    
    def _build_api_url(self, tcin: str) -> str:
        """Build the complete API URL for a given TCIN"""
        return (
            f"{self.base_url}?"
            f"key={self.api_key}&"
            f"tcin={tcin}&"
            f"is_bot=false&"
            f"store_id={self.store_id}&"
            f"pricing_store_id={self.pricing_store_id}&"
            f"has_pricing_store_id=true&"
            f"has_financing_options=true&"
            f"include_obsolete=true&"
            f"visitor_id=0198538661860201B9F1AD74ED8A1AE4&"
            f"skip_personalized=true&"
            f"skip_variation_hierarchy=true&"
            f"channel=WEB&"
            f"page=%2Fp%2FA-{tcin}"
        )