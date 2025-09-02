#!/usr/bin/env python3
"""
ADVANCED EVASION SETUP SCRIPT
Installs dependencies and configures the advanced anti-detection system

Run: python setup_advanced_evasion.py
"""

import subprocess
import sys
import os
import json
from pathlib import Path

def run_command(command, description):
    """Run command with error handling"""
    print(f"\n[SETUP] {description}")
    print(f"Running: {command}")
    
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[OK] Success: {description}")
            if result.stdout.strip():
                print(f"Output: {result.stdout.strip()}")
        else:
            print(f"[ERROR] Failed: {description}")
            if result.stderr.strip():
                print(f"Error: {result.stderr.strip()}")
        return result.returncode == 0
    except Exception as e:
        print(f"[ERROR] Error running command: {e}")
        return False

def check_python_version():
    """Check Python version compatibility"""
    print("[PYTHON] Checking Python version...")
    version = sys.version_info
    
    if version.major >= 3 and version.minor >= 8:
        print(f"[OK] Python {version.major}.{version.minor}.{version.micro} - Compatible")
        return True
    else:
        print(f"[ERROR] Python {version.major}.{version.minor}.{version.micro} - Requires Python 3.8+")
        return False

def install_dependencies():
    """Install required dependencies"""
    
    dependencies = [
        ("asyncio", "Built-in async support", None),
        ("aiohttp", "Async HTTP client", "aiohttp"),
        ("curl_cffi", "Advanced TLS fingerprint spoofing", "curl-cffi"),
        ("requests", "HTTP library fallback", "requests"),
        ("tls_client", "Custom TLS client (optional)", "tls-client"),
        ("undetected_chromedriver", "Selenium anti-detection (optional)", "undetected-chromedriver"),
    ]
    
    print("\n[DEPS] Installing Dependencies")
    print("=" * 40)
    
    success_count = 0
    
    for name, description, pip_name in dependencies:
        if pip_name is None:
            print(f"[OK] {name}: {description} (built-in)")
            success_count += 1
            continue
            
        try:
            # Check if already installed
            __import__(name.replace('-', '_'))
            print(f"[OK] {name}: Already installed")
            success_count += 1
        except ImportError:
            # Try to install
            success = run_command(f"pip install {pip_name}", f"Installing {name}")
            if success:
                success_count += 1
    
    print(f"\n[SUMMARY] Installation Summary: {success_count}/{len(dependencies)} dependencies available")
    return success_count >= 4  # Need at least core dependencies

def create_config_files():
    """Create example configuration files"""
    
    print("\n[CONFIG] Creating Configuration Files")
    print("=" * 40)
    
    # Create directories
    os.makedirs("src", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    os.makedirs("config", exist_ok=True)
    
    configs_created = 0
    
    # Basic configuration
    basic_config = {
        "evasion_settings": {
            "default_method": "ultra_stealth",
            "use_proxies": False,
            "warmup_sessions": True,
            "adaptive_timing": True,
            "browser_fingerprint_rotation": True,
            "anti_bot_params": True
        },
        
        "timing_settings": {
            "base_delay": 3.0,
            "delay_variation": 0.5,
            "burst_detection_prevention": True,
            "session_fatigue_simulation": True
        },
        
        "monitoring_settings": {
            "log_level": "INFO",
            "save_responses": False,
            "track_success_rates": True,
            "proxy_health_monitoring": True
        }
    }
    
    try:
        with open("config/advanced_evasion_config.json", "w") as f:
            json.dump(basic_config, f, indent=2)
        print("[OK] Created: config/advanced_evasion_config.json")
        configs_created += 1
    except Exception as e:
        print(f"[ERROR] Failed to create config file: {e}")
    
    # Create example files if they don't exist
    example_files = [
        ("proxies_example.json", "Example proxy configuration"),
        ("tcins_example.txt", "Example TCIN list"),
    ]
    
    for filename, description in example_files:
        if os.path.exists(filename):
            print(f"[OK] {description}: Already exists ({filename})")
            configs_created += 1
        else:
            print(f"[WARNING] {description}: Create manually ({filename})")
    
    return configs_created >= 1

def run_system_test():
    """Run basic system test"""
    
    print("\n[TEST] Running System Test")
    print("=" * 40)
    
    try:
        # Test import of core modules
        sys.path.append("src")
        
        print("Testing core imports...")
        
        try:
            from ultra_stealth_bypass import UltraStealthBypass
            print("[OK] Ultra Stealth Bypass module")
        except Exception as e:
            print(f"[ERROR] Ultra Stealth Bypass: {e}")
            return False
        
        try:
            from advanced_evasion_engine import AdvancedEvasionEngine  
            print("[OK] Advanced Evasion Engine module")
        except Exception as e:
            print(f"[ERROR] Advanced Evasion Engine: {e}")
            return False
        
        try:
            from residential_proxy_network import ResidentialProxyNetwork
            print("[OK] Residential Proxy Network module")
        except Exception as e:
            print(f"[ERROR] Residential Proxy Network: {e}")
            return False
        
        # Test basic initialization
        print("\nTesting module initialization...")
        
        try:
            bypass = UltraStealthBypass()
            print("[OK] Ultra Stealth Bypass initialized")
            
            evasion = AdvancedEvasionEngine()  
            print("[OK] Advanced Evasion Engine initialized")
            
            network = ResidentialProxyNetwork()
            print("[OK] Residential Proxy Network initialized")
            
        except Exception as e:
            print(f"[ERROR] Initialization failed: {e}")
            return False
        
        print("\n[OK] System test passed!")
        return True
        
    except Exception as e:
        print(f"[ERROR] System test failed: {e}")
        return False

def print_usage_instructions():
    """Print usage instructions"""
    
    print("\n[COMPLETE] SETUP COMPLETE - Usage Instructions")
    print("=" * 50)
    
    print("\n[USAGE] Quick Start:")
    print("1. Test single TCIN:")
    print("   python advanced_stock_monitor.py --tcin 89542109")
    
    print("\n2. Test with proxy configuration:")
    print("   python advanced_stock_monitor.py --tcin 89542109 --proxy-config proxies.json")
    
    print("\n3. Batch monitoring:")
    print("   python advanced_stock_monitor.py --batch tcins.txt")
    
    print("\n4. System information:")
    print("   python advanced_stock_monitor.py --info")
    
    print("\n[CONFIG] Configuration:")
    print("• Edit proxies_example.json with your proxy credentials")
    print("• Add TCINs to tcins_example.txt for batch monitoring") 
    print("• Adjust config/advanced_evasion_config.json for fine-tuning")
    
    print("\n[FEATURES] Anti-Detection Features:")
    print("[+] JA3/JA4 TLS fingerprint spoofing")
    print("[+] Browser fingerprint randomization") 
    print("[+] Request pattern obfuscation")
    print("[+] Anti-bot parameter injection (isBot=false)")
    print("[+] Intelligent adaptive timing")
    print("[+] Session warming and rotation")
    print("[+] Residential proxy rotation")
    print("[+] Real-time blocking detection")
    
    print("\n[NOTES] Important Notes:")
    print("• Use residential proxies for best results")
    print("• Rotate proxy credentials regularly")
    print("• Monitor success rates and adjust timing")
    print("• Test with small batches first")
    
    print("\n[ADVANCED] Advanced Usage:")
    print("• Run test_ultimate_bypass.py for comprehensive testing")
    print("• Check logs/ directory for detailed operation logs")
    print("• Monitor proxy health and rotation statistics")

def main():
    """Main setup process"""
    
    print("ADVANCED EVASION SYSTEM SETUP")
    print("=" * 50)
    print("Setting up military-grade anti-detection capabilities...")
    
    # Check Python version
    if not check_python_version():
        print("\n[ERROR] Setup failed: Incompatible Python version")
        sys.exit(1)
    
    # Install dependencies
    if not install_dependencies():
        print("\n[WARNING] Warning: Some dependencies failed to install")
        print("The system may work with reduced capabilities")
    
    # Create config files
    create_config_files()
    
    # Run system test
    if run_system_test():
        print("\n[SUCCESS] SETUP SUCCESSFUL!")
        print_usage_instructions()
    else:
        print("\n[ERROR] Setup completed with errors")
        print("Some features may not work correctly")
        print("Check error messages above and install missing dependencies")

if __name__ == "__main__":
    main()