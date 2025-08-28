#!/usr/bin/env python3
"""
Main entry point for Target Monitor
"""

import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from monitor import TargetMonitor

def print_banner():
    """Print startup banner"""
    print("""
    ╔══════════════════════════════════════════════╗
    ║         TARGET MONITOR - AUTO CHECKOUT       ║
    ║              Production System                ║
    ╚══════════════════════════════════════════════╝
    """)

def check_setup():
    """Verify setup is complete"""
    session_file = Path('sessions/target_storage.json')
    config_file = Path('config/product_config.json')
    
    if not session_file.exists():
        print("❌ No session found! Run: python setup.py login")
        return False
    
    if not config_file.exists():
        print("❌ No config found! Run: python setup.py init")
        return False
    
    return True

async def main():
    """Main entry point"""
    print_banner()
    
    # Check setup
    if not check_setup():
        sys.exit(1)
    
    # Set environment variables based on arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == 'production':
            os.environ['CHECKOUT_MODE'] = 'PRODUCTION'
            print("⚠️  PRODUCTION MODE - Will attempt real checkouts!")
            response = input("Are you sure? Type 'YES' to continue: ")
            if response != 'YES':
                print("Aborted.")
                sys.exit(0)
        elif sys.argv[1] == 'test':
            os.environ['CHECKOUT_MODE'] = 'TEST'
            print("✅ TEST MODE - Will not complete checkout")
    else:
        os.environ['CHECKOUT_MODE'] = 'TEST'
        print("✅ TEST MODE (default) - Will not complete checkout")
    
    # Start monitor
    monitor = TargetMonitor()
    
    try:
        await monitor.run()
    except KeyboardInterrupt:
        print("\n\nShutdown requested...")
        print("Monitor stopped.")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())