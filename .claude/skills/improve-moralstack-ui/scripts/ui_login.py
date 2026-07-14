#!/usr/bin/env python3
"""Mint a Playwright storage-state for the MoralStack dashboard.

Why this exists
---------------
``moralstack/ui/app.py`` keeps sessions in a process-local dict (``_SESSIONS``)
and hands out an ``httponly`` cookie. Two consequences the loop must survive:

* every restart of ``moralstack-ui`` invalidates every previously issued cookie,
  so "log in once by hand and reuse the browser profile forever" is not a real
  strategy — it breaks the first time the server is restarted;
* the cookie cannot be read or set from page JavaScript.

So the loop re-mints authentication whenever it needs it. This script reads the
credentials from ``.env`` **in-process**, performs the form POST itself, captures
the ``Set-Cookie``, and writes a Playwright storage-state file that the MCP
server loads with ``--isolated --storage-state``. The agent driving the browser
never sees, and never needs, the credentials.

Exit codes: 0 authenticated, 1 misconfigured/unreachable, 2 credentials rejected.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from _common import (
    STORAGE_STATE_PATH,
    config,
    ensure_dirs,
    ui_base_url,
    write_json_atomic,
)

COOKIE_NAME = "moralstack_session"


def _probe(base_url: str) -> bool:
    """True when the UI answers and has credentials configured."""
    try:
        with urllib.request.urlopen(f"{base_url}/auth-status", timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"FAIL: MoralStack UI is not reachable at {base_url} ({exc.__class__.__name__}).", file=sys.stderr)
        print("      Start it with the /mstack-run skill (moralstack-ui).", file=sys.stderr)
        return False
    if '"credentials_configured":true' not in body.replace(" ", ""):
        print("FAIL: the UI reports that no credentials are configured.", file=sys.stderr)
        print("      Set MORALSTACK_UI_USERNAME and MORALSTACK_UI_PASSWORD in .env and restart the UI.", file=sys.stderr)
        return False
    return True


def _login(base_url: str, username: str, password: str) -> str | None:
    """POST the login form and return the session cookie value."""
    jar = http.cookiejar.CookieJar()

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):  # type: ignore[override]
            return None  # the 303 to /runs carries the Set-Cookie we want

    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPCookieProcessor(jar))
    payload = urllib.parse.urlencode({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        f"{base_url}/login",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        opener.open(request, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print("FAIL: the UI rejected the credentials in .env (401).", file=sys.stderr)
            return None
        if exc.code not in (302, 303):
            print(f"FAIL: unexpected status {exc.code} from POST /login.", file=sys.stderr)
            return None
    except (urllib.error.URLError, OSError) as exc:
        print(f"FAIL: POST /login failed ({exc.__class__.__name__}).", file=sys.stderr)
        return None

    for cookie in jar:
        if cookie.name == COOKIE_NAME and cookie.value:
            return cookie.value
    print("FAIL: login succeeded but no session cookie was returned.", file=sys.stderr)
    return None


def _write_storage_state(base_url: str, token: str) -> None:
    host = urllib.parse.urlparse(base_url).hostname or "localhost"
    state = {
        "cookies": [
            {
                "name": COOKIE_NAME,
                "value": token,
                "domain": host,
                "path": "/",
                "expires": time.time() + 6 * 3600,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    ensure_dirs()
    write_json_atomic(STORAGE_STATE_PATH, state)
    try:  # best effort; Windows ignores POSIX modes
        os.chmod(STORAGE_STATE_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="probe the UI without minting a session")
    args = parser.parse_args()

    base_url = ui_base_url()
    if not _probe(base_url):
        return 1
    if args.check_only:
        print(f"OK: UI reachable at {base_url} with credentials configured.")
        return 0

    username = config("MORALSTACK_UI_USERNAME")
    password = config("MORALSTACK_UI_PASSWORD")
    if not username or not password:
        print("FAIL: MORALSTACK_UI_USERNAME / MORALSTACK_UI_PASSWORD are not set in .env.", file=sys.stderr)
        return 1

    token = _login(base_url, username, password)
    if not token:
        return 2

    _write_storage_state(base_url, token)
    # Never print the token, the username, or the password.
    print(f"OK: session minted for {base_url}; storage state written to .claude/ui-loop/runtime/storage-state.json")
    print("NOTE: call mcp__playwright browser_close before navigating so the new state is loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
