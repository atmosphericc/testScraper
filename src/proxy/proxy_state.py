"""
Per-IP proxy state: visitor_id assignment, 403 streak tracking, auto-park,
auto-retest. Persisted atomically to state/proxy_state.json so restarts pick
up where they left off.

Each proxy is identified by its BD-pinned exit IP. State carries:
  - pinned_ip            : the BD exit IP (also serves as primary key)
  - local_port           : the local forwarder port that fronts it
  - visitor_id           : 32-hex Target-style GUID, generated once + stable
  - status               : "active" | "parked" | "burned"
  - consec_403           : current consecutive-403 streak
  - parked_until         : epoch seconds; ≤now means eligible to wake
  - park_count           : how many times we've parked it (for diagnostics)
  - last_status          : last HTTP status observed
  - last_status_at       : epoch seconds of last status update
  - total_success        : cumulative 200 count
  - total_403            : cumulative 403 count
  - created_at           : epoch seconds when state was first created

Policy constants (tuned for Refract-style operation):
  - PARK_AFTER_403_STREAK = 2     # consecutive 403s before parking
  - PARK_DURATION_S      = 600    # 10 min initial park (soft-flagged recovers fast)
  - BURN_AFTER_PARKS     = 4      # after 4 park cycles, mark hard-burned
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Policy
PARK_AFTER_403_STREAK = 2
PARK_DURATION_S = 600           # 10 min
BURN_AFTER_PARKS = 4            # after this many park cycles, give up on the IP
STALE_RETEST_S = 300            # parked IPs retest every 5 min by background loop


def generate_visitor_id() -> str:
    """Target-style 32-hex GUID. The real cookie format from the user's cURL was
    019DF0F3165B0200B07AA4D4DD129A68 — 32 hex chars uppercase. We mimic that."""
    return secrets.token_hex(16).upper()


@dataclass
class ProxyEntry:
    pinned_ip: str
    local_port: int
    visitor_id: str
    status: str = "active"                # active | parked | burned
    consec_403: int = 0
    parked_until: float = 0.0
    park_count: int = 0
    last_status: int = 0
    last_status_at: float = 0.0
    total_success: int = 0
    total_403: int = 0
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyEntry":
        return cls(**d)

    def is_active_now(self, now: float) -> bool:
        if self.status == "burned":
            return False
        if self.status == "parked":
            return self.parked_until <= now
        return True


class ProxyState:
    """Thread-safe, atomic-persisted per-IP state."""

    def __init__(self, state_file: Path):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._entries: dict[str, ProxyEntry] = {}     # keyed by pinned_ip
        self._load()

    # ───────── persistence ─────────

    def _load(self):
        if not self.state_file.exists():
            logger.info(f"[PROXY_STATE] no existing state file; starting fresh")
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            for ip, d in data.items():
                self._entries[ip] = ProxyEntry.from_dict(d)
            logger.info(f"[PROXY_STATE] loaded {len(self._entries)} entries")
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning(f"[PROXY_STATE] failed to load: {e}; starting fresh")
            self._entries = {}

    def _save_locked(self):
        """Atomic JSON write — caller holds the lock."""
        tmp = self.state_file.with_suffix(f".tmp.{os.getpid()}")
        try:
            payload = {ip: entry.to_dict() for ip, entry in self._entries.items()}
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except Exception as e:
            logger.warning(f"[PROXY_STATE] save failed: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass

    def save(self):
        with self._lock:
            self._save_locked()

    # ───────── registration ─────────

    def register(self, pinned_ip: str, local_port: int) -> ProxyEntry:
        """Get-or-create entry for an IP. Generates visitor_id on first call."""
        with self._lock:
            entry = self._entries.get(pinned_ip)
            if entry is None:
                entry = ProxyEntry(
                    pinned_ip=pinned_ip,
                    local_port=local_port,
                    visitor_id=generate_visitor_id(),
                    created_at=time.time(),
                )
                self._entries[pinned_ip] = entry
                logger.info(f"[PROXY_STATE] registered new IP {pinned_ip} port={local_port} "
                            f"visitor_id={entry.visitor_id}")
                self._save_locked()
            else:
                # Update the local_port in case the forwarder remapped ports
                if entry.local_port != local_port:
                    entry.local_port = local_port
                    self._save_locked()
            return entry

    def bulk_register(self, ip_to_port: dict[str, int]):
        for ip, port in ip_to_port.items():
            self.register(ip, port)

    # ───────── status updates ─────────

    def record_status(self, pinned_ip: str, status: int):
        """Record one observation; update streak / park / burn state."""
        with self._lock:
            entry = self._entries.get(pinned_ip)
            if entry is None:
                logger.warning(f"[PROXY_STATE] record_status for unknown IP {pinned_ip}")
                return
            now = time.time()
            entry.last_status = status
            entry.last_status_at = now

            if status == 200:
                entry.total_success += 1
                entry.consec_403 = 0
                # If it was parked and we got a success — unpark
                if entry.status == "parked":
                    logger.info(f"[PROXY_STATE] {pinned_ip} recovered from parked → active")
                    entry.status = "active"
                    entry.parked_until = 0.0
            elif status == 403:
                entry.total_403 += 1
                entry.consec_403 += 1
                if (entry.consec_403 >= PARK_AFTER_403_STREAK
                        and entry.status == "active"):
                    entry.park_count += 1
                    if entry.park_count >= BURN_AFTER_PARKS:
                        entry.status = "burned"
                        logger.warning(
                            f"[PROXY_STATE] {pinned_ip} BURNED after "
                            f"{entry.park_count} park cycles"
                        )
                    else:
                        entry.status = "parked"
                        entry.parked_until = now + PARK_DURATION_S
                        logger.info(
                            f"[PROXY_STATE] {pinned_ip} PARKED for "
                            f"{PARK_DURATION_S}s (park_count={entry.park_count})"
                        )
            self._save_locked()

    # ───────── queries ─────────

    def active_entries(self) -> list[ProxyEntry]:
        """All entries currently usable (active OR parked-but-due)."""
        now = time.time()
        with self._lock:
            out = [e for e in self._entries.values() if e.is_active_now(now)]
            # Sort by lowest 403 rate to bias toward healthy IPs
            out.sort(key=lambda e: (
                -1 if e.status == "active" else 0,
                e.total_403 / max(e.total_success + e.total_403, 1),
                e.last_status_at,
            ))
            return out

    def parked_entries(self) -> list[ProxyEntry]:
        with self._lock:
            return [e for e in self._entries.values() if e.status == "parked"]

    def burned_entries(self) -> list[ProxyEntry]:
        with self._lock:
            return [e for e in self._entries.values() if e.status == "burned"]

    def all_entries(self) -> list[ProxyEntry]:
        with self._lock:
            return list(self._entries.values())

    def stats_summary(self) -> dict:
        with self._lock:
            total = len(self._entries)
            active = sum(1 for e in self._entries.values() if e.status == "active")
            parked = sum(1 for e in self._entries.values() if e.status == "parked")
            burned = sum(1 for e in self._entries.values() if e.status == "burned")
            return {
                "total": total,
                "active": active,
                "parked": parked,
                "burned": burned,
                "total_success": sum(e.total_success for e in self._entries.values()),
                "total_403": sum(e.total_403 for e in self._entries.values()),
            }

    # ───────── retest hook ─────────

    def retest_due(self) -> list[ProxyEntry]:
        """Return parked IPs whose park has expired (ready for retest)."""
        now = time.time()
        with self._lock:
            return [e for e in self._entries.values()
                    if e.status == "parked" and e.parked_until <= now]

    # ───────── manual ops ─────────

    def force_unpark(self, pinned_ip: str):
        with self._lock:
            entry = self._entries.get(pinned_ip)
            if entry and entry.status == "parked":
                entry.status = "active"
                entry.parked_until = 0.0
                entry.consec_403 = 0
                self._save_locked()

    def force_burn(self, pinned_ip: str):
        with self._lock:
            entry = self._entries.get(pinned_ip)
            if entry:
                entry.status = "burned"
                self._save_locked()
