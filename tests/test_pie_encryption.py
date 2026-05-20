"""
Unit tests for walmart/pie.py — Walmart's PIE.js CVV encryption.

5 core scenarios + edge cases. Pure Python: generates a test RSA keypair,
runs encrypt + decrypt round-trip, asserts output shape matches what
/api/checkout-customer/encrypted-pan expects.

Critical correctness:
  - PKCS1v15 padding (NOT OAEP) — matches Walmart's PIE.js scheme.
  - Ciphertext is non-deterministic (random padding) — two encrypts of
    same plaintext produce different ciphertexts.
  - 3-4 digit CVVs only (Amex is 4, others are 3).

Run: python tests/test_pie_encryption.py
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.backends import default_backend   # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa   # noqa: E402

from walmart.pie import (   # noqa: E402
    DEFAULT_PUBLIC_EXPONENT, EncryptedCvv, PieKey,
    encrypt_cvv, parse_pie_key_js,
)


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


# ── helpers ──────────────────────────────────────────────────────────────


def _gen_test_keypair(bit_length: int = 2048):
    """Generate a fresh RSA keypair for round-trip tests."""
    return rsa.generate_private_key(
        public_exponent=DEFAULT_PUBLIC_EXPONENT,
        key_size=bit_length, backend=default_backend(),
    )


def _build_getkey_js(modulus_hex: str, key_id: str = "test-keyid",
                    phase: str = "0", bit_length: int = 2048) -> str:
    return (
        f'var PIE = {{ K0c: "{key_id}", key_id: "{key_id}", '
        f'phase: "{phase}", L: {bit_length}, k: "{modulus_hex}", c1: 1 }};'
    )


# ── tests ────────────────────────────────────────────────────────────────


def test_pubkey_parse_canonical_shape():
    """parse_pie_key_js handles the canonical Walmart shape."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    js = _build_getkey_js(n_hex)
    key = parse_pie_key_js(js)
    _check("parses non-None", key is not None)
    if key:
        _check("parses key_id", key.key_id == "test-keyid")
        _check("parses phase", key.phase == "0")
        _check("parses bit_length", key.bit_length == 2048)
        _check("parses modulus hex", key.modulus_hex == n_hex.lower())
        _check("public exponent defaults to 65537",
               key.public_exponent == 0x10001)


def test_pubkey_parse_shape_variations():
    """Different field-name conventions / whitespace shouldn't break parsing."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")

    # n instead of k
    js = f'var PIE = {{key_id:"abc",phase:"1",L:2048,n:"{n_hex}"}};'
    key = parse_pie_key_js(js)
    _check("accepts 'n' as modulus field",
           key is not None and key.modulus_hex == n_hex.lower())

    # Lots of whitespace
    js = f'var PIE = {{\n  K0c: "xyz" ,\n  L : 2048 ,\n  k : "{n_hex}"\n}};'
    key = parse_pie_key_js(js)
    _check("tolerates whitespace + newlines",
           key is not None and key.key_id == "xyz")


def test_parse_rejects_missing_fields():
    """Required fields missing → None, not crash."""
    _check("None body → None", parse_pie_key_js(None) is None)
    _check("Empty body → None", parse_pie_key_js("") is None)
    _check("Only key_id → None (no modulus)",
           parse_pie_key_js('var PIE = {key_id: "abc"};') is None)
    _check("Only modulus → None (no key_id)",
           parse_pie_key_js('var PIE = {k: "abcdef"};') is None)


def test_round_trip_3_digit_cvv():
    """Bot encrypts CVV → sim decrypts with private key → plaintext matches."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None
    enc = encrypt_cvv(key, "123")

    _check("encrypted CVV is hex-encoded",
           all(c in "0123456789abcdefABCDEF" for c in enc.encryptedSecurityCode))

    # Decrypt with private key
    ct = bytes.fromhex(enc.encryptedSecurityCode)
    plain = priv.decrypt(ct, padding.PKCS1v15())
    _check("decrypted plaintext is b'123'", plain == b"123",
           detail=f"got {plain!r}")
    _check("key_id flows through", enc.key_id == "test-keyid")
    _check("phase flows through", enc.phase == "0")


def test_round_trip_4_digit_cvv():
    """Amex CVVs are 4 digits."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None
    enc = encrypt_cvv(key, "1234")
    ct = bytes.fromhex(enc.encryptedSecurityCode)
    plain = priv.decrypt(ct, padding.PKCS1v15())
    _check("4-digit decrypted plaintext", plain == b"1234")


def test_output_shape_matches_endpoint_contract():
    """EncryptedCvv.to_dict() shape matches what
    /api/checkout-customer/encrypted-pan expects (encryptedSecurityCode,
    key_id, phase + optional integrityCheck / encryptedPan / encryptedCardNumber)."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None
    enc = encrypt_cvv(key, "555")
    payload = enc.to_dict()
    _check("payload has encryptedSecurityCode",
           "encryptedSecurityCode" in payload and payload["encryptedSecurityCode"])
    _check("payload has key_id", payload.get("key_id") == "test-keyid")
    _check("payload has phase", payload.get("phase") == "0")
    _check("payload has integrityCheck field (None ok)",
           "integrityCheck" in payload)
    _check("payload has encryptedPan field (None ok)",
           "encryptedPan" in payload)
    _check("payload has encryptedCardNumber field (None ok)",
           "encryptedCardNumber" in payload)


def test_wrong_length_cvv_rejected():
    """3 or 4 digits ONLY."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None

    for bad in ("12", "12345", "", "1"):
        try:
            encrypt_cvv(key, bad)
            _check(f"rejects bad-length cvv {bad!r}", False,
                   detail=f"got encrypted output (should have raised)")
        except ValueError:
            _check(f"rejects bad-length cvv {bad!r}", True)
        except Exception as e:
            _check(f"rejects bad-length cvv {bad!r}", False,
                   detail=f"raised {type(e).__name__}: {e}")


def test_non_digit_cvv_rejected():
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None

    for bad in ("abc", "1a3", "12 ", " 12", "1.3"):
        try:
            encrypt_cvv(key, bad)
            _check(f"rejects non-digit cvv {bad!r}", False)
        except ValueError:
            _check(f"rejects non-digit cvv {bad!r}", True)


def test_empty_or_none_cvv_rejected():
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None

    for bad in (None, ""):
        try:
            encrypt_cvv(key, bad)
            _check(f"rejects empty/None cvv {bad!r}", False)
        except (ValueError, TypeError, AttributeError):
            _check(f"rejects empty/None cvv {bad!r}", True)


def test_encryption_non_deterministic():
    """RSA-PKCS1v15 padding includes random bytes per encryption.
    Two encrypts of the same plaintext MUST produce different ciphertexts.

    This guards against accidentally using deterministic padding (which
    would let attackers do a dictionary lookup against known CVVs).
    """
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    key = parse_pie_key_js(_build_getkey_js(n_hex))
    assert key is not None
    enc1 = encrypt_cvv(key, "777")
    enc2 = encrypt_cvv(key, "777")
    _check("two encryptions of same CVV → different ciphertexts",
           enc1.encryptedSecurityCode != enc2.encryptedSecurityCode,
           detail="if these match, padding scheme is deterministic — "
                  "use OAEP or check cryptography library version")

    # But both must still decrypt to the same plaintext
    ct1 = bytes.fromhex(enc1.encryptedSecurityCode)
    ct2 = bytes.fromhex(enc2.encryptedSecurityCode)
    _check("both decrypt to b'777' despite different ciphertexts",
           priv.decrypt(ct1, padding.PKCS1v15()) == b"777"
           and priv.decrypt(ct2, padding.PKCS1v15()) == b"777")


def test_ciphertext_size_matches_modulus():
    """Ciphertext is exactly modulus length (in bytes) — RSA-PKCS1v15 produces
    one ciphertext block per encryption, sized to the key."""
    for bit_length in (1024, 2048, 3072):
        priv = _gen_test_keypair(bit_length)
        n_hex = format(priv.public_key().public_numbers().n, "x")
        key = parse_pie_key_js(_build_getkey_js(n_hex, bit_length=bit_length))
        assert key is not None
        enc = encrypt_cvv(key, "123")
        ct = bytes.fromhex(enc.encryptedSecurityCode)
        expected_bytes = bit_length // 8
        _check(f"{bit_length}-bit key → {expected_bytes}-byte ciphertext",
               len(ct) == expected_bytes,
               detail=f"got {len(ct)} bytes")


def test_modulus_hex_prefix_tolerated():
    """Modulus may come in with '0x' prefix or uppercase. Both should work."""
    priv = _gen_test_keypair()
    n_hex = format(priv.public_key().public_numbers().n, "x")
    for variant in (n_hex, n_hex.upper(), "0x" + n_hex):
        key = parse_pie_key_js(_build_getkey_js(variant))
        assert key is not None
        enc = encrypt_cvv(key, "123")
        ct = bytes.fromhex(enc.encryptedSecurityCode)
        plain = priv.decrypt(ct, padding.PKCS1v15())
        _check(f"variant {variant[:4]}... decrypts to b'123'",
               plain == b"123")


# ── runner ───────────────────────────────────────────────────────────────


TESTS = [
    ("pubkey parse: canonical shape", test_pubkey_parse_canonical_shape),
    ("pubkey parse: shape variations", test_pubkey_parse_shape_variations),
    ("pubkey parse: rejects missing fields", test_parse_rejects_missing_fields),
    ("round-trip: 3-digit CVV", test_round_trip_3_digit_cvv),
    ("round-trip: 4-digit CVV (Amex)", test_round_trip_4_digit_cvv),
    ("output payload shape matches endpoint contract",
     test_output_shape_matches_endpoint_contract),
    ("wrong-length CVV rejected", test_wrong_length_cvv_rejected),
    ("non-digit CVV rejected", test_non_digit_cvv_rejected),
    ("empty/None CVV rejected", test_empty_or_none_cvv_rejected),
    ("encryption non-deterministic (PKCS1v15 padding random)",
     test_encryption_non_deterministic),
    ("ciphertext size = modulus byte length",
     test_ciphertext_size_matches_modulus),
    ("modulus hex prefix tolerated (0x / uppercase)",
     test_modulus_hex_prefix_tolerated),
]


def main():
    print("=" * 70)
    print("PIE.js encryption — unit tests (Day 4)")
    print("=" * 70)
    for name, fn in TESTS:
        print(f"\n=== {name} ===")
        try:
            fn()
        except Exception as e:
            results.append(TestResult(
                name=name, status="FAIL",
                detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}",
            ))

    print()
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:3]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
