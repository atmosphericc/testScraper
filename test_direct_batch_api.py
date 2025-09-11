#!/usr/bin/env python3
import json
from main_dashboard import stealth_checker

if __name__ == "__main__":
    print("\n=== Direct Batch API Test ===")
    batch_data = stealth_checker.make_ultimate_stealth_batch_call()
    print(f"Returned {len(batch_data)} products")
    for tcin, data in batch_data.items():
        print(f"TCIN: {tcin}")
        print(f"  Name: {data.get('name')}")
        print(f"  Status: {data.get('status')}")
        print(f"  Available: {data.get('available')}")
        print(f"  Availability Status: {data.get('availability_status')}")
        print(f"  Is Marketplace: {data.get('is_marketplace')}")
        print(f"  Is Target Direct: {data.get('is_target_direct')}")
        print(f"  Preorder: {data.get('is_preorder')}")
        print()
