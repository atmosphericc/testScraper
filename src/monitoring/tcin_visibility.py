"""
TCIN visibility state -- shared contract between the resilient checker (writer)
and pre-drop readiness scripts (readers).

Why (2026-08-25 incident): on the 08-24 overnight run 4 of 13 armed TCINs were
ABSENT from every RedSky bulk response (unpublished on Target); a drop on them
could never have been detected, and the only trace was a logger.warning nobody read.

State file: <state_dir>/tcin_visibility.json (schema 1):
  {
    "schema": 1,
    "updated_at": "<local naive ISO>", "updated_at_unix": <float>,
    "run_started_at_unix": <float|null>,
    "configured": [...], "visible": [...], "invisible": [...],   # sorted str TCINs
    "last_seen_unix": {"<tcin>": <float|null>, ...},             # null = never seen
    "verified": true|false,          # false = ground-truth reads were failing (lists = last known)
    "gt_fail_streak": <int>, "verification_failed_since_unix": <float|null>
  }
Semantics: "seen" / "visible" means RedSky returned a product summary carrying that
tcin in some 200 response (sweep or ground-truth) within the grace window; it does
NOT prove the summary was sellable. "invisible" = absent from the cache-bust read
AND unseen for longer than RESILIENT_TCIN_INVISIBLE_GRACE_S (an unpublished TCIN is
never returned at all, which is the 08-24 case). The legacy every-5th-cycle
"[GROUND-TRUTH] N configured TCIN(s) absent" warning is the RAW (undebounced) list
and can name more TCINs than this file.
Stdlib only. Readers must never raise on a missing/unreadable file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILENAME = "tcin_visibility.json"
SCHEMA_VERSION = 1


_WRITE_WARN_EVERY_S = 600.0
_last_write_warn_ts = 0.0


def write_state_atomic(path: Path, payload: dict) -> bool:
    """json.dumps(indent=1) to a per-process tmp then os.replace. Never raises.
    Returns True on success. On Windows os.replace fails while a reader holds the
    file open, so retry once immediately; on failure remove the tmp and log at
    WARNING no more than once per 10 min (a persistently unwritable state dir
    must be visible, but must not spam)."""
    global _last_write_warn_ts
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=1))
            os.replace(tmp, path)
            return True
        except Exception as e:
            last_err = e          # immediate second attempt; never sleep on the
                                  # caller's (event-loop) thread
    try:
        if tmp.exists():
            tmp.unlink()
    except Exception:
        pass
    try:
        now = time.time()
        if now - _last_write_warn_ts >= _WRITE_WARN_EVERY_S:
            _last_write_warn_ts = now
            logger.warning(f"[TCIN-VISIBILITY] state write failed (non-fatal): {last_err}")
        else:
            logger.debug(f"[TCIN-VISIBILITY] state write failed (non-fatal): {last_err}")
    except Exception:
        pass
    return False


def load_state(state_dir: Path) -> Optional[dict]:
    """Return the parsed state dict, or None if missing/unreadable/schema != 1."""
    try:
        path = Path(state_dir) / STATE_FILENAME
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
            return None
        return data
    except Exception as e:
        logger.debug(f"[TCIN-VISIBILITY] state load failed: {e}")
        return None


@dataclass
class VisibilitySummary:
    age_s: float
    configured: list = field(default_factory=list)
    visible: list = field(default_factory=list)
    invisible_enabled: list = field(default_factory=list)   # enabled TCINs absent from RedSky in the last run
    unchecked_enabled: list = field(default_factory=list)   # enabled TCINs the last run never monitored
    stale: bool = False
    verified: bool = True          # False = the run's ground-truth reads were failing
                                   # when the state was last written (lists = last known)
    gt_fail_streak: int = 0
    verification_failed_since_unix: Optional[float] = None


def summarize(state: dict, enabled_tcins: list, now: float,
              stale_after_s: float = 86400.0) -> VisibilitySummary:
    """Cross the last run's visibility state with the CURRENTLY enabled TCINs."""
    enabled = [str(t) for t in enabled_tcins]
    configured = [str(t) for t in (state.get("configured") or [])]
    visible = [str(t) for t in (state.get("visible") or [])]
    invisible = set(str(t) for t in (state.get("invisible") or []))
    configured_set = set(configured)
    try:
        age_s = float(now) - float(state.get("updated_at_unix") or 0.0)
    except Exception:
        age_s = float("inf")
    verified = state.get("verified", True)
    verified = True if verified is None else bool(verified)
    try:
        streak = int(state.get("gt_fail_streak") or 0)
    except Exception:
        streak = 0
    since = state.get("verification_failed_since_unix")
    try:
        since = float(since) if since is not None else None
    except Exception:
        since = None
    return VisibilitySummary(
        age_s=age_s,
        configured=sorted(configured),
        visible=sorted(visible),
        invisible_enabled=sorted(t for t in enabled if t in invisible),
        unchecked_enabled=sorted(t for t in enabled if t not in configured_set),
        stale=age_s > float(stale_after_s),
        verified=verified,
        gt_fail_streak=streak,
        verification_failed_since_unix=since,
    )


def format_invisible_warning(summary: VisibilitySummary) -> list:
    """ASCII-only lines for scripts to print. Empty list when nothing to warn about."""
    lines: list = []
    if not summary.invisible_enabled and not summary.unchecked_enabled and summary.verified:
        return lines
    if summary.age_s == float("inf"):
        age_txt = "unknown age"
    elif summary.age_s >= 3600.0:
        age_txt = f"{summary.age_s / 3600.0:.1f}h old"
    else:
        age_txt = f"{summary.age_s / 60.0:.0f}m old"
    stale_txt = " [STALE - older than the freshness window; re-run the bot to refresh]" if summary.stale else ""
    lines.append("!" * 70)
    lines.append(f"[TCIN-VISIBILITY] last run state is {age_txt}{stale_txt}")
    if not summary.verified:
        lines.append(
            f"[TCIN-VISIBILITY] the last run could NOT verify visibility: {summary.gt_fail_streak} "
            f"consecutive ground-truth reads failed -- the lists below are the last KNOWN state, "
            f"not a fresh verdict (>30 armed TCINs, throttled pool, or no ready session)"
        )
    if summary.invisible_enabled:
        lines.append(
            f"[TCIN-VISIBILITY] {len(summary.invisible_enabled)} enabled TCIN(s) were INVISIBLE to RedSky "
            f"in the last run (absent from the bulk response) -- a drop on them CANNOT be "
            f"detected until Target publishes them: {summary.invisible_enabled}"
        )
        lines.append("[TCIN-VISIBILITY]   verify each number at the source: typo => never fires; "
                     "unpublished => auto-appears mid-run and the bot logs NOW VISIBLE")
    if summary.unchecked_enabled:
        lines.append(
            f"[TCIN-VISIBILITY] {len(summary.unchecked_enabled)} enabled TCIN(s) were NOT monitored "
            f"by the last run (added since) -- visibility unknown until the bot runs: "
            f"{summary.unchecked_enabled}"
        )
    lines.append("!" * 70)
    return lines
