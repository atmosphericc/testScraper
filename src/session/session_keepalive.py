#!/usr/bin/env python3
"""
Session Keep-Alive System - Background maintenance for persistent sessions
Keeps Target.com session active with minimal, realistic interactions
"""

import asyncio
import logging
import threading
import time
import random
from datetime import datetime, timedelta
from typing import Optional, Callable

from .session_manager import SessionManager


class SessionKeepAlive:
    """Background service to maintain session health and prevent expiration"""

    def __init__(self, session_manager: SessionManager, status_callback: Optional[Callable] = None):
        self.session_manager = session_manager
        self.status_callback = status_callback
        self.logger = logging.getLogger(__name__)

        # Thread control
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()

        # Keep-alive configuration
        self.validation_interval = 300  # 5 minutes
        self.keep_alive_interval = 600  # 10 minutes
        self.max_idle_time = 1800  # 30 minutes before forced refresh

        # State tracking
        self.last_validation = None
        self.last_keep_alive = None
        self.last_activity = None
        self.keep_alive_failures = 0
        self.max_failures = 3

    def start(self):
        """Start the background keep-alive service"""
        if self._running:
            self.logger.warning("Keep-alive service already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_service, daemon=True)
        self._thread.start()

        self.logger.info("🔄 Session keep-alive service started")
        self._notify_status("keep_alive_started", {"status": "active"})

    def stop(self):
        """Stop the background keep-alive service"""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self.logger.info(" Session keep-alive service stopped")
        self._notify_status("keep_alive_stopped", {"status": "inactive"})

    def _run_service(self):
        """Main service loop"""
        self.logger.info("Keep-alive service loop starting...")

        while self._running and not self._stop_event.is_set():
            try:
                # Run async operations in event loop
                asyncio.run(self._service_cycle())

                # Wait before next cycle (with early exit on stop)
                for _ in range(30):  # Check stop event every 1 second for 30 seconds
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                self.logger.error(f" Keep-alive service error: {e}")
                time.sleep(10)  # Wait before retry

        self.logger.info("Keep-alive service loop ended")

    async def _service_cycle(self):
        """Single service cycle - validation and keep-alive"""
        current_time = datetime.now()

        try:
            # Check if session manager is healthy
            if not await self.session_manager.is_healthy():
                self.logger.warning(" Session manager unhealthy, attempting refresh...")
                if await self.session_manager.refresh_session():
                    self.logger.info(" Session refreshed successfully")
                    self.keep_alive_failures = 0
                else:
                    self.logger.error(" Session refresh failed")
                    self.keep_alive_failures += 1
                    self._notify_status("keep_alive_error", {"error": "refresh_failed"})
                    return

            # Perform validation check
            await self._perform_validation(current_time)

            # Perform keep-alive interaction
            await self._perform_keep_alive(current_time)

            # Check for idle timeout
            await self._check_idle_timeout(current_time)

        except Exception as e:
            self.logger.error(f" Service cycle error: {e}")
            self.keep_alive_failures += 1

            if self.keep_alive_failures >= self.max_failures:
                self.logger.error(f" Too many keep-alive failures ({self.keep_alive_failures}), forcing refresh")
                await self.session_manager.refresh_session()
                self.keep_alive_failures = 0

    async def _perform_validation(self, current_time: datetime):
        """Validate session if needed"""
        if (not self.last_validation or
            (current_time - self.last_validation).total_seconds() > self.validation_interval):

            self.logger.debug("Performing session validation...")

            if await self.session_manager._validate_session():
                self.last_validation = current_time
                self.logger.debug(" Session validation passed")
                self._notify_status("session_validated", {"timestamp": current_time.isoformat()})
            else:
                self.logger.warning(" Session validation failed")
                self.keep_alive_failures += 1
                self._notify_status("session_validation_failed", {"failures": self.keep_alive_failures})

    async def _perform_keep_alive(self, current_time: datetime):
        """Perform keep-alive interaction if needed"""
        if (not self.last_keep_alive or
            (current_time - self.last_keep_alive).total_seconds() > self.keep_alive_interval):

            self.logger.debug("Performing keep-alive interaction...")

            try:
                page = await self.session_manager.get_page()
                if page:
                    # Perform minimal interaction - just navigate to homepage
                    await page.goto("https://www.target.com",
                                  wait_until='domcontentloaded',
                                  timeout=10000)

                    # Add small delay to appear human-like
                    await asyncio.sleep(random.uniform(0.5, 2.0))

                    # Optional: scroll slightly to appear active
                    await page.evaluate("window.scrollTo(0, window.scrollY + 100)")
                    await asyncio.sleep(random.uniform(0.2, 0.8))

                    self.last_keep_alive = current_time
                    self.last_activity = current_time
                    self.logger.debug(" Keep-alive interaction completed")
                    self._notify_status("keep_alive_completed", {"timestamp": current_time.isoformat()})

            except Exception as e:
                self.logger.warning(f" Keep-alive interaction failed: {e}")
                self.keep_alive_failures += 1
                self._notify_status("keep_alive_failed", {"error": str(e)})

    async def _check_idle_timeout(self, current_time: datetime):
        """Check if session has been idle too long"""
        if self.last_activity:
            idle_time = (current_time - self.last_activity).total_seconds()
            if idle_time > self.max_idle_time:
                self.logger.warning(f" Session idle for {idle_time/60:.1f} minutes, forcing refresh")
                if await self.session_manager.refresh_session():
                    self.last_activity = current_time
                    self.logger.info(" Idle session refreshed successfully")
                else:
                    self.logger.error(" Idle session refresh failed")

                self._notify_status("idle_refresh", {"idle_time_minutes": idle_time/60})

    def mark_activity(self):
        """Mark that the session has been used (called by purchase system)"""
        self.last_activity = datetime.now()
        self.logger.debug("Session activity marked")

    def get_status(self) -> dict:
        """Get current keep-alive status"""
        current_time = datetime.now()

        return {
            'running': self._running,
            'last_validation': self.last_validation.isoformat() if self.last_validation else None,
            'last_keep_alive': self.last_keep_alive.isoformat() if self.last_keep_alive else None,
            'last_activity': self.last_activity.isoformat() if self.last_activity else None,
            'failures': self.keep_alive_failures,
            'next_validation': (self.last_validation + timedelta(seconds=self.validation_interval)).isoformat()
                              if self.last_validation else None,
            'next_keep_alive': (self.last_keep_alive + timedelta(seconds=self.keep_alive_interval)).isoformat()
                              if self.last_keep_alive else None,
            'idle_time_seconds': (current_time - self.last_activity).total_seconds()
                               if self.last_activity else None
        }

    def _notify_status(self, event: str, data: dict):
        """Notify status callback if available"""
        if self.status_callback:
            try:
                self.status_callback(event, data)
            except Exception as e:
                self.logger.warning(f"Status callback failed: {e}")