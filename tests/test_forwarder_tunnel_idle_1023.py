#!/usr/bin/env python3
"""Offline loopback test for FWD_TUNNEL_IDLE_S in src/proxy/local_forwarder.py (2026-10-23).

A fake upstream proxy on 127.0.0.1 answers CONNECT with 200 and then goes SILENT (never answers the bytes the
client sends, never closes) -- the shape of a residential session whose tunnels the vendor has dropped without a
FIN. With the flag off (default) the tunnel stays open for the whole test window (pre-10-23 behaviour, the client
hangs); with FWD_TUNNEL_IDLE_S=1 the forwarder closes both sides within ~2 s, the client reads EOF, and
stats.idle_closes counts 1. A tunnel that keeps moving bytes is NOT closed by the flag.
Loopback only: no browser, no Target, no internet, no bot."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS: list = []
CHECKS: list = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    CHECKS.append(name)
    if not cond:
        FAILS.append(name)


class SilentUpstream:
    """Accepts a CONNECT, answers 200, then (mode 'silent') reads and never replies, or (mode 'echo') echoes bytes."""

    def __init__(self, mode="silent"):
        self.mode, self.server, self.port = mode, None, 0

    async def start(self):
        self.server = await asyncio.start_server(self._h, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=2)
        except Exception:
            pass

    async def _h(self, r, w):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = await r.read(4096)
                if not chunk:
                    return
                head += chunk
            w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await w.drain()
            while True:
                data = await r.read(4096)
                if not data:
                    break
                if self.mode == "echo":
                    w.write(data)
                    await w.drain()
                # silent: swallow, never answer, never close
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass


async def tunnel_through(mod, up_port):
    pool = mod.ForwarderPool()
    port = 0
    import socket
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    pool.add_upstream(f"http://u:p@127.0.0.1:{up_port}", port)
    await pool.start_all()
    up = next(iter(pool.upstreams.values()))
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(b"CONNECT www.example.com:443 HTTP/1.1\r\nHost: www.example.com:443\r\n\r\n")
    await w.drain()
    head = await asyncio.wait_for(r.readline(), timeout=5)
    while True:
        line = await asyncio.wait_for(r.readline(), timeout=5)
        if line in (b"\r\n", b""):
            break
    return pool, up, r, w, head


async def scenario(mod, idle_env, mode, send_every=None, window_s=3.0):
    os.environ["FWD_TUNNEL_IDLE_S"] = idle_env
    try:
        fake = SilentUpstream(mode)
        await fake.start()
        pool, up, r, w, head = await tunnel_through(mod, fake.port)
        w.write(b"hello")
        await w.drain()
        t0 = time.time()
        eof_at = None
        got = b""
        while time.time() - t0 < window_s:
            if send_every and mode == "echo":
                w.write(b"x")
                await w.drain()
            try:
                data = await asyncio.wait_for(r.read(64), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if not data:
                eof_at = time.time() - t0
                break
            got += data
            if send_every:
                await asyncio.sleep(send_every)
        idle_closes = int(getattr(up.stats, "idle_closes", 0))
        try:
            w.close()
        except Exception:
            pass
        await pool.stop_all()
        await fake.stop()
        return head, eof_at, idle_closes, got
    finally:
        os.environ.pop("FWD_TUNNEL_IDLE_S", None)


async def main_async():
    import src.proxy.local_forwarder as mod
    check("flag_off_by_default", mod._tunnel_idle_s() == 0.0, mod._tunnel_idle_s())
    os.environ["FWD_TUNNEL_IDLE_S"] = "abc"
    check("flag_bad_value_is_off", mod._tunnel_idle_s() == 0.0)
    os.environ["FWD_TUNNEL_IDLE_S"] = "1.5"
    check("flag_parses", mod._tunnel_idle_s() == 1.5)
    os.environ.pop("FWD_TUNNEL_IDLE_S", None)

    # 1. flag OFF: a silent upstream leaves the tunnel open for the whole window (pre-10-23 behaviour)
    head, eof_at, idle_closes, _ = await scenario(mod, "0", "silent", window_s=2.5)
    check("off_connect_200", head.startswith(b"HTTP/1.1 200"), head)
    check("off_silent_tunnel_stays_open", eof_at is None and idle_closes == 0, (eof_at, idle_closes))

    # 2. flag ON (1 s): the silent tunnel is closed within ~2 s, the client reads EOF, idle_closes == 1
    head, eof_at, idle_closes, _ = await scenario(mod, "1", "silent", window_s=4.0)
    check("on_connect_200", head.startswith(b"HTTP/1.1 200"), head)
    check("on_silent_tunnel_closed", eof_at is not None and eof_at < 3.0 and idle_closes == 1, (eof_at, idle_closes))

    # 3. flag ON (1 s): a tunnel that keeps moving bytes is NOT closed
    head, eof_at, idle_closes, got = await scenario(mod, "1", "echo", send_every=0.3, window_s=2.5)
    check("on_active_tunnel_kept", eof_at is None and idle_closes == 0 and len(got) >= 3, (eof_at, idle_closes, len(got)))


def main():
    asyncio.run(main_async())
    print(f"\n=== {len(CHECKS) - len(FAILS)}/{len(CHECKS)} passed ===")
    if FAILS:
        print("  FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
