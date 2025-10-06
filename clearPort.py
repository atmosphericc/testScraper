#!/usr/bin/env python3
"""
Clear Port 5001 - Cross-platform port cleaner
Works on Windows, Linux, and macOS
"""

import subprocess
import sys
import time
import platform
import re

def clear_port_5001():
    """Kill all processes using port 5001 - cross-platform"""
    system = platform.system().lower()
    print(f"[SYSTEM] Detected OS: {system}")

    if system == "windows":
        clear_port_windows()
    else:
        clear_port_unix()

def clear_port_windows():
    """Windows-specific port clearing"""
    try:
        print("[WINDOWS] Finding processes using port 5001...")

        # Use netstat to find processes using port 5001
        result = subprocess.run(['netstat', '-ano'],
                              capture_output=True, text=True)

        if result.returncode != 0:
            print("[ERROR] Failed to run netstat")
            fallback_kill_python_processes()
            return

        pids_to_kill = set()
        lines = result.stdout.split('\n')

        for line in lines:
            if ':5001' in line and 'LISTENING' in line:
                # Extract PID from the last column
                parts = line.split()
                if parts:
                    try:
                        pid = parts[-1]
                        if pid.isdigit():
                            pids_to_kill.add(pid)
                            print(f"[FOUND] Process {pid} using port 5001")
                    except (IndexError, ValueError):
                        continue

        if not pids_to_kill:
            print("[SUCCESS] No processes found using port 5001")
            return

        print(f"[ACTION] Found {len(pids_to_kill)} processes to kill: {', '.join(pids_to_kill)}")

        # Kill each process
        killed_count = 0
        for pid in pids_to_kill:
            try:
                subprocess.run(['taskkill', '/F', '/PID', pid],
                             check=True, capture_output=True)
                print(f"[KILLED] Successfully killed process {pid}")
                killed_count += 1
            except subprocess.CalledProcessError as e:
                print(f"[WARNING] Could not kill process {pid}: {e}")

        # Wait for processes to terminate
        time.sleep(1)

        # Verify port is clear
        verify_result = subprocess.run(['netstat', '-ano'],
                                     capture_output=True, text=True)
        if verify_result.returncode == 0:
            still_using = False
            for line in verify_result.stdout.split('\n'):
                if ':5001' in line and 'LISTENING' in line:
                    still_using = True
                    break

            if still_using:
                print("[WARNING] Some processes may still be using port 5001")
                fallback_kill_python_processes()
            else:
                print(f"[SUCCESS] Port 5001 is now clear! Killed {killed_count} processes.")

    except Exception as e:
        print(f"[ERROR] Windows port clearing failed: {e}")
        fallback_kill_python_processes()

def clear_port_unix():
    """Unix/Linux/macOS-specific port clearing"""
    try:
        print("[UNIX] Finding processes using port 5001...")

        # Try lsof first
        try:
            result = subprocess.run(['lsof', '-ti:5001'],
                                  capture_output=True, text=True)

            if result.returncode == 0 and result.stdout.strip():
                pids = [pid.strip() for pid in result.stdout.strip().split('\n') if pid.strip()]
                print(f"[FOUND] Found {len(pids)} processes using port 5001: {', '.join(pids)}")

                # Kill each process
                killed_count = 0
                for pid in pids:
                    try:
                        subprocess.run(['kill', '-9', pid], check=True)
                        print(f"[KILLED] Successfully killed process {pid}")
                        killed_count += 1
                    except subprocess.CalledProcessError:
                        print(f"[WARNING] Could not kill process {pid} (may already be dead)")

                # Wait and verify
                time.sleep(1)
                verify_result = subprocess.run(['lsof', '-ti:5001'],
                                            capture_output=True, text=True)
                if verify_result.returncode == 0 and verify_result.stdout.strip():
                    print("[WARNING] Some processes may still be using port 5001")
                    fallback_kill_python_processes()
                else:
                    print(f"[SUCCESS] Port 5001 is now clear! Killed {killed_count} processes.")
                return
            else:
                print("[SUCCESS] No processes found using port 5001")
                return

        except FileNotFoundError:
            print("[WARNING] lsof command not found, trying netstat...")

        # Fallback to netstat on Unix
        try:
            # Try different netstat syntaxes for different Unix variants
            for cmd in [['netstat', '-tlnp'], ['netstat', '-an'], ['ss', '-tlnp']]:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        pids_to_kill = set()
                        for line in result.stdout.split('\n'):
                            if ':5001' in line:
                                # Extract PID from various netstat formats
                                match = re.search(r'(\d+)/\w+', line)
                                if match:
                                    pid = match.group(1)
                                    pids_to_kill.add(pid)
                                    print(f"[FOUND] Process {pid} using port 5001")

                        if pids_to_kill:
                            print(f"[ACTION] Found {len(pids_to_kill)} processes to kill")
                            killed_count = 0
                            for pid in pids_to_kill:
                                try:
                                    subprocess.run(['kill', '-9', pid], check=True)
                                    print(f"[KILLED] Successfully killed process {pid}")
                                    killed_count += 1
                                except subprocess.CalledProcessError:
                                    print(f"[WARNING] Could not kill process {pid}")

                            time.sleep(1)
                            print(f"[SUCCESS] Port clearing completed! Killed {killed_count} processes.")
                            return
                        else:
                            print("[SUCCESS] No processes found using port 5001")
                            return

                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue

            print("[WARNING] No suitable network tools found, using fallback method")
            fallback_kill_python_processes()

        except Exception as e:
            print(f"[ERROR] Unix port clearing failed: {e}")
            fallback_kill_python_processes()

    except Exception as e:
        print(f"[ERROR] Unix port clearing failed: {e}")
        fallback_kill_python_processes()

def fallback_kill_python_processes():
    """Fallback: kill Python processes that might be running dashboards"""
    print("[FALLBACK] Using fallback method to kill Python dashboard processes...")

    system = platform.system().lower()
    killed_count = 0

    try:
        if system == "windows":
            # Windows: Find and kill Python processes running dashboard-related scripts
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
                                  capture_output=True, text=True)

            if result.returncode == 0:
                lines = result.stdout.split('\n')[1:]  # Skip header
                for line in lines:
                    if 'python.exe' in line and line.strip():
                        # Extract PID from CSV format
                        parts = [p.strip('"') for p in line.split('","')]
                        if len(parts) >= 2:
                            try:
                                pid = parts[1]  # PID is usually the second column
                                # Kill the process
                                subprocess.run(['taskkill', '/F', '/PID', pid],
                                             check=True, capture_output=True)
                                print(f"[KILLED] Python process {pid}")
                                killed_count += 1
                            except (subprocess.CalledProcessError, ValueError):
                                continue
        else:
            # Unix: Kill Python processes with dashboard-related names
            for pattern in ['test_app.py', 'app.py', 'main_dashboard', 'dashboard']:
                try:
                    result = subprocess.run(['pkill', '-f', pattern],
                                          capture_output=True, text=True)
                    if result.returncode == 0:
                        print(f"[KILLED] Processes matching '{pattern}'")
                        killed_count += 1
                except FileNotFoundError:
                    continue
                except subprocess.CalledProcessError:
                    continue

        if killed_count > 0:
            print(f"[SUCCESS] Fallback method completed! Killed {killed_count} processes.")
        else:
            print("[INFO] No Python dashboard processes found to kill")

    except Exception as e:
        print(f"[ERROR] Fallback method failed: {e}")

def verify_port_clear():
    """Verify that port 5001 is actually clear"""
    print("[VERIFY] Checking if port 5001 is clear...")

    system = platform.system().lower()

    try:
        if system == "windows":
            result = subprocess.run(['netstat', '-ano'],
                                  capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if ':5001' in line and 'LISTENING' in line:
                        print("[WARNING] Port 5001 is still in use!")
                        return False
        else:
            # Try lsof first, then netstat
            try:
                result = subprocess.run(['lsof', '-i:5001'],
                                      capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    print("[WARNING] Port 5001 is still in use!")
                    return False
            except FileNotFoundError:
                # Fallback to netstat
                try:
                    result = subprocess.run(['netstat', '-an'],
                                          capture_output=True, text=True)
                    if result.returncode == 0:
                        for line in result.stdout.split('\n'):
                            if ':5001' in line and 'LISTEN' in line:
                                print("[WARNING] Port 5001 is still in use!")
                                return False
                except FileNotFoundError:
                    pass

        print("[SUCCESS] Port 5001 is clear!")
        return True

    except Exception as e:
        print(f"[ERROR] Could not verify port status: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("[START] Clearing port 5001...")
    print("=" * 50)

    clear_port_5001()

    print("\n" + "=" * 50)
    print("[VERIFY] Final verification...")
    print("=" * 50)

    verify_port_clear()

    print("\n" + "=" * 50)
    print("[DONE] Port clearing completed!")
    print("=" * 50)