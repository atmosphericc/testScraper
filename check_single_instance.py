"""Single-instance guard for the Target bot (2026-09-08).

Running two ``app.py`` bots on this machine at once is the 2026-09-04 disaster:
two instances put ~6 req/s through the Bright Data sweep pool and poisoned the
whole pool's HUMAN/PerimeterX standing for days (see
docs/HUMAN_PX_DIAGNOSIS_2026_09_08.md). This is a READ-ONLY check the launch
wrapper runs BEFORE the relaunch loop: if another ``app.py`` python is already
running, it prints the offending PID(s) and exits 9 so the wrapper can refuse to
start a second bot. It never kills anything.

Exit codes:
  0  no other app.py running (safe to launch)
  9  another app.py is already running (wrapper should abort)
  0  on any internal error (fail-open: never block a launch over a guard bug)
"""
from __future__ import annotations

import os
import sys


def _other_app_py_pids() -> list:
    me = os.getpid()
    found = []
    try:
        import psutil
    except Exception:
        return found  # psutil absent -> fail open (return none)
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = proc.info.get('pid')
            if pid == me:
                continue
            name = (proc.info.get('name') or '').lower()
            if 'python' not in name:
                continue
            cmdline = proc.info.get('cmdline') or []
            # Match a process whose command line actually runs app.py (not
            # app.pyc, not this guard, not relogin_one.py). Compare basenames.
            runs_app = any(
                os.path.basename(str(part)).lower() == 'app.py'
                for part in cmdline
            )
            if runs_app:
                found.append(pid)
        except Exception:
            continue
    return found


def main() -> int:
    try:
        pids = _other_app_py_pids()
    except Exception as e:  # fail-open: a guard bug must never block a launch
        print(f"[GUARD] single-instance check errored ({e}); allowing launch")
        return 0
    if pids:
        pid_str = ",".join(str(p) for p in sorted(set(pids)))
        print(f"[GUARD] app.py is ALREADY running (PID {pid_str}) "
              f"- refusing to start a second bot.")
        return 9
    print("[GUARD] no other app.py running - safe to launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
