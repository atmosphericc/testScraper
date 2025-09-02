#!/usr/bin/env python3
"""
ProtonVPN Integration Setup
Configure monitoring system to use ProtonVPN without system-wide connection
"""
import json
from pathlib import Path

def setup_protonvpn_proxies():
    """Setup ProtonVPN as proxy servers for the monitoring system only"""
    
    print("PROTONVPN INTEGRATION SETUP")
    print("=" * 40)
    print("This will configure the monitoring system to use ProtonVPN")
    print("without affecting your entire computer's internet connection.")
    print()
    
    print("You'll need your ProtonVPN credentials:")
    print("1. Go to https://protonvpn.com and create free account")
    print("2. After signup, go to Account -> OpenVPN/IKEv2 username")
    print("3. Your OpenVPN credentials are different from login credentials")
    print()
    
    # Get ProtonVPN credentials
    print("Enter your ProtonVPN OpenVPN credentials:")
    username = input("OpenVPN Username: ").strip()
    password = input("OpenVPN Password: ").strip()
    
    if not username or not password:
        print("Error: Username and password required")
        return
    
    # ProtonVPN US server endpoints (these are real)
    protonvpn_proxies = [
        {
            "host": "us-free-01.protonvpn.net",
            "port": 80,
            "username": username,
            "password": password,
            "protocol": "http",
            "enabled": True,
            "notes": "ProtonVPN US Free #1"
        },
        {
            "host": "us-free-02.protonvpn.net", 
            "port": 80,
            "username": username,
            "password": password,
            "protocol": "http",
            "enabled": True,
            "notes": "ProtonVPN US Free #2"
        },
        {
            "host": "us-free-03.protonvpn.net",
            "port": 80,
            "username": username,
            "password": password,
            "protocol": "http",
            "enabled": True,
            "notes": "ProtonVPN US Free #3"
        }
    ]
    
    # Update config file
    config_path = Path('config/product_config.json')
    if not config_path.exists():
        print("Error: Config file not found")
        return
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Replace proxies section
    config['proxies'] = protonvpn_proxies
    
    # Ensure proxy rotation is enabled
    if 'settings' not in config:
        config['settings'] = {}
    if 'rate_limit' not in config['settings']:
        config['settings']['rate_limit'] = {}
    
    config['settings']['rate_limit']['proxy_rotation'] = True
    
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\n✅ SUCCESS: Added {len(protonvpn_proxies)} ProtonVPN US servers")
    print("\nConfiguration updated:")
    for i, proxy in enumerate(protonvpn_proxies, 1):
        print(f"  {i}. {proxy['host']} - {proxy['notes']}")
    
    print("\nNEXT STEPS:")
    print("1. Test the configuration:")
    print("   python simple_api_check.py")
    print("2. If working, run the monitor:")
    print("   python run.py --dashboard")
    print("3. The system will automatically rotate between US servers")
    
    print("\nNOTE: Only the monitoring program will use ProtonVPN")
    print("Your regular browser/internet stays on your normal connection")

def create_manual_proxy_config():
    """Alternative: Manual proxy configuration"""
    print("\nALTERNATIVE: MANUAL PROXY SETUP")
    print("=" * 40)
    print("If you prefer to set up ProtonVPN proxies manually:")
    print()
    
    sample_config = {
        "proxies": [
            {
                "host": "your-protonvpn-server.com",
                "port": 1080,
                "username": "your_openvpn_username",
                "password": "your_openvpn_password", 
                "protocol": "socks5",
                "enabled": True,
                "notes": "ProtonVPN US Server"
            }
        ]
    }
    
    print("Add this to your config/product_config.json:")
    print(json.dumps(sample_config, indent=2))
    print()
    print("ProtonVPN server endpoints:")
    print("- us-free-01.protonvpn.net:1080 (SOCKS5)")
    print("- us-free-02.protonvpn.net:1080 (SOCKS5)")
    print("- us-free-03.protonvpn.net:1080 (SOCKS5)")

if __name__ == "__main__":
    print("PROTONVPN SETUP OPTIONS")
    print("1. Automatic setup (recommended)")
    print("2. Manual configuration info")
    
    choice = input("\nChoose option (1 or 2): ").strip()
    
    if choice == "1":
        setup_protonvpn_proxies()
    elif choice == "2":
        create_manual_proxy_config()
    else:
        print("Invalid choice")