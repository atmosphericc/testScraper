# Things to Remember - Target.com Competitive Purchasing Bot

## 🎯 **Project Overview**
This is a competitive purchasing bot system designed to monitor Target.com products and automatically purchase them the moment they come in stock. Built for high-demand, limited-availability items where speed is critical.

## 🏗️ **Core Architecture**

### **Main Components**
- **`app.py`** - Master dashboard and orchestrator (Flask web server)
- **`buy_bot.py`** - Individual purchase executor (can run standalone)
- **`save_login.py`** - Session setup tool (one-time login creation)
- **Session System** - Persistent browser management (NEW architecture)

### **Key Features**
1. **Real-time Dashboard** - http://127.0.0.1:5001 with live updates
2. **Multi-product Monitoring** - Checks 9+ TCINs every 15-17 seconds
3. **Automatic Purchasing** - Instant purchase attempts when products come in stock
4. **Complete Checkout** - Full payment processing with "Thanks for your order!" confirmation
5. **Status Tracking** - Real-time purchase states (ready/attempting/purchased/failed)
6. **Server-Sent Events** - Live dashboard updates without refresh

## 🔄 **Purchase Flow**
```
Stock Monitor → Detects In-Stock → Purchase Attempt → Add to Cart → Checkout → Payment → "Thanks for your order!" → Status: "purchased"
```

## ⚡ **Critical Success Factors**
- **Speed** - Purchases happen within seconds of stock detection
- **Accuracy** - "purchased" status only set when order actually confirmed
- **Shipping** - Automatically selects shipping over pickup
- **Persistence** - Continues monitoring after successful purchases
- **Competitive** - Designed to compete with other bots for limited inventory

## 🛡️ **Safety Features**
- Purchase authorization controls
- Clear cart before adding new items
- Session validation and recovery
- Detailed logging and monitoring
- Manual intervention capability

## 📊 **Current Status**
- ✅ **Working**: Complete purchase flow, order confirmation detection
- ✅ **Tested**: Both regular products and preorders
- ⚠️ **Issue**: Persistent session system falls back to subprocess mode
- ✅ **Result**: Still successfully completes purchases

## 🎮 **How to Use**
1. **Setup**: `python save_login.py` (one-time login)
2. **Start**: `python app.py` (launches dashboard)
3. **Monitor**: Visit http://127.0.0.1:5001
4. **Standalone**: `TARGET_TCIN="12345" python buy_bot.py`

## 🔑 **Key Files & Data**
- **`target.json`** - Persistent session data
- **`purchase_states.json`** - Current purchase states
- **`logs/`** - Activity logs and screenshots
- **Product TCINs** - Currently monitoring 9 different products

## 💡 **Remember**
This system is designed for competitive purchasing scenarios where milliseconds matter. The bot will attempt to purchase ANY monitored product that comes in stock automatically.