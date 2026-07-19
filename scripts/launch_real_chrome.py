#!/usr/bin/env python3
"""Launch an isolated, ordinary Chrome profile for manual H-E-B login."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PROFILE = Path("~/.texas-grocery-mcp/chrome-profile").expanduser()
DEFAULT_URL = "https://www.heb.com/my-account/login"
MAC_BROWSER_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
LINUX_BROWSER_PATHS = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def _find_browser(explicit: str | None) -> str:
    if explicit:
        if Path(explicit).is_file():
            return explicit
        sys.exit(f"Browser executable does not exist: {explicit}")
    for candidate in (*MAC_BROWSER_PATHS, *LINUX_BROWSER_PATHS):
        if Path(candidate).is_file():
            return candidate
    sys.exit("Could not find Chrome, Edge, or Chromium. Pass --browser /path/to/executable.")


def _endpoint(port: int, path: str = "/json/version") -> str:
    return f"http://127.0.0.1:{port}{path}"


def _cdp_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(_endpoint(port), timeout=1) as response:
            return bool(json.load(response).get("webSocketDebuggerUrl"))
    except Exception:
        return False


def _open_tab(port: int, url: str) -> None:
    encoded = urllib.parse.quote(url, safe="")
    request = urllib.request.Request(_endpoint(port, f"/json/new?{encoded}"), method="PUT")
    with urllib.request.urlopen(request, timeout=3):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Path to Chrome/Edge/Chromium executable")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    profile = args.profile.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    os.chmod(profile, stat.S_IRWXU)

    if _cdp_ready(args.port):
        _open_tab(args.port, args.url)
        print(f"Reused dedicated browser on port {args.port} and opened H-E-B login.")
    else:
        browser = _find_browser(args.browser)
        command = [
            browser,
            f"--remote-debugging-port={args.port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            args.url,
        ]
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(40):
            if _cdp_ready(args.port):
                break
            time.sleep(0.25)
        else:
            sys.exit("Chrome launched, but its local DevTools endpoint did not become ready.")
        print(f"Launched isolated real Chrome with profile: {profile}")

    print("Log in manually in that window. Credentials stay in Chrome.")
    print("After login, capture with:")
    print("  .venv/bin/python scripts/capture_session.py --watch-seconds 60")


if __name__ == "__main__":
    main()
