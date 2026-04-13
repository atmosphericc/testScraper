#!/usr/bin/env python3
"""
Walmart Logging Manager - Mirrors Target's logging infrastructure
Provides structured logging for error tracking, activity history, and purchase states
"""

import json
import pickle
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

class WalmartLogger:
    """Centralized logger for Walmart operations matching Target's format"""

    def __init__(self, log_dir: str = 'walmart/logs', error_log: str = 'walmart/logs/error_log.txt'):
        self.log_dir = Path(log_dir)
        self.error_log_path = Path(error_log)
        self.activity_log_path = self.log_dir / 'activity_log.pkl'
        self.purchase_states_path = self.log_dir / 'purchase_states.json'
        self.purchase_logs_dir = self.log_dir / 'purchases'

        # Create directories
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.purchase_logs_dir.mkdir(parents=True, exist_ok=True)

        # Thread safety
        self._lock = threading.Lock()

        # In-memory activity log (synced to disk)
        self._activity_log: List[Dict[str, Any]] = self._load_activity_log()

    def _load_activity_log(self) -> List[Dict[str, Any]]:
        """Load activity log from pickle file"""
        try:
            if self.activity_log_path.exists():
                with open(self.activity_log_path, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            self.log_error("activity_log_load", f"Failed to load activity log: {e}")
        return []

    def _save_activity_log(self):
        """Save activity log to pickle file"""
        try:
            with open(self.activity_log_path, 'wb') as f:
                pickle.dump(self._activity_log, f)
        except Exception as e:
            print(f"[ERROR] Failed to save activity log: {e}")

    def log_error(self, category: str, message: str, exception: Optional[Exception] = None):
        """Log error to error_log.txt and activity log"""
        with self._lock:
            timestamp = datetime.now()
            time_str = timestamp.strftime('%H:%M:%S')
            date_str = timestamp.strftime('%Y-%m-%d')
            full_time = timestamp.strftime('%Y-%m-%d %H:%M:%S')

            # Format error line
            error_line = f"[{full_time}] [{category}] {message}"
            if exception:
                error_line += f"\n{traceback.format_exc()}"

            # Write to error log file
            try:
                with open(self.error_log_path, 'a', encoding='utf-8') as f:
                    f.write(error_line + '\n')
                    if exception:
                        f.write('\n')
            except Exception as e:
                print(f"[ERROR] Failed to write error log: {e}")

            # Add to activity log
            self._activity_log.append({
                'timestamp': timestamp.isoformat(),
                'message': message,
                'level': 'ERROR',
                'category': category,
                'time_str': time_str,
                'date_str': date_str,
                'full_time': full_time
            })

            # Print to console
            print(f"[{full_time}] [ERROR] [{category}] {message}")
            if exception:
                print(traceback.format_exc())

            self._save_activity_log()

    def log_activity(self, message: str, category: str = 'PURCHASE', level: str = 'INFO'):
        """Log activity (purchase attempt, status change, etc)"""
        with self._lock:
            timestamp = datetime.now()
            time_str = timestamp.strftime('%H:%M:%S')
            date_str = timestamp.strftime('%Y-%m-%d')
            full_time = timestamp.strftime('%Y-%m-%d %H:%M:%S')

            self._activity_log.append({
                'timestamp': timestamp.isoformat(),
                'message': message,
                'level': level,
                'category': category,
                'time_str': time_str,
                'date_str': date_str,
                'full_time': full_time
            })

            print(f"[{full_time}] [{category}] {message}")
            self._save_activity_log()

    def log_purchase_state(self, item_id: str, state: Dict[str, Any]):
        """Log purchase state (mirrors Target's purchase_states.json format)

        State dict should include:
        - status: 'ready' | 'queued' | 'attempting' | 'success' | 'failure'
        - final_outcome: 'purchased' | 'oos' | 'timeout' | 'unknown' | 'antibot' | None
        - order_id: str or None
        - timestamp: ISO timestamp
        - attempt_count: int
        - last_error: str or None
        """
        with self._lock:
            try:
                states = {}
                if self.purchase_states_path.exists():
                    with open(self.purchase_states_path, 'r') as f:
                        states = json.load(f)

                states[str(item_id)] = state

                with open(self.purchase_states_path, 'w') as f:
                    json.dump(states, f, indent=2)
            except Exception as e:
                self.log_error("purchase_state_write", f"Failed to write purchase state for {item_id}: {e}")

    def get_purchase_state(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get current purchase state for item"""
        try:
            if self.purchase_states_path.exists():
                with open(self.purchase_states_path, 'r') as f:
                    states = json.load(f)
                    return states.get(str(item_id))
        except Exception as e:
            self.log_error("purchase_state_read", f"Failed to read purchase state for {item_id}: {e}")
        return None

    def create_purchase_log(self, item_id: str, timestamp_str: str) -> Path:
        """Create a per-purchase log file (like Target's purchase_*.log)

        Returns the file path for use with _PurchaseLogTee
        """
        log_path = self.purchase_logs_dir / f"purchase_{item_id}_{timestamp_str}.log"
        # Create empty file
        log_path.touch()
        return log_path

    def get_activity_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent activity log entries"""
        with self._lock:
            return self._activity_log[-limit:] if self._activity_log else []

    def clear_activity_log(self):
        """Clear activity log (use with caution)"""
        with self._lock:
            self._activity_log = []
            self._save_activity_log()


class _WalmartPurchaseLogTee:
    """Tees sys.stdout to a purchase log file (mirrors Target's _PurchaseLogTee)"""
    def __init__(self, original_stdout, log_path: Path):
        self._orig = original_stdout
        self._file = open(log_path, 'w', encoding='utf-8', buffering=1)
        self._lock = threading.Lock()

    def write(self, data):
        with self._lock:
            self._orig.write(data)
            try:
                self._file.write(data)
            except Exception:
                pass

    def flush(self):
        self._orig.flush()
        try:
            self._file.flush()
        except Exception:
            pass

    def close(self):
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._orig, name)


# Global logger instance
_walmart_logger: Optional[WalmartLogger] = None

def get_walmart_logger() -> WalmartLogger:
    """Get or create global Walmart logger instance"""
    global _walmart_logger
    if _walmart_logger is None:
        _walmart_logger = WalmartLogger()
    return _walmart_logger


# Convenience functions for direct use
def log_error(category: str, message: str, exception: Optional[Exception] = None):
    """Log error via global logger"""
    get_walmart_logger().log_error(category, message, exception)

def log_activity(message: str, category: str = 'PURCHASE', level: str = 'INFO'):
    """Log activity via global logger"""
    get_walmart_logger().log_activity(message, category, level)

def log_purchase_state(item_id: str, state: Dict[str, Any]):
    """Log purchase state via global logger"""
    get_walmart_logger().log_purchase_state(item_id, state)

def get_purchase_state(item_id: str) -> Optional[Dict[str, Any]]:
    """Get purchase state via global logger"""
    return get_walmart_logger().get_purchase_state(item_id)
