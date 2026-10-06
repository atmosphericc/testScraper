#!/usr/bin/env python3
"""Query the run-log event store (logs/events/events.sqlite, built by build.py).

    python tools/events/q.py <name | "SQL"> [--run SUBSTR[,SUBSTR..]] [--csv | --json]
                             [-p NAME=VALUE ...] [--db PATH]
    python tools/events/q.py --list

<name> runs tools/events/queries/<name>.sql (a file may hold several statements; each
result set is printed). Anything else is run as SQL.

--run   keeps only runs whose run_id contains one of the comma-separated substrings. It
        works for ANY query: every run-scoped table is shadowed by a TEMP view of the same
        name, so the SQL itself needs no filter. Example: --run 20260930 or --run 0925,0930
-p      binds a :NAME parameter in the SQL. An unset parameter falls back to the env var
        EVENTS_<NAME> (e.g. EVENTS_TREATED=alt-1), else NULL. Example: arms needs
        -p treated=alt-1 (or EVENTS_TREATED=alt-1).

Tables: runs, shots, races, flips, windows, tickets, loop_ends, decoys, monitor_stats,
orders, idents, unparsed, checks, place_orders (v2), stock_status, sellable_oos, shadow206,
atc_net, gate_events (v3), ingested. Read-only; stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUERIES = HERE / 'queries'
DEFAULT_DB = HERE.parents[1] / 'logs' / 'events' / 'events.sqlite'
RUN_TABLES = ['runs', 'shots', 'races', 'flips', 'windows', 'tickets', 'loop_ends', 'decoys',
              'monitor_stats', 'orders', 'idents', 'unparsed', 'checks', 'place_orders',
              'stock_status', 'sellable_oos', 'shadow206',        # 2026-10-05
              'atc_net', 'gate_events']                           # 2026-10-05 parser v3


def statements(sql: str):
    """Split a script into complete statements (sqlite3.complete_statement)."""
    buf = ''
    for line in sql.splitlines(True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip().strip(';').strip():
                yield buf.strip()
            buf = ''
    if buf.strip() and not re.fullmatch(r'(?:\s|--[^\n]*)*', buf):
        yield buf.strip()


def fmt(v):
    if v is None:
        return ''
    if isinstance(v, float):
        s = ('%.4f' % v).rstrip('0').rstrip('.')
        return s if s not in ('', '-0') else '0'
    return str(v)


def print_table(cols, rows, out=sys.stdout):
    cells = [[fmt(v) for v in r] for r in rows]
    num = [all(isinstance(r[i], (int, float)) or r[i] is None for r in rows) for i in range(len(cols))]
    w = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]
    line = '  '.join(c.rjust(w[i]) if num[i] else c.ljust(w[i]) for i, c in enumerate(cols))
    out.write(line.rstrip() + '\n')
    out.write('  '.join('-' * x for x in w) + '\n')
    for r in cells:
        out.write('  '.join(v.rjust(w[i]) if num[i] else v.ljust(w[i]) for i, v in enumerate(r)).rstrip() + '\n')
    out.write('(%d row%s)\n' % (len(rows), '' if len(rows) == 1 else 's'))


def run_sql(con, sql, a):
    """Apply --run (TEMP views shadowing each run-scoped table), bind :params, execute every
    statement; returns [(columns, rows)] or None on an SQL error."""
    if a.run:
        subs = [s.strip() for s in a.run.split(',') if s.strip()]
        cond = ' OR '.join("run_id LIKE '%%%s%%'" % s.replace("'", "''") for s in subs)
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in RUN_TABLES:
            if t in have:
                con.execute('CREATE TEMP VIEW %s AS SELECT * FROM main.%s WHERE %s' % (t, t, cond))

    params = {}
    for kv in a.param:
        k, _, v = kv.partition('=')
        params[k.strip()] = v
    for name in set(re.findall(r'(?<![:\w]):([A-Za-z_]\w*)', sql)):
        if name not in params:
            env = os.environ.get('EVENTS_' + name.upper())
            params[name] = env
            if env is None:
                print('note: :%s is unset (NULL) -- pass -p %s=VALUE or EVENTS_%s' % (name, name, name.upper()),
                      file=sys.stderr)

    results = []
    for st in statements(sql):
        used = {k: params.get(k) for k in set(re.findall(r'(?<![:\w]):([A-Za-z_]\w*)', st))}
        try:
            cur = con.execute(st, used)
        except sqlite3.Error as e:
            print('SQL error: %s\n%s' % (e, st[:400]), file=sys.stderr)
            return None
        if cur.description is None:
            continue
        cols = [d[0] for d in cur.description]
        results.append((cols, cur.fetchall()))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Query the run-log event store.',
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('query', nargs='?', help='named query (tools/events/queries/<name>.sql) or SQL')
    ap.add_argument('--run', help='comma-separated run_id substrings')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--csv', action='store_true')
    g.add_argument('--json', action='store_true')
    ap.add_argument('-p', '--param', action='append', default=[], help='NAME=VALUE for :NAME')
    ap.add_argument('--db', default=str(DEFAULT_DB))
    ap.add_argument('--list', action='store_true', help='list the named queries')
    a = ap.parse_args(argv)

    if a.list or not a.query:
        for f in sorted(QUERIES.glob('*.sql')):
            first = next((l[2:].strip() for l in f.read_text(encoding='utf-8').splitlines()
                          if l.startswith('--')), '')
            print('%-10s %s' % (f.stem, first))
        return 0 if a.list else 2

    qf = QUERIES / (a.query + '.sql')
    sql = qf.read_text(encoding='utf-8') if (re.fullmatch(r'[\w-]+', a.query) and qf.exists()) else a.query
    if not Path(a.db).exists():
        print('no event store at %s -- run: python tools/events/build.py' % a.db, file=sys.stderr)
        return 2
    con = sqlite3.connect('file:%s?mode=ro' % Path(a.db).as_posix(), uri=True)
    try:
        results = run_sql(con, sql, a)
    finally:
        con.close()
    if results is None:
        return 1

    if a.json:
        out = [[dict(zip(c, r)) for r in rows] for c, rows in results]
        json.dump(out[0] if len(out) == 1 else out, sys.stdout, indent=1, default=str)
        sys.stdout.write('\n')
    elif a.csv:
        w = csv.writer(sys.stdout, lineterminator='\n')
        for i, (c, rows) in enumerate(results):
            if i:
                sys.stdout.write('\n')
            w.writerow(c)
            w.writerows(rows)
    else:
        for i, (c, rows) in enumerate(results):
            if i:
                sys.stdout.write('\n')
            print_table(c, rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
