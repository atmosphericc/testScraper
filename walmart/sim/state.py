"""
SimState — the brain of the Walmart simulator.

Holds per-test, per-item, per-queue state so tests can deterministically
script scenarios like:
  - "after 3 checkTicket calls, transition pending → valid"
  - "after 5 calls, transition to expired"
  - "for the first 2 hashes, return PERSISTED_QUERY_NOT_FOUND"
  - "item X is OOS, item Y is in stock, item Z queues"

Two scope levels:
  - Global default state (set once per test setup)
  - Per-item / per-queue overrides (set for one scenario)

Thread safety: mitmproxy's addon runs in a single event loop, so no locks
needed for the SimState object itself. Tests that mutate state from outside
mitmproxy (e.g., asyncio harness) should hold a single reference and call
methods sequentially before the bot makes a request.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class ItemAvailability(Enum):
    IN_STOCK = "in_stock"
    OOS = "oos"
    THIRD_PARTY = "third_party"
    QUEUED = "queued"           # /ip/<id> redirects to /qp


class QueueState(Enum):
    PENDING = "pending"
    VALID = "valid"
    EXPIRED = "expired"


class AdmissionLikelihood(Enum):
    LIKELY = "likely"
    MODERATE = "moderate"
    UNLIKELY = "unlikely"


@dataclass
class QueueScenario:
    """How a queue should behave for one test run.

    `state_after_n_polls`: list of (poll_number, new_state) transitions.
    Example: [(3, "valid")] means after 3rd checkTicket call, return valid.
                [(2, "expired")] means after 2nd call, return expired.
                [] means stay pending forever.
    """
    queue_id: str = "qa484c0ebd7014"
    item_id: str = "19012610850"
    initial_state: QueueState = QueueState.PENDING
    initial_likelihood: AdmissionLikelihood = AdmissionLikelihood.LIKELY
    next_refresh_relative_time_ms: int = 2000   # short for tests (real Walmart: 36000)
    # Transitions: ordered list of (poll_count_at_or_after, target_state)
    state_transitions: list[tuple[int, QueueState]] = field(default_factory=list)
    # Likelihood transitions: ordered list of (poll_count, target_likelihood)
    likelihood_transitions: list[tuple[int, AdmissionLikelihood]] = field(default_factory=list)
    # Per-poll counter
    poll_count: int = 0

    def current_state(self) -> QueueState:
        """Return the appropriate state given how many polls have happened."""
        latest = self.initial_state
        for threshold, target in self.state_transitions:
            if self.poll_count >= threshold:
                latest = target
        return latest

    def current_likelihood(self) -> AdmissionLikelihood:
        latest = self.initial_likelihood
        for threshold, target in self.likelihood_transitions:
            if self.poll_count >= threshold:
                latest = target
        return latest

    def increment_poll(self) -> None:
        self.poll_count += 1


@dataclass
class HashScenario:
    """Controls how a GraphQL hash behaves.

    `known_hashes`: set of hashes the sim recognizes. Requests with other
    hashes get PersistedQueryNotFound until the client retries with full
    `query` body.
    """
    known_hashes: set[str] = field(default_factory=set)
    # When True, every request returns APQ miss to force fallback retry
    force_miss: bool = False
    # Recorded request log for assertions
    requests_seen: list[dict[str, Any]] = field(default_factory=list)


class SimState:
    """The single source of truth for what the simulator does next."""

    def __init__(self):
        # Per-item availability map (key: item_id, value: ItemAvailability)
        self.items: dict[str, ItemAvailability] = {}
        # Default for unknown items
        self.default_availability: ItemAvailability = ItemAvailability.OOS
        # Active queue scenarios (key: queue_id)
        self.queues: dict[str, QueueScenario] = {}
        # GraphQL hash scenarios per operation name
        self.hashes: dict[str, HashScenario] = {}
        # Recorded request log for tests to inspect
        self.request_log: list[dict[str, Any]] = []
        # The last item_id fetched via /ip/ — used to personalize cart/checkout
        # fixtures so the bot's _read_cart_context() can match by usItemId.
        self.last_ip_item_id: Optional[str] = None
        # PIE: keypair set on init or after pie_setup() call
        self.pie_keypair: Optional[Any] = None       # cryptography RSA priv key
        self.pie_public_key_id: str = "test-pie-key-id-0"
        self.pie_phase: str = "0"
        # PIE: decryption log — tests can verify what CVVs were submitted
        self.pie_decrypted: list[dict[str, Any]] = []
        # Fingerprint probe reports — populated when the probe page POSTs
        # its results (one entry per page load)
        self.fp_reports: list[dict[str, Any]] = []
        # Sim start time for absolute timestamps
        self.start_time = time.time()
        # Initialize PIE keypair so the sim is ready to decrypt out of the box
        self._init_pie_keypair()

    def _init_pie_keypair(self) -> None:
        """Generate a 2048-bit RSA keypair. Done once on init AND on reset()
        so each test scenario has fresh keys (no cross-scenario decryption
        contamination).

        2048 chosen because: matches real Walmart PIE.js typical L value;
        fast enough to generate in ~50ms (1024 risks Cybersource rejection
        in real prod; 3072+ is slow).
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
            self.pie_keypair = rsa.generate_private_key(
                public_exponent=0x10001, key_size=2048,
                backend=default_backend(),
            )
        except ImportError:
            # cryptography lib not installed — sim PIE routes will fail,
            # but the rest of the sim still works for non-PIE tests.
            self.pie_keypair = None

    def get_pie_modulus_hex(self) -> str:
        """Return the RSA modulus n as hex (no 0x prefix, lowercase).
        Used by sim's getkey.js route to construct the var PIE JS body."""
        if self.pie_keypair is None:
            return "00"   # placeholder if cryptography unavailable
        n = self.pie_keypair.public_key().public_numbers().n
        return format(n, "x")

    def decrypt_pie_payload(self, ciphertext_hex: str) -> Optional[str]:
        """Decrypt a hex-encoded PIE ciphertext with the sim's private key.
        Returns the plaintext (e.g. CVV digits) on success, None on failure.

        Tests use this to verify the bot encrypted the expected CVV without
        having to inspect the wire bytes directly.
        """
        if self.pie_keypair is None:
            return None
        try:
            from cryptography.hazmat.primitives.asymmetric import padding
            ciphertext = bytes.fromhex(ciphertext_hex)
            plaintext = self.pie_keypair.decrypt(
                ciphertext, padding.PKCS1v15(),
            )
            return plaintext.decode("ascii", errors="replace")
        except Exception:
            return None

    # ── item availability ────────────────────────────────────────────────

    def set_item(self, item_id: str, availability: ItemAvailability) -> None:
        self.items[item_id] = availability

    def get_item(self, item_id: str) -> ItemAvailability:
        return self.items.get(item_id, self.default_availability)

    # ── queue scenarios ──────────────────────────────────────────────────

    def set_queue(self, scenario: QueueScenario) -> None:
        self.queues[scenario.queue_id] = scenario

    def get_queue(self, queue_id: str) -> Optional[QueueScenario]:
        return self.queues.get(queue_id)

    def advance_queue(self, queue_id: str) -> Optional[QueueScenario]:
        """Increment poll counter, return current scenario. Returns None if
        queue not configured (sim should 404 or pick a default)."""
        q = self.queues.get(queue_id)
        if q is None:
            return None
        q.increment_poll()
        return q

    # ── hash scenarios ───────────────────────────────────────────────────

    def set_hash_scenario(self, op_name: str, scenario: HashScenario) -> None:
        self.hashes[op_name] = scenario

    def is_hash_known(self, op_name: str, hash_value: str) -> bool:
        scenario = self.hashes.get(op_name)
        if scenario is None:
            # If sim has no opinion, default to "known" (don't force miss)
            return True
        if scenario.force_miss:
            return False
        return hash_value in scenario.known_hashes

    def record_graphql_request(
        self, op_name: str, hash_value: Optional[str],
        has_full_query: bool, body: dict[str, Any],
    ) -> None:
        scenario = self.hashes.setdefault(op_name, HashScenario())
        scenario.requests_seen.append({
            "op_name": op_name,
            "hash": hash_value,
            "has_full_query": has_full_query,
            "body_keys": list(body.keys()) if isinstance(body, dict) else None,
            "t": time.time() - self.start_time,
        })

    # ── general request logging ──────────────────────────────────────────

    def log_request(self, **kwargs: Any) -> None:
        kwargs["t"] = time.time() - self.start_time
        self.request_log.append(kwargs)

    # ── fixture loading helpers ──────────────────────────────────────────

    @staticmethod
    def load_fixture(name: str) -> bytes:
        """Read fixture file from walmart/sim/fixtures/<name>.
        Raises FileNotFoundError with a useful message if not present."""
        path = FIXTURES_DIR / name
        if not path.exists():
            raise FileNotFoundError(
                f"Sim fixture not found: {path}. "
                f"Available: {sorted(p.name for p in FIXTURES_DIR.glob('*'))}"
            )
        return path.read_bytes()

    @staticmethod
    def load_fixture_json(name: str) -> dict[str, Any]:
        return json.loads(SimState.load_fixture(name).decode("utf-8"))

    # ── reset for next test ──────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all state for the next test scenario.

        Note: regenerates the PIE keypair so cross-scenario decryption is
        impossible. A test that scripts ctl.reset() between scenarios
        will get a fresh public modulus served by getkey.js.
        """
        self.items.clear()
        self.queues.clear()
        self.hashes.clear()
        self.request_log.clear()
        self.pie_decrypted.clear()
        self.fp_reports.clear()
        self.last_ip_item_id = None
        self.start_time = time.time()
        self._init_pie_keypair()


# Default canonical queue scenario — matches the verbatim real Pokemon TCG
# response from matthew7j2014/walmart-queue-tracker README.
DEFAULT_QUEUE = QueueScenario(
    queue_id="qa484c0ebd7014",
    item_id="19012610850",
    initial_state=QueueState.PENDING,
    initial_likelihood=AdmissionLikelihood.LIKELY,
    next_refresh_relative_time_ms=2000,
)


def build_ticket_response(scenario: QueueScenario) -> dict[str, Any]:
    """Construct a JSON body matching the real walmart-queue-tracker schema."""
    state = scenario.current_state()
    likelihood = scenario.current_likelihood()
    now_ms = int(time.time() * 1000)
    return {
        "site": "usgm",
        "queue": scenario.queue_id,
        "shard": 49,
        "ticket": 2529,
        "state": state.value,
        "expires": now_ms + 600_000,         # 10 min in future
        "signature": "evse+tJEvFgVsOpinrNpD/aBXPv3UHVwqVv7j4wbQkE=",
        "itemId": scenario.item_id,
        "expectedTurnTimeUnixTimestamp": now_ms + 30_000,
        "nextRefreshUnixTimestamp": now_ms + scenario.next_refresh_relative_time_ms,
        "nextRefreshRelativeTime": scenario.next_refresh_relative_time_ms,
        "customMetadata": {
            "admissionLikelihood": likelihood.value,
            "title": "This deal is going fast" if likelihood == AdmissionLikelihood.LIKELY
                     else "Items may sell out",
            "item": {
                "name": "Pokemon TCG Test Item",
                "currentPrice": "$29.97",
                "itemID": scenario.item_id,
            },
        },
    }


def build_qpdata_url(queue_id: str, item_id: str) -> str:
    """Build the qpdata URL-encoded JSON parameter that real Walmart redirects
    to. Matches the format observed in walmart.com/qp?qpdata=... URLs."""
    import urllib.parse
    payload = {
        "queued": True,
        "queue": queue_id,
        "url": f"https://api.waiting-room.walmart.com/issueTicket?queue={queue_id}",
        "customMetadata": {
            "item": {
                "itemID": item_id,
                "name": "Pokemon TCG Test Item",
                "imageURL": "https://i5.walmartimages.com/asr/test.jpeg",
                "currentPrice": "$29.97",
                "itemURL": f"/ip/{item_id}",
            },
        },
    }
    return urllib.parse.quote(json.dumps(payload))
