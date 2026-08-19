"""
walmart/queue_events.py — structured observability for the Walmart queue path.

Two independent, env-gated sinks, both zero-cost when their flag is off:

  1. EVENT LOG (WALMART_QUEUE_EVENTLOG=1, default ON):
     One JSON object per line to logs/queue_events_<ts>.jsonl. Every queue
     state transition, race outcome, and ATC/checkout status is recorded with
     a monotonic-derived wall timestamp so a drop can be reconstructed exactly
     ("session s3 entered queue at T, admitted at T+83s, ATC 200 at T+84s").
     Cheap (a dict + a line append), so it defaults ON — an unobserved drop is
     a wasted drop.

  2. RAW CAPTURE (WALMART_QUEUE_CAPTURE=1, default OFF):
     Verbatim request/response bodies for the /qp redirect, the
     api.waiting-room.walmart.com ticket API, and the checkout GraphQL POSTs,
     to logs/queue_capture_<ts>.jsonl. This is the artifact that resolves the
     SIM-ONLY blockers: the real ticket shape + the live GraphQL persisted-query
     hashes. Heavier (full bodies), so it's opt-in for a drop you intend to
     capture. Pass-through only — recording never blocks or mutates the flow.

Both are process-wide singletons keyed off one shared run timestamp so all
sessions racing the same drop write to the same pair of files. The run
timestamp is captured once at first use (Date.now()-free path for workflow
compatibility is irrelevant here — this is the live app, not a workflow).

Design constraints:
  - stdlib only — must never break walmart_app's import chain.
  - never raises into the caller: every public fn is wrapped so a logging bug
    can't kill a purchase. A drop lost to a logging exception is unforgivable.
  - line-buffered append + flush per event so a mid-drop crash still leaves a
    complete, readable log up to the last event.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# ── env gates ────────────────────────────────────────────────────────────

def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def eventlog_enabled() -> bool:
    # Default ON — the event log is cheap and an unobserved drop is wasted.
    return _flag("WALMART_QUEUE_EVENTLOG", "1")


def capture_enabled() -> bool:
    # Default OFF — full-body capture is heavier; opt in for a real drop.
    return _flag("WALMART_QUEUE_CAPTURE", "0")


# ── shared run-scoped file handles ───────────────────────────────────────

_lock = threading.Lock()
_run_ts: Optional[str] = None
_event_fh = None
_capture_fh = None
_start_monotonic: Optional[float] = None
_start_walltime: Optional[float] = None


def _ensure_open():
    """Open the run's files on first use. Caller must hold _lock."""
    global _run_ts, _event_fh, _capture_fh, _start_monotonic, _start_walltime
    if _run_ts is not None:
        return
    _run_ts = time.strftime("%Y%m%d_%H%M%S")
    _start_monotonic = time.monotonic()
    _start_walltime = time.time()
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.debug("[QUEUE_EVENTS] could not make log dir: %s", e)
    if eventlog_enabled():
        try:
            _event_fh = open(_LOG_DIR / f"queue_events_{_run_ts}.jsonl", "a",
                             encoding="utf-8", buffering=1)
            logger.info("[QUEUE_EVENTS] event log → logs/queue_events_%s.jsonl", _run_ts)
        except Exception as e:
            logger.warning("[QUEUE_EVENTS] event log open failed: %s", e)
    if capture_enabled():
        try:
            _capture_fh = open(_LOG_DIR / f"queue_capture_{_run_ts}.jsonl", "a",
                               encoding="utf-8", buffering=1)
            logger.warning("[QUEUE_EVENTS] RAW CAPTURE ON → logs/queue_capture_%s.jsonl", _run_ts)
        except Exception as e:
            logger.warning("[QUEUE_EVENTS] capture open failed: %s", e)


def _rel_ms() -> Optional[int]:
    """Milliseconds since the run started — the drop reconstruction clock."""
    if _start_monotonic is None:
        return None
    return int((time.monotonic() - _start_monotonic) * 1000)


def _write(fh, obj: dict):
    """Append one JSON line + flush. Never raises."""
    if fh is None:
        return
    try:
        fh.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")
        fh.flush()
    except Exception as e:
        logger.debug("[QUEUE_EVENTS] write failed: %s", e)


# ── public: structured event log ─────────────────────────────────────────

def event(kind: str, **fields: Any) -> None:
    """Record one structured queue event as a JSONL line.

    kind: short slug, e.g. 'queue_entered', 'ticket', 'admitted', 'expired',
          'race_start', 'race_won', 'race_lost', 'atc', 'checkout', 'bail'.
    fields: any JSON-serializable extras (item_id, session_id, state,
            likelihood, ticket_num, status_code, url, reason, ...).

    Always safe to call; a no-op (minus the open) when the event log is off.
    Every event carries t_ms (ms since run start) and ts (wall clock) so the
    file alone reconstructs the drop timeline.
    """
    if not eventlog_enabled():
        return
    try:
        with _lock:
            _ensure_open()
            rec = {
                "t_ms": _rel_ms(),
                "ts": time.strftime("%H:%M:%S"),
                "kind": kind,
            }
            rec.update(fields)
            _write(_event_fh, rec)
    except Exception as e:
        logger.debug("[QUEUE_EVENTS] event() failed: %s", e)


# ── public: raw capture ──────────────────────────────────────────────────

def capture(channel: str, url: str = "", body: Any = None, **meta: Any) -> None:
    """Record a raw request/response body for post-drop analysis.

    channel: 'qp_redirect' | 'ticket_api' | 'checkout' | 'atc' | 'error'.
    url:     the request/response URL.
    body:    the raw body — str (kept verbatim) or already-parsed JSON/dict.
    meta:    extras (status_code, method, request_id, item_id, session_id).

    No-op when capture is off. Pass-through only; never blocks the caller.
    Bodies are stored verbatim (truncated only past a hard cap) so the real
    ticket shape and GraphQL hashes survive intact.
    """
    if not capture_enabled():
        return
    try:
        with _lock:
            _ensure_open()
            # Keep bodies whole but cap pathological sizes (a stuck stream).
            if isinstance(body, str) and len(body) > 200_000:
                body = body[:200_000] + f"...<truncated {len(body)} bytes>"
            rec = {
                "t_ms": _rel_ms(),
                "ts": time.strftime("%H:%M:%S"),
                "channel": channel,
                "url": url,
                "body": body,
            }
            rec.update(meta)
            _write(_capture_fh, rec)
    except Exception as e:
        logger.debug("[QUEUE_EVENTS] capture() failed: %s", e)


def run_timestamp() -> Optional[str]:
    """The <ts> shared by this run's event/capture files (None before first use)."""
    return _run_ts
