from flask import Flask, render_template, jsonify
import json
from pathlib import Path
from datetime import datetime
import logging

app = Flask(__name__)
app.secret_key = 'target-monitor-2025'

# Disable Flask logging in production
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

class DashboardData:
    @staticmethod
    def get_config():
        """Load current configuration"""
        config_path = Path('../config/product_config.json')
        if config_path.exists():
            with open(config_path, 'r') as f:
                return json.load(f)
        return {"products": [], "settings": {}}
    
    @staticmethod
    def get_recent_logs(lines=50):
        """Get recent log entries"""
        log_path = Path('../logs/monitor.log')
        if not log_path.exists():
            return []
        
        try:
            with open(log_path, 'r') as f:
                return f.readlines()[-lines:]
        except:
            return []
    
    @staticmethod
    def get_purchase_logs(lines=20):
        """Get recent purchase attempts"""
        log_path = Path('../logs/purchases.log')
        if not log_path.exists():
            return []
        
        try:
            with open(log_path, 'r') as f:
                return f.readlines()[-lines:]
        except:
            return []
    
    @staticmethod
    def parse_status():
        """Parse current status from logs"""
        logs = DashboardData.get_recent_logs(100)
        status = {
            'monitoring': False,
            'total_checks': 0,
            'in_stock_count': 0,
            'last_update': None,
            'recent_stock': [],
            'recent_purchases': []
        }
        
        for line in reversed(logs):
            if 'Status - Cycle:' in line:
                # Parse status line
                try:
                    parts = line.split('|')
                    for part in parts:
                        if 'Checks:' in part:
                            status['total_checks'] = int(part.split(':')[1].strip())
                        elif 'In Stock:' in part:
                            status['in_stock_count'] = int(part.split(':')[1].strip())
                    status['monitoring'] = True
                    status['last_update'] = line.split(' - ')[0]
                except:
                    pass
                break
            
            if 'IN STOCK:' in line:
                status['recent_stock'].append(line.strip())
                if len(status['recent_stock']) >= 5:
                    break
            
            if 'PURCHASE SUCCESS' in line or 'PURCHASE FAILED' in line:
                status['recent_purchases'].append(line.strip())
                if len(status['recent_purchases']) >= 10:
                    break
        
        return status

@app.route('/')
def dashboard():
    """Main dashboard view"""
    config = DashboardData.get_config()
    status = DashboardData.parse_status()
    
    return render_template('dashboard.html', 
                         config=config, 
                         status=status,
                         timestamp=datetime.now())

@app.route('/api/status')
def api_status():
    """API endpoint for live status"""
    return jsonify(DashboardData.parse_status())

@app.route('/api/logs')
def api_logs():
    """API endpoint for recent logs"""
    return jsonify({
        'monitor': DashboardData.get_recent_logs(50),
        'purchases': DashboardData.get_purchase_logs(20)
    })

if __name__ == '__main__':
    print("="*60)
    print("TARGET MONITOR DASHBOARD")
    print("Access at: http://localhost:5000")
    print("="*60)
    app.run(debug=False, host='0.0.0.0', port=5000)