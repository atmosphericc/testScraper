#!/usr/bin/env python3
"""
Test all imports to ensure compatibility is working
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def test_imports():
    """Test all critical imports"""
    print("🔧 TESTING IMPORT COMPATIBILITY")
    print("=" * 50)
    
    # Test 1: Basic dependencies
    print("\n1. Testing basic dependencies...")
    try:
        import flask
        print("  ✅ flask")
    except ImportError as e:
        print(f"  ❌ flask: {e}")
    
    try:
        import flask_cors
        print("  ✅ flask_cors")
    except ImportError as e:
        print(f"  ❌ flask_cors: {e}")
        
    try:
        import aiohttp
        print("  ✅ aiohttp")
    except ImportError as e:
        print(f"  ❌ aiohttp: {e}")
        
    try:
        from curl_cffi import requests
        print("  ✅ curl_cffi")
    except ImportError as e:
        print(f"  ❌ curl_cffi: {e}")
    
    # Test 2: Core components
    print("\n2. Testing core components...")
    
    try:
        from src.adaptive_rate_limiter import adaptive_limiter
        print("  ✅ adaptive_rate_limiter")
    except ImportError as e:
        print(f"  ❌ adaptive_rate_limiter: {e}")
    
    try:
        from src.behavioral_session_manager import session_manager
        print("  ✅ behavioral_session_manager")
    except ImportError as e:
        print(f"  ❌ behavioral_session_manager: {e}")
        
    try:
        from src.response_analyzer import response_analyzer
        print("  ✅ response_analyzer")
    except ImportError as e:
        print(f"  ❌ response_analyzer: {e}")
    
    try:
        from src.request_pattern_obfuscator import request_obfuscator
        print("  ✅ request_pattern_obfuscator")
    except ImportError as e:
        print(f"  ❌ request_pattern_obfuscator: {e}")
        
    # Test 3: Advanced components
    print("\n3. Testing advanced components...")
    
    try:
        from src.authenticated_stock_checker import AuthenticatedStockChecker
        print("  ✅ authenticated_stock_checker")
    except ImportError as e:
        print(f"  ❌ authenticated_stock_checker: {e}")
    
    try:
        from src.ultra_fast_stock_checker import UltraFastStockChecker
        print("  ✅ ultra_fast_stock_checker")
    except ImportError as e:
        print(f"  ❌ ultra_fast_stock_checker: {e}")
        
    try:
        from src.dashboard_optimized_checker import DashboardOptimizedChecker
        print("  ✅ dashboard_optimized_checker")
    except ImportError as e:
        print(f"  ❌ dashboard_optimized_checker: {e}")
    
    # Test 4: New enhanced components
    print("\n4. Testing new enhanced components...")
    
    try:
        from src.intelligent_rate_limiter import intelligent_limiter
        print("  ✅ intelligent_rate_limiter")
    except ImportError as e:
        print(f"  ❌ intelligent_rate_limiter: {e}")
        
    try:
        from src.request_fingerprint_rotator import fingerprint_rotator
        print("  ✅ request_fingerprint_rotator")
    except ImportError as e:
        print(f"  ❌ request_fingerprint_rotator: {e}")
        
    try:
        from src.enhanced_stealth_requester import enhanced_stealth_requester
        print("  ✅ enhanced_stealth_requester")
    except ImportError as e:
        print(f"  ❌ enhanced_stealth_requester: {e}")
    
    # Test 5: Dashboard components
    print("\n5. Testing dashboard components...")
    
    try:
        from dashboard.ultra_fast_dashboard import UltraFastDashboard
        print("  ✅ ultra_fast_dashboard")
    except ImportError as e:
        print(f"  ❌ ultra_fast_dashboard: {e}")
    
    print("\n" + "=" * 50)
    print("✅ Import compatibility test completed!")
    print("\nIf any components show ❌, those need to be fixed.")

if __name__ == "__main__":
    test_imports()