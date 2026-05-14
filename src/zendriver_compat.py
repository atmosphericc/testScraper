"""Idempotent runtime patches for zendriver CDP parser quirks.

Imported once from app.py module top (before any other zendriver-using
module) so the patches are installed BEFORE any Chrome is launched in
this process. Otherwise the patch would race the CDP event listener and
the first browser would still spam KeyErrors.
"""

from __future__ import annotations


def patch_client_security_state() -> None:
    """Chrome 148+ renamed `privateNetworkRequestPolicy` →
    `localNetworkAccessRequestPolicy` in the ClientSecurityState CDP
    payload. Older zendriver builds (which we're pinned to) unconditionally
    access the old key and KeyError on every Network.requestWillBeSentExtraInfo
    event — the listener_loop catches it but logs each one with a full
    traceback, flooding 8h-run logs.

    Inject the renamed field from the new one (or a sensible default)
    before delegating, eliminating the spam at the source. Idempotent.
    """
    from zendriver.cdp import network as _zdn

    _orig = _zdn.ClientSecurityState.from_json
    if getattr(_orig, "_resilient_patched", False):
        return

    def _safe_from_json(cls, json):
        if "privateNetworkRequestPolicy" not in json:
            json = {**json,
                    "privateNetworkRequestPolicy":
                        json.get("localNetworkAccessRequestPolicy", "Allow")}
        return _orig.__func__(cls, json)

    _safe_from_json._resilient_patched = True
    _zdn.ClientSecurityState.from_json = classmethod(_safe_from_json)


# Apply at module import time — caller just does `import src.zendriver_compat`.
patch_client_security_state()
