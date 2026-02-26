#!/usr/bin/env python3
"""
Clear all ports - kills processes running on common Flask ports
Works on Windows, macOS, and Linux.
"""

import subprocess
import sys
import platform

def clear_port(port):
    """Kill any process running on the specified port"""
    try:
        if platform.system() == "Windows":
            # Find PIDs using netstat
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True
            )
            pids = set()
            for line in result.stdout.splitlines():
                if f':{port} ' in line and ('LISTENING' in line or 'ESTABLISHED' in line):
                    parts = line.strip().split()
                    if parts:
                        pids.add(parts[-1])

            if pids:
                for pid in pids:
                    try:
                        subprocess.run(['taskkill', '/F', '/PID', pid],
                                       capture_output=True)
                        print(f"Killed process {pid} on port {port}")
                    except Exception as e:
                        print(f"Failed to kill PID {pid}: {e}")
                print(f"Port {port} cleared")
            else:
                print(f"Port {port} is already free")
        else:
            # macOS / Linux
            result = subprocess.run(
                ['lsof', '-ti', f':{port}'],
                capture_output=True,
                text=True
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    if pid:
                        subprocess.run(['kill', '-9', pid])
                        print(f"Killed process {pid} on port {port}")
                print(f"Port {port} cleared")
            else:
                print(f"Port {port} is already free")

    except Exception as e:
        print(f"Error clearing port {port}: {e}")

def main():
    ports = [5001, 5002, 5003, 5000]

    print("=" * 60)
    print("CLEARING ALL PORTS")
    print("=" * 60)

    for port in ports:
        clear_port(port)

    print("=" * 60)
    print("All ports cleared!")
    print("=" * 60)

if __name__ == '__main__':
    main()
