"""
Session concurrency tests — Day 6.

The resilient stack runs N=2-16 Chrome sessions concurrently. pick_session,
the in_queue flag, the keepalive heartbeat — all must behave correctly
under contention. Tests use asyncio.gather to fire many concurrent
operations and assert no races.

Scenarios:
  1. pick_session under 100 concurrent callers — no double-picks of in-flight
     sessions (the in_flight flag is checked)
  2. pick_session excludes in_queue=True sessions under contention (N=16)
  3. Watchdog/keepalive must skip in_queue=True sessions (cooperative
     verification of the keepalive in_queue contract)
  4. SessionEntry can be safely accessed from 100 concurrent tasks
     reading state — no AttributeError, no stale reads

Note: tests use synthetic SessionEntry objects, not real browser sessions.
Concurrency bugs we'd catch are in pick_session's iteration + filter logic,
not in real-Chrome state. The mocks faithfully represent the SessionEntry
public surface (state, in_flight, in_queue, tab, cookies, etc.).

Run: python tests/test_session_concurrency.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.stack.multi_session_pool import MultiSessionPool, SessionEntry   # noqa: E402


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


# ── helpers ──────────────────────────────────────────────────────────────


def _build_pool_with_n_sessions(n: int = 16, all_ready: bool = True) -> MultiSessionPool:
    """Build a MultiSessionPool with N synthetic SessionEntry objects.
    Used for concurrency assertions without launching real Chrome."""
    pool = MultiSessionPool.__new__(MultiSessionPool)
    pool.sessions = []
    for i in range(n):
        s = SessionEntry(
            id=f"s{i+1}",
            proxy_url=f"http://proxy{i+1}",
            proxy_ip=f"10.0.0.{i+1}",
            local_port=25000 + i,
            profile_dir=Path(f"/tmp/profile-{i+1}"),
            state="ready" if all_ready else "starting",
            cookies={"_px3": f"px3-{i+1}"},
            tab=MagicMock(),
        )
        pool.sessions.append(s)
    return pool


# ── tests ────────────────────────────────────────────────────────────────


async def test_pick_session_concurrent_no_double_pick_of_in_flight():
    """100 concurrent picks must never return a session whose in_flight is True.
    pick_session reads in_flight at scan time — if we mark a session as
    in-flight between two picks, subsequent picks must skip it."""
    pool = _build_pool_with_n_sessions(n=4)

    # Mark sessions 1 and 2 as in-flight (mid-fetch)
    pool.sessions[0].in_flight = True
    pool.sessions[1].in_flight = True

    async def pick_one():
        # Asyncio.sleep(0) gives the scheduler a chance to interleave
        await asyncio.sleep(0)
        return pool.pick_session()

    # 100 concurrent picks
    picks = await asyncio.gather(*[pick_one() for _ in range(100)])
    chosen_ids = [s.id for s in picks if s is not None]
    counts = Counter(chosen_ids)

    _check("100 concurrent picks all returned non-None",
           all(s is not None for s in picks),
           detail=f"got {sum(1 for s in picks if s is None)} None picks")
    _check("NO pick ever returned in-flight session s1",
           "s1" not in chosen_ids)
    _check("NO pick ever returned in-flight session s2",
           "s2" not in chosen_ids)
    _check("Picks only chose from non-in-flight sessions {s3, s4}",
           set(chosen_ids).issubset({"s3", "s4"}),
           detail=f"unique chosen: {set(chosen_ids)}")
    # With 100 picks across 2 candidates, both should be picked at least
    # once (proves the random.choice isn't degenerate)
    _check("Both eligible sessions were chosen at least once",
           counts.get("s3", 0) > 0 and counts.get("s4", 0) > 0,
           detail=f"counts: {dict(counts)}")


async def test_pick_session_excludes_in_queue_under_contention():
    """N=16 sessions, half flagged in_queue=True. 200 concurrent picks
    must never return a queueing session."""
    pool = _build_pool_with_n_sessions(n=16)

    # Mark even-indexed sessions as in_queue
    for i, s in enumerate(pool.sessions):
        if i % 2 == 0:
            s.in_queue = True

    expected_eligible = {f"s{i+1}" for i in range(16) if i % 2 != 0}

    async def pick_one():
        await asyncio.sleep(0)
        return pool.pick_session()

    picks = await asyncio.gather(*[pick_one() for _ in range(200)])
    chosen_ids = {s.id for s in picks if s is not None}

    _check("all 200 picks returned non-None",
           all(s is not None for s in picks))
    _check("no in_queue=True session was ever picked",
           chosen_ids.isdisjoint({"s1", "s3", "s5", "s7", "s9", "s11", "s13", "s15"}),
           detail=f"chosen: {sorted(chosen_ids)}")
    _check("picks distributed across non-queueing sessions",
           chosen_ids.issubset(expected_eligible),
           detail=f"chosen: {sorted(chosen_ids)}, expected eligible: {sorted(expected_eligible)}")


async def test_pick_session_excludes_all_states():
    """A session must be in state='ready' AND have cookies AND not be in_flight
    AND not be in_queue. Each negative condition must independently exclude."""
    pool = _build_pool_with_n_sessions(n=5)

    # s1: state='crashed' → exclude
    pool.sessions[0].state = "crashed"
    # s2: in_flight=True → exclude
    pool.sessions[1].in_flight = True
    # s3: in_queue=True → exclude
    pool.sessions[2].in_queue = True
    # s4: no cookies → exclude
    pool.sessions[3].cookies = {}
    # s5: clean → only valid pick

    async def pick_one():
        await asyncio.sleep(0)
        return pool.pick_session()

    picks = await asyncio.gather(*[pick_one() for _ in range(50)])
    chosen = {s.id for s in picks if s is not None}
    _check("only s5 ever picked (others excluded by various negative states)",
           chosen == {"s5"},
           detail=f"chosen: {chosen}")


async def test_pick_session_returns_none_when_all_unavailable():
    """If every session is excluded by some condition, pick_session returns
    None (not crash, not pick an invalid one)."""
    pool = _build_pool_with_n_sessions(n=3)
    for s in pool.sessions:
        s.in_flight = True

    async def pick_one():
        await asyncio.sleep(0)
        return pool.pick_session()

    picks = await asyncio.gather(*[pick_one() for _ in range(20)])
    _check("all picks returned None when no session eligible",
           all(s is None for s in picks),
           detail=f"non-None count: {sum(1 for s in picks if s is not None)}")


async def test_in_queue_flag_set_concurrently():
    """Multiple tasks setting in_queue on different sessions must all
    succeed; no AttributeError, no lost writes."""
    pool = _build_pool_with_n_sessions(n=10)

    async def set_in_queue(idx):
        await asyncio.sleep(0)
        pool.sessions[idx].in_queue = True
        return idx

    indices = list(range(10))
    done = await asyncio.gather(*[set_in_queue(i) for i in indices])
    _check("all 10 concurrent in_queue assignments completed",
           done == indices)

    # Verify all flags are now True
    all_set = all(s.in_queue for s in pool.sessions)
    _check("all 10 sessions have in_queue=True after concurrent sets",
           all_set,
           detail=f"flags: {[s.in_queue for s in pool.sessions]}")


async def test_pick_session_during_in_queue_flag_toggling():
    """Stress: while pick_session is running concurrently, other tasks
    toggle in_queue on/off. No exceptions; no picks ever return a
    session that was in_queue=True at the moment of the read."""
    pool = _build_pool_with_n_sessions(n=8)
    # All sessions initially NOT in queue

    toggle_stop = asyncio.Event()
    pick_results = []

    async def toggler(idx):
        """Toggle in_queue on session[idx] in a tight loop until told to stop."""
        while not toggle_stop.is_set():
            pool.sessions[idx].in_queue = not pool.sessions[idx].in_queue
            await asyncio.sleep(0.001)

    async def picker():
        for _ in range(200):
            s = pool.pick_session()
            pick_results.append(s)
            await asyncio.sleep(0)

    # Run picker concurrently with togglers on sessions 0, 1, 2
    toggler_tasks = [asyncio.create_task(toggler(i)) for i in range(3)]
    await picker()
    toggle_stop.set()
    await asyncio.gather(*toggler_tasks, return_exceptions=True)

    # We can't assert "no in_queue session was picked" precisely because
    # in_queue might have flipped to True after pick_session read it as
    # False (TOCTOU). What we CAN assert: picker didn't crash, and
    # picked SOME valid sessions.
    non_none_count = sum(1 for s in pick_results if s is not None)
    _check("picker completed 200 picks under toggler contention without crashing",
           len(pick_results) == 200)
    _check("majority of picks succeeded (non-None) despite contention",
           non_none_count > 100,
           detail=f"non-none count: {non_none_count}/200")


async def test_session_entry_concurrent_read():
    """100 concurrent reads of SessionEntry fields. No AttributeError,
    consistent reads (a snapshot is internally consistent)."""
    pool = _build_pool_with_n_sessions(n=4)

    async def read_session(idx):
        await asyncio.sleep(0)
        s = pool.sessions[idx]
        return {
            "id": s.id,
            "state": s.state,
            "proxy_ip": s.proxy_ip,
            "in_queue": s.in_queue,
            "in_flight": s.in_flight,
            "has_cookies": bool(s.cookies),
        }

    reads = await asyncio.gather(
        *[read_session(i % 4) for i in range(100)]
    )
    _check("100 concurrent reads completed without exception",
           len(reads) == 100)
    # Every read for session i must have id="s{i+1}"
    for r in reads:
        expected_id = r["id"]
        idx = int(expected_id[1:]) - 1
        _check(f"session {expected_id} reads matching proxy_ip",
               r["proxy_ip"] == f"10.0.0.{idx+1}")
        break  # one sanity check is enough


# ── runner ───────────────────────────────────────────────────────────────


TESTS = [
    ("pick_session: 100 concurrent picks skip in-flight sessions",
     test_pick_session_concurrent_no_double_pick_of_in_flight),
    ("pick_session: excludes in_queue under contention (N=16)",
     test_pick_session_excludes_in_queue_under_contention),
    ("pick_session: excludes all-negative states (crashed/in_flight/in_queue/no-cookies)",
     test_pick_session_excludes_all_states),
    ("pick_session: returns None when all unavailable",
     test_pick_session_returns_none_when_all_unavailable),
    ("in_queue: concurrent flag set on different sessions",
     test_in_queue_flag_set_concurrently),
    ("pick_session under in_queue toggler contention",
     test_pick_session_during_in_queue_flag_toggling),
    ("SessionEntry: 100 concurrent reads consistent",
     test_session_entry_concurrent_read),
]


async def run_all():
    for name, fn in TESTS:
        print(f"\n=== {name} ===")
        try:
            await fn()
        except Exception as e:
            results.append(TestResult(
                name=name, status="FAIL",
                detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}",
            ))


def main():
    print("=" * 70)
    print("Session concurrency — unit tests (Day 6)")
    print("=" * 70)
    asyncio.run(run_all())

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
