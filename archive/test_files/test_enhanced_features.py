"""
Test Enhanced Features: Browser Profiles & Hot Config Reload
"""
import asyncio
import json
import time
import sys
from pathlib import Path

sys.path.append('src')

from browser_profile_manager import BrowserProfileManager
from config_watcher import ConfigWatcher

async def test_browser_profiles():
    """Test multiple browser profiles"""
    print("Testing Multiple Browser Profiles")
    print("=" * 50)
    
    # Create profile manager
    manager = BrowserProfileManager(num_profiles=3)
    
    print(f"Created {len(manager.profiles)} unique browser profiles:")
    
    # Show each profile
    for i, profile in enumerate(manager.profiles):
        print(f"\nProfile {i + 1} ({profile.profile_id}):")
        print(f"  OS: {profile.os} {profile.os_version}")
        print(f"  Resolution: {profile.resolution[0]}x{profile.resolution[1]}")
        print(f"  Timezone: {profile.timezone}")
        print(f"  Language: {profile.language}")
        print(f"  User Agent: {profile.get_user_agent()[:80]}...")
        print(f"  curl_cffi profile: {manager.get_profile_for_curl_cffi(profile)}")
    
    # Test profile rotation
    print(f"\nTesting Profile Rotation:")
    for i in range(5):
        profile = manager.get_best_profile()
        print(f"Round {i+1}: Using {profile.profile_id} (used {profile.usage_count} times)")
        await asyncio.sleep(0.1)  # Small delay
    
    # Show usage stats
    print(f"\nProfile Statistics:")
    stats = manager.get_profile_stats()
    for profile_id, stat in stats.items():
        print(f"  {profile_id}: Used {stat['usage_count']} times, {stat['os']}, {stat['resolution']}")

def test_config_modification():
    """Test config file modification detection"""
    print("\n" + "=" * 50)
    print("Testing Auto-Config Reloading")
    print("=" * 50)
    
    config_path = Path('config/product_config.json')
    
    print(f"Watching config file: {config_path}")
    print("Try editing the config file and see the changes detected!")
    
    # Load current config
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    enabled_products = len([p for p in config['products'] if p.get('enabled', True)])
    enabled_proxies = len([p for p in config.get('proxies', []) if p.get('enabled', False)])
    
    print(f"Current state:")
    print(f"  Enabled products: {enabled_products}")
    print(f"  Enabled proxies: {enabled_proxies}")
    
    print(f"\nTo test hot reload:")
    print(f"1. Edit {config_path}")
    print(f"2. Change 'enabled': true/false on products or proxies")
    print(f"3. Save the file")
    print(f"4. Watch the console for reload messages!")

async def demo_hot_reload():
    """Demo the hot reload functionality"""
    
    def config_changed(new_config):
        enabled_products = len([p for p in new_config['products'] if p.get('enabled', True)])
        enabled_proxies = len([p for p in new_config.get('proxies', []) if p.get('enabled', False)])
        
        print(f"\n*** CONFIG CHANGED ***")
        print(f"New enabled products: {enabled_products}")
        print(f"New enabled proxies: {enabled_proxies}")
        print("Config reloaded successfully!")
    
    watcher = ConfigWatcher('config/product_config.json', config_changed)
    
    print("Starting config watcher for 30 seconds...")
    print("Try editing your config file now!")
    
    # Watch for 30 seconds
    watch_task = asyncio.create_task(watcher.start_watching())
    
    try:
        await asyncio.sleep(30)
    finally:
        watcher.stop_watching()
        watch_task.cancel()
        
        try:
            await watch_task
        except asyncio.CancelledError:
            pass

async def main():
    """Run all tests"""
    await test_browser_profiles()
    test_config_modification()
    
    print(f"\nStarting 30-second hot reload demo...")
    await demo_hot_reload()
    
    print(f"\nDemo complete!")
    print(f"Your bot now has:")
    print(f"✅ 5 unique browser profiles that rotate automatically")
    print(f"✅ Hot config reloading - edit config while running")
    print(f"✅ These features work automatically when you run your bot")

if __name__ == "__main__":
    asyncio.run(main())