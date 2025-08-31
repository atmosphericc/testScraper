# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an advanced, commercial-grade Python-based Target product monitoring system with enterprise-level stealth capabilities. The system features sophisticated anti-detection measures including proxy rotation, browser profile management, TLS fingerprint masking, and intelligent request timing to avoid API blocking while automatically attempting product purchases.

## Common Commands

### Initial Setup
```bash
# Initialize directories and configuration
python setup.py init

# Setup Target login session (interactive)
python setup.py login

# Test setup completion
python setup.py test

# Install advanced stealth dependencies (optional but recommended)
python install_stealth_deps.py
```

### Running the Monitor
```bash
# Run in test mode (default - won't complete purchases)
python run.py

# Run in test mode explicitly
python run.py test

# Run in production mode (will attempt real purchases)
python run.py production
```

### Dashboard & Testing
```bash
# Start web dashboard (port 5000)
python dashboard/app.py

# Test individual components
python test_proxy_rotation.py
python test_enhanced_features.py
python debug_stock.py
```

## Architecture Overview

### Core Components

**TargetMonitor** (`src/monitor.py`):
- Main orchestrator that coordinates all stealth and monitoring components
- Manages monitoring loops, intelligent batching, and dynamic rate limiting
- Handles configuration hot-reloading and comprehensive status tracking
- Integrates proxy rotation, session management, and browser profile rotation
- Real-time analytics integration with dashboard API

**Advanced Stealth System**:
- **ProxyManager** (`src/proxy_manager.py`): Commercial-grade proxy rotation with success rate tracking, automatic blocking detection, and intelligent proxy selection
- **StealthRequester** (`src/stealth_requester.py`): Ultra-stealth HTTP client using curl_cffi for perfect browser TLS impersonation with HTTP/2 support
- **MultiSessionManager** (`src/multi_session_manager.py`): Multiple browser profiles with unique fingerprints, viewport randomization, and anti-detection scripting
- **SessionRotator** (`src/session_rotator.py`): Smart session rotation with usage tracking and intelligent timing patterns
- **BrowserProfileManager** (`src/browser_profile_manager.py`): Manages multiple browser profiles with randomized characteristics

**Core Monitoring Components**:
- **StockChecker** (`src/stock_checker.py`): Enhanced stock checker with proxy integration and intelligent retry logic
- **BuyBot** (`src/buy_bot.py`): Advanced purchase automation with screenshot debugging
- **SessionManager** (`src/session_manager.py`): Target.com authentication with periodic validation

**Configuration & Monitoring**:
- **ConfigWatcher** (`src/config_watcher.py`): Hot-reload configuration changes without restart
- **BatchStockChecker** (`src/batch_stock_checker.py`): Optimized batch processing for multiple products

### Advanced Stealth Features

1. **Proxy Rotation System**:
   - Automatic proxy health monitoring and rotation
   - Success rate tracking and intelligent proxy selection  
   - Automatic blocked proxy detection and cooldown management
   - Support for HTTP, HTTPS, and SOCKS5 proxies with authentication

2. **Perfect Browser Impersonation**:
   - curl_cffi integration for authentic browser TLS fingerprints
   - HTTP/2 support matching real browser behavior
   - Randomized browser profiles (Chrome, Firefox, Safari, Edge)
   - Dynamic user agent and header randomization

3. **Multi-Session Management**:
   - Multiple isolated browser contexts with unique fingerprints
   - Viewport, timezone, and locale randomization
   - Anti-automation detection scripts
   - WebGL and plugin fingerprint spoofing

4. **Intelligent Request Timing**:
   - Smart delay patterns mimicking human browsing
   - Product-specific staggered request timing
   - Priority-based check frequency adjustment
   - Burst and pause patterns to avoid detection

5. **Configuration Hot-Reloading**:
   - Real-time config updates without service restart
   - Dynamic proxy enable/disable
   - Live product list modifications

6. **Analytics & Monitoring**:
   - Real-time dashboard integration
   - Proxy performance analytics
   - Stock check response time tracking
   - Purchase attempt success rate monitoring

### Configuration Structure

**Product Configuration** (`config/product_config.json`):
- `products[]`: Enhanced product definitions with priority, check frequency, and enabling flags
- `proxies[]`: Proxy pool configuration with enable/disable flags and authentication
- `settings.rate_limit`: Advanced rate limiting with smart timing, session rotation, and proxy rotation flags
- `settings.purchase`: Purchase behavior configuration
- `settings.session`: Session validation and storage settings  
- `settings.logging`: Comprehensive logging configuration

### Key Directories

- `src/`: Core application modules including all stealth components
- `config/`: JSON configuration files with hot-reload support
- `logs/`: Application logs, error logs, and debug screenshots
- `sessions/`: Playwright session storage and browser state
- `dashboard/`: Flask web dashboard with analytics API
- `browser_profiles/`: Browser profile storage for multi-session management
- `profiles/`: Individual browser profile directories
- `TESTFiles/`: Testing utilities and validation scripts
- `monitor/`: Additional monitoring components
- `OldImplementation/`: Legacy code archive

### Entry Points

- `run.py`: Main application with enhanced startup and mode selection
- `setup.py`: Comprehensive setup with session creation and validation
- `dashboard/app.py`: Analytics dashboard server
- `install_stealth_deps.py`: Optional advanced stealth library installer

### Dependencies

**Core Dependencies**:
- `asyncio`: Async programming foundation
- `aiohttp`: HTTP client for API requests with session support  
- `playwright`: Browser automation and session management
- `flask`: Web dashboard and analytics API

**Advanced Stealth Dependencies** (optional, installed via `install_stealth_deps.py`):
- `curl-cffi`: Perfect browser TLS fingerprint impersonation
- `playwright-stealth`: Enhanced anti-detection capabilities
- `fake-useragent`: Dynamic user agent generation
- `httpx[http2]`: HTTP/2 client support
- `tls-client`: Custom TLS implementation

### Development Notes

- **Commercial-Grade Performance**: System designed for high-frequency monitoring with minimal API blocking
- **Stealth-First Architecture**: Every component includes anti-detection measures
- **Fault Tolerance**: Automatic failover between proxies, sessions, and browser profiles  
- **Real-Time Analytics**: Live monitoring of all system components via dashboard
- **Hot Configuration**: Changes can be made without service interruption
- **Comprehensive Logging**: Detailed logs for debugging and performance analysis
- **Test Mode Safety**: Extensive safeguards prevent accidental purchases during testing

### Stealth Operational Features

- **Proxy Health Monitoring**: Automatic detection and rotation of blocked/failed proxies
- **Browser Fingerprint Randomization**: Each session uses unique browser characteristics
- **Request Pattern Obfuscation**: Human-like timing patterns and request distribution
- **Session Lifecycle Management**: Automatic session validation and renewal
- **Rate Limit Adaptation**: Dynamic adjustment based on API responses
- **Geographic Distribution**: Support for residential and datacenter proxy networks

### Testing & Validation

The system includes comprehensive testing utilities:
- `test_proxy_rotation.py`: Validate proxy rotation functionality
- `test_enhanced_features.py`: Test advanced stealth features
- `debug_stock.py`: Debug individual stock check operations
- `TESTFiles/`: Collection of API testing and validation scripts

### Security & Compliance

- **Session Isolation**: Browser sessions stored locally in encrypted format
- **Proxy Authentication**: Secure credential handling for proxy services
- **Production Safeguards**: Multiple confirmation layers for production mode
- **Activity Logging**: Comprehensive audit trail of all operations
- **API Key Management**: Secure handling of Target API credentials
- **Geographic Compliance**: Respect for regional access restrictions