#!/usr/bin/env python3
"""
Monitor Statistics - View logs and stats
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

class MonitorStats:
    def __init__(self):
        self.log_dir = Path("../logs")
        self.config_path = Path("../product_config.json")
    
    def read_recent_logs(self, hours=24):
        """Read recent log entries"""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        stats = {
            'checks': 0,
            'in_stock_events': [],
            'purchases': [],
            'errors': []
        }
        
        # Read monitor log
        monitor_log = self.log_dir / "monitor.log"
        if monitor_log.exists():
            with open(monitor_log, 'r') as f:
                for line in f:
                    if 'IN STOCK:' in line:
                        stats['in_stock_events'].append(line.strip())
                    elif 'checks,' in line:
                        # Extract check count
                        try:
                            checks = int(line.split()[3])
                            stats['checks'] = checks
                        except:
                            pass
                    elif 'ERROR' in line or 'Error' in line:
                        stats['errors'].append(line.strip())
        
        # Read purchase log
        purchase_log = self.log_dir / "purchases.log"
        if purchase_log.exists():
            with open(purchase_log, 'r') as f:
                for line in f:
                    stats['purchases'].append(line.strip())
        
        return stats
    
    def get_config_info(self):
        """Get current configuration"""
        with open(self.config_path, 'r') as f:
            config = json.load(f)
        
        enabled = [p for p in config['products'] if p.get('enabled', True)]
        return {
            'total': len(config['products']),
            'enabled': len(enabled),
            'products': enabled
        }
    
    def print_dashboard(self):
        """Print stats dashboard"""
        print("\n" + "="*60)
        print("MONITOR STATISTICS DASHBOARD")
        print("="*60)
        
        # Config info
        config = self.get_config_info()
        print(f"\nCONFIGURATION:")
        print(f"  Products: {config['enabled']} enabled / {config['total']} total")
        
        # Recent stats
        stats = self.read_recent_logs(24)
        
        print(f"\nLAST 24 HOURS:")
        print(f"  Total Checks: {stats['checks']}")
        print(f"  In-Stock Events: {len(stats['in_stock_events'])}")
        print(f"  Purchase Attempts: {len(stats['purchases'])}")
        print(f"  Errors: {len(stats['errors'])}")
        
        # Recent in-stock events
        if stats['in_stock_events']:
            print(f"\nRECENT IN-STOCK EVENTS:")
            for event in stats['in_stock_events'][-5:]:
                # Extract time and TCIN
                try:
                    parts = event.split(' - ')
                    time_str = parts[0]
                    message = parts[1] if len(parts) > 1 else ""
                    print(f"  {time_str} - {message}")
                except:
                    print(f"  {event[:80]}")
        
        # Recent purchases
        if stats['purchases']:
            print(f"\nRECENT PURCHASE ATTEMPTS:")
            success = sum(1 for p in stats['purchases'] if 'SUCCESS' in p)
            failed = sum(1 for p in stats['purchases'] if 'FAILED' in p)
            print(f"  Success: {success}")
            print(f"  Failed: {failed}")
            
            # Show last few
            for purchase in stats['purchases'][-3:]:
                try:
                    parts = purchase.split(' - ')
                    time_str = parts[0]
                    message = ' - '.join(parts[2:]) if len(parts) > 2 else parts[1]
                    print(f"    {time_str} - {message[:60]}")
                except:
                    print(f"    {purchase[:80]}")
        
        print("\n" + "="*60)
    
    def tail_logs(self):
        """Follow logs in real-time"""
        import time
        import subprocess
        import sys
        
        print("Following monitor logs (Ctrl+C to stop)...")
        print("-"*60)
        
        try:
            # Use tail command if available (Linux/Mac)
            subprocess.run(['tail', '-f', str(self.log_dir / 'monitor.log')])
        except:
            # Fallback for Windows
            log_file = self.log_dir / 'monitor.log'
            with open(log_file, 'r') as f:
                # Go to end of file
                f.seek(0, 2)
                
                while True:
                    line = f.readline()
                    if line:
                        print(line.strip())
                    else:
                        time.sleep(0.1)

def main():
    """Main entry point"""
    import sys
    
    stats = MonitorStats()
    
    if len(sys.argv) > 1 and sys.argv[1] == 'tail':
        # Follow logs in real-time
        stats.tail_logs()
    else:
        # Show dashboard
        stats.print_dashboard()
        print("\nTip: Run 'python monitor_stats.py tail' to follow logs in real-time")

if __name__ == "__main__":
    main()