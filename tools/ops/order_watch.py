#!/usr/bin/env python3
"""Read-only order / ambiguous-commit watcher (OPS-ORDER-PING-1015, 2026-10-15).

The standing rules want an order-history glance within 2 minutes of any order and "at once" on
an [AMBIGUOUS_COMMIT]; the drop runs ~02:00 CT with the operator asleep and no notification path
exists in the repo. This tails the newest logs/runs/run_*.log and prints ONE flushed stdout line per
event, each starting with a class tag so a Monitor can grep it:

    python tools/ops/order_watch.py                       # follow (start at the END of the newest log)
    python tools/ops/order_watch.py --replay <run.log>    # whole file, for tests / dry runs
    options: --logs DIR (default <repo>/logs/runs)  --poll S  --newer-every S  --rate-s S
             --raw-max N  --heartbeat-min M

    ORDER      "[FAST_LANE] *** ORDER PLACED ***", "[API_PLACE_ORDER] Order placed" (also the
               "(HTTP n) but order_id NOT FOUND" variant) and "[PURCHASE] Checkout complete". One line
               per ORDER, never suppressed or rate-limited: two orders in one minute is exactly the
               duplicate that must be seen. An order that carries an order_id is deduped by that id
               (a print+logger pair of the same line, a repeated line); "Checkout complete" follows
               every order line within ~10 lines, so it is absorbed by the order line before it and
               is an ORDER of its own only when no order line came first (the DOM path, an id-less
               order) -- those print "src=checkout-complete-only".
    AMBIGUOUS  every "[AMBIGUOUS_COMMIT]" line (latched, restored from the previous process, "in
               stock but every candidate identity is latched", latch write errors). Never suppressed.
    WONCART    "[WON_CART_DIRECT] start entry=" / "end reason=" only (not the ~230 other lines).
    REBUY      every "[REBUY]" line.
    WONCART / REBUY are limited to 1 line per class per --rate-s (default 60) of LOG time; the
    next printed line of the class carries "+N suppressed", and a closing "WATCH suppressed ..."
    line reports a count that never got a following line. WATCH lines (start, switch, summary,
    errors, heartbeat) are status, not events: they never start with a class tag.

Each event line: `CLASS <log time>[~] <run>:L<line> <acct|near> [oid=..] [+N suppressed]
[seen=<wall clock>] | <raw, trimmed>`. The ORDER lines carry no account name; `acct=` is a tag
inside the event's own line, else `near=<tag>(-nL)` is the nearest preceding single-account tag
within 30 lines (ident=, W<n>/name, [HARVEST/name], <tcin>#W<n>), else `near=?` -- other threads
interleave, so it is a hint, not an attribution (the event store attributes by the thread mark
afterwards: tools/events/q.py orders). `~` = the time is the last logger stamp seen, because
print() lines carry none. L<line> is the physical line number (grep -n, the store's `line`).
`seen=` (follow mode only) is the wall clock when the watcher printed it.

Under the Monitor tool: command `python tools/ops/order_watch.py` (persistent; every line is
flushed as it is printed), notify on a
line matching `^(ORDER|AMBIGUOUS) `; the main session then calls PushNotification. On an ORDER
or AMBIGUOUS line: order-history glance on all accounts at once (standing rule), and
`python tools/events/q.py orders` after the run for the attributed record.

Follow mode starts at the END of the newest run_*.log (no replayed old alerts), re-checks for a
newer run_*.log every --newer-every seconds (a wrapper relaunch), drains the old file, and reads
the new one from byte 0. READ-ONLY by construction: it opens run logs with 'rb', writes nothing,
and never touches the bot, the bat, the session jars or config/. Needs a session that stays open
(Monitor + PushNotification on an ORDER / AMBIGUOUS line); if that dies the watcher dies and
nothing else changes. When nobody can watch, the post-run opens with `q.py orders` plus a raw grep
for [AMBIGUOUS_COMMIT].
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter, deque
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "logs" / "runs"

# Same file-name rule as tools/predrop/boot_check.RUN_NAME / newest_run_log.
RUN_NAME = re.compile(r"run_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.log$")
# tools/events/build.py LOGGER_RE: a physical line is cut at every embedded logger timestamp
# (print and logger output glue together when a write lands mid-line).
LOGGER_RE = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3} "
                       r"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL) (?:[\w.\-]+: )?")

MARK_RE = re.compile(
    r"(?P<FL>(?:\[FAST_LANE\] )?\*\*\* ORDER PLACED \*\*\*)"
    r"|(?P<API>\[API_PLACE_ORDER\] Order placed)"
    r"|(?P<CHK>\[PURCHASE\] Checkout complete)"
    r"|(?P<AMB>\[AMBIGUOUS_COMMIT\])"
    r"|(?P<REBUY>\[REBUY\])"
    r"|(?P<WC>\[WON_CART_DIRECT\] (?:start entry=|end reason=))")
CLASS_OF = {"FL": "ORDER", "API": "ORDER", "CHK": "ORDER", "AMB": "AMBIGUOUS",
            "REBUY": "REBUY", "WC": "WONCART"}
LIMITED = ("WONCART", "REBUY")            # the only classes that may be rate-limited
ORDER_ID = re.compile(r"order_id=([0-9A-Za-z-]{6,40})")

MARK_WORDS = ("ORDER PLACED", "Order placed", "Checkout complete", "AMBIGUOUS_COMMIT",
              "[REBUY]", "WON_CART_DIRECT")      # cheap substring pre-test: MARK_RE only runs on a hit

_NAME = r"[A-Za-z][A-Za-z0-9_]*(?:-\d+)?"
# One scan for the four account-tag forms: ident=[W<n>/]name, W<n>/name, [HARVEST/name], <tcin>#W<n>
TAG_ANY = re.compile(r"\bident=(?:W(?P<iw>\d{1,2})/)?(?P<in>" + _NAME + r")"
                     r"|\bW(?P<ww>\d{1,2})/(?P<wn>" + _NAME + r")"
                     r"|\[HARVEST/(?P<hn>" + _NAME + r")\]"
                     r"|\d{6,12}#W(?P<mw>\d{1,2})(?!\d)")
BAD_NAMES = {"None", "none", "null", "unknown", "nan"}

NEAR_MAX_LINES = 30         # an account tag further back than this is not shown as "near"
DUP_SEG_WINDOW = 4          # a print+logger pair of one message sits within a few segments
CREDIT_SEGS = 600           # an order line's "Checkout complete" credit lives this many segments
CREDIT_S = 60.0             # ... or this many seconds of log time
READ_CHUNK = 4 << 20


def newest_run_log(runs: Path):
    """Newest logs/runs/run_*.log by name (the rule of boot_check.newest_run_log)."""
    best = None
    try:
        for p in Path(runs).iterdir():
            if RUN_NAME.search(p.name) and (best is None or p.name > best.name):
                best = p
    except OSError:
        return None
    return best


def run_logs(runs: Path):
    out = []
    try:
        for p in Path(runs).iterdir():
            if RUN_NAME.search(p.name):
                out.append(p)
    except OSError:
        pass
    return sorted(out, key=lambda p: p.name)


def split_segments(line: str):
    """[(kind, text, stamp|None)], kind 'print' or 'logger'; cut at every embedded logger
    timestamp (glued lines), as tools/events/build.py split_segments does."""
    ms = list(LOGGER_RE.finditer(line))
    if not ms:
        return [("print", line, None)] if line else []
    out = []
    if ms[0].start() > 0:
        out.append(("print", line[:ms[0].start()], None))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(line)
        out.append(("logger", line[m.end():end], m.group(1)))
    return out


def account_tag(text: str):
    """The single account tag a line names (None when it names none or several)."""
    names, ws = set(), set()
    for m in TAG_ANY.finditer(text):
        if m.group("in") is not None:
            if m.group("in") not in BAD_NAMES:
                names.add(m.group("in"))
                if m.group("iw"):
                    ws.add(m.group("iw"))
        elif m.group("wn") is not None:
            if m.group("wn") not in BAD_NAMES:
                names.add(m.group("wn"))
                ws.add(m.group("ww"))
        elif m.group("hn") is not None:
            names.add(m.group("hn"))
        else:
            ws.add(m.group("mw"))
    if len(names) > 1 or len(ws) > 1:
        return None                      # a race/roster line names several accounts
    if names:
        n = next(iter(names))
        return f"W{next(iter(ws))}/{n}" if ws else n
    if ws:
        return f"#W{next(iter(ws))}"
    return None


class Watcher:
    """Feed it physical log lines; it calls emit(str) for each event line."""

    def __init__(self, emit, *, rate_s: float = 60.0, raw_max: int = 300, show_seen: bool = False):
        self.emit_fn = emit
        self.rate_s = float(rate_s)
        self.raw_max = max(40, int(raw_max))
        self.show_seen = show_seen
        self.stats = Counter()
        self.seen_oids = set()
        self.run = "?"
        self.line_no = 0
        self.seg_no = 0
        self.stamp = None
        self._epochs = {}
        self.tag = None
        self.tag_line = 0
        self.recent = deque(maxlen=16)       # [norm, kind, seg_no, paired]
        self.credits = deque()               # (seg_no, log epoch) per order line awaiting its Checkout
        self.lim = {c: {"last": None, "sup": 0} for c in LIMITED}

    # ---- time -------------------------------------------------------------------------
    def log_epoch(self) -> float:
        s = self.stamp
        if s is None:
            return time.time()
        v = self._epochs.get(s)
        if v is None:
            try:
                v = time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
            except (ValueError, OverflowError):
                v = time.time()
            if len(self._epochs) > 64:
                self._epochs.clear()
            self._epochs[s] = v
        return v

    # ---- file / line plumbing ---------------------------------------------------------
    def begin_file(self, name: str, line_base: int = 0) -> None:
        self.run = name[:-4] if name.endswith(".log") else name
        self.line_no = line_base
        self.tag, self.tag_line = None, 0
        self.recent.clear()
        self.credits.clear()

    def feed(self, raw) -> None:
        """One physical line (bytes or str, trailing CR/LF ignored)."""
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", "replace")
        line = raw.rstrip("\r\n")
        self.line_no += 1
        self.stats["lines"] += 1
        for kind, text, stamp in split_segments(line):
            self.seg_no += 1
            if stamp is not None:
                self.stamp = stamp
            if any(k in text for k in MARK_WORDS):
                hits = list(MARK_RE.finditer(text))
                for i, m in enumerate(hits):
                    end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
                    self._hit(kind, m.lastgroup, text[m.start():end])
            t = account_tag(text)
            if t is not None:
                self.tag, self.tag_line = t, self.line_no

    # ---- classification ---------------------------------------------------------------
    def _dup_form(self, kind: str, norm: str) -> bool:
        """True when this hit is the other half of a print+logger pair of one message."""
        for r in self.recent:
            if (not r[3] and r[0] == norm and r[1] != kind
                    and self.seg_no - r[2] <= DUP_SEG_WINDOW):
                r[3] = True
                return True
        self.recent.append([norm, kind, self.seg_no, False])
        return False

    def _take_credit(self) -> bool:
        now_t = self.log_epoch()
        while self.credits and (self.seg_no - self.credits[0][0] > CREDIT_SEGS
                                or now_t - self.credits[0][1] > CREDIT_S):
            self.credits.popleft()
            self.stats["credit_expired"] += 1
        if self.credits:
            self.credits.popleft()
            return True
        return False

    def _hit(self, kind: str, name: str, piece: str) -> None:
        cls = CLASS_OF[name]
        if name == "WC":
            self.stats["wc_start" if "start entry=" in piece else "wc_end"] += 1
        norm = " ".join(piece.split())
        if self._dup_form(kind, norm):
            self.stats["dup_forms"] += 1
            return
        if name in ("FL", "API"):
            m = ORDER_ID.search(piece)
            oid = m.group(1) if m else None
            if oid is not None:
                if oid in self.seen_oids:
                    self.stats["dup_order_id"] += 1
                    return
                self.seen_oids.add(oid)
            self.credits.append((self.seg_no, self.log_epoch()))
            self.stats["order_id" if oid else "order_no_id"] += 1
            self._emit(cls, kind, piece, extra=f"oid={oid or ('NOT_FOUND' if name == 'API' else '?')}")
        elif name == "CHK":
            if self._take_credit():
                self.stats["checkout_absorbed"] += 1
                return
            self.stats["checkout_fallback"] += 1
            self._emit(cls, kind, piece, extra="oid=? src=checkout-complete-only")
        elif cls in LIMITED:
            self._limited(cls, kind, piece)
        else:                                           # AMBIGUOUS: never suppressed
            self.stats["ambiguous"] += 1
            self._emit(cls, kind, piece)

    def _limited(self, cls: str, kind: str, piece: str) -> None:
        st = self.lim[cls]
        t = self.log_epoch()
        if self.rate_s <= 0 or st["last"] is None or abs(t - st["last"]) >= self.rate_s:
            sup, st["sup"], st["last"] = st["sup"], 0, t
            self._emit(cls, kind, piece, sup=sup)
        else:
            st["sup"] += 1
            self.stats[cls.lower() + "_suppressed"] += 1

    # ---- output -----------------------------------------------------------------------
    def _emit(self, cls: str, kind: str, piece: str, *, extra: str = "", sup: int = 0) -> None:
        self.stats[cls.lower() + "_emitted"] += 1
        raw = " ".join(piece.split())
        if len(raw) > self.raw_max:
            raw = raw[:self.raw_max - 3] + "..."
        own = account_tag(piece)
        back = self.line_no - self.tag_line
        if own is not None:
            who = f"acct={own}"
        elif self.tag is not None and back <= NEAR_MAX_LINES:
            who = f"near={self.tag}(-{max(0, back)}L)"
        else:                              # a tag from far back is another thread's: say unknown
            who = "near=?"
            self.stats["near_unknown"] += 1
        when = (self.stamp or "?") + ("" if kind == "logger" else "~")
        parts = [cls, when, f"{self.run}:L{self.line_no}", who]
        if extra:
            parts.append(extra)
        if sup:
            parts.append(f"+{sup} suppressed")
        if self.show_seen:
            parts.append("seen=" + time.strftime("%H:%M:%S"))
        self.say(" ".join(parts) + " | " + raw)

    def say(self, text: str) -> None:
        try:
            self.emit_fn(text)
        except Exception:
            pass                       # a dead pipe must never kill the follow loop

    def status(self, text: str) -> None:
        self.say("WATCH " + text)

    def flush_suppressed(self, now: float, force: bool = False) -> None:
        """Report a suppressed count no later line of its class has carried."""
        for cls in LIMITED:
            st = self.lim[cls]
            if st["sup"] and st["last"] is not None and (force or now - st["last"] >= self.rate_s):
                self.status(f"suppressed {cls}={st['sup']} (rate limit {self.rate_s:g}s; "
                            f"last printed {time.strftime('%H:%M:%S', time.localtime(st['last']))})")
                st["sup"] = 0

    def summary(self) -> str:
        s = self.stats
        return (f"summary lines={s['lines']} ORDER={s['order_emitted']} "
                f"(with_id={s['order_id']} no_id={s['order_no_id']} "
                f"checkout_absorbed={s['checkout_absorbed']} checkout_fallback={s['checkout_fallback']} "
                f"dup_order_id={s['dup_order_id']}) AMBIGUOUS={s['ambiguous_emitted']} "
                f"WONCART emitted={s['woncart_emitted']} suppressed={s['woncart_suppressed']} "
                f"(start={s['wc_start']} end={s['wc_end']}) "
                f"REBUY emitted={s['rebuy_emitted']} suppressed={s['rebuy_suppressed']} "
                f"dup_forms={s['dup_forms']}")


def replay(path: Path, watcher: Watcher) -> None:
    """Whole file, read-only. Closes with a WATCH summary line."""
    watcher.begin_file(Path(path).name)
    with open(path, "rb") as fh:
        for raw in fh:
            watcher.feed(raw)
    watcher.flush_suppressed(0.0, force=True)
    watcher.status(watcher.summary())


def _tail_start(path: Path):
    """(byte offset to follow from, physical lines before it): the END of the file, backed up
    to the start of a half-written last line so that line is read whole when it completes."""
    n, last_nl, pos = 0, -1, 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(READ_CHUNK)
            if not chunk:
                break
            n += chunk.count(b"\n")
            i = chunk.rfind(b"\n")
            if i >= 0:
                last_nl = pos + i
            pos += len(chunk)
    return (last_nl + 1 if pos else 0), n


class Follower:
    """One step() = read what the newest log grew by; every newer_every s look for a newer file."""

    def __init__(self, runs_dir, watcher: Watcher, *, newer_every: float = 30.0,
                 heartbeat_min: float = 0.0):
        self.dir = Path(runs_dir)
        self.w = watcher
        self.newer_every = float(newer_every)
        self.heartbeat_s = float(heartbeat_min) * 60.0
        self.path = None
        self.offset = 0
        self.buf = b""
        self.next_check = 0.0
        self.next_beat = None

    def start(self, now: float = None) -> None:
        now = time.time() if now is None else now
        self.next_check = now + self.newer_every
        if self.heartbeat_s > 0:
            self.next_beat = now + self.heartbeat_s
        p = newest_run_log(self.dir)
        if p is None:
            self.w.status(f"no run_*.log in {self.dir} yet; waiting")
            return
        try:
            self.offset, base = _tail_start(p)
        except OSError as e:
            self.w.status(f"cannot read {p.name} ({type(e).__name__}); retrying")
            return
        self.path, self.buf = p, b""
        self.w.begin_file(p.name, base)
        self.w.status(f"following {p.name} from byte {self.offset} (end of file); "
                      f"newer-file check every {self.newer_every:g}s")

    def _lines(self, data: bytes, final: bool = False) -> int:
        self.buf += data
        parts = self.buf.split(b"\n")
        self.buf = parts.pop()
        if final and self.buf:
            parts.append(self.buf)
            self.buf = b""
        for ln in parts:
            self.w.feed(ln)
        return len(parts)

    def _pump(self, final: bool = False) -> int:
        if self.path is None:
            return 0
        n = 0
        try:
            size = self.path.stat().st_size
            if size < self.offset:                      # truncated / replaced in place
                self.w.status(f"{self.path.name} shrank ({size} < {self.offset}); reading from byte 0")
                self.offset, self.buf = 0, b""
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                while True:
                    chunk = fh.read(READ_CHUNK)
                    if not chunk:
                        break
                    self.offset += len(chunk)
                    n += self._lines(chunk)
        except OSError as e:
            self.w.stats["read_errors"] += 1
            self.w.status(f"read error on {self.path.name}: {type(e).__name__}")
        if final:
            n += self._lines(b"", final=True)
        return n

    def _adopt_first(self) -> int:
        p = newest_run_log(self.dir)
        if p is None:
            return 0
        self.path, self.offset, self.buf = p, 0, b""
        self.w.begin_file(p.name)
        self.w.status(f"following {p.name} from byte 0 (it appeared after the watcher started)")
        return self._pump()

    def _check_newer(self) -> int:
        if self.path is None:
            return 0
        newer = [p for p in run_logs(self.dir) if p.name > self.path.name]
        if not newer:
            return 0
        n = self._pump(final=True)                      # drain the old file to its last byte
        for p in newer[:-1]:                            # relaunches that came and went in between
            self.w.begin_file(p.name)
            try:
                with open(p, "rb") as fh:
                    for raw in fh:
                        self.w.feed(raw)
                        n += 1
            except OSError as e:
                self.w.status(f"read error on {p.name}: {type(e).__name__}")
        last = newer[-1]
        self.w.status(f"switched {self.path.name} -> {last.name} (reading from byte 0)")
        self.path, self.offset, self.buf = last, 0, b""
        self.w.begin_file(last.name)
        return n + self._pump()

    def step(self, now: float = None) -> int:
        now = time.time() if now is None else now
        n = 0
        try:
            n += self._pump() if self.path is not None else self._adopt_first()
            if now >= self.next_check:
                self.next_check = now + self.newer_every
                n += self._check_newer()
            self.w.flush_suppressed(now)
            if self.next_beat is not None and now >= self.next_beat:
                self.next_beat = now + self.heartbeat_s
                name = self.path.name if self.path else "(no log yet)"
                self.w.status(f"alive {name} L{self.w.line_no} ORDER={self.w.stats['order_emitted']} "
                              f"AMBIGUOUS={self.w.stats['ambiguous_emitted']}")
        except Exception as e:                          # never let a surprise end the watch
            self.w.stats["step_errors"] += 1
            if self.w.stats["step_errors"] <= 5:
                self.w.status(f"step error {type(e).__name__}: {e}")
        return n


def _out(text: str) -> None:
    try:
        print(text, flush=True)
    except (BrokenPipeError, ValueError):       # the reader (Monitor) is gone: end the watch quietly
        raise SystemExit(0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only order / ambiguous-commit watcher.")
    ap.add_argument("--logs", default=str(RUNS_DIR), help="run-log directory (default <repo>/logs/runs)")
    ap.add_argument("--replay", metavar="FILE", help="print the events of one whole log file and exit")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between reads in follow mode")
    ap.add_argument("--newer-every", type=float, default=30.0, help="seconds between newer-log checks")
    ap.add_argument("--rate-s", type=float, default=60.0, help="WONCART / REBUY limit, log seconds (0 = off)")
    ap.add_argument("--raw-max", type=int, default=300, help="trim the raw line to this many chars")
    ap.add_argument("--heartbeat-min", type=float, default=0.0, help="print a WATCH alive line every N minutes")
    a = ap.parse_args(argv)

    try:
        if a.replay:
            w = Watcher(_out, rate_s=a.rate_s, raw_max=a.raw_max)
            replay(Path(a.replay), w)
            return 0
        w = Watcher(_out, rate_s=a.rate_s, raw_max=a.raw_max, show_seen=True)
        f = Follower(a.logs, w, newer_every=a.newer_every, heartbeat_min=a.heartbeat_min)
        f.start()
        while True:
            f.step()
            time.sleep(max(0.05, a.poll))
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        return 0
    except OSError as e:
        print(f"WATCH fatal {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
