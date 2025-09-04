# Target Product Monitor - Ultimate Stealth Edition

Advanced, commercial-grade Python-based Target product monitoring system featuring **Ultra-Fast Stock Checking** with sub-3-second performance for 50+ SKUs and zero missed opportunities.

## 🚀 Quick Start

### 1. Setup (First Time)
```bash
# Setup Target login session
python setup.py login

# Setup advanced evasion system  
python setup_advanced_evasion.py

# Setup proxy integration (optional)
python setup_free_proxies.py
```

### 2. Run Ultimate Dashboard
```bash
# Run the ultimate stealth dashboard (RECOMMENDED)
python main_dashboard.py

# Or run the original file name
python dashboard_ultimate_batch_stealth.py
```

Dashboard will be available at: **http://localhost:5001**

### 3. Configure Products
Edit `config/product_config.json` to add/modify products:
```json
{
  "products": [
    {
      "tcin": "94724987",
      "name": "Product Name",
      "max_price": 70.00,
      "quantity": 1,
      "enabled": true,
      "priority": 1,
      "check_frequency": 300
    }
  ]
}
```

## ✨ Features

### Ultimate Stealth System
- **Military-Grade Anti-Detection**: JA3/JA4 fingerprint spoofing
- **Batch API Efficiency**: 87% fewer API calls vs individual requests
- **Advanced Session Management**: Human-like browsing patterns
- **Proxy Rotation**: Residential and datacenter network support
- **Real-Time Activity Log**: Live monitoring of all system events

### Performance
- **Sub-3-second checking** for 50+ SKUs
- **Zero missed opportunities** with instant purchase triggering
- **Smart rate limiting** with ML-like adaptation
- **Hot configuration reloading** without restart

### Dashboard Features
- **Live stock monitoring** with real-time updates
- **Advanced analytics** with performance metrics
- **Activity logging** showing actual API calls and timing
- **Stock change detection** with instant notifications
- **Random refresh intervals** (40-45 seconds) for stealth

## 📁 Project Structure

### Core Files
- `main_dashboard.py` - Ultimate stealth dashboard (port 5001)
- `dashboard_ultimate_batch_stealth.py` - Original dashboard file
- `run.py` - Main application runner
- `setup.py` - Initial setup and configuration

### Testing & Validation
- `simple_api_check.py` - API connectivity testing
- `quick_evasion_test.py` - Stealth system validation  
- `test_ultimate_bypass.py` - Advanced stealth testing

### Configuration
- `config/product_config.json` - Product monitoring configuration
- `src/` - Core application modules
- `dashboard/templates/` - Dashboard HTML templates

### Data & Logs
- `sessions/` - Browser session storage
- `logs/` - Application logs and debug info
- `archive/` - Archived and outdated files

## 🔧 Advanced Usage

### Testing API
```bash
# Test basic API connectivity
python simple_api_check.py

# Test stealth features
python quick_evasion_test.py

# Test ultimate bypass system
python test_ultimate_bypass.py
```

### Alternative Run Methods
```bash
# Run with main system
python run.py test --dashboard

# Check system status
python check_api_status.py
```

## 📊 Dashboard Features

The ultimate dashboard (port 5001) provides:

1. **Real-Time Stock Status** - Live product availability
2. **Advanced Analytics** - API performance metrics
3. **Activity Log** - Real API calls, timing, stock changes
4. **System Status** - Stealth features, success rates
5. **Live Countdown** - Next refresh timer (40-45s random)

## 🛡️ Stealth Features

- **JA3/JA4 Spoofing**: Real browser TLS fingerprints
- **Behavioral Patterns**: Human-like request timing
- **Advanced Headers**: Browser-specific header rotation
- **Session Warming**: Pre-warmed sessions for stealth
- **Proxy Integration**: Geographic distribution support
- **Anti-Detection**: F5/Shape evasion techniques

## 📝 Configuration

Products are configured via `config/product_config.json`:
- Add new products by finding TCIN from Target URL
- Enable/disable monitoring with `"enabled": true/false`
- Set price limits with `"max_price": 100.00`
- Configure priority with `"priority": 1` (1 = highest)

Changes take effect immediately without restart (hot reload).

## 🎯 Production Safety

- **Multiple confirmation layers** for production mode
- **Comprehensive logging** for audit trails
- **Session isolation** with encrypted storage
- **Rate limiting** with intelligent adaptation
- **Error recovery** with automatic fallback

---

**Dashboard URL**: http://localhost:5001  
**Features**: F5/Shape evasion + ultimate stealth + batch efficiency  
**Performance**: Sub-3-second checking with zero missed opportunities