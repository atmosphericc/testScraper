#!/usr/bin/env python3
"""Offline test for tools/events/queries/cred_source.sql and cart_limit_empty.sql (readiness audit 2026-10-19, lane queries).

A synthetic event store in a temp dir (runs / shots / harvest only); BOTH saved SQL files are run through the real
tools/events/q.py as a subprocess (--db, --json, read-only), so the test exercises the files on disk, not an inline copy.
When the main tree's gitignored logs/events/events.sqlite is reachable, the audit's credential-source totals are also
printed and compared against it (read-only, a mismatch is a WARN: the store's parser version moves those numbers); otherwise
that part SKIPs. No bot, no browser, no network, no build.py rebuild.
Run: python tests/test_readout_queries_1019.py
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Q = ROOT / "tools" / "events" / "q.py"

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


# gate -> (status, err_key) as the parser classifies them
GATES = {
    "w1": (429, "ERR_A2C_TCIN_RATE_LIMITED", "wall1_limited"),
    "w2": (401, None, "wall2_denied"),
    "fs": (429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEEDED", "admitted_fs"),
    "cart": (201, None, "cart"),
    "climit": (400, "MAX_PURCHASE_LIMIT_EXCEEDED", "cart_limit"),
}

RUN_A = "run_20261001_000000"
RUN_B = "run_20261002_000000"
RUN_OLD = "run_20260901_000000"           # before since_run: excluded by cred_source


def build_store(path: Path):
    con = sqlite3.connect(str(path))
    con.executescript("""
CREATE TABLE runs (run_id TEXT PRIMARY KEY, file TEXT, first_ts TEXT, last_ts TEXT, lines INTEGER, parser_version INTEGER, utc_offset_min INTEGER, tz_src TEXT, atc_resp_form TEXT, flip_log INTEGER);
CREATE TABLE shots (run_id TEXT, line INTEGER, ts_ms INTEGER, ident TEXT, tcin TEXT, status INTEGER, err_key TEXT, gate TEXT, proxied INTEGER, cred_source TEXT);
CREATE TABLE harvest (run_id TEXT, line INTEGER, ts_ms INTEGER, acct TEXT, event TEXT);
""")
    n = [0]

    def shot(run, ident, tcin, kind, cred, proxied=0):
        n[0] += 1
        st, ek, gate = GATES[kind]
        con.execute("INSERT INTO shots(run_id,line,ts_ms,ident,tcin,status,err_key,gate,proxied,cred_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run, n[0], 1000 + n[0], ident, tcin, st, ek, gate, proxied, cred))

    for r in (RUN_A, RUN_B, RUN_OLD):
        con.execute("INSERT INTO runs(run_id,parser_version) VALUES (?,?)", (r, 7))

    # RUN_A: banked_replay 6 shots (3 edge 429, 1 401, 1 fs, 1 cart); page_signed 4 shots (3 edge 429, 1 401); blank 2 (edge 429)
    for _ in range(3):
        shot(RUN_A, "primary", "1000000001", "w1", "banked_replay")
    shot(RUN_A, "primary", "1000000001", "w2", "banked_replay")
    shot(RUN_A, "primary", "1000000001", "fs", "banked_replay")
    shot(RUN_A, "primary", "1000000001", "cart", "banked_replay")          # line 6: primary 201 on TCIN ...001
    for _ in range(3):
        shot(RUN_A, "alt-1", "1000000002", "w1", "page_signed")
    shot(RUN_A, "alt-1", "1000000002", "w2", "page_signed")
    for _ in range(2):
        shot(RUN_A, "alt-1", "1000000002", "w1", None)
    # cart_limit probes in RUN_A
    shot(RUN_A, "business", "1000000009", "climit", "banked_replay")       # line 13: FIRST ADD (no earlier 201, business, ...009)
    shot(RUN_A, "primary", "1000000001", "climit", "banked_replay")        # line 14: after primary's 201 on ...001 -> first_add 0
    shot(RUN_A, None, "1000000001", "climit", None)                        # line 15: NULL ident -> listed apart
    # RUN_B: banked_replay 3 (2 edge 429, 1 cart), page_signed 3 (1 fs, 2 edge 429), blank 1 (401)
    shot(RUN_B, "primary", "1000000003", "w1", "banked_replay")
    shot(RUN_B, "primary", "1000000003", "w1", "banked_replay")
    shot(RUN_B, "primary", "1000000003", "cart", "banked_replay")
    shot(RUN_B, "business", "1000000003", "fs", "page_signed")
    shot(RUN_B, "business", "1000000003", "w1", "page_signed")
    shot(RUN_B, "business", "1000000003", "w1", "page_signed")
    shot(RUN_B, "alt-1", "1000000003", "w2", None)
    # RUN_B: clamped 201 on TCIN ...009 by ANOTHER ident AFTER business's cart_limit in RUN_A is irrelevant (other run): a 201 in RUN_B
    # on ...009 must NOT make RUN_A's business row an "after_201" row
    shot(RUN_B, "alt-1", "1000000009", "cart", "banked_replay")
    # a proxied shot and an old-run shot: both excluded from cred_source
    shot(RUN_B, "primary", "1000000003", "cart", "page_signed", proxied=1)
    shot(RUN_OLD, "primary", "1000000003", "cart", "banked_replay")
    # harvest rows: RUN_B has some (first by ts_ms = 'empty'); RUN_A none -> NULL
    con.execute("INSERT INTO harvest VALUES (?,?,?,?,?)", (RUN_B, 5, 2000, "primary", "captured"))
    con.execute("INSERT INTO harvest VALUES (?,?,?,?,?)", (RUN_B, 3, 1500, "primary", "empty"))
    con.commit()
    con.close()


def q(db: Path, name: str):
    r = subprocess.run([sys.executable, str(Q), name, "--db", str(db), "--json"], capture_output=True, text=True,
                       timeout=120, cwd=str(ROOT), encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None, r.stderr[-600:]
    return json.loads(r.stdout), r.stderr


def test_files_exist():
    for n in ("cred_source", "cart_limit_empty"):
        check(f"rq_{n}_file_exists", (ROOT / "tools" / "events" / "queries" / f"{n}.sql").is_file())
    head = (ROOT / "tools" / "events" / "queries" / "cred_source.sql").read_text(encoding="utf-8")
    check("rq_cred_rule_header", "PRE-REGISTERED RULE" in head and "past_w1 >= 30" in head and "n = 8" in head)
    head = (ROOT / "tools" / "events" / "queries" / "cart_limit_empty.sql").read_text(encoding="utf-8")
    check("rq_climit_rule_header", "PRE-REGISTERED RULE" in head and "cart_qty = 1" in head and "cart_qty = 2" in head
          and "NULL_IDENT" in head)


def test_cred_source():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "events.sqlite"
        build_store(db)
        before = db.stat().st_mtime_ns
        out, err = q(db, "cred_source")
        check("rq_cred_rc0", out is not None, err)
        if out is None:
            return
        check("rq_cred_two_result_sets", len(out) == 2, len(out))
        totals, cells = out
        by = {r["cred"]: r for r in totals}
        # banked_replay: A has 6 + 2 climit = 8 shots, B has 3 + 1 (alt-1 201) = 4 -> 12 (proxied / old run excluded)
        b = by["banked_replay"]
        check("rq_cred_banked_totals", (b["shots"], b["past_w1"], b["past_w2"], b["carts"], b["runs"]) == (12, 7, 6, 3, 2), b)
        check("rq_cred_banked_n_to_30", b["n_to_30"] == 23, b)
        p = by["page_signed"]
        check("rq_cred_page_totals", (p["shots"], p["past_w1"], p["past_w2"], p["carts"], p["runs"]) == (7, 2, 1, 0, 2), p)
        check("rq_cred_page_n_to_30", p["n_to_30"] == 28, p)
        k = by["blank"]
        check("rq_cred_blank_totals", (k["shots"], k["past_w1"], k["past_w2"], k["carts"]) == (4, 2, 1, 0), k)
        check("rq_cred_order", [r["cred"] for r in totals] == ["banked_replay", "page_signed", "blank"], totals)
        # run x cred cells
        cell = {(r["run"], r["cred"]): r for r in cells}
        check("rq_cred_cell_count", len(cells) == 6, [(r["run"], r["cred"]) for r in cells])
        ab = cell[(RUN_A, "banked_replay")]
        check("rq_cred_cell_A_banked", (ab["shots"], ab["past_w1"], ab["past_w2"], ab["carts"]) == (8, 5, 4, 1), ab)
        ap = cell[(RUN_A, "page_signed")]
        check("rq_cred_cell_A_page", (ap["shots"], ap["past_w1"], ap["past_w2"], ap["carts"]) == (4, 1, 0, 0), ap)
        bp = cell[(RUN_B, "page_signed")]
        check("rq_cred_cell_B_page", (bp["shots"], bp["past_w1"], bp["past_w2"], bp["carts"]) == (3, 1, 1, 0), bp)
        check("rq_cred_old_and_proxied_excluded", all(r["run"] != RUN_OLD for r in cells) and bp["carts"] == 0)
        check("rq_cred_harvest_at_boot", cell[(RUN_A, "banked_replay")]["harvest_at_boot"] is None
              and cell[(RUN_B, "page_signed")]["harvest_at_boot"] == "empty", (ab, bp))
        check("rq_cred_run_order", [r["run"] for r in cells] == sorted(r["run"] for r in cells), cells)
        check("rq_cred_read_only", db.stat().st_mtime_ns == before)


def test_cart_limit_empty():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "events.sqlite"
        build_store(db)
        before = db.stat().st_mtime_ns
        out, err = q(db, "cart_limit_empty")
        check("rq_cl_rc0", out is not None, err)
        if out is None:
            return
        check("rq_cl_two_result_sets", len(out) == 2, len(out))
        summary, rows = out
        s = summary[0]
        check("rq_cl_summary", (s["cart_limit_shots"], s["first_add_rows"], s["after_201_rows"], s["null_ident_rows"]) == (3, 1, 1, 1), s)
        by_line = {r["line"]: r for r in rows}
        check("rq_cl_row_count", len(rows) == 3, rows)
        first = by_line[13]
        check("rq_cl_first_add_row", first["first_add"] == 1 and first["ident"] == "business" and first["tcin"] == "1000000009"
              and first["prior_201_ident"] == 0 and first["ident_state"] == "ident", first)
        # a 201 on ...009 exists only in ANOTHER run (RUN_B): it must not turn this row into an after-201 row nor appear in run_201_*
        check("rq_cl_other_run_201_ignored", first["run_201_tcin"] == 0 and first["run_201_lines"] is None, first)
        after = by_line[14]
        check("rq_cl_after_201_row", after["first_add"] == 0 and after["prior_201_ident"] == 1 and after["run_201_tcin"] == 1
              and after["run_201_lines"] == "primary@6", after)
        null = by_line[15]
        check("rq_cl_null_ident_apart", null["ident_state"] == "NULL_IDENT" and null["first_add"] is None, null)
        check("rq_cl_cart_qty_null", all(r["cart_qty"] is None for r in rows), rows)
        check("rq_cl_fields", first["status"] == 400 and first["err_key"] == "MAX_PURCHASE_LIMIT_EXCEEDED"
              and first["cred_source"] == "banked_replay" and first["proxied"] == 0, first)
        check("rq_cl_read_only", db.stat().st_mtime_ns == before)


def test_clamped_201_other_ident_same_run():
    """The pre-registered 'clamped 201' scenario: a limit-1 TCIN, one account's qty-2 add refused (first_add = 1) while another
    account's 201 for the same TCIN exists in the same run -> run_201_tcin = 1 and its 'ident@line' is listed (cart_qty is then read
    by hand from the log line named there)."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "events.sqlite"
        build_store(db)
        con = sqlite3.connect(str(db))
        # business hits the limit on ...009 at line 100; alt-1 had a 201 on ...009 at line 90 (clamped to qty 1 by Target)
        con.execute("INSERT INTO shots(run_id,line,ts_ms,ident,tcin,status,err_key,gate,proxied,cred_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (RUN_A, 90, 9000, "alt-1", "1000000009", 201, None, "cart", 0, "banked_replay"))
        con.execute("INSERT INTO shots(run_id,line,ts_ms,ident,tcin,status,err_key,gate,proxied,cred_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (RUN_A, 100, 9100, "business", "1000000009", 400, "MAX_PURCHASE_LIMIT_EXCEEDED", "cart_limit", 0, "banked_replay"))
        con.commit()
        con.close()
        out, err = q(db, "cart_limit_empty")
        check("rq_cl2_rc0", out is not None, err)
        if out is None:
            return
        summary, rows = out
        by_line = {r["line"]: r for r in rows}
        r100, r13 = by_line[100], by_line[13]
        check("rq_cl2_first_add_with_other_201", r100["first_add"] == 1 and r100["run_201_tcin"] == 1
              and r100["run_201_lines"] == "alt-1@90", r100)
        # the line-13 business row now ALSO sees alt-1's later 201 in the run (any ident, any line), still a first add for business
        check("rq_cl2_line13_still_first_add", r13["first_add"] == 1 and r13["run_201_tcin"] == 1, r13)
        check("rq_cl2_summary", summary[0]["first_add_rows"] == 2 and summary[0]["cart_limit_shots"] == 4, summary)


def test_real_store():
    """The audit's credential-source totals (home IP, since run_20260911): banked_replay 1,891 shots / 14 carts; page_signed 2,258 / 0 carts
    / 8 past wall 1; blank 27 / 0. The store keeps growing and a parser rebuild moves rows: a mismatch is a WARN, never a failure."""
    cands = [ROOT / "logs" / "events" / "events.sqlite"] + [p / "logs" / "events" / "events.sqlite" for p in ROOT.parents[:3]]
    db = next((c for c in cands if c.exists()), None)
    if db is None:
        print("[SKIP] no event store reachable (gitignored); synthetic tests only")
        return
    for name in ("cred_source", "cart_limit_empty"):
        out, err = q(db, name)
        check(f"rq_real_{name}_runs", out is not None, err)
        if out is None:
            return
        if name == "cred_source":
            by = {r["cred"]: r for r in out[0]}
            got = {k: (v["shots"], v["carts"], v["past_w1"]) for k, v in by.items()}
            want = {"banked_replay": (1891, 14, None), "page_signed": (2258, 0, 8), "blank": (27, 0, 0)}
            for k, (sh, ca, pw) in want.items():
                g = got.get(k)
                ok = g is not None and g[0] == sh and g[1] == ca and (pw is None or g[2] == pw)
                print(f"[{'INFO' if ok else 'WARN'}] real-store {k}: shots/carts/past_w1 = {g} (audit {sh}/{ca}/{pw})")
        else:
            print(f"[INFO] real-store cart_limit_empty summary: {out[0]}")


def main() -> int:
    for t in (test_files_exist, test_cred_source, test_cart_limit_empty, test_clamped_201_other_ident_same_run, test_real_store):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAILED:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
