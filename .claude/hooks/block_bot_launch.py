#!/usr/bin/env python3
"""PreToolUse hook (Bash / PowerShell): stop Claude and its sub-agents from
launching the live bot or a live test.

Enforces the standing rule "bot start = user only" (.claude/agent-context.md
section 1) deterministically. Until 2026-09-30 the rule lived only in prose,
and .claude/settings.local.json even allow-lists `python app.py`.

Blocks (exit 2, reason on stderr) a command that would EXECUTE:
  - app.py, test_app.py, relogin_one.py, test_resilient_stack.py (any python),
    or `python -m app` / `-m test_app`;
  - run_bot_with_nightly_restart.bat or any hand_login*.bat;
  - a tests/ file that is not in tests/run_offline_suite.py's OFFLINE list
    (run_offline_suite.py itself is always allowed), any pytest run that is
    not pinned to such files, and loops / fan-outs over tests/ files.
Mentions are fine: only the executable position of each command segment is
inspected, heredoc bodies and quoted strings are data (unless they are handed
to a shell: bash -c "...", cmd /c "...", powershell -Command "...", iex), so
grep / sed / cat / git on these files and commit messages about them pass.

Fail-open by design: any parse problem exits 0. The operator starts the bot
outside Claude; nothing here affects that.
"""
import json
import os
import re
import shlex
import sys

BLOCK_SCRIPTS = {'app.py', 'test_app.py', 'relogin_one.py', 'test_resilient_stack.py'}
BLOCK_MODULES = {'app', 'test_app', 'relogin_one', 'test_resilient_stack'}
# Words that sit IN FRONT of the real executable of a segment.
WRAPPERS = {
    'cmd', 'cmd.exe', '/c', '/k', '/s', '/q', '/b', '/min', '/wait', 'start', 'call', '&',
    '.', 'nohup', 'time', 'env', 'exec', 'command', 'builtin', 'powershell', 'powershell.exe',
    'pwsh', 'pwsh.exe', '-command', '-c', '-noprofile', '-noninteractive', '-nologo',
    '-executionpolicy', 'bypass', 'unrestricted', 'start-process', 'saps', '-filepath',
    '-argumentlist', '-wait', '-nonewwindow', '-windowstyle', 'hidden', 'timeout', 'watch',
    'winpty', 'nice', 'ionice', 'stdbuf', 'sudo', 'invoke-expression', 'iex', 'do', 'then',
    'else', 'elif', '{', '}', '(', ')', 'xargs', 'wsl', 'wsl.exe', 'if', 'while', 'until',
}
SHELLS = {'bash', 'bash.exe', 'sh', 'sh.exe', 'zsh', 'dash', 'ksh', 'git-bash', 'git-bash.exe'}
SHELL_SCRIPT_FLAGS = {'-c', '-lc', '-ic', '-command', '/c', '/k', '-encodedcommand'}
# A glob or a fan-out over tests/ plus a python / pytest call in the same command:
# `for t in tests/test_*.py; do python $t; done` ran two LIVE tests on 2026-09-14.
TESTS_GLOB = re.compile(r'\btests[\\/][^\s;|&"\']*\*')
TESTS_FANOUT = re.compile(r'(-exec\b|\bxargs\b|foreach-object|\bforeach\b|(^|[\s;|])%\s*\{|\bfor\s+\w+\s+in\b)', re.I)
TESTS_REF = re.compile(r'\btests\b|test_\*', re.I)
PY_OR_PYTEST = re.compile(r'(^|[\s;|&({`$])(python[\w.]*|py|pytest|py\.test)(\.exe)?(\s|$|["\'])', re.I)
PY_EXE = re.compile(r'^(python(\d+(\.\d+)*)?w?|py)$')
PY_FLAGS_WITH_ARG = {'-X', '-W', '-Q'}
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
PS_HERESTRING = re.compile(r"@(['\"])[ \t]*\r?\n.*?\r?\n[ \t]*\1@", re.S)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def _root() -> str:
    return os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd()


def _offline_names() -> set:
    """Base names (no .py) listed in tests/run_offline_suite.py OFFLINE = [...]."""
    try:
        src = open(os.path.join(_root(), 'tests', 'run_offline_suite.py'), encoding='utf-8').read()
        m = re.search(r'^OFFLINE\s*=\s*\[(.*?)^\]', src, re.S | re.M)
        body = m.group(1) if m else ''
        body = re.sub(r'#[^\n]*', '', body)
        return set(re.findall(r'["\']([A-Za-z0-9_]+)["\']', body))
    except Exception:
        return set()


def _clean(tok: str) -> str:
    """One token as the shell would see its word: quotes, subshell and grouping
    punctuation removed ($(...), backticks, parentheses, braces, a trailing ;)."""
    t = tok.strip()
    for _ in range(3):
        t = re.sub(r'^(\$\(|`|\(|\{)+', '', t)
        t = re.sub(r'(\)|`|\}|;)+$', '', t)
        t = t.strip('"\'')
    return t


def _base(tok: str) -> str:
    return re.split(r'[\\/]', _clean(tok))[-1].lower()


def _is_tests_path(tok: str) -> bool:
    t = _clean(tok).replace('\\', '/')
    return t.startswith('tests/') or '/tests/' in t or t in ('tests', './tests', 'tests/', '.\\tests')


def _tokens(seg: str) -> list:
    try:
        raw = shlex.split(seg, posix=False)
    except ValueError:
        raw = seg.split()
    out = []
    for t in raw:
        c = _clean(t)
        if c:
            out.append(c)
    return out


def _strip_data_blocks(cmd: str) -> str:
    """Remove heredoc bodies (data) unless the heredoc feeds a shell, and
    PowerShell here-string bodies. A body that feeds bash/sh is kept, because
    that body IS commands."""
    cmd = PS_HERESTRING.sub("@''@", cmd)
    lines = cmd.split('\n')
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = HEREDOC.search(line)
        if not m:
            out.append(line)
            i += 1
            continue
        out.append(line[:m.start()] + line[m.end():])
        dash, delim = m.group(1) == '-', m.group(3)
        owner_toks = _tokens(_split_segments(line[:m.start()])[-1]) if line[:m.start()].strip() else []
        k = 0
        while k < len(owner_toks) and (owner_toks[k].lower() in WRAPPERS or re.match(r'^[A-Za-z_]\w*=', owner_toks[k])):
            k += 1
        owner = _base(owner_toks[k]) if k < len(owner_toks) else ''
        keep = owner in SHELLS or owner in ('cmd', 'cmd.exe', 'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe')
        j = i + 1
        while j < len(lines):
            probe = lines[j].rstrip('\r')
            if (probe.lstrip('\t') if dash else probe) == delim:
                break
            if keep:
                out.append(lines[j])
            j += 1
        i = j + 1
    return '\n'.join(out)


def _split_segments(cmd: str) -> list:
    """Split on ; | || && & and newlines OUTSIDE quotes; a redirection like 2>&1
    is not a separator."""
    segs, cur, q, i, n = [], [], None, 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if q:
            cur.append(ch)
            if ch == '\\' and q == '"' and i + 1 < n:
                cur.append(cmd[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
            i += 1
            continue
        if ch in ('"', "'"):
            q = ch
            cur.append(ch)
            i += 1
            continue
        two = cmd[i:i + 2]
        if two in ('&&', '||'):
            segs.append(''.join(cur))
            cur = []
            i += 2
            continue
        if ch in ';|\n':
            segs.append(''.join(cur))
            cur = []
            i += 1
            continue
        if ch == '&':
            prev = cmd[i - 1] if i > 0 else ''
            nxt = cmd[i + 1] if i + 1 < n else ''
            if prev not in '<>' and not prev.isdigit() and nxt != '>':
                segs.append(''.join(cur))
                cur = []
                i += 1
                continue
        cur.append(ch)
        i += 1
    segs.append(''.join(cur))
    return segs


def _check_tests_file(tok: str, offline: set):
    name = _base(tok)
    stem = name[:-3] if name.endswith('.py') else name
    if name == 'run_offline_suite.py':
        return None
    if stem in offline:
        return None
    return (f"'{_clean(tok)}' is under tests/ but not in tests/run_offline_suite.py OFFLINE. tests/ holds "
            "LIVE tests that launch Chrome against Target and can fire real purchases. Run "
            "`python tests/run_offline_suite.py`, or add the file to OFFLINE only after reading "
            "it and confirming it is offline.")


def _check_pytest(args: list, offline: set):
    files = [a for a in args if not a.startswith('-')]
    if not files:
        return "a bare pytest run collects tests/, which holds LIVE tests. Use `python tests/run_offline_suite.py`."
    for f in files:
        f0 = f.split('::', 1)[0]
        if not f0.endswith('.py'):
            return f"pytest on '{f}' is a directory/blanket run. Use `python tests/run_offline_suite.py`."
        if _is_tests_path(f0):
            r = _check_tests_file(f0, offline)
            if r:
                return r
    return None


def check_segment(seg: str, offline: set, depth: int = 0):
    toks = _tokens(seg)
    i = 0
    while i < len(toks):
        t = toks[i]
        tl = t.lower()
        if (tl in WRAPPERS or re.match(r'^[A-Za-z_][\w.]*=', t) or t.isdigit()
                or re.match(r'^/[a-z]{1,4}$', tl) or (tl.startswith('-') and ' ' not in t)):
            i += 1
            continue
        break
    if i >= len(toks):
        return None
    exe = toks[i]
    args = toks[i + 1:]

    # A quoted multi-word string in executable position was handed to a shell
    # (cmd /c "...", powershell -Command "...", iex '...'): check it as a command.
    if ' ' in exe.strip() and depth < 4:
        return check_command(exe, offline, depth + 1)

    exe_base = _base(exe)
    exe_stem = exe_base[:-4] if exe_base.endswith('.exe') else exe_base

    if exe_stem in {s.replace('.exe', '') for s in SHELLS}:
        for k, a in enumerate(args):
            if a.lower() in SHELL_SCRIPT_FLAGS and k + 1 < len(args) and depth < 4:
                return check_command(args[k + 1], offline, depth + 1)
        return None

    if exe_base.endswith('.bat') or exe_base.endswith('.cmd'):
        if exe_base == 'run_bot_with_nightly_restart.bat' or exe_base.startswith('hand_login'):
            return (f"'{exe}' starts the live bot / a login. Bot start is the operator's call "
                    "(agent-context.md section 1). Ask the operator to run it.")
        return None

    if exe_stem in ('pytest', 'py.test'):
        return _check_pytest(args, offline)

    if PY_EXE.match(exe_stem):
        j = 0
        while j < len(args):
            a = args[j]
            if a == '-c':
                return None                     # inline code, not a script launch
            if a == '-m':
                mod = args[j + 1] if j + 1 < len(args) else ''
                if mod.lower() in ('pytest', 'py.test'):
                    return _check_pytest(args[j + 2:], offline)
                if mod.lower() in BLOCK_MODULES:
                    return f"`python -m {mod}` starts the live bot. Bot start is the operator's call."
                return None
            if a in PY_FLAGS_WITH_ARG:
                j += 2
                continue
            if a.startswith('-'):
                j += 1
                continue
            break
        if j >= len(args):
            return None
        script = args[j]
        sb = _base(script)
        if sb in BLOCK_SCRIPTS:
            return (f"'{script}' launches the live bot, a browser or a login. Bot start is the "
                    "operator's call (agent-context.md section 1). Ask the operator to run it.")
        if _is_tests_path(script):
            return _check_tests_file(script, offline)
    return None


def check_command(cmd: str, offline=None, depth: int = 0):
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    offline = _offline_names() if offline is None else offline
    code = _strip_data_blocks(cmd)
    unquoted = QUOTED.sub('""', code)
    if (TESTS_GLOB.search(unquoted) or (TESTS_FANOUT.search(unquoted) and TESTS_REF.search(unquoted))) \
            and PY_OR_PYTEST.search(unquoted):
        return ("a loop / glob / fan-out over tests/ combined with python or pytest runs every file it "
                "matches, and tests/ holds LIVE tests (the 2026-09-14 incident). Use "
                "`python tests/run_offline_suite.py`.")
    for seg in _split_segments(code):
        r = check_segment(seg, offline, depth)
        if r:
            return r
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
        if data.get('tool_name') not in ('Bash', 'PowerShell'):
            return 0
        reason = check_command((data.get('tool_input') or {}).get('command') or '')
    except Exception:
        return 0
    if reason:
        sys.stderr.write('[block_bot_launch] BLOCKED: ' + reason + '\n')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
