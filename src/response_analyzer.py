#!/usr/bin/env python3
"""
Advanced response analysis and behavioral adaptation system
Learns from Target's responses to dynamically adjust evasion strategies
"""
import json
import time
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
import re

class ResponseAnalyzer:
    """Analyzes API responses to detect patterns and adapt behavior"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.response_history = []
        self.pattern_db_path = Path('data/response_patterns.json')
        self.pattern_db_path.parent.mkdir(exist_ok=True)
        
        # Load historical patterns
        self.patterns = self._load_patterns()
        
        # Response classification
        self.threat_indicators = [
            'rate limit', 'too many requests', 'blocked', 'captcha',
            'verification', 'unusual activity', 'suspicious', 'bot'
        ]
        
        self.success_indicators = [
            'IN_STOCK', 'OUT_OF_STOCK', 'available_to_promise_quantity',
            'fulfillment', 'product_description'
        ]
    
    def _load_patterns(self) -> Dict:
        """Load historical response patterns"""
        try:
            if self.pattern_db_path.exists():
                with open(self.pattern_db_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.debug(f"Could not load patterns: {e}")
        
        return {
            'response_times': [],
            'success_rate': 1.0,
            'threat_level': 0,
            'optimal_delays': [],
            'working_patterns': [],
            'failed_patterns': []
        }
    
    def _save_patterns(self):
        """Save learned patterns to disk"""
        try:
            with open(self.pattern_db_path, 'w') as f:
                json.dump(self.patterns, f, indent=2)
        except Exception as e:
            self.logger.error(f"Could not save patterns: {e}")
    
    def analyze_response(self, response_data: Dict, request_metadata: Dict) -> Dict:
        """Analyze response and extract intelligence"""
        analysis = {
            'threat_level': 0,
            'success_type': 'unknown',
            'response_time': request_metadata.get('response_time', 0),
            'timestamp': time.time(),
            'recommendations': []
        }
        
        response_text = str(response_data).lower()
        
        # Detect threat indicators
        threat_score = 0
        for indicator in self.threat_indicators:
            if indicator in response_text:
                threat_score += 1
                analysis['recommendations'].append(f'Detected threat indicator: {indicator}')
        
        analysis['threat_level'] = min(threat_score / len(self.threat_indicators), 1.0)
        
        # Detect success type
        if any(indicator in response_text for indicator in self.success_indicators):
            analysis['success_type'] = 'api_success'
        elif 'error' in response_text:
            analysis['success_type'] = 'api_error'
            if '429' in response_text or 'rate' in response_text:
                analysis['success_type'] = 'rate_limited'
        
        # Store response metadata
        self.response_history.append(analysis)
        if len(self.response_history) > 100:  # Keep last 100 responses
            self.response_history = self.response_history[-100:]
        
        # Update patterns
        self._update_patterns(analysis, request_metadata)
        
        return analysis
    
    def _update_patterns(self, analysis: Dict, metadata: Dict):
        """Update learned patterns based on analysis"""
        # Update response times
        rt = analysis['response_time']
        self.patterns['response_times'].append(rt)
        if len(self.patterns['response_times']) > 50:
            self.patterns['response_times'] = self.patterns['response_times'][-50:]
        
        # Update success rate
        recent_responses = self.response_history[-20:]  # Last 20 responses
        if recent_responses:
            success_count = sum(1 for r in recent_responses if r['success_type'] == 'api_success')
            self.patterns['success_rate'] = success_count / len(recent_responses)
        
        # Update threat level
        if recent_responses:
            avg_threat = statistics.mean([r['threat_level'] for r in recent_responses])
            self.patterns['threat_level'] = avg_threat
        
        # Learn optimal delays
        if analysis['success_type'] == 'api_success':
            delay_used = metadata.get('delay_used', 0)
            if delay_used > 0:
                self.patterns['optimal_delays'].append(delay_used)
                if len(self.patterns['optimal_delays']) > 20:
                    self.patterns['optimal_delays'] = self.patterns['optimal_delays'][-20:]
        
        # Save patterns periodically
        if len(self.response_history) % 10 == 0:
            self._save_patterns()
    
    def get_adaptive_recommendations(self) -> Dict:
        """Get adaptive recommendations based on learned patterns"""
        recommendations = {
            'suggested_delay_range': (1.0, 5.0),
            'threat_level': self.patterns['threat_level'],
            'success_rate': self.patterns['success_rate'],
            'should_slow_down': False,
            'should_change_pattern': False,
            'confidence': 'medium'
        }
        
        # Analyze recent performance
        recent_responses = self.response_history[-10:]
        if len(recent_responses) >= 5:
            recent_threat = statistics.mean([r['threat_level'] for r in recent_responses])
            recent_success = sum(1 for r in recent_responses if r['success_type'] == 'api_success')
            
            # High threat level - slow down significantly
            if recent_threat > 0.3:
                recommendations['suggested_delay_range'] = (10.0, 30.0)
                recommendations['should_slow_down'] = True
                recommendations['confidence'] = 'high'
            
            # Low success rate - change patterns
            elif recent_success < len(recent_responses) * 0.7:
                recommendations['should_change_pattern'] = True
                recommendations['suggested_delay_range'] = (5.0, 15.0)
            
            # Good performance - optimize for speed
            elif recent_threat < 0.1 and recent_success > len(recent_responses) * 0.9:
                if self.patterns['optimal_delays']:
                    avg_optimal = statistics.mean(self.patterns['optimal_delays'])
                    recommendations['suggested_delay_range'] = (avg_optimal * 0.8, avg_optimal * 1.2)
        
        return recommendations
    
    def get_threat_assessment(self) -> Dict:
        """Get current threat assessment"""
        recent_responses = self.response_history[-20:]
        
        if not recent_responses:
            return {'level': 'unknown', 'confidence': 'low', 'details': []}
        
        threat_levels = [r['threat_level'] for r in recent_responses]
        avg_threat = statistics.mean(threat_levels)
        
        assessment = {
            'level': 'low',
            'confidence': 'medium',
            'avg_threat_score': avg_threat,
            'recent_failures': sum(1 for r in recent_responses if r['success_type'] != 'api_success'),
            'details': []
        }
        
        if avg_threat > 0.5:
            assessment['level'] = 'high'
            assessment['confidence'] = 'high'
            assessment['details'].append('Multiple threat indicators detected')
        elif avg_threat > 0.2:
            assessment['level'] = 'medium'
            assessment['details'].append('Some suspicious patterns detected')
        
        return assessment
    
    def should_trigger_evasion_mode(self) -> bool:
        """Determine if we should enter enhanced evasion mode"""
        threat = self.get_threat_assessment()
        recommendations = self.get_adaptive_recommendations()
        
        return (
            threat['level'] in ['medium', 'high'] or
            recommendations['success_rate'] < 0.7 or
            recommendations['should_slow_down']
        )

# Global response analyzer instance
response_analyzer = ResponseAnalyzer()