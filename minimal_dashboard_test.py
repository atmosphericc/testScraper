#!/usr/bin/env python3
"""
Minimal dashboard test - just the API endpoints
"""

import json
import threading
from datetime import datetime
from flask import Flask, jsonify
from pathlib import Path

app = Flask(__name__)

# Mock the global data that would be populated by the background monitor
latest_stock_data = {
    '94886127': {
        'tcin': '94886127',
        'name': 'Test Product 1',
        'status': 'OUT_OF_STOCK',
        'available': False,
        'last_checked': datetime.now().isoformat()
    },
    '94300072': {
        'tcin': '94300072', 
        'name': 'Test Product 2',
        'status': 'IN_STOCK',
        'available': True,
        'last_checked': datetime.now().isoformat()
    }
}
latest_data_lock = threading.Lock()

@app.route('/api/live-stock-status')
def api_live_stock_status():
    """Test endpoint - should return clean stock data"""
    print("API live-stock-status called")
    
    with latest_data_lock:
        stock_data = latest_stock_data.copy()
    
    print(f"Returning data: {stock_data}")
    return jsonify(stock_data)

@app.route('/api/initial-stock-check') 
def api_initial_stock_check():
    """Test endpoint - should return clean stock data"""
    print("API initial-stock-check called")
    
    with latest_data_lock:
        stock_data = latest_stock_data.copy()
    
    products_array = []
    for tcin, data in stock_data.items():
        products_array.append(data)
    
    result = {
        'success': True,
        'products': products_array,
        'timestamp': datetime.now().isoformat(),
        'method': 'test'
    }
    
    print(f"Returning result: {result}")
    return jsonify(result)

if __name__ == '__main__':
    print("Starting minimal dashboard test...")
    print("Visit: http://localhost:5002/api/live-stock-status")
    print("Visit: http://localhost:5002/api/initial-stock-check")
    app.run(host='127.0.0.1', port=5002, debug=False)