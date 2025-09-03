#!/usr/bin/env python3
"""
Main entry point for Target Monitor
Updated to use Ultra-Fast system with fallback to legacy system
"""

import asyncio
import sys
import os
from pathlib import Path
import argparse

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def print_banner():
    """Print startup banner"""
    print("""
    ================================================
    TARGET MONITOR - ULTRA-FAST STOCK CHECK
    Sub-3s for 50+ SKUs - Zero Missed Opportunities  
    ================================================
    """)

def check_setup():
    """Verify setup is complete"""
    session_file = Path('sessions/target_storage.json')
    
    if not session_file.exists():
        print("ERROR: No session found! Run: python setup.py login")
        return False
    
    return True

async def run_ultra_fast_system(test_mode: bool, use_dashboard: bool):
    """Run the enhanced ultra-fast monitoring system with adaptive evasion"""
    try:
        # Import the enhanced system components
        from src.authenticated_stock_checker import AuthenticatedStockChecker
        import json
        import time
        
        # Load product configuration
        config_path = Path('config/product_config.json')
        if not config_path.exists():
            print("ERROR: Product configuration not found")
            return False
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        products = config.get('products', [])
        enabled_products = [p for p in products if p.get('enabled', True)]
        
        if not enabled_products:
            print("ERROR: No enabled products to monitor")
            return False
        
        print(f"Enhanced Ultra-Fast Monitor: {len(enabled_products)} products with adaptive evasion")
        
        # Start dashboard if requested
        if use_dashboard:
            import threading
            # Use simple stealth dashboard for now
            import subprocess
            
            dashboard_cmd = [sys.executable, 'dashboard/app.py']
            dashboard_process = subprocess.Popen(dashboard_cmd, cwd=os.getcwd())
            print("Ultra-Fast System with Real Data Integration")
            print("Enhanced Dashboard available at: http://localhost:5000")
        
        # Initialize enhanced checker and shared data
        checker = AuthenticatedStockChecker()
        
        # Import shared data for dashboard integration
        from src.shared_stock_data import shared_stock_data
        shared_stock_data.set_monitoring_active(True)
        
        # Monitoring loop with enhanced adaptive evasion
        print("\n[ENHANCED] Ultra-Fast Monitor Starting...")
        print("Features: ML-like adaptation, behavioral sessions, threat detection")
        
        while True:
            start_time = time.time()
            tcins = [p['tcin'] for p in enabled_products]
            
            try:
                # Use enhanced adaptive checking
                results = await checker.check_multiple_products(tcins)
                
                # Update shared data for dashboard
                check_time = time.time() - start_time
                results_dict = {r['tcin']: r for r in results}
                shared_stock_data.update_stock_data(results_dict, check_time)
                
                # Display results
                in_stock_count = sum(1 for r in results if r.get('available', False))
                
                print(f"\n[{time.strftime('%H:%M:%S')}] Check complete: {in_stock_count}/{len(results)} in stock ({check_time:.1f}s)")
                
                # Show adaptive intelligence
                stats = checker.get_adaptive_performance_stats()
                strategy = stats['adaptive_limiter']['current_strategy']
                threat = stats['threat_assessment']['level']
                success_rate = stats['adaptive_limiter']['overall_success_rate']
                
                print(f"Adaptive: Strategy={strategy}, Threat={threat}, Success={success_rate:.1%}")
                
                # Show in-stock items
                for result in results:
                    if result.get('available', False):
                        name = result.get('name', 'Unknown')[:50]
                        price = result.get('formatted_price', 'N/A')
                        print(f"  [IN STOCK] {name}: {price}")
                
                # Wait before next check (use config frequency)
                await asyncio.sleep(30)  # 30 second intervals
                
            except KeyboardInterrupt:
                print("\n[STOPPED] Monitor stopped by user")
                break
            except Exception as e:
                print(f"[ERROR] Check error: {e}")
                await asyncio.sleep(60)  # Wait longer on errors
        
    except ImportError as e:
        print(f"ERROR: Enhanced ultra-fast system import failed: {e}")
        return False
    except Exception as e:
        print(f"ERROR: Enhanced ultra-fast system error: {e}")
        return False
    
    return True

async def run_legacy_system(test_mode: bool, use_dashboard: bool = False):
    """Run the legacy monitoring system"""
    try:
        from src.monitor import TargetMonitor
        
        # Set environment variable for legacy system
        os.environ['CHECKOUT_MODE'] = 'TEST' if test_mode else 'PRODUCTION'
        
        # Start dashboard if requested
        if use_dashboard:
            import threading
            import subprocess
            import sys
            
            # Start dashboard in separate process
            dashboard_cmd = [sys.executable, 'dashboard/app.py']
            dashboard_process = subprocess.Popen(dashboard_cmd, cwd=os.getcwd())
            print("Legacy Dashboard starting at: http://localhost:5000")
        
        monitor = TargetMonitor()
        await monitor.run()
        
    except Exception as e:
        print(f"ERROR: Legacy system error: {e}")
        return False
    
    return True

async def main():
    """Main entry point with system selection"""
    parser = argparse.ArgumentParser(description='Target Stock Monitor')
    parser.add_argument('mode', nargs='?', choices=['test', 'production'], default='test',
                       help='Run in test or production mode (default: test)')
    parser.add_argument('--legacy', action='store_true', 
                       help='Force use of legacy system instead of ultra-fast')
    parser.add_argument('--dashboard', action='store_true',
                       help='Start web dashboard (ultra-fast system only)')
    
    args = parser.parse_args()
    
    print_banner()
    
    # Check setup
    if not check_setup():
        sys.exit(1)
    
    test_mode = args.mode == 'test'
    
    # Mode confirmation
    if not test_mode:
        print("WARNING: PRODUCTION MODE - Will attempt real purchases!")
        response = input("Are you sure? Type 'CONFIRM' to continue: ")
        if response != 'CONFIRM':
            print("Aborted.")
            sys.exit(0)
    else:
        print("[TEST MODE] - Will not complete purchases")
    
    # System selection
    if args.legacy:
        print("INFO: Using Legacy Monitor System")
        success = await run_legacy_system(test_mode, args.dashboard)
    else:
        print("INFO: Using Ultra-Fast Monitor System")
        success = await run_ultra_fast_system(test_mode, args.dashboard)
        
        # Fallback to legacy if ultra-fast fails
        if not success:
            print("INFO: Falling back to Legacy Monitor System")
            success = await run_legacy_system(test_mode, args.dashboard)
    
    if not success:
        print("ERROR: All systems failed to start")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())