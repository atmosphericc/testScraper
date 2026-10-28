"""Cross-path purchase claim registry.

ONE shared, strictly-exclusive per-(account_id, tcin) claim consulted by BOTH
purchase paths:

  * the main manager  -- src/purchasing/bulletproof_purchase_manager.py
  * the lane pool     -- src/lanes/lane_pool.py

so that at most ONE place-order attempt is live for a given (account_id, tcin)
across both paths at once. Without it, under TARGET_LANE_MODE=1 the same
in-stock TCIN is handed to both paths (app.py:2097 + app.py:2099-2103), each
checks out as the SAME logged-in member account and the SAME saved card, and
two real orders / two charges can land on one account x TCIN. (The lane pool
also fans one flip to every lane of an account, so the lanes can double-fire an
account against themselves -- this registry closes that collision too.)

Engaged ONLY when lanes are armed. app.py constructs one instance under
MODE=1 and injects it into both paths; when no registry is injected the main
path never calls it, so MODE=0 behaviour is byte-for-byte unchanged.

Why not reuse the main path's _worker_reservations? Its idempotency test
(`holder[0] == tcin`, bulletproof_purchase_manager.py:2714) hands a worker back
to a SECOND claimant on the same TCIN -- which is exactly the main-vs-lane
collision. This claim is strictly exclusive per (account_id, tcin): a re-claim
by the SAME holder is idempotent (the main path may re-reserve its own worker
for the same TCIN, and a lane runner may retry), but ANY other holder is
refused until the owner releases.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple

__all__ = ["PurchaseClaimRegistry"]


class PurchaseClaimRegistry:
    """Atomic compare-and-set claim over (account_id, tcin) slots.

    Holder tokens identify the claimant and never collide across paths:
    the main path uses the worker label ("W1/primary"); a lane uses its
    lane_id ("primary@bd0"). Two lanes of one account hold distinct ids,
    so only the first to claim an account x TCIN fires it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (account_id, tcin) -> holder token
        self._claims: Dict[Tuple[str, str], str] = {}

    @staticmethod
    def _key(account_id: str, tcin: str) -> Tuple[str, str]:
        return (str(account_id), str(tcin))

    def try_claim(self, account_id: str, tcin: str, holder: str) -> bool:
        """Grant the slot to `holder` and return True iff it is free or already
        held by `holder` (idempotent for the same holder). Any other current
        holder -> return False and leave the existing claim untouched."""
        key = self._key(account_id, tcin)
        h = str(holder)
        with self._lock:
            cur = self._claims.get(key)
            if cur is None or cur == h:
                self._claims[key] = h
                return True
            return False

    def release(self, account_id: str, tcin: str, holder: str) -> None:
        """Release the slot, but ONLY if `holder` still owns it. Defensive: a
        stale releaser must never free a slot a different holder now owns.
        Never raises."""
        key = self._key(account_id, tcin)
        h = str(holder)
        with self._lock:
            if self._claims.get(key) == h:
                del self._claims[key]

    def release_all_for_holder(self, holder: str) -> int:
        """Release every slot owned by `holder` (e.g. a crashed/retired racer
        or lane). Returns how many were freed. Never raises."""
        h = str(holder)
        freed = 0
        with self._lock:
            for key in [k for k, v in self._claims.items() if v == h]:
                del self._claims[key]
                freed += 1
        return freed

    def owner(self, account_id: str, tcin: str) -> Optional[str]:
        """Current holder token for the slot, or None. Diagnostics only."""
        with self._lock:
            return self._claims.get(self._key(account_id, tcin))

    def active_count(self) -> int:
        with self._lock:
            return len(self._claims)

    def snapshot(self) -> Dict[Tuple[str, str], str]:
        with self._lock:
            return dict(self._claims)
