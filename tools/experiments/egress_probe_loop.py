#!/usr/bin/env python3
"""Exit-IP proof for proxied ACCOUNTS (2026-10-22). The bot's own egress probe covers only the direct-bind (hotspot)
routes; an account on a proxy URL (proxy_ref / proxy_url) gets a local forwarder port 2300x and no public-IP proof.
This tool asks an IP echo service THROUGH each live forwarder port and directly (home), and appends one line per
probe to logs/experiments/egress_probe_<date>.log:

    2026-10-23T00:05:12 port=23002 ip=73.12.x.x#3f9a home=67.184.x.x#b210 same_as_home=0 ms=412
    2026-10-23T00:05:12 port=23000 ip=? (closed)

IPs are masked to two octets plus a 4-hex tag of the full address, so a sticky session that silently changed shows up
as a tag change without the full IP landing in a log that could be pasted somewhere. Exit code 0 = every open port
answered with an IP different from home; 1 otherwise (the first pass only; the loop keeps going).

Usage:
  python tools/experiments/egress_probe_loop.py --once                       # one pass over ports 23000-23007
  python tools/experiments/egress_probe_loop.py --every-min 30 --until 06:30 # background proof for a drop night
  python tools/experiments/egress_probe_loop.py --ports 23002 --once
Bytes: ~1 KB per probe per port. Never prints a credential (it never sees one: the forwarder injects the auth).
"""
import argparse
import datetime as dt
import hashlib
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ops"))
try:
    import home_ip as H  # noqa: E402
except Exception:  # pragma: no cover
    H = None

ECHO = "http://api.ipify.org"
IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _tag(ip: str) -> str:
    return hashlib.sha1(ip.encode()).hexdigest()[:4]


def _mask(ip: str) -> str:
    if not IP_RE.match(ip or ""):
        return "?"
    a, b = ip.split(".")[:2]
    return f"{a}.{b}.x.x#{_tag(ip)}"


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except Exception:
        return False


def _via(port: int | None, timeout: float = 12.0) -> tuple[str, int]:
    """Public IP as seen through 127.0.0.1:<port> (or direct when port is None); ('ERR <type>', ms) on failure."""
    t0 = time.time()
    try:
        if port is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{port}"}))
        with opener.open(urllib.request.Request(ECHO, headers={"User-Agent": "egress-probe"}), timeout=timeout) as r:
            ip = r.read(64).decode("ascii", "replace").strip()
        return (ip if IP_RE.match(ip) else f"ERR bad_echo"), int((time.time() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}", int((time.time() - t0) * 1000)


def one_pass(ports: list[int], out) -> bool:
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    home, hms = _via(None)
    out(f"{stamp} home={_mask(home)} ms={hms}")
    ok = True
    for port in ports:
        if not _port_open(port):
            out(f"{stamp} port={port} ip=? (closed)")
            continue
        ip, ms = _via(port)
        if ip.startswith("ERR"):
            out(f"{stamp} port={port} ip=? err={ip[4:]} home={_mask(home)} ms={ms}")
            ok = False
            continue
        same = int(IP_RE.match(home or "") is not None and ip == home)
        out(f"{stamp} port={port} ip={_mask(ip)} home={_mask(home)} same_as_home={same} ms={ms}")
        if same:
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ports", default="23000-23007", help="port or range, e.g. 23002 or 23000-23007")
    ap.add_argument("--every-min", type=float, default=0.0, help="repeat every N minutes (0 = once)")
    ap.add_argument("--until", default="06:30", help="local HH:MM to stop when looping (at most 9 h)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if "-" in a.ports:
        lo, hi = (int(x) for x in a.ports.split("-", 1))
        ports = list(range(lo, hi + 1))
    else:
        ports = [int(x) for x in a.ports.split(",") if x.strip()]
    log_dir = ROOT / "logs" / "experiments"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"egress_probe_{dt.datetime.now():%Y%m%d}.log"

    def out(line: str) -> None:
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    first = one_pass(ports, out)
    if a.once or a.every_min <= 0:
        return 0 if first else 1
    hh, mm = (int(x) for x in a.until.split(":"))
    now = dt.datetime.now()
    until = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if until <= now:
        until += dt.timedelta(days=1)
    until = min(until, now + dt.timedelta(hours=9))
    while dt.datetime.now() < until:
        time.sleep(max(30.0, a.every_min * 60.0))
        one_pass(ports, out)
    return 0 if first else 1


if __name__ == "__main__":
    sys.exit(main())
