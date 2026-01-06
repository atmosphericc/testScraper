#!/usr/bin/env python3
"""
Clear all ports - kills processes running on common Flask ports
"""

import subprocess
import sys

def clear_port(port):
    """Kill any process running on the specified port"""
    try:
        # Find process using the port
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True
        )

        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid:
                    print(f"Killing process {pid} on port {port}...")
                    subprocess.run(['kill', '-9', pid])
                    print(f"✓ Port {port} cleared")
        else:
            print(f"✓ Port {port} is already free")

    except Exception as e:
        print(f"Error clearing port {port}: {e}")

def main():
    """Clear common Flask ports used by this application"""
    ports = [5001, 5002, 5003, 5000]  # Common ports used by app.py and test instances

    print("=" * 60)
    print("CLEARING ALL PORTS")
    print("=" * 60)

    for port in ports:
        clear_port(port)

    print("=" * 60)
    print("✓ All ports cleared!")
    print("=" * 60)

if __name__ == '__main__':
    main()
