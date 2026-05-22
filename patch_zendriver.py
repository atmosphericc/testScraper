"""
Re-apply the zendriver hotfix after a venv rebuild.

zendriver 0.15.3's connection.py Transaction.__call__ calls set_result /
set_exception on the transaction's future without checking whether that
future is already done. When a CDP response arrives after a client-side
timeout cancelled the future (or a duplicate delivery), set_result raises
asyncio.InvalidStateError. In listener_loop the response branch calls
tx(**message) UNGUARDED, so that exception propagates out and kills the
CDP listener loop for that browser — the 2026-05-22 audit's
InvalidStateError storm (1,262 occurrences) and 157 session recycles.

Fix: guard Transaction.__call__ with `if self.done(): return`.

venv/ is not version-controlled, so the patch is lost on a venv rebuild.
Run this once after any `pip install` that reinstalls zendriver:

    python patch_zendriver.py

Idempotent — safe to run repeatedly.
"""
import sys
from pathlib import Path

NEEDLE = '''        :param response:
        :return:
        """

        if "error" in response:'''

REPLACEMENT = '''        :param response:
        :return:
        """
        # Guard: a CDP response can arrive after this transaction future is
        # already settled (client-side timeout cancelled it, or a duplicate
        # delivery). set_result / set_exception on a done future raises
        # asyncio.InvalidStateError and kills the listener loop — the
        # 2026-05-22 InvalidStateError storm + session-recycle churn. Drop it.
        if self.done():
            return

        if "error" in response:'''


def main() -> int:
    try:
        import zendriver
    except ImportError:
        print("[patch_zendriver] zendriver not installed in this environment")
        return 1
    conn = Path(zendriver.__file__).parent / "core" / "connection.py"
    if not conn.exists():
        print(f"[patch_zendriver] not found: {conn}")
        return 1
    src = conn.read_text(encoding="utf-8")
    if "InvalidStateError storm" in src:
        print(f"[patch_zendriver] already patched: {conn}")
        return 0
    if NEEDLE not in src:
        print(f"[patch_zendriver] ERROR: anchor not found in {conn}\n"
              f"  zendriver internals changed — apply the `if self.done(): return`\n"
              f"  guard to Transaction.__call__ by hand.")
        return 1
    conn.write_text(src.replace(NEEDLE, REPLACEMENT, 1), encoding="utf-8")
    print(f"[patch_zendriver] patched OK: {conn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
