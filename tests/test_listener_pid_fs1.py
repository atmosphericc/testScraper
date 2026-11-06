#!/usr/bin/env python3
"""Offline regression test for FS1 (2026-11-06): tools/experiments/limkey_guest_probe.listener_pid.

On this Windows / psutil 7.2.2 a freshly-bound 127.0.0.1 listener is reported by
psutil.net_connections with status CONN_NONE (not CONN_LISTEN) even though its owning pid IS
attributed. The old `status == CONN_LISTEN` filter therefore skipped it and returned None, so
attach_check aborted every guest launch with "port held by pid None" (the 37-day-parked
EXP-LIMKEY-INWINDOW blocker). listener_pid now matches a local socket on the port that has no
remote peer (raddr empty) accepting LISTEN or NONE and carrying a pid; attach_check still requires
lp == own, so the ownership guarantee is unchanged.

Loopback sockets only -- no browser, no network to Target, no bot.

    python tests/test_listener_pid_fs1.py
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import limkey_guest_probe as P  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def _listener():
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sk.bind(("127.0.0.1", 0))
    sk.listen(5)
    return sk, sk.getsockname()[1]


def test_finds_own_listener():
    """The fix: our own fresh listener resolves to our pid (returned None before FS1)."""
    sk, port = _listener()
    try:
        # diagnostic: show the status psutil assigns this listener (CONN_NONE is the quirk)
        import psutil
        st = next((str(c.status) for c in psutil.net_connections(kind="tcp")
                   if c.laddr and getattr(c.laddr, "port", None) == port and not c.raddr), "not-enumerated")
        print(f"[info] psutil status for our listener on {port}: {st}")
        lp = P.listener_pid(port)
        check("listener_pid_returns_own_pid", lp == os.getpid(), f"got {lp!r}, want {os.getpid()}")
    finally:
        sk.close()


def test_established_not_mistaken_for_listener():
    """raddr filter: a port we hold only as an ESTABLISHED connection (raddr set) is not a
    listener; the real listener (raddr empty) still resolves to our pid."""
    sk, port = _listener()
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn = None
    try:
        cli.connect(("127.0.0.1", port))
        conn, _ = sk.accept()
        check("listener_resolves_with_established_present", P.listener_pid(port) == os.getpid(),
              f"got {P.listener_pid(port)!r}")
        cport = cli.getsockname()[1]
        check("established_only_port_is_not_a_listener", P.listener_pid(cport) is None,
              f"got {P.listener_pid(cport)!r} for connected-only port {cport}")
    finally:
        if conn:
            conn.close()
        cli.close()
        sk.close()


def test_unused_port_is_none():
    sk, port = _listener()
    sk.close()  # free it
    check("unused_port_returns_none", P.listener_pid(port) is None, f"got {P.listener_pid(port)!r}")


def main() -> int:
    try:
        import psutil
        psutil.net_connections(kind="tcp")  # probe: some locked-down hosts deny this
    except Exception as ex:  # pragma: no cover
        print(f"[SKIP] psutil.net_connections unavailable ({type(ex).__name__}: {ex}) -- cannot test; not a failure")
        return 0
    for t in (test_finds_own_listener, test_established_not_mistaken_for_listener, test_unused_port_is_none):
        try:
            t()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(ex).__name__}: {ex}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print(f"  FAILED: {f}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
