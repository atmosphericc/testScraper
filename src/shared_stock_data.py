#!/usr/bin/env python3
"""
Shared stock data storage for communication between monitor and dashboard
"""
import json
import time
from datetime import datetime
from pathlib import Path
import threading
import logging

class SharedStockData:
    """Thread-safe shared stock data storage"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._stock_data = {}
        self._last_update = None
        self._monitoring_active = False
        self._system_stats = {
            'total_checks': 0,
            'successful_checks': 0,
            'failed_checks': 0,
            'last_check_duration': 0,
            'average_response_time': 0
        }
    
    def _initialize_placeholder_data(self):
        """Initialize with common Target product placeholders to show immediate status"""
        placeholder_products = {
            '94724987': {
                'tcin': '94724987',
                'name': 'PS5 Digital Edition Console',
                'available': False,
                'status': 'initializing',
                'price': None,
                'url': 'https://www.target.com/p/playstation-5-digital-edition-console/-/A-94724987',
                'response_time': 0,
                'last_checked': datetime.now().isoformat(),
                'message': 'Starting monitoring...'
            },
            '94681785': {
                'tcin': '94681785', 
                'name': 'PS5 Console',
                'available': False,
                'status': 'initializing',
                'price': None,
                'url': 'https://www.target.com/p/playstation-5-console/-/A-94681785',
                'response_time': 0,
                'last_checked': datetime.now().isoformat(),
                'message': 'Starting monitoring...'
            }
        }
        
        self._stock_data = placeholder_products
        self.logger.info(f"Initialized with {len(placeholder_products)} placeholder products")
        
        # Load existing data if available
        self._load_from_cache()
        
        # Initialize with placeholder data if empty
        if not self._stock_data:
            self._initialize_placeholder_data()
    
    def update_stock_data(self, stock_results: dict, check_duration: float = 0):
        """Update stock data from monitoring system"""
        with self._lock:
            try:
                self._stock_data = stock_results.copy()
                self._last_update = datetime.now()
                self._system_stats['last_check_duration'] = check_duration
                self._system_stats['total_checks'] += 1
                
                # Count successful vs failed
                successful = len([r for r in stock_results.values() if r.get('status') == 'success'])
                failed = len(stock_results) - successful
                
                self._system_stats['successful_checks'] += successful
                self._system_stats['failed_checks'] += failed
                
                # Calculate average response time
                response_times = [r.get('response_time', 0) for r in stock_results.values() if r.get('response_time', 0) > 0]
                if response_times:
                    self._system_stats['average_response_time'] = sum(response_times) / len(response_times)
                
                self.logger.info(f"Updated stock data: {successful} successful, {failed} failed")
                
                # Save to cache
                self._save_to_cache()
                
            except Exception as e:
                self.logger.error(f"Error updating stock data: {e}")
    
    def get_stock_data(self) -> dict:
        """Get current stock data for dashboard"""
        with self._lock:
            if not self._stock_data:
                # Return placeholder data if no real data available
                return self._get_placeholder_data()
            
            # Add metadata
            data = {
                'stocks': self._stock_data.copy(),
                'last_update': self._last_update.isoformat() if self._last_update else None,
                'monitoring_active': self._monitoring_active,
                'stats': self._system_stats.copy(),
                'data_age_seconds': (datetime.now() - self._last_update).total_seconds() if self._last_update else None
            }
            
            return data
    
    def get_stock_status_for_tcin(self, tcin: str) -> dict:
        """Get stock status for a specific TCIN"""
        with self._lock:
            return self._stock_data.get(tcin, {
                'tcin': tcin,
                'available': False,
                'status': 'unknown',
                'error': 'No data available'
            })
    
    def set_monitoring_active(self, active: bool):
        """Set monitoring system status"""
        with self._lock:
            self._monitoring_active = active
            if active:
                self.logger.info("Monitoring system activated")
            else:
                self.logger.info("Monitoring system deactivated")
    
    def get_summary_stats(self) -> dict:
        """Get summary statistics for dashboard"""
        with self._lock:
            total_products = len(self._stock_data)
            in_stock_count = len([r for r in self._stock_data.values() if r.get('available', False)])
            
            return {
                'total_products': total_products,
                'in_stock_count': in_stock_count,
                'out_of_stock_count': total_products - in_stock_count,
                'last_update': self._last_update.isoformat() if self._last_update else None,
                'monitoring_active': self._monitoring_active,
                'data_age_seconds': (datetime.now() - self._last_update).total_seconds() if self._last_update else None,
                'system_stats': self._system_stats.copy()
            }
    
    def _get_placeholder_data(self) -> dict:
        """Generate placeholder data when no real data is available"""
        return {
            'stocks': {},
            'last_update': None,
            'monitoring_active': False,
            'stats': {
                'total_checks': 0,
                'successful_checks': 0,
                'failed_checks': 0,
                'last_check_duration': 0,
                'average_response_time': 0
            },
            'data_age_seconds': None,
            'message': 'Monitoring system starting up...'
        }
    
    def _save_to_cache(self):
        """Save current data to cache file"""
        try:
            cache_file = Path('cache/stock_data_cache.json')
            cache_file.parent.mkdir(exist_ok=True)
            
            cache_data = {
                'stock_data': self._stock_data,
                'last_update': self._last_update.isoformat() if self._last_update else None,
                'system_stats': self._system_stats,
                'cached_at': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"Error saving cache: {e}")
    
    def _load_from_cache(self):
        """Load data from cache file if available"""
        try:
            cache_file = Path('cache/stock_data_cache.json')
            if not cache_file.exists():
                return
            
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            self._stock_data = cache_data.get('stock_data', {})
            
            if cache_data.get('last_update'):
                self._last_update = datetime.fromisoformat(cache_data['last_update'])
            
            self._system_stats.update(cache_data.get('system_stats', {}))
            
            # Check if cache is recent (less than 10 minutes old)
            if self._last_update and (datetime.now() - self._last_update).total_seconds() < 600:
                self.logger.info(f"Loaded recent cached data from {self._last_update}")
            else:
                self.logger.info("Cache data is stale, will refresh on next monitor cycle")
                
        except Exception as e:
            self.logger.error(f"Error loading cache: {e}")
    
    def clear_data(self):
        """Clear all data (for testing/reset)"""
        with self._lock:
            self._stock_data = {}
            self._last_update = None
            self._system_stats = {
                'total_checks': 0,
                'successful_checks': 0,
                'failed_checks': 0,
                'last_check_duration': 0,
                'average_response_time': 0
            }

# Global shared data instance
shared_stock_data = SharedStockData()