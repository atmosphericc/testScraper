"""
Data service layer for managing product data fetching and processing
"""
import json
import time
from typing import List, Dict, Any
from datetime import datetime

from api_client import TargetAPIClient
from product_processor import ProductProcessor
from config import MONITORED_TCINS, API_DELAY_SECONDS, DATA_FILE


class DataService:
    """Service for managing product data operations"""
    
    def __init__(self):
        self.api_client = TargetAPIClient()
        self.processor = ProductProcessor()
        self.last_refresh_time = None
    
    def get_dashboard_data(self) -> List[Dict[str, Any]]:
        """
        PRODUCTION: Get current dashboard data for business-critical TCINs
        """
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching real-time inventory data...")
        
        products = self.fetch_product_data(MONITORED_TCINS)
        self.last_refresh_time = datetime.now()
        
        # Save data for audit trail
        self._save_data(products)
        
        # Business intelligence logging
        success_count = sum(1 for p in products if p['status'] == 'success')
        error_count = len(products) - success_count
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Inventory update complete: {success_count} success, {error_count} errors")
        
        return products
    
    def fetch_product_data(self, tcin_list: List[str]) -> List[Dict[str, Any]]:
        """
        PRODUCTION: Fetch and process multiple Target products
        """
        products = []
        
        print(f"📊 Fetching data for {len(tcin_list)} products...")
        
        for i, tcin in enumerate(tcin_list, 1):
            print(f"Processing {i}/{len(tcin_list)}: {tcin}")
            
            # Fetch raw data from Target API
            api_response = self.api_client.get_product_info(tcin)
            
            if api_response:
                # Process the business logic
                product_info = self.processor.process_product(tcin, api_response)
            else:
                # API failure fallback
                product_info = self._create_api_error_product(tcin)
            
            products.append(product_info)
            
            # Rate limiting: Be respectful to Target's API
            if i < len(tcin_list):
                time.sleep(API_DELAY_SECONDS)
        
        return products
    
    def get_refresh_info(self, products: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate refresh information for dashboard display"""
        return {
            'last_refresh': self.last_refresh_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_refresh_time else 'Never',
            'total_products': len(products),
            'in_stock_count': sum(1 for p in products if p['stock'] == 'In Stock'),
            'out_of_stock_count': sum(1 for p in products if p['stock'] == 'Out of Stock')
        }
    
    def _save_data(self, products: List[Dict[str, Any]]) -> None:
        """Save product data to file for audit trail"""
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(products, f, indent=2)
        except Exception as e:
            print(f"⚠️  Warning: Could not save data file: {e}")
    
    def _create_api_error_product(self, tcin: str) -> Dict[str, Any]:
        """Create API error product entry"""
        return {
            "tcin": tcin,
            "name": f"API Error - TCIN {tcin}",
            "price": "$0.00",
            "seller": "error",
            "stock": "API Error",
            "link": f"https://www.target.com/p/-/A-{tcin}",
            "want_to_buy": False,
            "purchase_limit": 0,
            "preorder": "",
            "status": "api_error"
        }