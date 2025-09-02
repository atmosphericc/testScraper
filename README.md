# Target Stock Monitor - Ultra-Fast System

⚡ **Sub-3-second stock checking for 50+ SKUs with zero missed opportunities**

## Quick Start

### 1. Setup (One-time)
```bash
python setup.py login
```

### 2. Run Ultra-Fast Monitor
```bash
# Test mode with dashboard (recommended)
python run.py --dashboard

# Production mode (real purchases)
python run.py production
```

### 3. Access Dashboard
- **Ultra-Fast Dashboard**: http://localhost:5001 (recommended)
- **Legacy Dashboard**: http://localhost:5000

## Key Features

🚀 **Ultra-Fast Performance**
- Sub-3-second total time for 50+ products
- Background browser sessions stay authenticated
- Parallel processing with intelligent batching

🎯 **Zero Missed Opportunities**  
- Hybrid API + browser verification
- Smart confidence scoring
- Automatic fallback strategies

📊 **Smart Scheduling**
- Priority-based product checking
- Adaptive frequency based on availability patterns
- Performance-optimized batching

🛡️ **Production Safety**
- Emergency stop mechanisms
- Purchase validation and limits  
- Automatic error recovery
- Comprehensive logging

## Commands

| Command | Purpose |
|---------|---------|
| `python run.py` | Ultra-fast system (test mode) |
| `python run.py --dashboard` | With web dashboard |
| `python run.py production` | Production mode |
| `python run.py --legacy` | Legacy system |
| `python setup.py login` | Setup Target session |

## Configuration

Products are managed through:
- **Ultra-fast**: `config/ultra_fast_config.json` (auto-created)
- **Legacy**: `config/product_config.json` 

Configuration supports hot-reload without restart.

## Architecture

- **Ultra-Fast System**: New high-performance system (recommended)
- **Legacy System**: Original system (stable fallback)
- **Automatic Fallback**: Ultra-fast → Legacy if needed

See `PROJECT_STRUCTURE.md` for detailed documentation.

---

**Quick Start:** `python setup.py login` → `python run.py --dashboard`