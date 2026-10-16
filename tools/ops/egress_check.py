#!/usr/bin/env python3
"""S1-EGRESS-READY: READ-ONLY readiness check for FX-1014-EGRESS (the phone-hotspot
second network line for one account, TARGET_ACCOUNT_EGRESS_BIND).

    python tools/ops/egress_check.py [--alias "Wi-Fi"] [--no-net] [--show-ips] [--json]

Prints GREEN / RED for each precondition the bot's own boot guard enforces, then the
exact next step. Run it before the hotspot soak, and again after joining the hotspot:

  metric     the egress adapter's IPv4 interface metric ranks BEHIND the home default
             route (Wi-Fi's automatic metric equals Ethernet's, 25: joining a hotspot
             first could move the WHOLE PC onto the phone). RED names
             tools\\ops\\hotspot_egress_setup.ps1. Works BEFORE the hotspot is joined.
  wifi_up    the adapter exists and has a usable IPv4 (not 169.254 / loopback)
  route      the bot's own guard (bulletproof_purchase_manager._egress_route_problem)
  public_ip  the public IP seen from a socket bound to the adapter differs from home
  dns        the adapter lists an IPv4 DNS server and it answers an A query sent from
             the adapter's address

Read-only. It changes nothing (no adapter, route, metric, DNS or bat edit), reads no
config and no account, prints no account values, and sends nothing to Target: the only
traffic is one HTTP GET to api.ipify.org over each route and one DNS query for
api.ipify.org to the adapter's DNS server. Public IPs are masked (a.b.x.x) unless
--show-ips. --no-net skips both network probes (they print SKIP and do not count
against the verdict). Exit 0 = GREEN, 1 = RED, 2 = the tool itself failed.

It does not arm anything: set TARGET_ACCOUNT_EGRESS_BIND only on a NON-drop night, then
`python tools/ops/bot_ctl.py restart` and soak (see CURRENT_STATE).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import random
import re
import socket
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_ALIAS = "Wi-Fi"
PROBE_HOST = "api.ipify.org"
SETUP_CMD = 'powershell -ExecutionPolicy Bypass -File tools\\ops\\hotspot_egress_setup.ps1 -Alias "{alias}"'

_IPV4 = re.compile(r"\d{1,3}(\.\d{1,3}){3}")


# ── formatting ───────────────────────────────────────────────────────────────

def fmt_ip(ip, show=False):
    """Private / link-local addresses print in full; public ones as a.b.x.x
    (the home public IP is not worth leaving in a transcript) unless show."""
    try:
        a = ipaddress.ip_address(str(ip))
    except ValueError:
        return str(ip)
    if show or a.is_private or a.is_link_local or a.is_loopback:
        return str(a)
    p = str(a).split(".")
    return f"{p[0]}.{p[1]}.x.x" if len(p) == 4 else str(a)


def _check(name, status, detail="", step=""):
    return {"name": name, "status": status, "detail": detail, "next": step}


# ── the real probes (each is injectable for the offline test) ────────────────

def _bot():
    """The bot's own egress helpers: one source of truth for the guard."""
    import src.purchasing.bulletproof_purchase_manager as bpm
    return bpm


def adapters_ipv4():
    """{adapter name: [IPv4, ...]} via psutil (the bot's own source)."""
    import psutil
    out = {}
    for name, addrs in (psutil.net_if_addrs() or {}).items():
        v4 = [a.address for a in addrs if getattr(a, "family", None) == socket.AF_INET]
        if v4:
            out[str(name)] = v4
    return out


def interface_metric(alias, timeout_s=10.0):
    """(InterfaceMetric:int, automatic:bool) of the adapter's IPv4 interface, or None
    on any error. Read-only Get-NetIPInterface; the alias travels in an env var."""
    try:
        cmd = ("$i = Get-NetIPInterface -InterfaceAlias $env:EGRESS_ALIAS -AddressFamily IPv4 "
               "-ErrorAction Stop | Select-Object -First 1; "
               "'{0}|{1}' -f $i.InterfaceMetric, $i.AutomaticMetric")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, timeout=timeout_s,
                           env={**os.environ, "EGRESS_ALIAS": alias})
        m = re.fullmatch(r"(\d+)\|(\w+)", (r.stdout or "").strip())
        if r.returncode != 0 or not m:
            return None
        return int(m.group(1)), m.group(2).lower() in ("enabled", "true")
    except Exception:
        return None


def default_routes():
    return _bot()._default_routes()


def route_problem(alias):
    return _bot()._egress_route_problem(alias)


def dns_server(alias):
    return _bot()._egress_dns_server(alias)


def http_ip(bind_ip=None, host=PROBE_HOST, timeout=6.0, port=80):
    """The public IPv4 `host` sees for a socket bound to bind_ip (None = the OS
    default route). Raises on any failure or a non-IPv4 body."""
    s = socket.create_connection((host, port), timeout=timeout,
                                 source_address=(bind_ip, 0) if bind_ip else None)
    try:
        s.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    body = buf.split(b"\r\n\r\n", 1)[-1].decode("ascii", "replace").strip()
    if not _IPV4.fullmatch(body):
        raise ValueError("answer was not an IPv4 address")
    return body


def dns_query(server, bind_ip, name=PROBE_HOST, port=53, timeout=3.0):
    """IPv4 A records for `name` from `server`, the query sent from bind_ip. Raises
    on timeout, a mismatched id, a non-zero rcode or an empty answer."""
    qid = random.randrange(0, 65536)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for label in name.strip(".").split("."):
        q += bytes([len(label)]) + label.encode("ascii")
    q += b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        s.bind((bind_ip, 0))
        s.sendto(q, (server, port))
        data, _ = s.recvfrom(4096)
    finally:
        s.close()
    if len(data) < 12:
        raise ValueError("short DNS answer")
    rid, flags, _qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != qid:
        raise ValueError("DNS id mismatch")
    if flags & 0x000F:
        raise ValueError(f"DNS rcode {flags & 0x000F}")
    pos = 12

    def _skip_name(p):
        while True:
            ln = data[p]
            if ln == 0:
                return p + 1
            if ln & 0xC0 == 0xC0:
                return p + 2
            p += 1 + ln
    pos = _skip_name(pos) + 4          # question: name + qtype + qclass
    out = []
    for _ in range(an):
        pos = _skip_name(pos)
        rtype, _rcls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 and rdlen == 4:
            out.append(".".join(str(b) for b in data[pos:pos + 4]))
        pos += rdlen
    if not out:
        raise ValueError("DNS answer held no A record")
    return out


def alias_from_env(adapters):
    """The adapter named by TARGET_ACCOUNT_EGRESS_BIND (the first entry's value: an
    adapter name, or an IPv4 resolved to its adapter). The account ids are never
    returned or printed. None when unset or unresolvable."""
    raw = (os.environ.get("TARGET_ACCOUNT_EGRESS_BIND", "") or "").strip()
    for part in raw.replace(",", ";").split(";"):
        _acct, sep, val = part.partition("=")
        val = val.strip()
        if not sep or not val:
            continue
        if _IPV4.fullmatch(val):
            for name, ips in (adapters or {}).items():
                if val in ips:
                    return name
            continue
        return val
    return None


# ── the checks ───────────────────────────────────────────────────────────────

def _usable_ipv4(ips):
    return [i for i in ips if not i.startswith("169.254.") and not i.startswith("127.")]


def run_checks(alias, deps, net=True, show_ips=False):
    """-> {"alias", "verdict", "checks": [...], "next": str}. Never raises: a probe
    that blows up is a RED check that names the exception type."""
    ck = []
    fmt = lambda ip: fmt_ip(ip, show_ips)  # noqa: E731
    setup = SETUP_CMD.format(alias=alias)

    # adapter list (shared by wifi_up and the bind address)
    adapters, adapters_err = {}, ""
    try:
        adapters = deps.adapters() or {}
    except Exception as e:
        adapters_err = f"{type(e).__name__}"
    mine = next((ips for name, ips in adapters.items() if name.lower() == alias.lower()), None)
    bind_ips = _usable_ipv4(mine or [])
    bind_ip = bind_ips[0] if bind_ips else ""

    # routes, read once: the home default route's effective metric
    try:
        routes = deps.default_routes()
    except Exception:
        routes = None
    home_metric = None
    if routes:
        others = [m for a, m in routes if a.lower() != alias.lower()]
        home_metric = min(others) if others else None

    # 1. metric -- the safety precondition, checkable before the hotspot is joined
    try:
        im = deps.interface_metric(alias)
    except Exception:
        im = None
    if im is None:
        ck.append(_check("metric", "RED",
                         f"could not read the IPv4 interface metric of '{alias}' (no such interface, "
                         f"or PowerShell Get-NetIPInterface failed)",
                         f"check the adapter name (--alias); the setup script lists the adapters: {setup}"))
    elif home_metric is None:
        ck.append(_check("metric", "RED",
                         f"'{alias}' interface metric is {im[0]} ({'automatic' if im[1] else 'fixed'}) but the "
                         f"home default route could not be read to compare against",
                         "run this from a normal Windows PowerShell on the bot PC (Get-NetRoute must work)"))
    elif im[0] <= home_metric:
        ck.append(_check("metric", "RED",
                         f"'{alias}' interface metric {im[0]} ({'automatic' if im[1] else 'fixed'}) ranks with "
                         f"or ahead of the home default route (effective metric {home_metric}): joining a "
                         f"hotspot could move EVERY account and the monitor onto the phone",
                         f"BEFORE joining the hotspot, in an ADMIN PowerShell: {setup}"))
    else:
        ck.append(_check("metric", "GREEN",
                         f"'{alias}' interface metric {im[0]} ({'automatic' if im[1] else 'fixed'}) ranks "
                         f"behind the home default route (effective metric {home_metric})"))

    # 2. wifi_up
    if adapters_err:
        ck.append(_check("wifi_up", "RED", f"could not list the network adapters ({adapters_err})",
                         "pip install psutil (the bot uses it for the same lookup)"))
    elif mine is None:
        ck.append(_check("wifi_up", "RED",
                         f"no adapter named '{alias}' has an IPv4 address (adapters with IPv4: {sorted(adapters)})",
                         "turn the PC's Wi-Fi on and join the phone hotspot from the Windows Wi-Fi menu; "
                         "pass --alias \"<name>\" if the adapter is named differently"))
    elif not bind_ip:
        ck.append(_check("wifi_up", "RED",
                         f"'{alias}' has only a link-local 169.254 / loopback address (not connected, no lease)",
                         "turn the phone hotspot on and join it from the Windows Wi-Fi menu, then re-run"))
    else:
        ck.append(_check("wifi_up", "GREEN", f"'{alias}' has IPv4 {fmt(bind_ip)}"))

    # 3. route -- the bot's own boot guard, verbatim
    try:
        why = deps.route_problem(alias)
    except Exception as e:
        why = f"route guard raised {type(e).__name__}"
    if not why:
        ck.append(_check("route", "GREEN", f"'{alias}' has its own default route ranked behind the home route"))
    else:
        low = why.lower()
        if "no default route" in low:
            step = ("join the phone hotspot from the Windows Wi-Fi menu (Wi-Fi gets its default route only "
                    "when connected), then re-run")
        elif "only default route" in low:
            step = "the home line is down or unplugged: reconnect Ethernet, then re-run"
        elif "could not read" in low or "raised" in low:
            step = "run this from a normal Windows PowerShell on the bot PC (Get-NetRoute must work)"
        else:
            step = f"in an ADMIN PowerShell: {setup}"
        ck.append(_check("route", "RED", why, step))

    # 4. public_ip
    if not net:
        ck.append(_check("public_ip", "SKIP", "skipped (--no-net)"))
    elif not bind_ip:
        ck.append(_check("public_ip", "SKIP", f"needs a usable IPv4 on '{alias}' (wifi_up is not GREEN)"))
    else:
        try:
            home = deps.http_ip(None)
        except Exception as e:
            home = None
            home_err = type(e).__name__
        try:
            via = deps.http_ip(bind_ip)
        except Exception as e:
            via = None
            via_err = type(e).__name__
        if home is None:
            ck.append(_check("public_ip", "RED", f"the home route did not answer {PROBE_HOST} ({home_err})",
                             "check the home internet connection, then re-run"))
        elif via is None:
            ck.append(_check("public_ip", "RED",
                             f"a socket bound to '{alias}' ({fmt(bind_ip)}) did not get an answer from "
                             f"{PROBE_HOST} ({via_err}): the hotspot has no data path",
                             "check the phone has mobile data and the hotspot is on; toggle it off/on and rejoin"))
        elif via == home:
            ck.append(_check("public_ip", "RED",
                             f"public IP via '{alias}' = {fmt(via)} is the SAME as home: the second network is "
                             f"not separate",
                             "the Wi-Fi is on the home router, not the phone hotspot: join the phone hotspot"))
        else:
            ck.append(_check("public_ip", "GREEN",
                             f"public IP via '{alias}' = {fmt(via)}; home = {fmt(home)} (different)"))

    # 5. dns
    try:
        srv = deps.dns_server(alias)
    except Exception:
        srv = ""
    if not srv:
        ck.append(_check("dns", "RED",
                         f"Windows lists no IPv4 DNS server for '{alias}' (the bot would fall back to the OS "
                         f"resolver = the HOME line's DNS for this account)",
                         f"join the hotspot, then re-run; if it persists: Get-DnsClientServerAddress "
                         f"-InterfaceAlias \"{alias}\" -AddressFamily IPv4"))
    elif not net:
        ck.append(_check("dns", "SKIP", f"{fmt(srv)} is listed; live query skipped (--no-net)"))
    elif not bind_ip:
        ck.append(_check("dns", "SKIP", f"{fmt(srv)} is listed; live query needs a usable IPv4 on '{alias}'"))
    else:
        try:
            recs = deps.dns_query(srv, bind_ip)
            ck.append(_check("dns", "GREEN",
                             f"DNS server {fmt(srv)} answered an A query for {PROBE_HOST} from {fmt(bind_ip)} "
                             f"({len(recs)} record(s))"))
        except Exception as e:
            ck.append(_check("dns", "RED",
                             f"DNS server {fmt(srv)} did not answer an A query for {PROBE_HOST} sent from "
                             f"{fmt(bind_ip)} ({type(e).__name__})",
                             "check the phone's data and hotspot; toggle the hotspot off/on and rejoin"))

    red = [c for c in ck if c["status"] == "RED"]
    soft = [c for c in ck if c["status"] == "SKIP"]
    if red:
        verdict, nxt = "RED", red[0]["next"] or "see the RED lines above"
    elif soft and not net:
        verdict = "GREEN"
        nxt = ("offline checks pass; re-run without --no-net for the public-IP and DNS probes before "
               "any soak")
    elif soft:
        verdict, nxt = "RED", "a check was skipped (see SKIP lines); fix the earlier RED and re-run"
    else:
        verdict = "GREEN"
        nxt = ("ready for a soak on a NON-drop night: set TARGET_ACCOUNT_EGRESS_BIND=<account>=<adapter> "
               "(and TARGET_ACCOUNT_EGRESS_AGE_EXEMPT=1), `python tools/ops/bot_ctl.py restart`, follow "
               "boot_check, then soak >= 4.3 h")
    return {"alias": alias, "verdict": verdict, "checks": ck, "next": nxt}


def default_deps():
    return SimpleNamespace(adapters=adapters_ipv4, interface_metric=interface_metric,
                           default_routes=default_routes, route_problem=route_problem,
                           dns_server=dns_server, http_ip=http_ip, dns_query=dns_query)


def render(res):
    lines = [f"EGRESS CHECK  adapter '{res['alias']}'  (read-only; nothing is sent to Target)"]
    for c in res["checks"]:
        lines.append(f"[{c['status']:<5}] {c['name']:<9} {c['detail']}")
        if c["status"] == "RED" and c["next"]:
            lines.append(f"{'':16}next: {c['next']}")
    lines.append(f"VERDICT: {res['verdict']} -- next step: {res['next']}")
    return "\n".join(lines)


def main(argv=None, deps=None):
    ap = argparse.ArgumentParser(description="Read-only FX-1014-EGRESS readiness check.")
    ap.add_argument("--alias", default=None, help='egress adapter name (default: from '
                    'TARGET_ACCOUNT_EGRESS_BIND, else "Wi-Fi")')
    ap.add_argument("--no-net", action="store_true", help="skip the public-IP and DNS network probes")
    ap.add_argument("--show-ips", action="store_true", help="print public IPs in full")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    deps = deps or default_deps()
    try:
        alias = args.alias
        if not alias:
            try:
                alias = alias_from_env(deps.adapters())
            except Exception:
                alias = None
        res = run_checks(alias or DEFAULT_ALIAS, deps, net=not args.no_net, show_ips=args.show_ips)
    except Exception as e:  # noqa: BLE001
        print(f"egress_check: tool error ({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1) if args.json else render(res))
    return 0 if res["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
