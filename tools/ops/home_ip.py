"""Record the home line's public IP, so a change can be lined up against a drop.

Why (2026-10-16 frame audit, C-1016-06): the home line's first shots got past the edge
limiter 21/59 on the six nights 09-11..09-30a and 2/69 on 10-02 / 10-09 / 10-16, and the
break is Target-side or network-side (nothing in what we send changed, 10-16 analyst). The home public IP
was never logged anywhere, so whether it changed cannot be answered from our side. (Unexpected shutdowns
are no marker: Kernel-Power 41 fires in the good era too - 09-24, 09-27, 09-28 x2, 09-29.)

    python tools/ops/home_ip.py            # record now, print the masked IP and whether it changed
    python tools/ops/home_ip.py --show     # print the recorded history (masked)

Traffic: one HTTP GET to api.ipify.org over the default route (the same host
tools/ops/egress_check.py uses). The full IP goes ONLY to state/home_ip_history.log, which
is gitignored (the repo is public); stdout and logs get the masked a.b.x.x form. Never raises
out of record(): a scheduled caller must not die on a network hiccup.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "state" / "home_ip_history.log"
HOST = "api.ipify.org"


def mask(ip: str) -> str:
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.x.x" if len(parts) == 4 else ip


def public_ip(timeout: float = 6.0) -> str:
    """The public IP seen over the default route, or 'ERR <type>'."""
    try:
        s = socket.create_connection((HOST, 80), timeout=timeout)
        try:
            s.sendall(f"GET / HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        ip = buf.split(b"\r\n\r\n", 1)[-1].decode("ascii", "replace").strip()[:45]
        parts = ip.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return ip
        return "ERR badbody"
    except Exception as e:
        return f"ERR {type(e).__name__}"


def last_recorded() -> str:
    try:
        lines = [l.split() for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
        for f in reversed(lines):
            if len(f) >= 3 and not f[2].startswith("ERR"):
                return f[2]
    except Exception:
        pass
    return ""


def record(source: str = "manual") -> str:
    """Append '<local time> <source> <ip>' to the history; return a masked one-line summary."""
    try:
        ip = public_ip()
        prev = last_recorded()
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {source} {ip}\n")
        if ip.startswith("ERR"):
            return f"[HOME_IP] lookup failed ({ip})"
        if not prev:
            return f"[HOME_IP] {mask(ip)} (first record)"
        if prev != ip:
            return f"[HOME_IP] CHANGED {mask(prev)} -> {mask(ip)}"
        return f"[HOME_IP] {mask(ip)} (unchanged)"
    except Exception as e:
        return f"[HOME_IP] record failed ({type(e).__name__})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--show", action="store_true", help="print the recorded history, masked")
    ap.add_argument("--source", default="manual")
    a = ap.parse_args(argv)
    if a.show:
        try:
            for line in HISTORY.read_text(encoding="utf-8").splitlines():
                f = line.split()
                if len(f) >= 3:
                    print(f[0], f[1], f[2] if f[2].startswith("ERR") else mask(f[2]))
        except FileNotFoundError:
            print("(no history yet)")
        return 0
    print(record(a.source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
