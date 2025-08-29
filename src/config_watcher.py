"""
Auto-Config Reloader - Hot-swap products without restart
Watches config file for changes and reloads automatically
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Callable, Any
import time
import hashlib

class ConfigWatcher:
    """Watches config file and reloads changes automatically"""
    
    def __init__(self, config_path: str, reload_callback: Callable[[Dict], None]):
        self.config_path = Path(config_path)
        self.reload_callback = reload_callback
        self.logger = logging.getLogger(__name__)
        
        # File monitoring
        self.last_modified = 0
        self.last_hash = ""
        self.check_interval = 2  # Check every 2 seconds
        self.is_watching = False
        
        # Config validation
        self.required_keys = ['products', 'settings']
        
    async def start_watching(self):
        """Start watching config file for changes"""
        if self.is_watching:
            return
            
        self.is_watching = True
        self.logger.info(f"Started watching config file: {self.config_path}")
        
        # Initial load
        await self.check_and_reload()
        
        # Watch loop
        while self.is_watching:
            await asyncio.sleep(self.check_interval)
            await self.check_and_reload()
    
    def stop_watching(self):
        """Stop watching config file"""
        self.is_watching = False
        self.logger.info("Stopped watching config file")
    
    async def check_and_reload(self):
        """Check if config changed and reload if needed"""
        try:
            if not self.config_path.exists():
                self.logger.warning(f"Config file not found: {self.config_path}")
                return
            
            # Check modification time
            current_modified = self.config_path.stat().st_mtime
            
            if current_modified <= self.last_modified:
                return  # No change
            
            # Check file hash to avoid partial writes
            current_hash = await self.get_file_hash()
            
            if current_hash == self.last_hash:
                return  # Content hasn't actually changed
            
            # File has changed - reload
            await self.reload_config()
            
            self.last_modified = current_modified
            self.last_hash = current_hash
            
        except Exception as e:
            self.logger.error(f"Error checking config file: {e}")
    
    async def get_file_hash(self) -> str:
        """Get MD5 hash of config file"""
        try:
            with open(self.config_path, 'rb') as f:
                content = f.read()
            return hashlib.md5(content).hexdigest()
        except Exception:
            return ""
    
    async def reload_config(self):
        """Reload and validate config"""
        try:
            self.logger.info("Config file changed - reloading...")
            
            # Load new config
            with open(self.config_path, 'r') as f:
                new_config = json.load(f)
            
            # Validate config
            if not self.validate_config(new_config):
                self.logger.error("Invalid config - skipping reload")
                return
            
            # Count changes
            changes = self.analyze_changes(new_config)
            
            # Call reload callback
            self.reload_callback(new_config)
            
            # Log changes
            self.log_changes(changes)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in config file: {e}")
        except Exception as e:
            self.logger.error(f"Error reloading config: {e}")
    
    def validate_config(self, config: Dict) -> bool:
        """Validate config structure"""
        try:
            # Check required keys
            for key in self.required_keys:
                if key not in config:
                    self.logger.error(f"Missing required config key: {key}")
                    return False
            
            # Validate products
            if not isinstance(config['products'], list):
                self.logger.error("Products must be a list")
                return False
            
            for i, product in enumerate(config['products']):
                if not isinstance(product, dict):
                    self.logger.error(f"Product {i} must be a dict")
                    return False
                
                required_product_keys = ['tcin', 'name', 'max_price']
                for key in required_product_keys:
                    if key not in product:
                        self.logger.error(f"Product {i} missing required key: {key}")
                        return False
            
            # Validate settings
            if not isinstance(config['settings'], dict):
                self.logger.error("Settings must be a dict")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Config validation error: {e}")
            return False
    
    def analyze_changes(self, new_config: Dict) -> Dict:
        """Analyze what changed in the config"""
        changes = {
            'products_added': 0,
            'products_removed': 0,
            'products_modified': 0,
            'settings_changed': False,
            'proxies_changed': False
        }
        
        try:
            # This is a simplified change detection
            # In a real implementation, you'd compare old vs new config
            
            # Count enabled products
            enabled_products = len([p for p in new_config['products'] if p.get('enabled', True)])
            changes['total_enabled_products'] = enabled_products
            
            # Check for proxy changes
            if 'proxies' in new_config:
                enabled_proxies = len([p for p in new_config['proxies'] if p.get('enabled', False)])
                changes['total_enabled_proxies'] = enabled_proxies
            
        except Exception as e:
            self.logger.error(f"Error analyzing changes: {e}")
        
        return changes
    
    def log_changes(self, changes: Dict):
        """Log what changed"""
        self.logger.info("=" * 50)
        self.logger.info("CONFIG RELOADED")
        self.logger.info("=" * 50)
        
        if 'total_enabled_products' in changes:
            self.logger.info(f"✅ Enabled products: {changes['total_enabled_products']}")
        
        if 'total_enabled_proxies' in changes:
            self.logger.info(f"🔄 Enabled proxies: {changes['total_enabled_proxies']}")
        
        self.logger.info("Config reload complete - changes applied!")
        self.logger.info("=" * 50)


class HotReloadMonitor:
    """Main class that integrates hot reloading into your monitor"""
    
    def __init__(self, monitor_instance):
        self.monitor = monitor_instance
        self.config_watcher = None
        self.logger = logging.getLogger(__name__)
    
    def start_hot_reload(self):
        """Start hot reloading for the monitor"""
        self.config_watcher = ConfigWatcher(
            config_path=self.monitor.config_path,
            reload_callback=self.handle_config_reload
        )
        
        # Start watching in background
        asyncio.create_task(self.config_watcher.start_watching())
        self.logger.info("🔥 Hot reload enabled - edit config while running!")
    
    def handle_config_reload(self, new_config: Dict):
        """Handle config reload in monitor"""
        try:
            # Update monitor config
            old_products = len(self.monitor.products)
            
            self.monitor.config = new_config
            self.monitor.products = [p for p in new_config['products'] if p.get('enabled', True)]
            
            new_products = len(self.monitor.products)
            
            # Update stock checker with new proxies
            if 'proxies' in new_config:
                enabled_proxies = [p for p in new_config['proxies'] if p.get('enabled', False)]
                self.monitor.stock_checker.proxies = enabled_proxies
                
                # Reset proxy stats for new proxies
                self.monitor.stock_checker.proxy_stats = {}
                for i, proxy in enumerate(enabled_proxies):
                    self.monitor.stock_checker.proxy_stats[i] = {
                        'success': 0,
                        'failure': 0,
                        'blocked_until': 0,
                        'last_used': 0
                    }
                
                self.logger.info(f"🔄 Updated to {len(enabled_proxies)} proxies")
            
            # Log product changes
            if new_products != old_products:
                self.logger.info(f"📦 Products: {old_products} → {new_products}")
            
            # Clear in-stock tracking for removed products
            current_tcins = {p['tcin'] for p in self.monitor.products}
            self.monitor.in_stock_items = {tcin for tcin in self.monitor.in_stock_items if tcin in current_tcins}
            
            self.logger.info("✅ Hot reload complete - monitor updated!")
            
        except Exception as e:
            self.logger.error(f"Error handling config reload: {e}")
    
    def stop_hot_reload(self):
        """Stop hot reloading"""
        if self.config_watcher:
            self.config_watcher.stop_watching()
            self.logger.info("Hot reload stopped")