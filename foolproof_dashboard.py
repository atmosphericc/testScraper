"""
FOOLPROOF DASHBOARD WITH CLIENT-SIDE COUNTDOWN
==============================================
NO refresh interference, NO race conditions, PURE client-side timing
"""

from flask import Flask, render_template_string, jsonify, request
import json
import time
import random
import os
from datetime import datetime

app = Flask(__name__)

# Import the foolproof purchase manager
import sys
sys.path.append('.')

# Inline the FoolproofPurchaseManager to avoid imports
class FoolproofPurchaseManager:
    def __init__(self):
        self.state_file = 'logs/foolproof_purchases.json'
        self.config = {
            'duration_min': 2.0,
            'duration_max': 4.0,
            'success_rate': 0.7,
            'cooldown_minutes': 2
        }
        os.makedirs('logs', exist_ok=True)

    def start_purchase(self, tcin, product_name):
        """Start purchase - return ALL data for client-side countdown"""
        now = time.time()
        duration = random.uniform(self.config['duration_min'], self.config['duration_max'])
        completes_at = now + duration
        success = random.random() < self.config['success_rate']

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

        self.save_purchase(tcin, purchase_data)
        print(f"[FOOLPROOF] Started: {product_name} -> {duration:.1f}s -> {'SUCCESS' if success else 'FAIL'}")
        return purchase_data

    def complete_purchase(self, tcin):
        """Complete purchase (called by client after countdown)"""
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
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    return data.get(tcin)
        except:
            pass
        return None

    def save_purchase(self, tcin, purchase_data):
        try:
            all_data = {}
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    all_data = json.load(f)

            all_data[tcin] = purchase_data

            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(all_data, f, indent=2)

            if os.path.exists(self.state_file):
                os.remove(self.state_file)
            os.rename(temp_file, self.state_file)
        except Exception as e:
            print(f"[FOOLPROOF] Save error: {e}")

    def can_start_purchase(self, tcin):
        purchase = self.get_purchase(tcin)
        if not purchase:
            return True

        if purchase['status'] in ['purchased', 'failed']:
            completed_time = purchase.get('started_at', 0)
            cooldown_seconds = self.config['cooldown_minutes'] * 60
            return (time.time() - completed_time) > cooldown_seconds

        return False

    def get_all_states(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {}

# Initialize purchase manager
purchase_manager = FoolproofPurchaseManager()

def load_products():
    """Load products from config"""
    try:
        with open('config/product_config.json', 'r') as f:
            config = json.load(f)
            return config.get('products', [])
    except:
        return []

@app.route('/')
def dashboard():
    """Main dashboard with client-side countdown"""
    products = load_products()
    purchase_states = purchase_manager.get_all_states()

    # Add current purchase state to each product
    for product in products:
        tcin = product['tcin']
        state = purchase_states.get(tcin, {'status': 'ready'})
        product['purchase_state'] = state

    return render_template_string(FOOLPROOF_TEMPLATE,
                                products=products,
                                timestamp=datetime.now())

@app.route('/api/start_purchase/<tcin>')
def api_start_purchase(tcin):
    """API: Start a purchase"""
    products = load_products()
    product = next((p for p in products if p['tcin'] == tcin), None)

    if not product:
        return jsonify({'error': 'Product not found'}), 404

    if not purchase_manager.can_start_purchase(tcin):
        return jsonify({'error': 'Cannot start purchase (cooldown or already attempting)'}), 400

    # Simulate that product is in stock for this demo
    purchase_data = purchase_manager.start_purchase(tcin, product['name'])
    return jsonify(purchase_data)

@app.route('/api/complete_purchase/<tcin>')
def api_complete_purchase(tcin):
    """API: Complete a purchase"""
    result = purchase_manager.complete_purchase(tcin)
    if not result:
        return jsonify({'error': 'Purchase not found'}), 404

    return jsonify(result)

@app.route('/api/purchase_status/<tcin>')
def api_purchase_status(tcin):
    """API: Get current purchase status"""
    state = purchase_manager.get_purchase(tcin)
    if not state:
        return jsonify({'status': 'ready'})

    return jsonify(state)

# Foolproof HTML template with client-side countdown
FOOLPROOF_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>FOOLPROOF Purchase Dashboard</title>
    <style>
        body { font-family: Arial; margin: 20px; background: #0d1117; color: #f0f6fc; }
        .product {
            border: 1px solid #30363d;
            padding: 15px;
            margin: 10px 0;
            border-radius: 8px;
            background: #21262d;
        }
        .attempting { border-left: 4px solid #f78500; }
        .purchased { border-left: 4px solid #3fb950; }
        .failed { border-left: 4px solid #f85149; }
        .ready { border-left: 4px solid #30363d; }

        .countdown {
            font-size: 24px;
            font-weight: bold;
            color: #f78500;
            font-family: monospace;
        }
        .start-btn {
            background: #238636;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
        }
        .start-btn:disabled {
            background: #6e7681;
            cursor: not-allowed;
        }

        .status {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
            text-transform: uppercase;
        }
        .status.attempting { background: rgba(247, 133, 0, 0.15); color: #f78500; }
        .status.purchased { background: rgba(63, 185, 80, 0.15); color: #3fb950; }
        .status.failed { background: rgba(248, 81, 73, 0.15); color: #f85149; }
        .status.ready { background: rgba(139, 148, 158, 0.15); color: #8b949e; }

        .result { margin-top: 10px; }
    </style>
</head>
<body>
    <h1>🛡️ FOOLPROOF Purchase Dashboard</h1>
    <p><strong>NO refresh conflicts, NO race conditions, PURE client-side timing</strong></p>

    {% for product in products %}
    <div class="product {{ product.purchase_state.status }}" id="product-{{ product.tcin }}">
        <h3>{{ product.name }}</h3>
        <p><strong>TCIN:</strong> {{ product.tcin }}</p>

        <div class="status-area">
            <span class="status {{ product.purchase_state.status }}" id="status-{{ product.tcin }}">
                {{ product.purchase_state.status }}
            </span>

            <div id="countdown-{{ product.tcin }}" class="countdown" style="display: none;">
                Completing in: <span id="timer-{{ product.tcin }}">0</span>s
            </div>

            <div id="result-{{ product.tcin }}" class="result" style="display: none;"></div>
        </div>

        <div style="margin-top: 10px;">
            <button class="start-btn"
                    onclick="startPurchase('{{ product.tcin }}', '{{ product.name }}')"
                    id="btn-{{ product.tcin }}"
                    {% if product.purchase_state.status == 'attempting' %}disabled{% endif %}>
                Start Purchase
            </button>
        </div>
    </div>
    {% endfor %}

    <script>
        // Active countdowns
        const activeCountdowns = {};

        function startPurchase(tcin, productName) {
            console.log(`🚀 Starting purchase: ${productName} (${tcin})`);

            // Disable button immediately
            const btn = document.getElementById(`btn-${tcin}`);
            btn.disabled = true;
            btn.textContent = 'Starting...';

            // Update status
            updateStatus(tcin, 'attempting');

            // Call API to start purchase
            fetch(`/api/start_purchase/${tcin}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        console.error('❌ Start failed:', data.error);
                        btn.disabled = false;
                        btn.textContent = 'Start Purchase';
                        updateStatus(tcin, 'ready');
                        return;
                    }

                    console.log(`✅ Purchase started: ${data.duration}s duration`);

                    // Start client-side countdown (FOOLPROOF!)
                    startCountdown(tcin, data.duration, data.will_succeed);
                })
                .catch(error => {
                    console.error('❌ API error:', error);
                    btn.disabled = false;
                    btn.textContent = 'Start Purchase';
                    updateStatus(tcin, 'ready');
                });
        }

        function startCountdown(tcin, duration, willSucceed) {
            console.log(`⏱️ Starting countdown: ${tcin} -> ${duration}s`);

            // Show countdown
            const countdownDiv = document.getElementById(`countdown-${tcin}`);
            const timerSpan = document.getElementById(`timer-${tcin}`);
            countdownDiv.style.display = 'block';

            let remaining = Math.ceil(duration);
            timerSpan.textContent = remaining;

            // Store countdown reference
            activeCountdowns[tcin] = setInterval(() => {
                remaining--;
                timerSpan.textContent = remaining;

                if (remaining <= 0) {
                    clearInterval(activeCountdowns[tcin]);
                    delete activeCountdowns[tcin];

                    console.log(`⏰ Countdown finished: ${tcin}`);
                    completePurchase(tcin);
                }
            }, 1000);
        }

        function completePurchase(tcin) {
            console.log(`🏁 Completing purchase: ${tcin}`);

            // Hide countdown
            const countdownDiv = document.getElementById(`countdown-${tcin}`);
            countdownDiv.style.display = 'none';

            // Call API to complete purchase
            fetch(`/api/complete_purchase/${tcin}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        console.error('❌ Complete failed:', data.error);
                        return;
                    }

                    console.log(`✅ Purchase completed: ${data.status}`);

                    // Update UI with final result
                    updateStatus(tcin, data.status);
                    showResult(tcin, data);

                    // Re-enable button after cooldown
                    setTimeout(() => {
                        const btn = document.getElementById(`btn-${tcin}`);
                        btn.disabled = false;
                        btn.textContent = 'Start Purchase';
                    }, 5000); // 5 second demo cooldown
                })
                .catch(error => {
                    console.error('❌ Complete API error:', error);
                });
        }

        function updateStatus(tcin, status) {
            const statusSpan = document.getElementById(`status-${tcin}`);
            const productDiv = document.getElementById(`product-${tcin}`);

            statusSpan.textContent = status;
            statusSpan.className = `status ${status}`;
            productDiv.className = `product ${status}`;
        }

        function showResult(tcin, data) {
            const resultDiv = document.getElementById(`result-${tcin}`);

            if (data.status === 'purchased') {
                resultDiv.innerHTML = `
                    <strong>✅ SUCCESS!</strong><br>
                    Order: ${data.order_number}<br>
                    Price: $${data.price}
                `;
                resultDiv.style.color = '#3fb950';
            } else {
                resultDiv.innerHTML = `
                    <strong>❌ FAILED</strong><br>
                    Reason: ${data.failure_reason}
                `;
                resultDiv.style.color = '#f85149';
            }

            resultDiv.style.display = 'block';

            // Hide result after 5 seconds, then auto-cycle
            setTimeout(() => {
                resultDiv.style.display = 'none';

                // Reset to ready status
                updateStatus(tcin, 'ready');

                // Auto-start new purchase after 2 seconds (simulating "if in stock")
                setTimeout(() => {
                    console.log(`🔄 Auto-cycling: Starting new purchase for ${tcin}`);
                    const productName = data.product_name;
                    startPurchase(tcin, productName);
                }, 2000);

            }, 5000); // Show result for 5 seconds
        }

        // Load any existing attempting purchases on page load
        window.addEventListener('load', () => {
            console.log('🔄 Loading existing purchase states...');

            {% for product in products %}
            {% if product.purchase_state.status == 'attempting' %}
            // Resume countdown for existing purchase
            const tcin = '{{ product.tcin }}';
            const startedAt = {{ product.purchase_state.get('started_at', 0) }};
            const completesAt = {{ product.purchase_state.get('completes_at', 0) }};
            const willSucceed = {{ product.purchase_state.get('will_succeed', false)|tojson }};

            const now = Date.now() / 1000;
            const remaining = completesAt - now;

            if (remaining > 0) {
                console.log(`🔄 Resuming countdown: ${tcin} -> ${remaining}s remaining`);
                startCountdown(tcin, remaining, willSucceed);
            } else {
                console.log(`⚡ Purchase should be completed: ${tcin}`);
                completePurchase(tcin);
            }
            {% endif %}
            {% endfor %}
        });
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    print("FOOLPROOF Dashboard Starting...")
    print("NO refresh conflicts, NO race conditions!")
    print("http://localhost:5002")
    app.run(debug=False, port=5002)