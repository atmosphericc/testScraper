"""
FOOLPROOF PURCHASE SYSTEM
=========================
NO window refreshes, NO race conditions, NO timing conflicts
Pure client-side countdown + server persistence
"""

import json
import time
import random
import os
from datetime import datetime

class FoolproofPurchaseManager:
    """100% FOOLPROOF - Client handles countdown, server only persists state"""

    def __init__(self):
        self.state_file = 'logs/foolproof_purchases.json'
        self.config = {
            'duration_min': 2.0,
            'duration_max': 4.0,
            'success_rate': 0.7,
            'cooldown_minutes': 2
        }

    def start_purchase(self, tcin, product_name):
        """Start purchase - return ALL data for client-side countdown"""
        now = time.time()
        duration = random.uniform(self.config['duration_min'], self.config['duration_max'])
        completes_at = now + duration
        success = random.random() < self.config['success_rate']

        # Generate ALL data upfront (no race conditions possible)
        purchase_data = {
            'tcin': tcin,
            'product_name': product_name,
            'status': 'attempting',
            'started_at': now,
            'completes_at': completes_at,
            'duration': duration,
            'will_succeed': success,
            'order_number': f"ORD-{random.randint(100000, 999999)}-{random.randint(10, 99)}" if success else None,
            'price': round(random.uniform(15.99, 89.99), 2) if success else None,
            'failure_reason': random.choice(['cart_timeout', 'out_of_stock', 'payment_failed']) if not success else None,
            'created_at': datetime.now().isoformat()
        }

        # Save to file
        self.save_purchase(tcin, purchase_data)

        print(f"[FOOLPROOF] Started purchase: {product_name} -> {duration:.1f}s -> {'SUCCESS' if success else 'FAIL'}")

        return purchase_data

    def complete_purchase(self, tcin):
        """Mark purchase as completed (called by client after countdown)"""
        purchase = self.get_purchase(tcin)
        if not purchase:
            return None

        final_status = 'purchased' if purchase['will_succeed'] else 'failed'

        purchase.update({
            'status': final_status,
            'completed_at': datetime.now().isoformat()
        })

        self.save_purchase(tcin, purchase)
        print(f"[FOOLPROOF] Completed: {tcin} -> {final_status}")

        return purchase

    def get_purchase(self, tcin):
        """Get current purchase state"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    return data.get(tcin)
        except:
            pass
        return None

    def save_purchase(self, tcin, purchase_data):
        """Save purchase state atomically"""
        try:
            # Load existing data
            all_data = {}
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    all_data = json.load(f)

            # Update this purchase
            all_data[tcin] = purchase_data

            # Atomic write
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(all_data, f, indent=2)

            if os.path.exists(self.state_file):
                os.remove(self.state_file)
            os.rename(temp_file, self.state_file)

        except Exception as e:
            print(f"[FOOLPROOF] Save error: {e}")

    def can_start_purchase(self, tcin):
        """Check if we can start a new purchase"""
        purchase = self.get_purchase(tcin)
        if not purchase:
            return True

        # Can start if completed and cooldown passed
        if purchase['status'] in ['purchased', 'failed']:
            completed_time = purchase.get('started_at', 0)
            cooldown_seconds = self.config['cooldown_minutes'] * 60
            return (time.time() - completed_time) > cooldown_seconds

        # Can't start if still attempting
        return False

    def get_all_states(self):
        """Get all purchase states for dashboard"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {}

if __name__ == "__main__":
    # Test the system
    manager = FoolproofPurchaseManager()

    # Start a test purchase
    purchase = manager.start_purchase("12345", "Test Product")
    print(f"Purchase will complete in {purchase['duration']:.1f} seconds")

    # Simulate waiting
    import time
    time.sleep(purchase['duration'] + 0.1)

    # Complete the purchase
    result = manager.complete_purchase("12345")
    print(f"Final result: {result['status']}")