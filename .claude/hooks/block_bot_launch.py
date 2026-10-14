#!/usr/bin/env python3
"""PreToolUse hook (Bash / PowerShell): stop Claude and its sub-agents from a
BLANKET run over tests/ -- a launch that runs test files nobody named.

History: until 2026-10-14 this enforced "bot start = user only" and blocked every
tests/ file outside tests/run_offline_suite.py's OFFLINE list. Operator 2026-10-14:
"Remove the hook's block on live tests outside the OFFLINE list and its block on
launching a second bot". The bot, app.py, the wrapper .bat, hand_login*.bat,
relogin_one.py, test_resilient_stack.py and named tests/ files are no longer
blocked here; the wrapper keeps its own single-instance guard
(check_single_instance.py, exit 9), and tools/ops/bot_ctl.py restart stops the
old bot before it starts one.

Blocks (exit 2, reason on stderr) a command that would EXECUTE tests/ files it
does not spell out:
  - a glob (`python tests/test_*.py`, `for t in tests/test_*.py`), an unexpanded
    $var / $(...) in a tests/ path, or the tests/ directory itself;
  - a bare `pytest`, or pytest on a directory / non-.py target;
  - a while-loop, xargs, find -exec or ForEach that feeds python / pytest in a
    statement that names tests/.
A bash for-loop over literal names is expanded and checked item by item, so
`for t in a b; do python tests/$t.py; done` passes. Why the blanket rule stays:
on 2026-09-14 `for t in tests/test_*.py` launched two LIVE end-to-end tests that
start a real Chrome against Target the night before a drop.

Mentions are fine: only the executable position of each command segment is
inspected, heredoc bodies and quoted strings are data (unless they are handed
to a shell: bash -c "...", cmd /c "...", powershell -Command "...", iex), so
grep / sed / cat / git on these files and commit messages about them pass.

Fail-open by design: any parse problem exits 0.
"""
import json
import re
import shlex
import sys

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
# A glob or a fan-out over tests/ plus a python / pytest call in the same statement:
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
# `for VAR in ITEMS; do BODY; done`. The body ends at the first `done`, so a nested
# loop cuts the outer body short and the inner loop is then checked without its
# `done` -- its `tests/$t.py` stays unexpanded, which blocks conservatively.
FOR_LOOP = re.compile(r'\bfor\s+([A-Za-z_]\w*)\s+in\b(.*?)[;\n]\s*do\b(.*?)[;\n]\s*done\b', re.S)
WHILE_LOOP = re.compile(r'\b(while|until)\b(.*?)[;\n]\s*do\b(.*?)[;\n]\s*done\b', re.S)
DYNAMIC_ITEM = re.compile(r'[*?\[$`]')
FANOUT_MSG = ("a loop / glob / fan-out over tests/ combined with python or pytest runs every file it "
              "matches, and tests/ holds LIVE tests that can fire real purchases (the 2026-09-14 "
              "incident). Name each file (a for-loop over literal names is fine), or use "
              "`python tests/run_offline_suite.py`.")


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


def _split_segments(cmd: str, pipes: bool = True) -> list:
    """Split on ; | || && & and newlines OUTSIDE quotes; a redirection like 2>&1
    is not a separator. pipes=False keeps a pipeline (a | b) in one piece."""
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
        if ch in ';\n' or (ch == '|' and pipes):
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


def _check_tests_file(tok: str):
    """A tests/ file named outright passes, live or offline (operator 2026-10-14). A
    path that does not spell its file out -- a glob, an unexpanded $var / $(...), the
    tests/ directory itself -- runs whatever it happens to match, so it blocks."""
    c = _clean(tok).replace('\\', '/')
    if not _base(tok) or c.rstrip('/') in ('tests', './tests') or DYNAMIC_ITEM.search(c):
        return (f"'{_clean(tok)}' does not name the test file(s) it runs (a glob, an unexpanded "
                "variable or the tests/ directory). tests/ holds LIVE tests that launch Chrome "
                "against Target and can fire real purchases. Name each file, or run "
                "`python tests/run_offline_suite.py`.")
    return None


def _check_pytest(args: list):
    files = [a for a in args if not a.startswith('-')]
    if not files:
        return ("a bare pytest run collects tests/, which holds LIVE tests. Name the files, or use "
                "`python tests/run_offline_suite.py`.")
    for f in files:
        f0 = f.split('::', 1)[0]
        if not f0.endswith('.py'):
            return (f"pytest on '{f}' is a directory/blanket run. Name the files, or use "
                    "`python tests/run_offline_suite.py`.")
        if _is_tests_path(f0):
            r = _check_tests_file(f0)
            if r:
                return r
    return None


def check_segment(seg: str, depth: int = 0):
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
        return check_command(exe, depth + 1)

    exe_base = _base(exe)
    exe_stem = exe_base[:-4] if exe_base.endswith('.exe') else exe_base

    if exe_stem in {s.replace('.exe', '') for s in SHELLS}:
        for k, a in enumerate(args):
            if a.lower() in SHELL_SCRIPT_FLAGS and k + 1 < len(args) and depth < 4:
                return check_command(args[k + 1], depth + 1)
        return None

    if exe_stem in ('pytest', 'py.test'):
        return _check_pytest(args)

    if PY_EXE.match(exe_stem):
        j = 0
        while j < len(args):
            a = args[j]
            if a == '-c':
                return None                     # inline code, not a script launch
            if a == '-m':
                mod = args[j + 1] if j + 1 < len(args) else ''
                if mod.lower() in ('pytest', 'py.test'):
                    return _check_pytest(args[j + 2:])
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
        if _is_tests_path(script):
            return _check_tests_file(script)
    return None


def _mask_quotes(code: str) -> str:
    """Same-length copy with quoted strings blanked, so loop keywords inside
    quotes are not read as loops and spans still index the original."""
    return QUOTED.sub(lambda m: 'Q' * len(m.group(0)), code)


def _check_for_loops(code: str, depth: int):
    """Check each bash for-loop by checking its body once per item. Literal items
    are substituted as written, so `for t in a b; do python tests/$t.py; done`
    runs two named files and passes. Globs, $vars and $(...) are not expanded:
    they become a wildcard item (under tests/ when the command names tests at
    all), which blocks wherever it lands in a tests/ launch. Returns (reason,
    code with the checked loops cut out)."""
    spans = []
    for m in FOR_LOOP.finditer(_mask_quotes(code)):
        var = m.group(1)
        items_txt, body = code[m.start(2):m.end(2)], code[m.start(3):m.end(3)]
        try:
            items = shlex.split(items_txt, posix=True)
        except ValueError:
            items = items_txt.split()
        literal = [it for it in items if not DYNAMIC_ITEM.search(it)][:200]
        if len(literal) < len(items) or not items:
            literal.append('tests/__FANOUT__*' if TESTS_REF.search(code) else '__FANOUT__*')
        ref = re.compile(r'\$\{%s\}|\$%s\b' % (var, var))
        for it in literal:
            if depth >= 4:
                return FANOUT_MSG, code
            r = check_command(ref.sub(lambda _m: it, body), depth + 1)
            if r:
                return r, code
        spans.append((m.start(), m.end()))
    for a, b in reversed(spans):
        code = code[:a] + ' ; ' + code[b:]
    return None, code


def check_command(cmd: str, depth: int = 0):
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    code = _strip_data_blocks(cmd)
    r, code = _check_for_loops(code, depth)
    if r:
        return r
    unquoted = QUOTED.sub('""', code)
    # while / until loops are not expanded: one whose body runs python / pytest in a
    # command that names tests (`ls tests/*.py | while read f; do python "$f"; done`) blocks.
    for m in WHILE_LOOP.finditer(_mask_quotes(code)):
        if PY_OR_PYTEST.search(QUOTED.sub('""', code[m.start(3):m.end(3)])) and TESTS_REF.search(unquoted):
            return FANOUT_MSG
    # Globs and the other fan-outs (xargs, find -exec, ForEach-Object, foreach) are judged
    # per statement (a whole pipeline): one that names tests and runs python / pytest blocks.
    for stmt in _split_segments(code, pipes=False):
        u = QUOTED.sub('""', stmt)
        if (TESTS_GLOB.search(u) or (TESTS_FANOUT.search(u) and TESTS_REF.search(u))) and PY_OR_PYTEST.search(u):
            return FANOUT_MSG
    for seg in _split_segments(code):
        r = check_segment(seg, depth)
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
