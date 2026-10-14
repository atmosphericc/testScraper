"""FX-1014-JARGUARD (2026-10-14 post-run): never overwrite a MEMBER session jar with a GUEST one.

Why: on 10-14 two savers wrote guest cookies (idToken sut=G) over the account jars without
reading sut (C-1014-02, C-1014-04, both CONFIRMED): relogin_one.py's validate-first pass decides
"already logged in" from the /account URL alone (relogin_one.py:93-101, 436-441 ->
harvest_accounts._write_session), and the in-bot sentinel's save_session_state decides from the
account-link selector. primary's jar went member -> guest at the 00:10:23 wrapper start pass,
after two forced hand logins had cleared its Chrome profile (C-1014-01, PARTIALLY CONFIRMED).
Nothing turns a guest jar back into a member session; a member jar with a live login-session
sometimes can. So the guard keeps the member jar and says so.

TARGET_JAR_GUEST_GUARD=1 -> a write whose idToken (accessToken as fallback) carries sut=G is
skipped when the jar on disk carries a member sut (R / M). Unset or 0 = the previous behaviour.
Any error -> the write goes ahead exactly as before. Standard library only, so the login
scripts can import it without loading the bot.
"""
import base64
import json
import os
from typing import Any, Dict, Optional, Tuple

_AUTH_COOKIES = ('idToken', 'accessToken')
_MEMBER_SUT = ('R', 'M', 'MEMBER')


def enabled() -> bool:
    return os.environ.get('TARGET_JAR_GUEST_GUARD', '0').strip().lower() in ('1', 'true', 'yes', 'on')


def _claims(value: Any) -> Dict[str, Any]:
    try:
        parts = str(value).split('.')
        if len(parts) < 2:
            return {}
        pad = parts[1] + '=' * (-len(parts[1]) % 4)
        out = json.loads(base64.urlsafe_b64decode(pad))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def sut_of(cookies: Any) -> Optional[str]:
    """sut of the target.com idToken (accessToken as fallback), upper-cased; None if absent."""
    try:
        for name in _AUTH_COOKIES:
            for c in cookies or []:
                if (isinstance(c, dict) and c.get('name') == name
                        and 'target.com' in str(c.get('domain', ''))):
                    sut = _claims(c.get('value')).get('sut')
                    if sut:
                        return str(sut).upper()
    except Exception:
        pass
    return None


def jar_sut(path: Any) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return sut_of(data.get('cookies') if isinstance(data, dict) else None)
    except Exception:
        return None


def inspect(path: Any, new_cookies: Any) -> Tuple[bool, Optional[str]]:
    """(block, message). block=True: skip the write, the jar on disk is a member's and the new
    cookies are a guest's. block=False with a message: the new cookies are a guest's but there is
    no member jar to protect (write as before, log the message). (False, None) otherwise, and
    always when the flag is off or anything fails."""
    if not enabled():
        return False, None
    try:
        new = sut_of(new_cookies)
        if new != 'G':
            return False, None
        name = os.path.basename(str(path))
        old = jar_sut(path)
        if old in _MEMBER_SUT:
            return True, (f"the live session is a GUEST (sut=G) and {name} holds a member "
                          f"(sut={old}) — kept {name}")
        return False, f"the live session is a GUEST (sut=G); {name} holds sut={old or 'none'}"
    except Exception:
        return False, None
