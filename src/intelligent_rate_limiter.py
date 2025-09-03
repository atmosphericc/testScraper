#!/usr/bin/env python3
"""
Intelligent rate limiting system that detects subtle rate limiting patterns
and implements retry-after-like behavior even when servers don't send explicit headers
"""
import time
import random
import statistics
import asyncio
from typing import Dict, List, Tuple, Optional
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

@dataclass
class ResponseMetrics:
    """Track response metrics to detect subtle rate limiting"""
    response_time: float
    status_code: int
    timestamp: float
    content_length: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    
class IntelligentRateLimiter:
    """Intelligent rate limiter that detects patterns and implements dynamic backing off"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Track response patterns
        self.response_history: List[ResponseMetrics] = []
        self.max_history = 100
        
        # Rate limiting detection
        self.baseline_response_time = None
        self.suspicious_response_threshold = 2.0  # 2x baseline = suspicious
        self.rate_limit_indicators = [
            'blocked', 'rate limit', 'too many', 'throttle', 'slow down',
            'try again later', 'quota exceeded', 'limit exceeded'
        ]
        
        # Adaptive timing
        self.current_delay = 30.0  # Start conservative - 30 seconds
        self.min_delay = 15.0
        self.max_delay = 300.0  # 5 minutes max
        self.backoff_multiplier = 1.8
        self.recovery_factor = 0.85
        
        # Pattern detection
        self.consecutive_slow_responses = 0
        self.consecutive_fast_responses = 0
        self.slow_response_threshold = 3
        
        # Circuit breaker
        self.circuit_breaker_active = False
        self.circuit_breaker_until = None
        self.circuit_breaker_duration = 600  # 10 minutes
        
    def record_response(self, response_metrics: ResponseMetrics, response_body: str = ""):
        """Record response for pattern analysis"""
        self.response_history.append(response_metrics)
        
        # Keep history manageable
        if len(self.response_history) > self.max_history:
            self.response_history = self.response_history[-self.max_history:]
        
        # Update baseline response time
        if self.baseline_response_time is None and len(self.response_history) >= 5:
            recent_times = [r.response_time for r in self.response_history[-10:] if r.status_code == 200]
            if recent_times:
                self.baseline_response_time = statistics.median(recent_times)
                self.logger.info(f"Established baseline response time: {self.baseline_response_time:.2f}s")
        
        # Analyze this response
        self._analyze_response(response_metrics, response_body)
        
    def _analyze_response(self, metrics: ResponseMetrics, response_body: str):
        """Analyze response for rate limiting indicators"""
        is_suspicious = False
        reasons = []
        
        # 1. Check for explicit rate limiting status codes
        if metrics.status_code == 429:
            is_suspicious = True
            reasons.append("HTTP 429 Too Many Requests")
            
            # Look for retry-after in headers
            retry_after = self._extract_retry_after(metrics.headers)
            if retry_after:
                self.current_delay = max(retry_after, self.min_delay)
                reasons.append(f"Retry-After header: {retry_after}s")
            else:
                self.current_delay = min(self.current_delay * self.backoff_multiplier, self.max_delay)
                reasons.append("No retry-after header, backing off")
        
        # 2. Check for blocking status codes
        elif metrics.status_code in [403, 404, 503]:
            is_suspicious = True
            reasons.append(f"HTTP {metrics.status_code} - potential IP blocking")
            self.current_delay = min(self.current_delay * self.backoff_multiplier, self.max_delay)
        
        # 3. Check response time patterns
        elif self.baseline_response_time and metrics.response_time > (self.baseline_response_time * self.suspicious_response_threshold):
            self.consecutive_slow_responses += 1
            self.consecutive_fast_responses = 0
            
            if self.consecutive_slow_responses >= self.slow_response_threshold:
                is_suspicious = True
                reasons.append(f"Consecutive slow responses ({self.consecutive_slow_responses})")
                self.current_delay = min(self.current_delay * 1.3, self.max_delay)
        
        # 4. Check response body for rate limiting messages
        elif any(indicator in response_body.lower() for indicator in self.rate_limit_indicators):
            is_suspicious = True
            reasons.append("Rate limiting keywords in response")
            self.current_delay = min(self.current_delay * self.backoff_multiplier, self.max_delay)
        
        # 5. Check for successful responses to potentially recover
        else:
            if metrics.status_code == 200 and (not self.baseline_response_time or metrics.response_time <= self.baseline_response_time * 1.2):
                self.consecutive_fast_responses += 1
                self.consecutive_slow_responses = 0
                
                # Gradually reduce delay on successful fast responses
                if self.consecutive_fast_responses >= 3 and self.current_delay > self.min_delay:
                    old_delay = self.current_delay
                    self.current_delay = max(self.current_delay * self.recovery_factor, self.min_delay)
                    self.logger.debug(f"Recovery: reduced delay from {old_delay:.1f}s to {self.current_delay:.1f}s")
        
        # Handle suspicious responses
        if is_suspicious:
            self.logger.warning(f"Rate limiting detected: {', '.join(reasons)}")
            
            # Activate circuit breaker for severe cases
            if metrics.status_code in [403, 404] or self.consecutive_slow_responses >= 5:
                self.circuit_breaker_active = True
                self.circuit_breaker_until = datetime.now() + timedelta(seconds=self.circuit_breaker_duration)
                self.logger.error(f"Circuit breaker activated for {self.circuit_breaker_duration/60:.1f} minutes")
    
    def _extract_retry_after(self, headers: Dict[str, str]) -> Optional[float]:
        """Extract retry-after value from headers"""
        for header_name in ['retry-after', 'Retry-After', 'x-retry-after', 'X-Retry-After']:
            if header_name in headers:
                try:
                    return float(headers[header_name])
                except ValueError:
                    # Could be HTTP date format
                    pass
        return None
    
    async def get_next_delay(self) -> Tuple[float, Dict]:
        """Get delay before next request with intelligent backing off"""
        
        # Check circuit breaker
        if self.circuit_breaker_active:
            if datetime.now() < self.circuit_breaker_until:
                remaining = (self.circuit_breaker_until - datetime.now()).total_seconds()
                return remaining, {
                    'strategy': 'circuit_breaker',
                    'reason': f'Circuit breaker active, {remaining/60:.1f} minutes remaining',
                    'delay': remaining
                }
            else:
                # Reset circuit breaker
                self.circuit_breaker_active = False
                self.circuit_breaker_until = None
                self.current_delay = self.min_delay
                self.logger.info("Circuit breaker reset")
        
        # Add randomness to avoid predictable patterns
        randomized_delay = self.current_delay * random.uniform(0.8, 1.2)
        
        # Add some human-like variability
        human_factor = random.choice([
            1.0,  # Normal
            1.5,  # Slightly distracted
            0.7,  # Focused/quick
            2.0   # Taking a break
        ])
        
        final_delay = randomized_delay * human_factor
        final_delay = max(self.min_delay, min(final_delay, self.max_delay))
        
        metadata = {
            'strategy': self._get_current_strategy(),
            'base_delay': self.current_delay,
            'final_delay': final_delay,
            'consecutive_slow': self.consecutive_slow_responses,
            'consecutive_fast': self.consecutive_fast_responses,
            'baseline_rt': self.baseline_response_time,
            'history_size': len(self.response_history)
        }
        
        return final_delay, metadata
    
    def _get_current_strategy(self) -> str:
        """Determine current strategy based on system state"""
        if self.circuit_breaker_active:
            return 'circuit_breaker'
        elif self.consecutive_slow_responses >= 3:
            return 'aggressive_backoff'
        elif self.current_delay >= self.max_delay * 0.8:
            return 'maximum_caution'
        elif self.current_delay <= self.min_delay * 1.2:
            return 'optimal_speed'
        else:
            return 'adaptive'
    
    def get_statistics(self) -> Dict:
        """Get current rate limiting statistics"""
        if not self.response_history:
            return {}
        
        recent_responses = self.response_history[-20:]
        
        avg_response_time = statistics.mean(r.response_time for r in recent_responses)
        status_codes = [r.status_code for r in recent_responses]
        success_rate = len([s for s in status_codes if s == 200]) / len(status_codes)
        
        return {
            'current_delay': self.current_delay,
            'baseline_response_time': self.baseline_response_time,
            'avg_recent_response_time': avg_response_time,
            'success_rate': success_rate,
            'consecutive_slow': self.consecutive_slow_responses,
            'consecutive_fast': self.consecutive_fast_responses,
            'circuit_breaker_active': self.circuit_breaker_active,
            'strategy': self._get_current_strategy(),
            'total_responses': len(self.response_history)
        }
    
    def force_backoff(self, reason: str, duration_seconds: float = None):
        """Manually force a backoff period"""
        if duration_seconds is None:
            duration_seconds = self.current_delay * self.backoff_multiplier
        
        self.current_delay = min(duration_seconds, self.max_delay)
        self.logger.warning(f"Forced backoff: {reason} - delay now {self.current_delay:.1f}s")
    
    def reset(self):
        """Reset rate limiter state"""
        self.current_delay = 30.0
        self.consecutive_slow_responses = 0
        self.consecutive_fast_responses = 0
        self.circuit_breaker_active = False
        self.circuit_breaker_until = None
        self.response_history.clear()
        self.baseline_response_time = None
        self.logger.info("Rate limiter reset")

# Global intelligent rate limiter
intelligent_limiter = IntelligentRateLimiter()