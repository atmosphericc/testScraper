"""
Install script for advanced stealth dependencies

Run this to install all stealth libraries:
python install_stealth_deps.py
"""
import subprocess
import sys

def install_package(package):
    """Install a package using pip"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"✅ Successfully installed {package}")
        return True
    except subprocess.CalledProcessError:
        print(f"❌ Failed to install {package}")
        return False

def main():
    print("🚀 Installing advanced stealth dependencies...")
    
    packages = [
        "curl-cffi",           # Perfect TLS fingerprinting
        "playwright-stealth",   # Advanced browser stealth
        "fake-useragent",      # Dynamic user agent generation
        "undetected-chromedriver",  # Alternative to Playwright
        "httpx[http2]",        # HTTP/2 support
        "tls-client",          # Custom TLS implementation
    ]
    
    success_count = 0
    
    for package in packages:
        print(f"\n📦 Installing {package}...")
        if install_package(package):
            success_count += 1
    
    print(f"\n🎉 Installation complete! {success_count}/{len(packages)} packages installed successfully.")
    
    if success_count == len(packages):
        print("\n🔥 All stealth libraries installed! Your bot is now ultra-stealthy.")
    else:
        print("\n⚠️ Some packages failed to install. Your bot will use fallback methods.")

if __name__ == "__main__":
    main()