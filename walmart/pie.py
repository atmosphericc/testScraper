"""
Walmart PIE.js (Payment Information Encryption) — pure-Python implementation.

Background
----------
Walmart's checkout encrypts the credit card CVV (and historically PAN) using
RSA before the data is submitted to Cybersource. The public key is served
from securedataweb.walmart.com/pie/v1/wmcom_us_vtg_pie/getkey.js as a JS
object literal:

    var PIE = {
        K0c: "<keyId>",
        key_id: "<keyId>",
        phase: "0",
        L: 2048,          // RSA modulus bit length
        k: "<modulus hex>",
        c1: 1
    };

The bot fetches this JS, parses the modulus + key_id, encrypts the CVV with
RSA-PKCS1v15 padding, and POSTs the hex-encoded ciphertext to
/api/checkout-customer/encrypted-pan (the URL is consistent across Walmart's
guest + authenticated flows).

Why this matters
----------------
DOM-driven CVV entry (typing one character at a time into an iframe) is the
single most-scrutinized form on Walmart's checkout flow. PerimeterX scores
keystroke biometrics aggressively here — inter-key dwell, flight time,
hold-down duration are all measured. By encrypting + POSTing the CVV
directly, we skip the DOM keystroke dance entirely.

References
----------
- bird-bot/walmart_encryption.py (open-source RSA encryption layer)
- Walmart's wmcom_us_vtg_pie bundle (live JS at securedataweb.walmart.com)

Padding scheme: RSA-PKCS1v15 (NOT OAEP). PIE.js predates OAEP popularity
and Cybersource's accept format uses v15. Ciphertext is hex-encoded.

This module is laptop-pure: no JS runtime, no browser dependency. The bot
calls fetch_pie_key(tab) to retrieve the JS body via tab.evaluate, then
encrypt_cvv() runs entirely in Python via the cryptography library.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPublicKey, RSAPublicNumbers,
)
from cryptography.hazmat.backends import default_backend


logger = logging.getLogger("walmart.pie")

# Walmart's PIE.js uses 65537 as the public exponent. The exponent is not
# exposed in the JS object literal — only the modulus (k) and length (L).
DEFAULT_PUBLIC_EXPONENT = 0x10001


# ── data shapes ──────────────────────────────────────────────────────────


@dataclass
class PieKey:
    """A parsed PIE public-key descriptor extracted from getkey.js."""
    key_id: str
    phase: str
    bit_length: int   # 1024, 2048, or 3072 typically
    modulus_hex: str  # the RSA modulus n, hex-encoded
    public_exponent: int = DEFAULT_PUBLIC_EXPONENT


@dataclass
class EncryptedCvv:
    """Output payload of encrypt_cvv() — matches the shape /api/checkout-customer/
    expects in its POST body."""
    encryptedSecurityCode: str   # hex-encoded ciphertext
    key_id: str
    phase: str
    # Real Walmart also includes integrityCheck (HMAC), encryptedPan,
    # encryptedCardNumber. We emit Nones for the omitted fields so the
    # payload shape is wire-compatible.
    integrityCheck: Optional[str] = None
    encryptedPan: Optional[str] = None
    encryptedCardNumber: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "encryptedSecurityCode": self.encryptedSecurityCode,
            "key_id": self.key_id,
            "phase": self.phase,
            "integrityCheck": self.integrityCheck,
            "encryptedPan": self.encryptedPan,
            "encryptedCardNumber": self.encryptedCardNumber,
        }


# ── parser ───────────────────────────────────────────────────────────────


def parse_pie_key_js(js_body: str) -> Optional[PieKey]:
    """Parse a getkey.js response into a PieKey descriptor.

    Accepts the canonical Walmart shape:
      var PIE = {K0c:"...",key_id:"...",phase:"0",L:2048,k:"...hex...",c1:1};

    Whitespace and field order tolerated. Returns None if any required
    field is missing.
    """
    if not js_body:
        return None

    def _grab(field: str) -> Optional[str]:
        # Match field name in JS object literal, allow single/double quotes,
        # accept unquoted numbers. Field names may or may not be quoted.
        pattern = rf'["\']?{re.escape(field)}["\']?\s*:\s*(?:"([^"]*)"|\'([^\']*)\'|(\d+))'
        m = re.search(pattern, js_body)
        if not m:
            return None
        # First non-None group
        return next((g for g in m.groups() if g is not None), None)

    key_id = _grab("K0c") or _grab("key_id") or _grab("keyId")
    phase = _grab("phase")
    bit_length_str = _grab("L")
    # Walmart uses `k` for modulus; some bundles use `n`
    modulus_hex = _grab("k") or _grab("n")

    if not key_id or not modulus_hex:
        logger.warning(
            "[PIE] parse_pie_key_js missing required fields: "
            f"key_id={'yes' if key_id else 'NO'} modulus={'yes' if modulus_hex else 'NO'}"
        )
        return None

    try:
        bit_length = int(bit_length_str) if bit_length_str else (len(modulus_hex) * 4)
    except (TypeError, ValueError):
        bit_length = len(modulus_hex) * 4

    # Strip optional 0x prefix or whitespace from modulus
    modulus_hex = modulus_hex.strip().lower().removeprefix("0x")

    return PieKey(
        key_id=key_id,
        phase=phase or "0",
        bit_length=bit_length,
        modulus_hex=modulus_hex,
    )


# ── encryption ───────────────────────────────────────────────────────────


def _build_public_key(key: PieKey) -> RSAPublicKey:
    """Build a cryptography RSAPublicKey from the PIE descriptor.

    The modulus n is hex-decoded to int; public_exponent defaults to 65537
    (PIE.js doesn't expose it; this is the universal RSA choice).
    """
    try:
        modulus_int = int(key.modulus_hex, 16)
    except ValueError as e:
        raise ValueError(f"PIE modulus is not valid hex: {e}")

    pub_numbers = RSAPublicNumbers(
        e=key.public_exponent, n=modulus_int,
    )
    return pub_numbers.public_key(default_backend())


def encrypt_cvv(key: PieKey, cvv: str) -> EncryptedCvv:
    """Encrypt the CVV with the given PIE public key.

    Raises ValueError on malformed inputs (empty cvv, wrong-shape modulus).
    Returns an EncryptedCvv with hex-encoded ciphertext + key_id + phase.

    The encryption is **non-deterministic** by design — RSA-PKCS1v15
    padding includes random bytes per encryption operation. Two calls
    with the same CVV produce different ciphertexts. This is correct
    behavior; tests should NOT rely on byte-identical output.
    """
    if not cvv or not isinstance(cvv, str):
        raise ValueError(f"cvv must be a non-empty string, got {type(cvv).__name__}")

    # CVV must be 3 or 4 digits (Amex is 4)
    cvv_stripped = cvv.strip()
    if not cvv_stripped.isdigit() or len(cvv_stripped) not in (3, 4):
        raise ValueError(
            f"cvv must be 3-4 digits, got {len(cvv_stripped)} chars (digits-only={cvv_stripped.isdigit()})"
        )

    pubkey = _build_public_key(key)

    # PKCS1v15 padding — what Walmart's PIE uses. NOT OAEP.
    # OAEP is more secure but Walmart's stack predates its adoption and
    # the Cybersource backend expects PKCS1v15.
    ciphertext = pubkey.encrypt(
        cvv_stripped.encode("ascii"),
        padding.PKCS1v15(),
    )

    return EncryptedCvv(
        encryptedSecurityCode=ciphertext.hex(),
        key_id=key.key_id,
        phase=key.phase,
    )


# ── tab integration ──────────────────────────────────────────────────────


async def fetch_pie_key(tab) -> Optional[PieKey]:
    """Fetch getkey.js via the tab's fetch context and parse out the
    public key descriptor.

    Runs `fetch('https://securedataweb.walmart.com/pie/v1/wmcom_us_vtg_pie/getkey.js')`
    inside the browser tab so cookies and origin are real-Walmart-grade.
    Returns None on fetch / parse failure.

    The bot's checkout flow calls this once per checkout (the key is
    valid for many minutes; no point re-fetching).
    """
    import json as _json
    js = """
        (async () => {
            try {
                const r = await fetch('https://securedataweb.walmart.com/pie/v1/wmcom_us_vtg_pie/getkey.js', {
                    credentials: 'include',
                });
                if (!r.ok) return {error: 'http_' + r.status};
                const text = await r.text();
                return {ok: true, body: text};
            } catch (e) {
                return {error: String(e).slice(0, 200)};
            }
        })()
    """
    try:
        res = await tab.evaluate(js, await_promise=True)
    except TypeError:
        res = await tab.evaluate(js)
    except Exception as e:
        logger.warning(f"[PIE] fetch_pie_key tab.evaluate raised: {e}")
        return None

    if not isinstance(res, dict) or not res.get("ok"):
        logger.warning(f"[PIE] fetch_pie_key failed: {res}")
        return None

    body = res.get("body", "")
    key = parse_pie_key_js(body)
    if key is None:
        logger.warning(
            f"[PIE] could not parse getkey.js (body preview: {body[:200]!r})"
        )
    else:
        logger.info(
            f"[PIE] key fetched: key_id={key.key_id} phase={key.phase} "
            f"L={key.bit_length}"
        )
    return key
