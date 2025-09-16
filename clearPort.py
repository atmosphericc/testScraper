#!/usr/bin/env python3
"""
Clear Port 5001 - Kill all processes using port 5001
"""

import subprocess
import sys
import time

def clear_port_5001():
    """Kill all processes using port 5001"""
    try:
        print("🔍 Finding processes using port 5001...")

        # Find processes using port 5001
        result = subprocess.run(['lsof', '-ti:5001'],
                              capture_output=True, text=True)

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            print(f"📋 Found {len(pids)} processes using port 5001: {', '.join(pids)}")

            # Kill each process
            for pid in pids:
                if pid:
                    try:
                        subprocess.run(['kill', '-9', pid], check=True)
                        print(f"✅ Killed process {pid}")
                    except subprocess.CalledProcessError:
                        print(f"⚠️ Could not kill process {pid} (may already be dead)")

            # Wait a moment for processes to die
            time.sleep(1)

            # Verify port is clear
            verify_result = subprocess.run(['lsof', '-ti:5001'],
                                        capture_output=True, text=True)
            if verify_result.returncode == 0 and verify_result.stdout.strip():
                print("⚠️ Some processes may still be using port 5001")
            else:
                print("✅ Port 5001 is now clear!")

        else:
            print("✅ No processes found using port 5001")

    except FileNotFoundError:
        print("❌ lsof command not found - trying alternative method...")

        # Alternative: kill all python dashboard processes
        try:
            subprocess.run(['pkill', '-9', '-f', 'dashboard'], check=False)
            subprocess.run(['pkill', '-9', '-f', 'main_dashboard'], check=False)
            print("✅ Killed dashboard processes using pkill")
        except FileNotFoundError:
            print("❌ pkill also not available")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("🚀 Clearing port 5001...")
    clear_port_5001()
    print("🎉 Done!")