#!/usr/bin/env python3
"""
Test file to generate API response for specific TCIN 89542109
Does not modify existing code - just shows what the response would be
"""

import json
import random
from datetime import datetime

def generate_tcin_response(tcin="89542109"):
    """Generate mock API response for specific TCIN using actual config data"""
    
    # Actual product mapping from your config
    product_data = {
        "94724987": {"name": "Pokémon Trading Card Game: Blooming Waters Premium Collection", "price": 69.99},
        "94681785": {"name": "Pokémon Trading Card Game: Scarlet & Violet—White Flare Booster Bundle", "price": 31.99},
        "94681770": {"name": "Pokémon Trading Card Game: Scarlet & Violet—Black Bolt Booster Bundle", "price": 31.99},
        "94336414": {"name": "Pokémon Trading Card Game: Scarlet & Violet—Prismatic Evolutions Surprise Box", "price": 24.99},
        "89542109": {"name": "Pokémon Trading Card Game: Quaquaval ex Deluxe Battle Deck", "price": 14.99}
    }
    
    # Get the actual product data for this TCIN
    if tcin in product_data:
        name = product_data[tcin]["name"]
        price = product_data[tcin]["price"]
    else:
        name = "Unknown Product"
        price = 0.00
    
    # Randomly determine if in stock (20% chance for realistic scarcity)
    available = random.random() < 0.2
    
    # Simulate realistic response times
    response_time = random.randint(120, 350)
    
    response = {
        tcin: {
            'available': available,
            'status': 'IN_STOCK' if available else 'OUT_OF_STOCK',
            'name': name,
            'tcin': tcin,
            'last_checked': datetime.now().isoformat(),
            'quantity': random.randint(0, 5) if available else 0,
            'availability_status': 'IN_STOCK' if available else 'UNAVAILABLE',
            'sold_out': not available,
            'price': price,
            'response_time': response_time,
            'confidence': 'high',
            'method': 'ultra_stealth_api'
        }
    }
    
    return response

if __name__ == '__main__':
    # Generate response for TCIN 89542109
    response = generate_tcin_response("89542109")
    print(json.dumps(response, indent=2))