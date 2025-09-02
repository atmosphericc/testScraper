#!/usr/bin/env python3
"""
Advanced behavioral session management
Simulates realistic user sessions with state persistence and behavioral patterns
"""
import json
import time
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
from dataclasses import dataclass, asdict
from enum import Enum

class UserBehaviorType(Enum):
    CASUAL_BROWSER = "casual_browser"
    TARGETED_SHOPPER = "targeted_shopper"
    COMPARISON_SHOPPER = "comparison_shopper"
    BULK_CHECKER = "bulk_checker"

@dataclass
class SessionState:
    session_id: str
    user_type: UserBehaviorType
    created_at: float
    last_activity: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    current_interest: str  # Current product category of interest
    browsing_history: List[str]  # TCINs viewed
    search_terms: List[str]
    time_spent_per_product: Dict[str, float]  # TCIN -> seconds spent
    behavioral_flags: Dict[str, bool]
    session_duration_limit: float

class BehavioralSessionManager:
    """Manages realistic user sessions with behavioral patterns"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions_dir = Path('data/sessions')
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_session: Optional[SessionState] = None
        self.behavior_patterns = self._load_behavior_patterns()
        
        # User behavior configurations
        self.user_behaviors = {
            UserBehaviorType.CASUAL_BROWSER: {
                'session_duration': (600, 3600),  # 10-60 minutes (longer)
                'requests_per_session': (2, 8),   # Fewer requests
                'time_per_product': (30, 180),    # 30-180 seconds per product (much slower)
                'categories': ['toys', 'electronics', 'home', 'clothing'],
                'search_probability': 0.7,
                'comparison_probability': 0.3
            },
            UserBehaviorType.TARGETED_SHOPPER: {
                'session_duration': (300, 1200),  # 5-20 minutes (longer)
                'requests_per_session': (1, 3),   # Very few requests
                'time_per_product': (60, 300),    # 1-5 minutes per product (much slower)
                'categories': ['specific_product'],
                'search_probability': 0.2,
                'comparison_probability': 0.1
            },
            UserBehaviorType.COMPARISON_SHOPPER: {
                'session_duration': (1200, 3600),  # 20-60 minutes (much longer)
                'requests_per_session': (3, 12),    # Fewer requests
                'time_per_product': (60, 300),      # 1-5 minutes per product (much slower)
                'categories': ['electronics', 'toys', 'home'],
                'search_probability': 0.9,
                'comparison_probability': 0.8
            },
            UserBehaviorType.BULK_CHECKER: {
                'session_duration': (300, 900),   # 5-15 minutes (much longer)
                'requests_per_session': (3, 15),  # Much fewer requests
                'time_per_product': (15, 60),     # 15-60 seconds per product (much slower)
                'categories': ['collectibles', 'toys'],
                'search_probability': 0.3,
                'comparison_probability': 0.9
            }
        }
    
    def _load_behavior_patterns(self) -> Dict:
        """Load historical behavior patterns"""
        pattern_file = self.sessions_dir / 'behavior_patterns.json'
        
        try:
            if pattern_file.exists():
                with open(pattern_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.debug(f"Could not load behavior patterns: {e}")
        
        return {
            'successful_patterns': [],
            'failed_patterns': [],
            'optimal_timings': {},
            'effective_user_types': []
        }
    
    def _save_behavior_patterns(self):
        """Save learned behavior patterns"""
        pattern_file = self.sessions_dir / 'behavior_patterns.json'
        
        try:
            with open(pattern_file, 'w') as f:
                json.dump(self.behavior_patterns, f, indent=2)
        except Exception as e:
            self.logger.error(f"Could not save behavior patterns: {e}")
    
    def start_new_session(self, force_user_type: Optional[UserBehaviorType] = None) -> SessionState:
        """Start a new behavioral session"""
        
        # Choose user behavior type
        if force_user_type:
            user_type = force_user_type
        else:
            # Weight selection based on successful patterns
            effective_types = self.behavior_patterns.get('effective_user_types', [])
            if effective_types:
                type_weights = {ut: effective_types.count(ut.value) for ut in UserBehaviorType}
                user_type = max(type_weights, key=type_weights.get)
            else:
                user_type = random.choice(list(UserBehaviorType))
        
        session_id = f"session_{int(time.time())}_{random.randint(1000, 9999)}"
        
        behavior_config = self.user_behaviors[user_type]
        session_duration = random.uniform(*behavior_config['session_duration'])
        
        self.current_session = SessionState(
            session_id=session_id,
            user_type=user_type,
            created_at=time.time(),
            last_activity=time.time(),
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            current_interest=random.choice(behavior_config['categories']),
            browsing_history=[],
            search_terms=[],
            time_spent_per_product={},
            behavioral_flags={
                'has_searched': False,
                'compared_products': False,
                'viewed_details': False,
                'added_to_cart': False
            },
            session_duration_limit=session_duration
        )
        
        self.logger.info(f"Started new session: {session_id} ({user_type.value})")
        return self.current_session
    
    def should_end_session(self) -> bool:
        """Determine if current session should end"""
        if not self.current_session:
            return True
        
        session_age = time.time() - self.current_session.created_at
        behavior_config = self.user_behaviors[self.current_session.user_type]
        
        # Check session duration limit
        if session_age > self.current_session.session_duration_limit:
            return True
        
        # Check request count limits
        max_requests = behavior_config['requests_per_session'][1]
        if self.current_session.total_requests >= max_requests:
            return True
        
        # Natural ending probability (increases over time)
        natural_end_probability = min(0.1, session_age / 3600)  # Max 10% per hour
        if random.random() < natural_end_probability:
            return True
        
        return False
    
    def record_product_interaction(self, tcin: str, success: bool, response_time: float):
        """Record interaction with a product"""
        if not self.current_session:
            self.start_new_session()
        
        session = self.current_session
        session.total_requests += 1
        session.last_activity = time.time()
        
        if success:
            session.successful_requests += 1
        else:
            session.failed_requests += 1
        
        # Record browsing behavior
        if tcin not in session.browsing_history:
            session.browsing_history.append(tcin)
        
        # Simulate time spent on product based on user type
        behavior_config = self.user_behaviors[session.user_type]
        time_spent = random.uniform(*behavior_config['time_per_product'])
        session.time_spent_per_product[tcin] = time_spent
        
        # Update behavioral flags
        if len(session.browsing_history) > 1:
            session.behavioral_flags['compared_products'] = True
        
        if time_spent > 30:  # Spent significant time
            session.behavioral_flags['viewed_details'] = True
        
        # Learn from successful patterns
        if success:
            pattern = {
                'user_type': session.user_type.value,
                'time_spent': time_spent,
                'session_age': time.time() - session.created_at,
                'products_viewed': len(session.browsing_history)
            }
            self.behavior_patterns['successful_patterns'].append(pattern)
            
            if len(self.behavior_patterns['successful_patterns']) > 50:
                self.behavior_patterns['successful_patterns'] = \
                    self.behavior_patterns['successful_patterns'][-50:]
    
    def get_realistic_delay_for_next_request(self) -> float:
        """Calculate realistic delay based on current session behavior"""
        if not self.current_session:
            return random.uniform(1, 5)
        
        session = self.current_session
        behavior_config = self.user_behaviors[session.user_type]
        
        # Base delay from user behavior type
        base_delay = random.uniform(*behavior_config['time_per_product'])
        
        # Adjust based on session state
        if session.user_type == UserBehaviorType.BULK_CHECKER:
            # Bulk checkers are faster but with some variance
            base_delay = random.uniform(2, 8)
        
        elif session.user_type == UserBehaviorType.CASUAL_BROWSER:
            # Casual browsers have more variance and occasional long pauses
            if random.random() < 0.3:  # 30% chance of long pause
                base_delay = random.uniform(30, 120)
        
        elif session.user_type == UserBehaviorType.COMPARISON_SHOPPER:
            # Comparison shoppers spend more time between similar products
            if len(session.browsing_history) > 1:
                base_delay *= 1.5  # Take more time to compare
        
        # Add session fatigue (longer delays as session progresses)
        session_progress = session.total_requests / behavior_config['requests_per_session'][1]
        fatigue_multiplier = 1 + (session_progress * 0.5)  # Up to 50% slower when tired
        
        final_delay = base_delay * fatigue_multiplier
        
        return max(1.0, final_delay)
    
    def get_session_context(self) -> Dict:
        """Get current session context for request customization"""
        if not self.current_session:
            return {}
        
        session = self.current_session
        session_age = time.time() - session.created_at
        
        return {
            'session_id': session.session_id,
            'user_type': session.user_type.value,
            'session_age': session_age,
            'requests_made': session.total_requests,
            'success_rate': session.successful_requests / max(1, session.total_requests),
            'current_interest': session.current_interest,
            'has_browsing_history': len(session.browsing_history) > 0,
            'behavioral_flags': session.behavioral_flags.copy(),
            'time_in_session': session_age,
            'should_end_soon': self.should_end_session()
        }
    
    def end_current_session(self):
        """End current session and save learnings"""
        if not self.current_session:
            return
        
        session = self.current_session
        success_rate = session.successful_requests / max(1, session.total_requests)
        
        # Record effective user types
        if success_rate > 0.8:  # Highly successful session
            self.behavior_patterns['effective_user_types'].append(session.user_type.value)
        
        # Save session data
        session_file = self.sessions_dir / f"{session.session_id}.json"
        try:
            with open(session_file, 'w') as f:
                json.dump(asdict(session), f, indent=2, default=str)
        except Exception as e:
            self.logger.error(f"Could not save session data: {e}")
        
        # Save behavior patterns
        self._save_behavior_patterns()
        
        self.logger.info(f"Ended session {session.session_id}: {success_rate:.2%} success rate")
        self.current_session = None
    
    def get_session_statistics(self) -> Dict:
        """Get current session statistics"""
        if not self.current_session:
            return {'active_session': False}
        
        session = self.current_session
        
        return {
            'active_session': True,
            'session_id': session.session_id,
            'user_type': session.user_type.value,
            'session_duration': time.time() - session.created_at,
            'total_requests': session.total_requests,
            'success_rate': session.successful_requests / max(1, session.total_requests),
            'products_viewed': len(session.browsing_history),
            'behavioral_flags': session.behavioral_flags,
            'time_remaining': session.session_duration_limit - (time.time() - session.created_at)
        }

# Global session manager instance
session_manager = BehavioralSessionManager()