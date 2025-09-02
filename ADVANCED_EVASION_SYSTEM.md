# Advanced Machine Learning-Like Evasion System

## Overview

The Target monitoring system now includes a sophisticated, machine learning-like adaptive evasion system that automatically learns and adapts to avoid bot detection. This system combines multiple advanced techniques to create realistic, human-like browsing behavior that continuously evolves based on server responses.

## Core Components

### 1. Behavioral Session Manager (`behavioral_session_manager.py`)

**Purpose**: Simulates realistic user sessions with different behavioral patterns.

**Features**:
- **4 User Behavior Types**:
  - `CASUAL_BROWSER`: Slow, varied browsing with occasional long pauses
  - `TARGETED_SHOPPER`: Quick, focused searches for specific items  
  - `COMPARISON_SHOPPER`: Extended sessions comparing multiple products
  - `BULK_CHECKER`: Fast, systematic checking of many items

- **Dynamic Session Properties**:
  - Session duration limits (2-40 minutes based on user type)
  - Request frequency patterns (1-50 requests per session)
  - Time spent per product (2-180 seconds)
  - Browsing history and search behavior simulation

- **Learning System**: Tracks successful behavioral patterns and weights future session selection

### 2. Adaptive Rate Limiter (`adaptive_rate_limiter.py`)

**Purpose**: Intelligent rate limiting that learns optimal request patterns.

**Features**:
- **4 Strategy Types**:
  - `aggressive`: Fast requests (1s delay)
  - `moderate`: Medium pace (3s delay)  
  - `conservative`: Slow, safe pace (8s delay)
  - `stealth`: Very cautious (15s delay)

- **4 Timing Patterns**:
  - `burst`: Quick bursts with cooldown periods
  - `steady`: Consistent timing with variance
  - `random`: Highly variable timing
  - `human`: Natural human browsing patterns

- **Machine Learning Features**:
  - Automatic strategy selection based on success rates
  - Exploration vs exploitation balance (20% exploration rate)
  - Circuit breaker for failure protection
  - Performance scoring and optimization

### 3. Response Analyzer (`response_analyzer.py`)

**Purpose**: Analyzes API responses to detect threats and learn patterns.

**Features**:
- **Threat Detection**: Identifies rate limiting, blocks, suspicious activity warnings
- **Success Pattern Learning**: Records optimal delays and successful strategies  
- **Adaptive Recommendations**: Suggests delay ranges and behavioral changes
- **Threat Assessment**: Provides real-time threat level scoring (low/medium/high)

**Intelligence Capabilities**:
- Pattern database with persistent learning
- Response time optimization
- Success rate tracking
- Automatic evasion mode triggering

### 4. Request Pattern Obfuscator (`request_pattern_obfuscator.py`)

**Purpose**: Adds human-like variations to requests and timing.

**Features**:
- **Human Browsing Simulation**: Burst patterns, quiet periods, irregular timing
- **Dynamic Parameters**: Occasionally omits optional parameters, adds browser-specific data
- **Realistic Headers**: Adds browsing-specific headers, Do-Not-Track, security preferences
- **Referer Chain Simulation**: Realistic navigation patterns from search/category pages

## Integration Architecture

The advanced evasion system is fully integrated into `authenticated_stock_checker.py`:

```python
# Session Management
session_context = session_manager.get_session_context()

# Adaptive Delay Calculation  
threat_level = response_analyzer.get_threat_assessment()
delay, metadata = await adaptive_limiter.get_next_delay(threat_level)

# Response Analysis
analysis = response_analyzer.analyze_response(response_data, metadata)
adaptive_limiter.record_request_result(success, response_time, analysis['threat_level'])
session_manager.record_product_interaction(tcin, success, response_time)
```

## Performance & Intelligence

### Adaptive Behavior Examples

**Low Threat Environment**:
- Strategy: `aggressive` or `moderate`  
- Pattern: `burst` or `steady`
- Delay: 1-5 seconds
- User Type: `bulk_checker` or `targeted_shopper`

**High Threat Environment**:
- Strategy: `conservative` or `stealth`
- Pattern: `human` or `random`  
- Delay: 15-45 seconds
- User Type: `casual_browser`
- Automatic session rotation

**Learning Adaptation**:
- Success rate > 90% → Try faster strategies
- Success rate < 70% → Increase delays and change patterns
- Threat level > 0.3 → Enter enhanced evasion mode
- Circuit breaker at 3 consecutive failures

### Real-World Results

From testing:
```
=== Adaptive Intelligence ===
Session ID: session_1756775483_2008
User Type: comparison_shopper
Strategy: conservative
Pattern: steady  
Threat Level: 0.000
Delay Applied: 76.39s
Response Time: 77.1s
Status: IN STOCK (successful)
```

## Advanced Features

### 1. Circuit Breaker Protection
- Activates after 3 consecutive failures
- Forces 1-3 minute delays in defensive mode
- Automatic reset after timeout period

### 2. Session Lifecycle Management
- Realistic session durations based on user behavior
- Natural ending probabilities that increase over time
- Automatic session rotation for long-running operations

### 3. Geographic and Timing Variation
- Multiple US locations (Chicago, NYC, LA, Miami, Dallas)
- Timezone-aware request timing
- Regional proxy support (when available)

### 4. TLS and Network Fingerprint Variation
- Custom SSL contexts with varied cipher suites
- Random DNS cache settings and timeouts
- Connection limit randomization
- HTTP/2 support for modern browser impersonation

## Usage Examples

### Basic Enhanced Checking
```python
checker = AuthenticatedStockChecker()
result = await checker.check_authenticated_stock("89542109")

# View adaptive metadata
adaptive_meta = result['adaptive_metadata']
print(f"Strategy: {adaptive_meta['strategy_used']}")
print(f"User Type: {adaptive_meta['user_type']}")
print(f"Threat Level: {adaptive_meta['threat_level']}")
```

### Batch Checking with Adaptation
```python
tcins = ["89542109", "94681785", "94724987"]
results = await checker.check_multiple_products(tcins)

# System automatically:
# - Manages session lifecycle
# - Adapts delays based on responses  
# - Rotates behavioral patterns
# - Triggers evasion mode if needed
```

### Performance Monitoring
```python
stats = checker.get_adaptive_performance_stats()
print(f"Current Strategy: {stats['adaptive_limiter']['current_strategy']}")
print(f"Success Rate: {stats['adaptive_limiter']['overall_success_rate']:.1%}")
print(f"Threat Level: {stats['threat_assessment']['level']}")
```

## Benefits

### 1. **Automatic Learning**
- No manual tuning required
- Continuously improves based on results
- Adapts to changes in Target's bot detection

### 2. **Realistic Behavior**
- Human-like session patterns
- Natural timing variations
- Authentic browsing simulation

### 3. **Robust Protection**
- Multiple layers of evasion
- Automatic failure recovery
- Circuit breaker protection

### 4. **Production Ready**
- Zero-cache, real-time operation
- Comprehensive logging and monitoring
- Performance optimization

### 5. **Scalable Architecture** 
- Modular component design
- Easy to extend with new strategies
- Configurable threat thresholds

## Configuration

The system is largely self-configuring, but key parameters can be adjusted:

```python
# In adaptive_rate_limiter.py
self.learning_rate = 0.1  # How fast to adapt
self.exploration_rate = 0.2  # % time trying new strategies

# In behavioral_session_manager.py
self.user_behaviors[UserBehaviorType.CASUAL_BROWSER] = {
    'session_duration': (300, 1800),  # 5-30 minutes
    'requests_per_session': (3, 12),
    'time_per_product': (10, 60)
}
```

## Future Enhancements

The architecture supports easy extension with:
- Proxy rotation integration
- Browser fingerprint randomization
- ML model integration for pattern prediction
- Distributed session management across multiple nodes
- Real-time threat intelligence feeds

## Technical Notes

- All delays and variations use cryptographically secure randomization
- Session data is persistently stored with learning patterns
- Response analysis uses both fulfillment and product APIs
- Full integration with existing Target authentication system
- Windows terminal compatibility (no Unicode characters in logs)

The advanced evasion system represents a significant leap forward in bot detection avoidance, providing enterprise-grade reliability while maintaining the speed and accuracy required for production stock monitoring.