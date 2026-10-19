#!/usr/bin/env python3
"""Offline loopback test for the NON-DIRECT (Bright Data) path of
src/proxy/local_forwarder.py (readiness audit 2026-10-18, S3).

Production runs every purchase Chrome through ForwarderPool upstreams that dial
brd.superproxy.io with an injected `Proxy-Authorization: Basic ...`. No test drove
that path (the EGRESS tests only cover the DIRECT listener), so a regression in the
FX-1014-EGRESS edit of the file could have broken every non-direct request unseen.

A fake upstream proxy listens on 127.0.0.1 and a ForwarderPool upstream is pointed
at it. Asserted, for the CURRENT module:
  * CONNECT www.target.com:443 reaches the upstream with the exact request bytes,
    Proxy-Authorization: Basic base64(user:pw) included; the 200 is relayed as
    "HTTP/1.1 200 Connection established"; bytes pipe both ways; stats count them
  * plain HTTP has the auth header injected just before the blank line
  * an upstream that refuses the TCP connection gives 502 + stats.errors+1
    (CONNECT and plain HTTP); a 407 from the upstream is relayed + errors+1
  * a malformed CONNECT line gets 400
and a DIFFERENTIAL: the same scenarios run against the pre-EGRESS module
(`git show 73425568:src/proxy/local_forwarder.py`) must give identical wire bytes,
client-visible bytes and stats. A byte difference in the non-direct path is a
regression.

Loopback sockets only: no browser, no Target, no internet, no bot.
Run: python tests/test_forwarder_upstream_path_1018.py
"""
from __future__ import annotations

import asyncio
import base64
import socket
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.proxy.local_forwarder as NEW  # noqa: E402

BASELINE_COMMIT = "73425568"   # last commit before FX-1014-EGRESS touched the file
USER = "brd-customer-x-zone-y-ip-1.2.3.4"
PW = "pw"
AUTH_B64 = base64.b64encode(f"{USER}:{PW}".encode()).decode()

PASSED: list = []
FAILED: list = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


def load_baseline():
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{BASELINE_COMMIT}:src/proxy/local_forwarder.py"],
        capture_output=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"git show failed: {out.stderr.decode(errors='replace')[:200]}")
    mod = types.ModuleType("local_forwarder_baseline_73425568")
    mod.__file__ = "<git:73425568:src/proxy/local_forwarder.py>"
    sys.modules[mod.__name__] = mod
    exec(compile(out.stdout.decode("utf-8"), mod.__file__, "exec"), mod.__dict__)
    return mod


# -- helpers ------------------------------------------------------------------

async def _wait(pred, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


async def _read_head(reader, timeout=5.0):
    """Bytes up to and including the first blank line."""
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)


def _refused_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()          # nothing listens here any more
    return p


class FakeUpstream:
    """Loopback stand-in for the BD superproxy. mode:
       'ok'  : CONNECT -> 200 then echo ("echo:"+data); plain HTTP -> fixed 200 reply
       '407' : answer every request 407 and close"""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.requests: list = []     # raw request head bytes, in arrival order
        self.server = None
        self.port = 0

    async def start(self):
        self.server = await asyncio.start_server(self._h, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), 2.0)
        except Exception:
            pass

    async def _h(self, r, w):
        try:
            head = await _read_head(r)
            self.requests.append(head)
            if self.mode == "407":
                w.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                        b"Proxy-Authenticate: Basic realm=x\r\n\r\n")
                await w.drain()
            elif head.startswith(b"CONNECT "):
                w.write(b"HTTP/1.1 200 Connection established\r\nVia: fake\r\n\r\n")
                await w.drain()
                while True:
                    data = await r.read(65536)
                    if not data:
                        break
                    w.write(b"echo:" + data)
                    await w.drain()
            else:
                w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await w.drain()
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass


async def _start_pool(mod, upstream_port):
    pool = mod.ForwarderPool()
    url = f"http://{USER}:{PW}@127.0.0.1:{upstream_port}"
    pool.add_upstream(url, 0)    # local port 0 = OS picks one
    up = next(iter(pool.upstreams.values()))
    await pool.start_all()
    up.port = up.server.sockets[0].getsockname()[1]
    return pool, up


async def _idle(up):
    await _wait(lambda: up.stats.connections_active == 0)


def _snap(up):
    s = up.stats
    return {"connections_total": s.connections_total, "bytes_in": s.bytes_in,
            "bytes_out": s.bytes_out, "errors": s.errors, "last_error": s.last_error}


async def _read_all(reader, timeout=5.0):
    try:
        return await asyncio.wait_for(reader.read(-1), timeout)
    except asyncio.TimeoutError:
        return b"<TIMEOUT>"


# -- scenarios: each returns an observation dict, run on both modules ------------

async def sc_connect(mod):
    fake = FakeUpstream("ok")
    await fake.start()
    pool, up = await _start_pool(mod, fake.port)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", up.port)
        w.write(b"CONNECT www.target.com:443 HTTP/1.1\r\nHost: www.target.com:443\r\n\r\n")
        await w.drain()
        established = await _read_head(r)
        w.write(b"hello")
        await w.drain()
        echo1 = await asyncio.wait_for(r.readexactly(10), 5.0)
        w.write(b"world!!")
        await w.drain()
        echo2 = await asyncio.wait_for(r.readexactly(12), 5.0)
        w.close()
        await _idle(up)
        return {"upstream_reqs": list(fake.requests), "client_head": established,
                "echo1": echo1, "echo2": echo2, "stats": _snap(up),
                "direct": getattr(up, "direct", False), "pinned_ip": up.pinned_ip, "auth_b64": up.auth_b64}
    finally:
        await pool.stop_all()
        await fake.stop()


async def sc_http(mod):
    fake = FakeUpstream("ok")
    await fake.start()
    pool, up = await _start_pool(mod, fake.port)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", up.port)
        w.write(b"GET http://www.target.com/x?y=1 HTTP/1.1\r\nHost: www.target.com\r\n"
                b"Accept: */*\r\n\r\n")
        await w.drain()
        reply = await _read_all(r)
        w.close()
        await _idle(up)
        return {"upstream_reqs": list(fake.requests), "client_reply": reply, "stats": _snap(up)}
    finally:
        await pool.stop_all()
        await fake.stop()


async def sc_refused_connect(mod):
    pool, up = await _start_pool(mod, _refused_port())
    try:
        r, w = await asyncio.open_connection("127.0.0.1", up.port)
        w.write(b"CONNECT www.target.com:443 HTTP/1.1\r\nHost: www.target.com:443\r\n\r\n")
        await w.drain()
        reply = await _read_all(r, 15.0)
        w.close()
        await _idle(up)
        return {"client_reply": reply, "stats": _snap(up)}
    finally:
        await pool.stop_all()


async def sc_refused_http(mod):
    pool, up = await _start_pool(mod, _refused_port())
    try:
        r, w = await asyncio.open_connection("127.0.0.1", up.port)
        w.write(b"GET http://www.target.com/x HTTP/1.1\r\nHost: www.target.com\r\n\r\n")
        await w.drain()
        reply = await _read_all(r, 15.0)
        w.close()
        await _idle(up)
        return {"client_reply": reply, "stats": _snap(up)}
    finally:
        await pool.stop_all()


async def sc_407(mod):
    fake = FakeUpstream("407")
    await fake.start()
    pool, up = await _start_pool(mod, fake.port)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", up.port)
        w.write(b"CONNECT www.target.com:443 HTTP/1.1\r\nHost: www.target.com:443\r\n\r\n")
        await w.drain()
        reply = await _read_all(r)
        w.close()
        await _idle(up)
        return {"upstream_reqs": list(fake.requests), "client_reply": reply, "stats": _snap(up)}
    finally:
        await pool.stop_all()
        await fake.stop()


async def sc_bad_connect(mod):
    fake = FakeUpstream("ok")
    await fake.start()
    pool, up = await _start_pool(mod, fake.port)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", up.port)
        w.write(b"CONNECT \r\n\r\n")
        await w.drain()
        reply = await _read_all(r)
        w.close()
        await _idle(up)
        return {"upstream_reqs": list(fake.requests), "client_reply": reply, "stats": _snap(up)}
    finally:
        await pool.stop_all()
        await fake.stop()


SCENARIOS = [("connect", sc_connect), ("http", sc_http), ("refused_connect", sc_refused_connect),
             ("refused_http", sc_refused_http), ("407", sc_407), ("bad_connect", sc_bad_connect)]


def run_all(mod):
    async def _go():
        return {name: await fn(mod) for name, fn in SCENARIOS}
    return asyncio.run(_go())


# -- assertions on the CURRENT module --------------------------------------------

EXPECT_CONNECT = (
    b"CONNECT www.target.com:443 HTTP/1.1\r\n"
    b"Host: www.target.com:443\r\n"
    b"Proxy-Authorization: Basic " + AUTH_B64.encode() + b"\r\n"
    b"User-Agent: Mozilla/5.0\r\n"
    b"Proxy-Connection: Keep-Alive\r\n"
    b"\r\n")
EXPECT_HTTP = (
    b"GET http://www.target.com/x?y=1 HTTP/1.1\r\n"
    b"Host: www.target.com\r\n"
    b"Accept: */*\r\n"
    b"Proxy-Authorization: Basic " + AUTH_B64.encode() + b"\r\n"
    b"\r\n")


def assert_current(obs):
    c = obs["connect"]
    check("up_is_non_direct", c["direct"] is False)
    check("up_pinned_ip_parsed", c["pinned_ip"] == "1.2.3.4", c["pinned_ip"])
    check("up_auth_b64_is_user_colon_pw", base64.b64decode(c["auth_b64"]) == f"{USER}:{PW}".encode())
    check("connect_reaches_upstream_once", len(c["upstream_reqs"]) == 1, str(len(c["upstream_reqs"])))
    check("connect_exact_wire_bytes_with_basic_auth",
          c["upstream_reqs"][:1] == [EXPECT_CONNECT], repr(c["upstream_reqs"][:1]))
    check("connect_proxy_authorization_header",
          b"Proxy-Authorization: Basic " + AUTH_B64.encode() + b"\r\n" in c["upstream_reqs"][0])
    check("connect_200_relayed_to_client",
          c["client_head"] == b"HTTP/1.1 200 Connection established\r\n\r\n", repr(c["client_head"]))
    check("connect_bytes_pipe_client_to_upstream_and_back",
          c["echo1"] == b"echo:hello" and c["echo2"] == b"echo:world!!", f"{c['echo1']!r} {c['echo2']!r}")
    check("connect_stats_bytes_out", c["stats"]["bytes_out"] == len(b"hello") + len(b"world!!"), str(c["stats"]))
    check("connect_stats_bytes_in", c["stats"]["bytes_in"] == len(b"echo:hello") + len(b"echo:world!!"), str(c["stats"]))
    check("connect_stats_no_errors", c["stats"]["errors"] == 0 and c["stats"]["connections_total"] == 1, str(c["stats"]))

    h = obs["http"]
    check("http_auth_injected_before_blank_line", h["upstream_reqs"] == [EXPECT_HTTP], repr(h["upstream_reqs"]))
    check("http_reply_relayed", h["client_reply"].endswith(b"\r\n\r\nok")
          and h["client_reply"].startswith(b"HTTP/1.1 200 OK"), repr(h["client_reply"]))
    check("http_stats_no_errors", h["stats"]["errors"] == 0, str(h["stats"]))

    for nm in ("refused_connect", "refused_http"):
        x = obs[nm]
        check(f"{nm}_gives_502", x["client_reply"] == b"HTTP/1.1 502 Bad Gateway\r\n\r\n", repr(x["client_reply"]))
        check(f"{nm}_stats_errors_plus_one", x["stats"]["errors"] == 1, str(x["stats"]))
        check(f"{nm}_last_error_upstream_connect",
              (x["stats"]["last_error"] or "").startswith("upstream_connect:"), str(x["stats"]["last_error"]))

    s = obs["407"]
    check("407_reached_upstream_with_auth", len(s["upstream_reqs"]) == 1
          and b"Proxy-Authorization: Basic " + AUTH_B64.encode() in s["upstream_reqs"][0])
    check("407_status_relayed_to_client",
          s["client_reply"].startswith(b"HTTP/1.1 407 Proxy Authentication Required\r\n"), repr(s["client_reply"]))
    check("407_stats_errors_plus_one", s["stats"]["errors"] == 1, str(s["stats"]))
    check("407_last_error_upstream_status",
          (s["stats"]["last_error"] or "").startswith("upstream_status: HTTP/1.1 407"), str(s["stats"]["last_error"]))

    b = obs["bad_connect"]
    check("bad_connect_gives_400_and_never_dials_upstream",
          b["client_reply"] == b"HTTP/1.1 400 Bad Request\r\n\r\n" and b["upstream_reqs"] == [],
          f"{b['client_reply']!r} {b['upstream_reqs']!r}")


def assert_differential(new_obs, old_obs):
    for name, _ in SCENARIOS:
        n, o = new_obs[name], old_obs[name]
        check(f"diff_{name}_identical_to_{BASELINE_COMMIT}", n == o,
              "\n   new=" + repr(n)[:700] + "\n   old=" + repr(o)[:700])
    check("diff_connect_wire_bytes_identical",
          new_obs["connect"]["upstream_reqs"] == old_obs["connect"]["upstream_reqs"])
    check("diff_http_wire_bytes_identical",
          new_obs["http"]["upstream_reqs"] == old_obs["http"]["upstream_reqs"])


def main():
    new_obs = run_all(NEW)
    assert_current(new_obs)
    try:
        baseline = load_baseline()
    except Exception as e:  # noqa: BLE001
        check("diff_baseline_loadable", False, f"{type(e).__name__}: {e}")
        baseline = None
    if baseline is not None:
        check("diff_baseline_has_no_direct_path", not hasattr(baseline.ForwarderPool, "add_direct"))
        assert_differential(new_obs, run_all(baseline))
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print(f"  FAILED: {f}"[:900])
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
