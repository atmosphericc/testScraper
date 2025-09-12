# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🚀 VERSION HISTORY & REFERENCE POINTS

### **January 2025 - Ultimate Stealth Dashboard (CURRENT REFERENCE POINT)**
**Status**: ✅ STABLE - This is the main iteration to reference

**Major Changes:**
- [x] **Ultimate stealth dashboard with advanced WebSocket integration**
- [x] **Real-time purchase automation** with mock purchase attempts and status tracking
- [x] **Enhanced stealth features** including 50+ user agent rotation, 30+ API key rotation
- [x] **F5/Shape evasion** with TLS fingerprinting (curl_cffi), session warmup, and human behavior simulation
- [x] **Batch API efficiency** (87% fewer API calls) with ultra-stealth bypass capabilities
- [x] **Preorder support** with PRE_ORDER_SELLABLE/UNSELLABLE detection
- [x] **Loading screen system** with progressive startup sequence
- [x] **Background monitoring** with timer-based stock checking (15-25s intervals)
- [x] **Advanced purchase status tracking** with refresh cycle management
- [x] **WebSocket live updates** for stock status, timer synchronization, and purchase updates

**Primary Entry Points (Use These):**
```bash
python start_dashboard.py           # Simple launcher (RECOMMENDED)
python main_dashboard.py           # Direct main dashboard (ultimate stealth version)
```

**Key Features Working:**
- ✅ Batch API for multiple products (87% fewer calls)
- ✅ Military-grade stealth (JA3/JA4 spoofing, behavioral patterns)
- ✅ Real-time WebSocket updates for stock status and purchase tracking
- ✅ Advanced purchase automation with mock purchase attempts
- ✅ Preorder detection and availability checking
- ✅ F5/Shape evasion with human behavior simulation
- ✅ Session warmup and TLS fingerprinting
- ✅ Dynamic product count from configuration
- ✅ Hot configuration reload without restart

**Files to Reference if Issues:**
- `main_dashboard.py` - Primary dashboard (ultimate stealth version with WebSocket integration)
- `start_dashboard.py` - Simple launcher script
- `dashboard/templates/` - Dashboard HTML templates

## Project Overview

This is an advanced, commercial-grade Python-based Target product monitoring system featuring **Ultra-Fast Stock Checking** with sub-3-second performance for 50+ SKUs and zero missed opportunities. The system combines intelligent API-first checking with background browser verification, smart priority scheduling, and comprehensive production safety systems.

## Common Commands

### Initial Setup
```bash
# Setup Target login session (required first)
python setup.py login

# Initialize directories and configuration (optional)
python setup.py init

# Test setup completion
python setup.py test

# Setup advanced evasion system (recommended)
python setup_advanced_evasion.py

# Setup free proxy integration
python setup_free_proxies.py
```

### Running the Ultimate Stealth Dashboard (Primary)
```bash
# Run main dashboard via launcher (RECOMMENDED)
python start_dashboard.py

# Or run directly
python main_dashboard.py

# Dashboard will be available at: http://localhost:5001
```

### Testing and API Validation
```bash
# Test API status and connectivity
python simple_api_check.py

# Quick stealth system validation
python quick_evasion_test.py

# Check advanced stealth features
python test_ultimate_bypass.py
```

### Testing and Development
```bash
# Test adaptive evasion system
python test_adaptive_evasion.py

# Test ultimate bypass system
python test_ultimate_bypass.py

# Quick evasion test
python quick_evasion_test.py

# Check API status
python check_api_status.py

# Archived test files available in archive/old_tests/
```

## Architecture Overview

### Core Components

**TargetMonitor** (`src/monitor.py`):
- Main orchestrator that coordinates all stealth and monitoring components
- Manages monitoring loops, intelligent batching, and dynamic rate limiting
- Handles configuration hot-reloading and comprehensive status tracking
- Integrates proxy rotation, session management, and browser profile rotation
- Real-time analytics integration with dashboard API

**Ultra-Fast Stock Checking System**:
- **UltraFastStockChecker** (`src/ultra_fast_stock_checker.py`): Sub-3-second checking for 50+ SKUs with zero missed opportunities
- **AuthenticatedStockChecker** (`src/authenticated_stock_checker.py`): Production-grade authenticated stock verification with adaptive evasion
- **UltraStealthBypass** (`src/ultra_stealth_bypass.py`): Military-grade anti-detection with JA3/JA4 spoofing and ML-based behavioral adaptation

**Advanced Evasion and Stealth System**:
- **ProxyManager** (`src/proxy_manager.py`): Commercial-grade proxy rotation with success rate tracking and intelligent selection
- **ProxyRotator** (`src/proxy_rotator.py`): Advanced proxy rotation with health monitoring
- **ResidentialProxyNetwork** (`src/residential_proxy_network.py`): Residential proxy integration for maximum stealth
- **StealthRequester** (`src/stealth_requester.py`): Ultra-stealth HTTP client using curl_cffi for perfect browser TLS impersonation
- **AdvancedEvasionEngine** (`src/advanced_evasion_engine.py`): Advanced bot detection countermeasures
- **RequestPatternObfuscator** (`src/request_pattern_obfuscator.py`): Request pattern randomization and obfuscation

**Session and Profile Management**:
- **MultiSessionManager** (`src/multi_session_manager.py`): Multiple browser profiles with unique fingerprints
- **BackgroundSessionManager** (`src/background_session_manager.py`): Background session warming and management
- **BehavioralSessionManager** (`src/behavioral_session_manager.py`): Human-like browsing behavior simulation
- **SessionRotator** (`src/session_rotator.py`): Smart session rotation with usage tracking
- **BrowserProfileManager** (`src/browser_profile_manager.py`): Browser profile management with randomized characteristics
- **SessionFingerprinter** (`src/session_fingerprinter.py`): Advanced fingerprinting and detection avoidance
- **SessionManager** (`src/session_manager.py`): Target.com authentication with periodic validation

**AI-Powered Adaptive Systems**:
- **AdaptiveRateLimiter** (`src/adaptive_rate_limiter.py`): Machine learning-like rate limiting adaptation
- **UltraFastSmartScheduler** (`src/ultra_fast_smart_scheduler.py`): Intelligent scheduling and priority management
- **ResponseAnalyzer** (`src/response_analyzer.py`): Advanced response analysis and threat detection

**Core Monitoring Components**:
- **StockChecker** (`src/stock_checker.py`): Hybrid stock checker with API fallback and website verification
- **WebsiteStockChecker** (`src/website_stock_checker.py`): Direct website-based stock checking with stealth browser settings
- **BuyBot** (`src/buy_bot.py`): Advanced purchase automation with screenshot debugging
- **DashboardOptimizedChecker** (`src/dashboard_optimized_checker.py`): Optimized checking for dashboard integration

**Configuration & Monitoring**:
- **ConfigWatcher** (`src/config_watcher.py`): Hot-reload configuration changes without restart
- **BatchStockChecker** (`src/batch_stock_checker.py`): Optimized batch processing for multiple products
- **UltraFastConfigManager** (`src/ultra_fast_config_manager.py`): Advanced configuration management
- **UltraFastSafetySystem** (`src/ultra_fast_safety_system.py`): Production safety and validation systems
- **CookieManager** (`src/cookie_manager.py`): Advanced cookie management and persistence

### Advanced Stealth Features

1. **Ultra-Fast Stock Checking System**:
   - **Sub-3-second performance**: Check 50+ SKUs in under 3 seconds
   - **Zero missed opportunities**: Instant purchase triggering on stock detection
   - **Adaptive evasion**: Machine learning-like bot detection avoidance
   - **Enhanced authenticated checking**: Real browser sessions with stealth capabilities

2. **Military-Grade Anti-Detection (UltraStealthBypass)**:
   - **JA3/JA4 fingerprint spoofing**: Real browser TLS fingerprints from live traffic
   - **Advanced HTTP/2 manipulation**: Realistic SETTINGS frames and header ordering
   - **Custom TLS cipher suites**: Browser-specific encryption preferences
   - **Anti-bot parameter injection**: `isBot=false` and related countermeasures

3. **Residential Proxy Network Integration**:
   - **Proxy warming system**: Realistic browsing behavior before target requests
   - **Intelligent proxy rotation**: Health monitoring and automatic failover
   - **Geographic distribution**: Support for residential and datacenter networks
   - **Success rate optimization**: Automatic blocked proxy detection and cooldown

4. **Advanced Session Management**:
   - **Behavioral session simulation**: Human-like browsing patterns and timing
   - **Background session warming**: Pre-warmed sessions for instant availability
   - **Multiple profile rotation**: Chrome, Firefox, Safari profiles with unique characteristics
   - **Fingerprint randomization**: Viewport, timezone, hardware specs, and WebGL variations

5. **AI-Powered Adaptive Systems**:
   - **Adaptive rate limiting**: ML-like learning from API responses and success rates
   - **Threat level assessment**: Real-time detection risk evaluation
   - **Smart scheduling**: Priority-based checking with intelligent intervals
   - **Response pattern analysis**: Advanced API response interpretation

6. **Enhanced Request Patterns**:
   - **Intelligent delay calculation**: Time-of-day aware delays with fatigue simulation
   - **Request pattern obfuscation**: Randomized timing and sequence variations
   - **Header order manipulation**: Browser-specific header ordering and variations
   - **Connection fingerprint spoofing**: Realistic connection characteristics

7. **Production Safety Systems**:
   - **Ultra-fast safety validation**: Multi-layer confirmation for production mode
   - **Configuration hot-reloading**: Real-time updates without service restart
   - **Comprehensive logging**: Detailed audit trails and performance monitoring
   - **Error handling and recovery**: Automatic fallback systems and error recovery

8. **Dashboard Integration**:
   - **Real-time WebSocket updates**: Live stock status, purchase tracking, and timer synchronization
   - **Advanced purchase automation**: Mock purchase attempts with status tracking and refresh cycle management
   - **Loading screen system**: Progressive startup sequence with background data loading
   - **Zero-cache policy**: All data fetched live for maximum accuracy
   - **Enhanced analytics**: Success rates, response times, and system health
   - **F5/Shape evasion integration**: Session warmup, human behavior simulation, and TLS fingerprinting
   - **Preorder support**: Detection and handling of PRE_ORDER_SELLABLE/UNSELLABLE items
   - **Ultimate dashboard**: Single integrated system (port 5001) with all advanced features

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

**Primary Dashboard System:**
- `start_dashboard.py`: Simple launcher for the ultimate stealth dashboard (RECOMMENDED)
- `main_dashboard.py`: Ultimate stealth dashboard with WebSocket integration, purchase automation, and F5/Shape evasion (port 5001)

**Setup and Configuration:**
- `setup.py`: Comprehensive setup with session creation and validation
- `setup_advanced_evasion.py`: Advanced evasion system setup and configuration
- `setup_free_proxies.py`: Free proxy integration and setup
- `setup_protonvpn_integration.py`: ProtonVPN integration for enhanced privacy
- `install_stealth_deps.py`: Optional advanced stealth library installer

**Legacy Systems:**
- `run.py`: Main application with enhanced startup and mode selection
- `run_ultra_fast_monitor.py`: Direct ultra-fast monitoring system
- `dashboard/app.py`: Legacy analytics dashboard server (port 5000)
- `dashboard/ultra_fast_dashboard.py`: Ultra-fast dashboard with live data (port 5001)
- `advanced_stock_monitor.py`: Advanced monitoring system with enhanced features

**Testing and Utilities:**
- `advanced_proxy_finder.py`: Intelligent proxy discovery and validation
- `auto_proxy_setup.py`: Automated proxy configuration and testing
- `get_free_proxies.py`: Free proxy source integration

### Dependencies

**Core Dependencies**:
- `requests`: HTTP client for API requests with session support
- `flask`: Web dashboard and analytics API
- `flask-socketio`: Real-time WebSocket communication for live updates
- `flask-cors`: Cross-origin resource sharing support
- `threading`: Multi-threaded background processing

**Advanced Stealth Dependencies** (optional, installed via `install_stealth_deps.py` or `setup_advanced_evasion.py`):
- `curl-cffi`: Perfect browser TLS fingerprint impersonation and JA3/JA4 spoofing
- `tls-client`: Custom TLS implementation with cipher suite control
- `undetected-chromedriver`: Advanced Chrome automation with anti-detection
- `selenium`: Browser automation framework
- `playwright-stealth`: Enhanced anti-detection capabilities
- `fake-useragent`: Dynamic user agent generation
- `httpx[http2]`: HTTP/2 client support
- `requests[security]`: Enhanced HTTP client with security features
- `cryptography`: Advanced cryptographic functions for fingerprinting

### Development Notes

- **Ultra-Fast Performance**: Sub-3-second checking for 50+ SKUs with zero missed opportunities
- **Military-Grade Stealth**: Advanced anti-detection with JA3/JA4 spoofing and behavioral adaptation
- **AI-Powered Adaptation**: Machine learning-like response to detection attempts and rate limiting
- **Commercial-Grade Reliability**: Fault tolerance with automatic failover and recovery systems
- **Residential Proxy Integration**: Advanced proxy warming and rotation for maximum stealth
- **Real-Time Analytics**: Live monitoring with zero-cache policy for maximum accuracy
- **Hot Configuration**: Changes can be made without service interruption
- **Comprehensive Safety**: Multi-layer production safeguards and validation systems
- **Enhanced Logging**: Detailed audit trails for debugging and performance analysis

### Stealth Operational Features

- **Ultra-Stealth API Integration**: Military-grade anti-detection with `isBot=false` parameter injection
- **Advanced TLS Fingerprinting**: Real browser JA3/JA4 signatures from live traffic analysis
- **Behavioral Session Warming**: Human-like browsing patterns before target requests
- **Intelligent Proxy Management**: Health monitoring, success tracking, and automatic rotation
- **Advanced Browser Impersonation**: Perfect Chrome, Firefox, and Safari profile simulation
- **Request Pattern Intelligence**: Time-of-day aware delays with fatigue simulation
- **Response Analysis Engine**: Advanced API response interpretation and threat detection
- **Geographic Proxy Distribution**: Support for residential and datacenter networks
- **Real-Time Threat Assessment**: Dynamic risk evaluation and countermeasure deployment
- **Zero-Cache Live Monitoring**: All stock data fetched live for maximum accuracy
- **Enhanced Error Recovery**: Automatic fallback systems and intelligent retry logic
- **Purchase Automation**: Advanced mock purchase system with realistic timing and status tracking
- **WebSocket Integration**: Real-time communication for stock updates, purchase status, and timer synchronization

### Testing & Validation

The system includes comprehensive testing utilities:
- `test_adaptive_evasion.py`: Test adaptive evasion and ML-like learning systems
- `test_ultimate_bypass.py`: Test military-grade stealth bypass capabilities
- `quick_evasion_test.py`: Quick validation of stealth systems
- `check_api_status.py`: API endpoint health and availability testing
- `simple_api_check.py`: Simple API functionality validation
- `TESTFiles/`: Collection of API testing and validation scripts
- `archive/old_tests/`: Legacy test files and performance benchmarks
- `archive/network_tests/`: Network traffic analysis and API discovery tools

### Security & Compliance

- **Session Isolation**: Browser sessions stored locally in encrypted format
- **Proxy Authentication**: Secure credential handling for proxy services
- **Production Safeguards**: Multiple confirmation layers for production mode
- **Activity Logging**: Comprehensive audit trail of all operations
- **API Key Management**: Secure handling of Target API credentials
- **Geographic Compliance**: Respect for regional access restrictions