#!/usr/bin/env python3
"""
Adaptive rate limiting system with machine learning-like behavior
Automatically adjusts request patterns based on server responses and success rates
"""
import time
import random
import statistics
from typing import Dict, List, Tuple
import logging
import asyncio

class AdaptiveRateLimiter:
    """Intelligent rate limiter that learns and adapts to server behavior"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Learning parameters
        self.learning_rate = 0.1
        self.exploration_rate = 0.2  # 20% chance to try new patterns
        
        # State tracking - START VERY CONSERVATIVE
        self.current_strategy = 'stealth'  # Start with most conservative
        self.strategy_performance = {
            'aggressive': {'successes': 0, 'failures': 0, 'avg_delay': 10.0},  # Much slower
            'moderate': {'successes': 0, 'failures': 0, 'avg_delay': 20.0},   # Much slower 
            'conservative': {'successes': 0, 'failures': 0, 'avg_delay': 30.0}, # Much slower
            'stealth': {'successes': 0, 'failures': 0, 'avg_delay': 60.0}      # Very slow start
        }
        
        # Request timing patterns - MUCH MORE CONSERVATIVE
        self.timing_patterns = {
            'burst': {'base_delay': 15.0, 'variance': 5.0, 'cool_down': 120.0},  # Slower bursts
            'steady': {'base_delay': 25.0, 'variance': 10.0, 'cool_down': 0},    # Much slower steady
            'random': {'base_delay': 30.0, 'variance': 20.0, 'cool_down': 0},    # Slower random
            'human': {'base_delay': 45.0, 'variance': 30.0, 'cool_down': 180.0}  # Very slow human
        }
        
        self.current_pattern = 'human'  # Start with most human-like pattern
        self.requests_in_current_session = 0
        self.session_start_time = time.time()
        
        # Adaptive parameters
        self.base_delay = 3.0
        self.current_multiplier = 1.0
        self.backoff_factor = 1.5
        self.recovery_factor = 0.9
        
        # Circuit breaker
        self.consecutive_failures = 0
        self.circuit_breaker_threshold = 3
        self.circuit_breaker_active = False
        self.circuit_breaker_timeout = 300  # 5 minutes
        self.circuit_breaker_opened_at = 0
    
    def record_request_result(self, success: bool, response_time: float, threat_level: float = 0):
        """Record the result of a request for learning"""
        self.requests_in_current_session += 1
        
        # Update strategy performance
        if success:
            self.strategy_performance[self.current_strategy]['successes'] += 1
            self.consecutive_failures = 0
            
            # Successful request - try to optimize (go slightly faster)
            if threat_level < 0.1:  # Low threat
                self.current_multiplier = max(0.5, self.current_multiplier * self.recovery_factor)
        else:
            self.strategy_performance[self.current_strategy]['failures'] += 1
            self.consecutive_failures += 1
            
            # Failed request - back off
            self.current_multiplier = min(5.0, self.current_multiplier * self.backoff_factor)
        
        # Circuit breaker logic
        if self.consecutive_failures >= self.circuit_breaker_threshold:
            self.circuit_breaker_active = True
            self.circuit_breaker_opened_at = time.time()
            self.logger.warning("Circuit breaker activated - entering defensive mode")
        
        # Reset circuit breaker if timeout passed
        if self.circuit_breaker_active and time.time() - self.circuit_breaker_opened_at > self.circuit_breaker_timeout:
            self.circuit_breaker_active = False
            self.consecutive_failures = 0
            self.current_multiplier = 1.0
            self.logger.info("Circuit breaker reset")
    
    def _calculate_strategy_score(self, strategy: str) -> float:
        """Calculate performance score for a strategy"""
        stats = self.strategy_performance[strategy]
        total_requests = stats['successes'] + stats['failures']
        
        if total_requests == 0:
            return 0.5  # Neutral score for untested strategies
        
        success_rate = stats['successes'] / total_requests
        speed_bonus = 1.0 / max(0.1, stats['avg_delay'])  # Faster = better
        
        return success_rate * 0.8 + speed_bonus * 0.2  # Weighted score
    
    def _select_optimal_strategy(self) -> str:
        """Select the best performing strategy with exploration"""
        if random.random() < self.exploration_rate:
            # Exploration: try random strategy
            return random.choice(list(self.strategy_performance.keys()))
        
        # Exploitation: choose best performing strategy
        best_strategy = 'conservative'
        best_score = 0
        
        for strategy in self.strategy_performance.keys():
            score = self._calculate_strategy_score(strategy)
            if score > best_score:
                best_score = score
                best_strategy = strategy
        
        return best_strategy
    
    def _should_change_pattern(self) -> bool:
        """Determine if we should change timing patterns"""
        session_duration = time.time() - self.session_start_time
        
        # Change pattern every 10-20 requests or 5-10 minutes
        return (
            self.requests_in_current_session > random.randint(10, 20) or
            session_duration > random.randint(300, 600) or
            self.consecutive_failures >= 2
        )
    
    async def get_next_delay(self, threat_level: float = 0, force_pattern: str = None, max_delay: float = None) -> Tuple[float, Dict]:
        """Calculate intelligent delay with learning and adaptation"""
        
        # Circuit breaker mode - very conservative
        if self.circuit_breaker_active:
            delay = random.uniform(60, 180)  # 1-3 minutes
            metadata = {
                'strategy': 'circuit_breaker',
                'pattern': 'defensive',
                'reason': 'Circuit breaker active'
            }
            return delay, metadata
        
        # Adapt strategy based on performance
        if self.requests_in_current_session > 5:  # Have enough data
            self.current_strategy = self._select_optimal_strategy()
        
        # Change timing pattern if needed
        if self._should_change_pattern():
            self.current_pattern = random.choice(list(self.timing_patterns.keys()))
            self.session_start_time = time.time()
            self.requests_in_current_session = 0
        
        # Get base timing from current pattern
        pattern = self.timing_patterns[self.current_pattern]
        base_delay = pattern['base_delay']
        variance = pattern['variance']
        
        # Apply strategy multiplier
        strategy_delay = self.strategy_performance[self.current_strategy]['avg_delay']
        combined_delay = (base_delay + strategy_delay) / 2
        
        # Apply adaptive multiplier based on recent performance
        adaptive_delay = combined_delay * self.current_multiplier
        
        # Apply threat level adjustment
        if threat_level > 0.3:  # High threat
            adaptive_delay *= (1 + threat_level * 2)
        
        # Add variance for human-like behavior
        variance_factor = random.uniform(-variance, variance)
        final_delay = max(0.5, adaptive_delay + variance_factor)
        
        # Respect max_delay limit if provided (for testing mode)
        if max_delay is not None:
            final_delay = min(final_delay, max_delay)
        
        # Special patterns
        if self.current_pattern == 'burst':
            # Burst pattern: quick requests then cooldown
            if self.requests_in_current_session > 0 and self.requests_in_current_session % 3 == 0:
                final_delay = pattern['cool_down']  # Cooldown after burst
        
        elif self.current_pattern == 'human':
            # Human pattern: occasional long pauses
            if random.random() < 0.3:  # 30% chance
                final_delay = random.uniform(30, 120)  # Long pause
        
        metadata = {
            'strategy': self.current_strategy,
            'pattern': self.current_pattern,
            'multiplier': self.current_multiplier,
            'threat_adjustment': threat_level,
            'session_requests': self.requests_in_current_session
        }
        
        # Update strategy performance tracking
        self.strategy_performance[self.current_strategy]['avg_delay'] = (
            self.strategy_performance[self.current_strategy]['avg_delay'] * 0.9 + final_delay * 0.1
        )
        
        return final_delay, metadata
    
    def get_performance_stats(self) -> Dict:
        """Get current performance statistics"""
        total_requests = sum(
            stats['successes'] + stats['failures'] 
            for stats in self.strategy_performance.values()
        )
        
        total_successes = sum(stats['successes'] for stats in self.strategy_performance.values())
        
        return {
            'current_strategy': self.current_strategy,
            'current_pattern': self.current_pattern,
            'total_requests': total_requests,
            'overall_success_rate': total_successes / max(1, total_requests),
            'current_multiplier': self.current_multiplier,
            'circuit_breaker_active': self.circuit_breaker_active,
            'consecutive_failures': self.consecutive_failures,
            'strategy_scores': {
                strategy: self._calculate_strategy_score(strategy) 
                for strategy in self.strategy_performance.keys()
            }
        }
    
    def force_strategy_change(self, new_strategy: str):
        """Manually force a strategy change"""
        if new_strategy in self.strategy_performance:
            self.current_strategy = new_strategy
            self.logger.info(f"Forced strategy change to: {new_strategy}")
    
    def reset_learning(self):
        """Reset all learning data"""
        for strategy in self.strategy_performance:
            self.strategy_performance[strategy] = {'successes': 0, 'failures': 0, 'avg_delay': 3.0}
        self.current_multiplier = 1.0
        self.consecutive_failures = 0
        self.circuit_breaker_active = False
        self.logger.info("Learning data reset")

# Global adaptive rate limiter instance
adaptive_limiter = AdaptiveRateLimiter()