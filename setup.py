#!/usr/bin/env python3
"""
Setup and management script for Target Monitor
"""

import sys
import asyncio
from pathlib import Path
import json

def setup_directories():
    """Create necessary directories"""
    dirs = ['config', 'logs', 'sessions', 'src', 'dashboard', 'dashboard/templates']
    for d in dirs:
        Path(d).mkdir(exist_ok=True)
    print("✓ Created directories")

def create_default_config():
    """Create default configuration if it doesn't exist"""
    config_path = Path('config/product_config.json')
    if not config_path.exists():
        default_config = {
            "products": [],
            "settings": {
                "mode": "test",
                "rate_limit": {
                    "requests_per_second": 2,
                    "batch_size": 3,
                    "batch_delay_seconds": 1.5
                },
                "purchase": {
                    "stop_after_success": False,
                    "cooldown_after_attempt_minutes": 5,
                    "max_attempts_per_session": 999
                },
                "session": {
                    "storage_path": "sessions/target_storage.json",
                    "validity_check_interval_minutes": 30
                },
                "logging": {
                    "level": "INFO",
                    "log_dir": "logs"
                }
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(default_config, f, indent=2)
        print("✓ Created default config")
    else:
        print("✓ Config already exists")

async def setup_login():
    """Run login setup"""
    # Add src to path
    sys.path.insert(0, str(Path('src')))
    
    from session_manager import SessionManager
    manager = SessionManager()
    success = await manager.create_session()
    if success:
        print("\n✅ Setup complete! You can now run the monitor.")
    else:
        print("\n❌ Setup failed. Please try again.")

def main():
    """Main setup entry point"""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python setup.py init     - Initialize directories and config")
        print("  python setup.py login    - Setup Target login session")
        print("  python setup.py test     - Test the setup")
        return
    
    command = sys.argv[1]
    
    if command == 'init':
        setup_directories()
        create_default_config()
        print("\n✅ Initialization complete!")
        print("Next step: python setup.py login")
        
    elif command == 'login':
        asyncio.run(setup_login())
        
    elif command == 'test':
        print("Testing configuration...")
        config_path = Path('config/product_config.json')
        session_path = Path('sessions/target_storage.json')
        
        if config_path.exists():
            print("✓ Config file exists")
        else:
            print("❌ Config file missing")
        
        if session_path.exists():
            print("✓ Session file exists")
        else:
            print("❌ Session file missing - run 'python setup.py login'")
        
    else:
        print(f"Unknown command: {command}")

if __name__ == "__main__":
    main()