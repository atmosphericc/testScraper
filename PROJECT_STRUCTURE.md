# Target Stock Monitor - Project Structure

## 🚀 Ultra-Fast System (NEW - Recommended)

### **Main Entry Points:**
```bash
# Ultra-fast system (recommended)
python run.py                    # Uses ultra-fast system in test mode
python run.py production         # Production mode with purchase confirmation
python run.py --dashboard        # With web dashboard at localhost:5001
python run.py --legacy          # Force legacy system

# Direct ultra-fast system
python run_ultra_fast_monitor.py --test --dashboard
```

### **Key Features:**
- ⚡ **Sub-3-second checking** for 50+ SKUs
- 🎯 **Zero missed opportunities** through hybrid verification
- 🔄 **Background browser sessions** stay authenticated
- 📊 **Smart priority scheduling** adapts to availability patterns
- 🛡️ **Production safety systems** with automatic recovery

## 📊 Dashboard Systems

### **Ultra-Fast Dashboard (Port 5001):**
```bash
python run.py --dashboard
# OR
python dashboard/ultra_fast_dashboard.py
```
- Real-time monitoring of ultra-fast system
- System controls (start/stop/reload)
- Performance metrics and analytics
- Product management

### **Legacy Dashboard (Port 5000):**
```bash
python dashboard/app.py
```
- Works with legacy monitoring system
- Historical analytics and logs
- SQLite-based data storage

## 🗂️ Directory Structure

```
testScraper/
├── run.py                          # Main entry point (supports both systems)
├── run_ultra_fast_monitor.py       # Direct ultra-fast system entry
├── setup.py                        # Initial setup and login
├── CLAUDE.md                       # Project documentation
├── PROJECT_STRUCTURE.md            # This file
│
├── src/                            # Core application code
│   ├── # ULTRA-FAST SYSTEM
│   ├── ultra_fast_stock_checker.py      # Main ultra-fast checker
│   ├── background_session_manager.py    # Persistent browser sessions
│   ├── ultra_fast_monitor_integration.py # System integration
│   ├── ultra_fast_config_manager.py     # Hot-reload configuration
│   ├── ultra_fast_safety_system.py      # Production safety & recovery
│   ├── ultra_fast_smart_scheduler.py    # Intelligent scheduling
│   │
│   ├── # LEGACY SYSTEM
│   ├── monitor.py                        # Legacy main monitor
│   ├── stock_checker.py                  # Hybrid stock checker
│   ├── authenticated_stock_checker.py    # Browser-based verification
│   ├── buy_bot.py                        # Purchase automation
│   ├── session_manager.py                # Session management
│   └── ... (other legacy components)
│
├── config/                         # Configuration files
│   ├── product_config.json         # Legacy product configuration
│   └── ultra_fast_config.json      # Ultra-fast system configuration
│
├── dashboard/                      # Web dashboards
│   ├── app.py                      # Legacy dashboard (port 5000)
│   ├── ultra_fast_dashboard.py     # Ultra-fast dashboard (port 5001)
│   └── templates/                  # HTML templates
│
├── sessions/                       # Browser session storage
│   └── target_storage.json         # Authenticated Target session
│
├── logs/                           # Application logs
│   ├── monitor.log                 # Legacy system logs
│   └── ultra_fast_monitor.log      # Ultra-fast system logs
│
├── data/                           # Runtime data
│   └── scheduler_state.json        # Smart scheduler state
│
└── archive/                        # Archived files
    ├── old_tests/                  # Old test files
    ├── analysis_files/             # API analysis results
    └── network_tests/              # Network analysis tools
```

## 🎯 Which System to Use?

### **Use Ultra-Fast System if:**
- You need to monitor 20+ products
- Speed is critical (sub-3 seconds)
- You want zero missed opportunities
- You need advanced scheduling and safety features
- You want real-time dashboard monitoring

### **Use Legacy System if:**
- You have simple monitoring needs (< 10 products)
- You prefer the existing configuration format
- You need compatibility with existing logs/analytics
- You're troubleshooting or debugging

## 📝 Configuration

### **Ultra-Fast System Config** (`config/ultra_fast_config.json`):
```json
{
  "products": {
    "89542109": {
      "name": "Product Name",
      "max_price": 50.0,
      "priority": "high",
      "check_frequency": 10,
      "enabled": true,
      "auto_purchase": false
    }
  },
  "ultra_fast": {
    "background_sessions": 4,
    "max_concurrent_checks": 50,
    "performance_target_time": 3.0
  },
  "safety": {
    "test_mode": true,
    "max_daily_purchases": 10,
    "purchase_cooldown": 300
  }
}
```

### **Legacy System Config** (`config/product_config.json`):
```json
{
  "products": [
    {
      "tcin": "89542109",
      "name": "Product Name",
      "max_price": 50.0,
      "enabled": true
    }
  ],
  "settings": {
    "check_interval": 30
  }
}
```

## 🔄 Migration Path

1. **Test Ultra-Fast System:**
   ```bash
   python run.py --dashboard
   ```

2. **Compare Performance:**
   ```bash
   python test_ultra_fast_performance.py
   ```

3. **Migrate Configuration:**
   - Ultra-fast system will auto-detect legacy config
   - Or create new ultra_fast_config.json

4. **Switch Permanently:**
   - Keep using `python run.py` (defaults to ultra-fast)
   - Legacy available with `python run.py --legacy`

## 🚨 Safety Features

### **Production Safety:**
- Emergency stop file monitoring
- Daily purchase limits
- Purchase cooldown periods
- Multi-layer validation before purchases
- Automatic error recovery
- Performance degradation alerts

### **Development Safety:**
- Test mode by default
- Production confirmation required
- Comprehensive logging
- Session isolation
- Configuration validation

## 📈 Performance Targets

### **Ultra-Fast System:**
- **50+ SKUs in ≤3 seconds** total time
- **Zero missed opportunities** through hybrid verification
- **Sub-100ms** API-only checks
- **Background browser sessions** for instant verification

### **Legacy System:**
- Traditional sequential checking
- Reliable but slower
- Good for small product lists
- Proven stability

## 🛠️ Development

### **Testing:**
```bash
# Test ultra-fast performance
python test_ultra_fast_performance.py

# Test individual components (archived)
python archive/old_tests/test_*.py
```

### **Debugging:**
```bash
# Check logs
tail -f logs/ultra_fast_monitor.log
tail -f logs/monitor.log

# Emergency stop
touch EMERGENCY_STOP
```

---

**Recommendation:** Start with the ultra-fast system using `python run.py --dashboard` for the best experience. The legacy system remains available as a fallback.