#!/usr/bin/env python3
"""
Real-time Purchase Cycle Monitor
Monitors purchase flow, detects product switches, logs state transitions
"""

import json
import time
import os
from datetime import datetime
from collections import defaultdict

class PurchaseCycleMonitor:
    def __init__(self, log_file="logs/cycle_monitor.log"):
        self.log_file = log_file
        self.purchase_states_path = "logs/purchase_states.json"
        self.last_states = {}
        self.cycle_history = []
        self.product_switches = []
        self.state_transitions = []
        self.start_time = time.time()

        # Statistics
        self.stats = {
            'cycles_monitored': 0,
            'product_switches_detected': 0,
            'state_transitions': 0,
            'concurrent_attempts_detected': 0,
            'errors_detected': 0
        }

        # Track current purchase
        self.current_purchase = None
        self.current_purchase_start = None

    def log(self, message, level="INFO"):
        """Write log message to both console and file"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_line = f"[{timestamp}] [{level}] {message}"

        print(log_line)

        with open(self.log_file, 'a') as f:
            f.write(log_line + "\n")

    def get_states(self):
        """Read current purchase states"""
        if os.path.exists(self.purchase_states_path):
            try:
                with open(self.purchase_states_path, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                self.log("Error reading purchase_states.json", "ERROR")
                return {}
        return {}

    def detect_product_switch(self, old_states, new_states):
        """Detect if product switched during active purchase"""
        # Find old attempting product
        old_attempting = None
        for tcin, state in old_states.items():
            if state.get('status') in ['attempting', 'queued']:
                old_attempting = tcin
                break

        # Find new attempting product
        new_attempting = None
        for tcin, state in new_states.items():
            if state.get('status') in ['attempting', 'queued']:
                new_attempting = tcin
                break

        # Product switch detected!
        if old_attempting and new_attempting and old_attempting != new_attempting:
            old_title = old_states[old_attempting].get('product_title', old_attempting)
            new_title = new_states[new_attempting].get('product_title', new_attempting)

            switch_info = {
                'timestamp': time.time(),
                'from_tcin': old_attempting,
                'from_title': old_title,
                'to_tcin': new_attempting,
                'to_title': new_title,
                'old_state': old_states[old_attempting].get('status'),
                'new_state': new_states[new_attempting].get('status')
            }

            self.product_switches.append(switch_info)
            self.stats['product_switches_detected'] += 1

            self.log(
                f"🚨 PRODUCT SWITCH DETECTED: {old_title} → {new_title}",
                "ALERT"
            )

            return True

        return False

    def detect_concurrent_attempts(self, states):
        """Detect multiple products in attempting/queued status"""
        attempting = []
        for tcin, state in states.items():
            if state.get('status') in ['attempting', 'queued']:
                attempting.append({
                    'tcin': tcin,
                    'title': state.get('product_title', tcin),
                    'status': state.get('status')
                })

        if len(attempting) > 1:
            self.stats['concurrent_attempts_detected'] += 1
            self.log(
                f"🚨 CONCURRENT ATTEMPTS DETECTED: {len(attempting)} products attempting simultaneously!",
                "ALERT"
            )
            for product in attempting:
                self.log(f"   - {product['title']} ({product['tcin']}): {product['status']}", "ALERT")
            return True

        return False

    def detect_state_transitions(self, old_states, new_states):
        """Log state transitions"""
        all_tcins = set(old_states.keys()) | set(new_states.keys())

        for tcin in all_tcins:
            old_status = old_states.get(tcin, {}).get('status', 'unknown')
            new_status = new_states.get(tcin, {}).get('status', 'unknown')

            if old_status != new_status:
                title = new_states.get(tcin, {}).get('product_title', tcin)

                transition_info = {
                    'timestamp': time.time(),
                    'tcin': tcin,
                    'title': title,
                    'from_status': old_status,
                    'to_status': new_status
                }

                self.state_transitions.append(transition_info)
                self.stats['state_transitions'] += 1

                # Log with appropriate level
                if new_status == 'attempting':
                    self.log(f"▶️  PURCHASE STARTED: {title} ({tcin})", "INFO")
                    self.current_purchase = tcin
                    self.current_purchase_start = time.time()

                elif new_status == 'purchased':
                    duration = ""
                    if self.current_purchase == tcin and self.current_purchase_start:
                        duration = f" ({time.time() - self.current_purchase_start:.1f}s)"
                    self.log(f"✅ PURCHASE COMPLETED: {title} ({tcin}){duration}", "SUCCESS")
                    self.current_purchase = None

                elif new_status == 'failed':
                    duration = ""
                    if self.current_purchase == tcin and self.current_purchase_start:
                        duration = f" ({time.time() - self.current_purchase_start:.1f}s)"
                    self.log(f"❌ PURCHASE FAILED: {title} ({tcin}){duration}", "ERROR")
                    self.stats['errors_detected'] += 1
                    self.current_purchase = None

                elif new_status == 'ready' and old_status in ['purchased', 'failed']:
                    self.log(f"🔄 RESET TO READY: {title} ({tcin})", "INFO")

                else:
                    self.log(f"   {title} ({tcin}): {old_status} → {new_status}", "DEBUG")

    def check_stuck_purchases(self, states):
        """Detect purchases stuck in attempting/queued"""
        now = time.time()

        for tcin, state in states.items():
            status = state.get('status')
            if status in ['attempting', 'queued']:
                started_at = state.get('started_at', now)
                duration = now - started_at

                # Alert if stuck for > 120 seconds
                if duration > 120:
                    title = state.get('product_title', tcin)
                    self.log(
                        f"⚠️  STUCK PURCHASE: {title} ({tcin}) in '{status}' for {duration:.0f}s",
                        "WARNING"
                    )

    def monitor_cycle(self):
        """Monitor one cycle"""
        states = self.get_states()

        if not self.last_states:
            # First run - just store states
            self.last_states = states
            self.log("📊 Monitoring started", "INFO")
            return

        # Detect issues
        self.detect_product_switch(self.last_states, states)
        self.detect_concurrent_attempts(states)
        self.detect_state_transitions(self.last_states, states)
        self.check_stuck_purchases(states)

        # Update
        self.last_states = states
        self.stats['cycles_monitored'] += 1

    def print_summary(self):
        """Print monitoring summary"""
        runtime = time.time() - self.start_time

        print("\n" + "="*70)
        print("MONITORING SUMMARY")
        print("="*70)
        print(f"Runtime: {runtime:.1f}s")
        print(f"Cycles Monitored: {self.stats['cycles_monitored']}")
        print(f"State Transitions: {self.stats['state_transitions']}")
        print(f"Product Switches: {self.stats['product_switches_detected']}")
        print(f"Concurrent Attempts: {self.stats['concurrent_attempts_detected']}")
        print(f"Errors: {self.stats['errors_detected']}")

        if self.product_switches:
            print("\n" + "="*70)
            print("PRODUCT SWITCHES DETECTED:")
            print("="*70)
            for switch in self.product_switches:
                ts = datetime.fromtimestamp(switch['timestamp']).strftime("%H:%M:%S")
                print(f"[{ts}] {switch['from_title']} → {switch['to_title']}")

        if self.stats['product_switches_detected'] == 0:
            print("\n✅ NO PRODUCT SWITCHES DETECTED - System working correctly!")
        else:
            print(f"\n❌ {self.stats['product_switches_detected']} PRODUCT SWITCHES DETECTED - Review logs!")

        print("="*70)

    def run(self, duration=300, interval=2):
        """Run monitor for specified duration"""
        self.log(f"🎯 Starting purchase cycle monitor (duration: {duration}s, interval: {interval}s)", "INFO")
        self.log("="*70, "INFO")

        end_time = time.time() + duration

        try:
            while time.time() < end_time:
                self.monitor_cycle()
                time.sleep(interval)

        except KeyboardInterrupt:
            self.log("\n⚠️  Monitoring interrupted by user", "WARNING")

        finally:
            self.print_summary()

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Monitor purchase cycle for issues')
    parser.add_argument('--duration', type=int, default=300,
                       help='Monitoring duration in seconds (default: 300)')
    parser.add_argument('--interval', type=int, default=2,
                       help='Check interval in seconds (default: 2)')
    parser.add_argument('--log', type=str, default='logs/cycle_monitor.log',
                       help='Log file path')

    args = parser.parse_args()

    monitor = PurchaseCycleMonitor(log_file=args.log)
    monitor.run(duration=args.duration, interval=args.interval)
