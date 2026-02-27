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
        # BUGFIX: Increased validation interval to reduce collision probability
        self.validation_interval = 900  # 15 minutes (increased from 5 min)
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

        self.logger.info("[KEEPALIVE] Session keep-alive service started")
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
                # Submit async operations to main event loop (thread-safe)
                future = self.session_manager.submit_async_task(self._service_cycle())
                # Wait for completion with timeout
                future.result(timeout=30)

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
                self.logger.warning("[WARNING] Session manager unhealthy, attempting refresh...")
                # refresh_session() now tries navigation refresh first (preserves context)
                if await self.session_manager.refresh_session():
                    self.logger.info("[OK] Session refreshed successfully")
                    self.keep_alive_failures = 0
                else:
                    self.logger.error("[ERROR] Session refresh failed")
                    self.keep_alive_failures += 1
                    self._notify_status("keep_alive_error", {"error": "refresh_failed"})
                    return

            # Perform validation check
            await self._perform_validation(current_time)

            # Perform keep-alive interaction
            await self._perform_keep_alive(current_time)

            # Check for idle timeout - but don't force refresh unless truly needed
            await self._check_idle_timeout(current_time)

        except Exception as e:
            self.logger.error(f"[ERROR] Service cycle error: {e}")
            self.keep_alive_failures += 1

            # Only force refresh after multiple consecutive failures
            if self.keep_alive_failures >= self.max_failures:
                self.logger.error(f"[CRITICAL] Too many keep-alive failures ({self.keep_alive_failures}), forcing refresh")
                # refresh_session() now tries navigation refresh first
                await self.session_manager.refresh_session()
                self.keep_alive_failures = 0

    async def _perform_validation(self, current_time: datetime):
        """Validate session if needed - DISABLED (session validation removed, login handled manually)"""
        # Session validation has been removed - login is handled manually via separate file
        # This method is now a no-op to prevent errors
        self.last_validation = current_time
        self.logger.debug("[OK] Session validation skipped (handled manually)")
        return

    async def _perform_keep_alive(self, current_time: datetime):
        """Perform keep-alive interaction if needed - bulletproof None handling"""
        if (not self.last_keep_alive or
            (current_time - self.last_keep_alive).total_seconds() > self.keep_alive_interval):

            self.logger.debug("Performing keep-alive interaction...")

            try:
                # BULLETPROOF: Get page with multiple safety checks
                page = await self.session_manager.get_page()

                # BULLETPROOF CHECK 1: Ensure page is not None
                if not page:
                    self.logger.warning("[WARNING] Keep-alive: No page available - attempting light refresh")
                    # Try to get page again before forcing refresh
                    page = await self.session_manager.get_page()

                    if not page:
                        # Only refresh if we truly can't get a page
                        self.logger.warning("[WARNING] Keep-alive: Still no page, attempting session refresh")
                        if await self.session_manager.refresh_session():
                            self.logger.info("[OK] Keep-alive: Session refreshed successfully")
                            page = await self.session_manager.get_page()

                    if not page:
                        self.logger.error("[ERROR] Keep-alive: Still no page after refresh - skipping this cycle")
                        self.keep_alive_failures += 1
                        self._notify_status("keep_alive_failed", {"error": "No page available after refresh"})
                        return

                # BULLETPROOF CHECK 2: Test page validity before use
                try:
                    # Quick health check - this will fail if page is broken
                    await page.evaluate("document.readyState")
                except Exception as health_error:
                    self.logger.warning(f"[WARNING] Keep-alive: Page health check failed: {health_error}")
                    # Try to get a fresh page
                    try:
                        await self.session_manager.refresh_session()
                        page = await self.session_manager.get_page()
                        if page:
                            await page.evaluate("document.readyState")
                        else:
                            raise Exception("No page after refresh")
                    except Exception:
                        self.logger.error("[ERROR] Keep-alive: Page unusable after refresh - skipping")
                        self.keep_alive_failures += 1
                        self._notify_status("keep_alive_failed", {"error": f"Page health check failed: {health_error}"})
                        return

                # BULLETPROOF CHECK 3: Lightweight health check (NO navigation to avoid purchase interference)
                try:
                    if not page:  # Double-check page is still valid
                        raise Exception("Page became None during keep-alive")

                    self.logger.debug("Keep-alive: performing non-intrusive health check...")

                    # Simple health check - validates page is responsive WITHOUT navigation
                    # This prevents interference with purchase thread which needs to navigate freely
                    await page.evaluate("document.readyState")

                    # Mark success
                    self.last_keep_alive = current_time
                    self.last_activity = current_time
                    self.keep_alive_failures = max(0, self.keep_alive_failures - 1)  # Reduce failure count on success
                    self.logger.debug("[OK] Keep-alive health check completed (non-intrusive)")
                    self._notify_status("keep_alive_completed", {"timestamp": current_time.isoformat()})

                except Exception as health_error:
                    self.logger.warning(f"Keep-alive health check failed: {health_error}")
                    self.keep_alive_failures += 1
                    self._notify_status("keep_alive_failed", {"error": f"Health check failed: {health_error}"})
                    return

            except Exception as e:
                self.logger.error(f"[ERROR] Keep-alive interaction failed with unexpected error: {e}")
                self.keep_alive_failures += 1
                self._notify_status("keep_alive_failed", {"error": str(e)})

                # Circuit breaker: if too many consecutive failures, request session restart
                if self.keep_alive_failures >= self.max_failures:
                    self.logger.error("[CRITICAL] CRITICAL: Keep-alive circuit breaker triggered - requesting session restart")
                    self._notify_status("keep_alive_circuit_open", {"consecutive_failures": self.keep_alive_failures})

    async def _check_idle_timeout(self, current_time: datetime):
        """Check if session has been idle too long"""
        if self.last_activity:
            idle_time = (current_time - self.last_activity).total_seconds()
            # Increased idle timeout to reduce unnecessary refreshes
            if idle_time > self.max_idle_time:
                self.logger.warning(f"[WARNING] Session idle for {idle_time/60:.1f} minutes, performing lightweight check")
                # Just perform a light validation WITHOUT navigation (no interference!)
                try:
                    page = await self.session_manager.get_page()
                    if page:
                        # Lightweight check - NO navigation to avoid interfering with purchases
                        await page.evaluate("document.readyState")
                        self.last_activity = current_time
                        self.logger.info("[OK] Idle session validated (non-intrusive)")
                    else:
                        # Only refresh if we can't get a page
                        if await self.session_manager.refresh_session():
                            self.last_activity = current_time
                            self.logger.info("[OK] Idle session refreshed successfully")
                        else:
                            self.logger.error("[ERROR] Idle session refresh failed")
                except Exception as e:
                    self.logger.error(f"[ERROR] Idle check failed: {e}")

                self._notify_status("idle_activity", {"idle_time_minutes": idle_time/60})

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