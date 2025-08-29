"""
Multiple Browser Profile Manager for Ultimate Stealth
Creates and rotates between different browser identities
"""
import random
import json
from pathlib import Path
from typing import Dict, List
import time

class BrowserProfile:
    """Represents a unique browser identity"""
    
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.created_time = time.time()
        self.usage_count = 0
        self.last_used = 0
        
        # Randomize browser characteristics
        self.generate_unique_profile()
    
    def generate_unique_profile(self):
        """Generate unique browser characteristics"""
        
        # Random screen resolutions (common ones)
        resolutions = [
            (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
            (2560, 1440), (1600, 900), (1280, 720), (1920, 1200)
        ]
        self.resolution = random.choice(resolutions)
        
        # Random operating systems
        os_choices = [
            ("Windows", "10.0"),
            ("Windows", "11.0"), 
            ("macOS", "10.15.7"),
            ("macOS", "12.6.0"),
            ("Linux", "X11")
        ]
        self.os, self.os_version = random.choice(os_choices)
        
        # Random browser versions (stay current)
        self.chrome_version = random.choice([128, 129, 130, 131])
        self.firefox_version = random.choice([128, 129, 130, 131])
        
        # Random timezone (US timezones)
        timezones = [
            "America/New_York", "America/Chicago", "America/Denver", 
            "America/Los_Angeles", "America/Phoenix", "America/Detroit"
        ]
        self.timezone = random.choice(timezones)
        
        # Random language preferences
        languages = [
            "en-US,en;q=0.9",
            "en-US,en;q=0.8,es;q=0.6", 
            "en-US,en;q=0.9,fr;q=0.8",
            "en-US,en;q=0.7,de;q=0.5"
        ]
        self.language = random.choice(languages)
        
        # Random device characteristics
        self.device_memory = random.choice([4, 8, 16, 32])  # GB
        self.hardware_concurrency = random.choice([4, 6, 8, 12, 16])  # CPU cores
        self.color_depth = random.choice([24, 30])
        self.pixel_ratio = random.choice([1, 1.25, 1.5, 2])
        
        # Random connection type
        self.connection_type = random.choice(['4g', 'wifi', 'ethernet'])
    
    def get_user_agent(self):
        """Generate user agent for this profile"""
        if self.os.startswith("Windows"):
            if random.choice([True, False]):  # Chrome
                return f"Mozilla/5.0 (Windows NT {self.os_version}; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome_version}.0.0.0 Safari/537.36"
            else:  # Firefox
                return f"Mozilla/5.0 (Windows NT {self.os_version}; Win64; x64; rv:{self.firefox_version}.0) Gecko/20100101 Firefox/{self.firefox_version}.0"
        
        elif self.os == "macOS":
            if random.choice([True, False]):  # Chrome
                return f"Mozilla/5.0 (Macintosh; Intel Mac OS X {self.os_version.replace('.', '_')}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome_version}.0.0.0 Safari/537.36"
            else:  # Firefox 
                return f"Mozilla/5.0 (Macintosh; Intel Mac OS X {self.os_version}) Gecko/20100101 Firefox/{self.firefox_version}.0"
        
        else:  # Linux
            return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome_version}.0.0.0 Safari/537.36"
    
    def get_viewport(self):
        """Get viewport size"""
        return {'width': self.resolution[0], 'height': self.resolution[1]}
    
    def get_extra_headers(self):
        """Get extra headers specific to this profile"""
        headers = {
            'Accept-Language': self.language,
            'Accept-Encoding': 'gzip, deflate, br',
        }
        
        # Add OS-specific headers
        if self.os.startswith("Windows"):
            headers['sec-ch-ua-platform'] = '"Windows"'
        elif self.os == "macOS":
            headers['sec-ch-ua-platform'] = '"macOS"'
        else:
            headers['sec-ch-ua-platform'] = '"Linux"'
        
        return headers
    
    def to_dict(self):
        """Export profile data"""
        return {
            'profile_id': self.profile_id,
            'resolution': self.resolution,
            'os': self.os,
            'os_version': self.os_version,
            'chrome_version': self.chrome_version,
            'firefox_version': self.firefox_version,
            'timezone': self.timezone,
            'language': self.language,
            'device_memory': self.device_memory,
            'hardware_concurrency': self.hardware_concurrency,
            'usage_count': self.usage_count,
            'last_used': self.last_used
        }


class BrowserProfileManager:
    """Manages multiple browser profiles for rotation"""
    
    def __init__(self, num_profiles=5):
        self.profiles: List[BrowserProfile] = []
        self.profiles_dir = Path("browser_profiles")
        self.profiles_dir.mkdir(exist_ok=True)
        
        # Create multiple profiles
        for i in range(num_profiles):
            profile = BrowserProfile(f"profile_{i:03d}")
            self.profiles.append(profile)
            
        # Save profiles
        self.save_profiles()
    
    def get_best_profile(self) -> BrowserProfile:
        """Get the least recently used profile"""
        # Sort by last used time (oldest first)
        available_profiles = sorted(self.profiles, key=lambda p: p.last_used)
        
        best_profile = available_profiles[0]
        best_profile.last_used = time.time()
        best_profile.usage_count += 1
        
        return best_profile
    
    def get_profile_for_curl_cffi(self, profile: BrowserProfile):
        """Get curl_cffi impersonation string for profile"""
        # Map profile characteristics to curl_cffi browser profiles
        if profile.os.startswith("Windows"):
            if profile.chrome_version >= 130:
                return "chrome120"  # Use closest available
            elif profile.chrome_version >= 120:
                return "chrome119"
            else:
                return "chrome110"
        elif profile.os == "macOS":
            return "safari15_5"
        else:
            return "chrome116"
    
    def save_profiles(self):
        """Save profiles to file"""
        profiles_data = [profile.to_dict() for profile in self.profiles]
        
        with open(self.profiles_dir / "profiles.json", 'w') as f:
            json.dump({
                'profiles': profiles_data,
                'created_time': time.time()
            }, f, indent=2)
    
    def load_profiles(self):
        """Load profiles from file"""
        profiles_file = self.profiles_dir / "profiles.json"
        
        if profiles_file.exists():
            with open(profiles_file, 'r') as f:
                data = json.load(f)
                
            # Check if profiles are too old (recreate weekly)
            if time.time() - data.get('created_time', 0) > (7 * 24 * 3600):
                return False  # Recreate profiles
                
            # Load existing profiles
            for profile_data in data['profiles']:
                profile = BrowserProfile(profile_data['profile_id'])
                # Restore saved data
                for key, value in profile_data.items():
                    if hasattr(profile, key):
                        setattr(profile, key, value)
                self.profiles.append(profile)
                
            return True
        
        return False
    
    def get_profile_stats(self):
        """Get usage statistics for all profiles"""
        stats = {}
        for profile in self.profiles:
            stats[profile.profile_id] = {
                'usage_count': profile.usage_count,
                'last_used': profile.last_used,
                'os': f"{profile.os} {profile.os_version}",
                'resolution': f"{profile.resolution[0]}x{profile.resolution[1]}",
                'timezone': profile.timezone
            }
        return stats