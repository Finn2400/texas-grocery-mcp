#!/usr/bin/env python3
"""Capture a manually authenticated H-E-B session from a real Chrome browser.

The browser must have been launched with a dedicated profile and a local Chrome
DevTools Protocol port. This script never asks for, reads, or stores a password.
It saves H-E-B cookies, H-E-B localStorage, the browser User-Agent, and optionally
the current persisted-query hashes observed while the user browses normally.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import Request, sync_playwright
except ImportError:
    sys.exit("playwright is not installed; run: pip install playwright")

from texas_grocery_mcp.clients.graphql import DEFAULT_PERSISTED_QUERIES
from texas_grocery_mcp.utils.secure_file import write_secure_json

DEFAULT_AUTH_PATH = Path("~/.texas-grocery-mcp/auth.json").expanduser()


def _record_graphql_request(
    request: Request,
    discovered: dict[str, str],
    known_operations: set[str],
) -> None:
    if "graphql" not in request.url.lower() or not request.post_data:
        return
    try:
        body: Any = json.loads(request.post_data)
    except json.JSONDecodeError:
        return

    entries = body if isinstance(body, list) else [body]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        operation = entry.get("operationName")
        persisted = (entry.get("extensions") or {}).get("persistedQuery") or {}
        sha = persisted.get("sha256Hash")
        if (
            operation in known_operations
            and isinstance(sha, str)
            and len(sha) == 64
            and discovered.get(operation) != sha
        ):
            discovered[operation] = sha
            print(f"captured current hash: {operation}")


def _write_secure_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)


def _load_hashes(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        name: sha
        for name, sha in value.items()
        if isinstance(name, str) and isinstance(sha, str) and len(sha) == 64
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome DevTools endpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_AUTH_PATH,
        help="Playwright storage-state output (default: %(default)s)",
    )
    parser.add_argument(
        "--origin-filter",
        default="heb.com",
        help="Only retain cookies and localStorage for this domain substring",
    )
    parser.add_argument(
        "--watch-seconds",
        type=int,
        default=0,
        help=(
            "Watch normal browser activity for current H-E-B GraphQL hashes before "
            "saving the session"
        ),
    )
    parser.add_argument(
        "--hashes-out",
        type=Path,
        help="Hash override file (default: hash_overrides.json beside --out)",
    )
    parser.add_argument(
        "--close-browser",
        action="store_true",
        help="Close the dedicated Chrome after capture; default is to leave it open",
    )
    args = parser.parse_args()

    auth_path = args.out.expanduser().resolve()
    hashes_path = (
        args.hashes_out.expanduser().resolve()
        if args.hashes_out
        else auth_path.parent / "hash_overrides.json"
    )
    known_operations = set(DEFAULT_PERSISTED_QUERIES)
    discovered: dict[str, str] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        if not browser.contexts:
            sys.exit(
                "No browser context found. Launch the dedicated Chrome with "
                "scripts/launch_real_chrome.py first."
            )
        context = browser.contexts[0]

        def attach(page: Any) -> None:
            page.on(
                "request",
                lambda request: _record_graphql_request(
                    request, discovered, known_operations
                ),
            )

        for page in context.pages:
            attach(page)
        context.on("page", attach)

        if args.watch_seconds > 0:
            print(
                f"Watching for {args.watch_seconds}s. In Chrome, browse H-E-B normally.\n"
                "Visit your shopping list and search for a product. To learn the list-add "
                "mutation hash, manually add one item you actually want."
            )
            deadline = time.monotonic() + args.watch_seconds
            while time.monotonic() < deadline:
                pages = context.pages
                if pages:
                    pages[0].wait_for_timeout(500)
                else:
                    time.sleep(0.5)

        state = context.storage_state()
        state["cookies"] = [
            cookie
            for cookie in state.get("cookies", [])
            if args.origin_filter in cookie.get("domain", "")
        ]

        origins: dict[str, list[dict[str, str]]] = {}
        user_agent: str | None = None
        for page in context.pages:
            if args.origin_filter not in page.url:
                continue
            try:
                origin = page.evaluate("() => location.origin")
                user_agent = user_agent or page.evaluate("() => navigator.userAgent")
                entries = page.evaluate(
                    "() => Object.entries(window.localStorage)"
                    ".map(([name, value]) => ({ name, value }))"
                )
            except Exception:
                continue
            origins[origin] = entries
        state["origins"] = [
            {"origin": origin, "localStorage": values}
            for origin, values in origins.items()
        ]

        write_secure_json(auth_path, state)
        if user_agent:
            _write_secure_text(auth_path.parent / "browser_ua.txt", user_agent)

        if discovered:
            merged = _load_hashes(hashes_path)
            merged.update(discovered)
            write_secure_json(hashes_path, merged)

        if args.close_browser:
            browser.close()

    local_storage_keys = [
        item["name"]
        for origin in state.get("origins", [])
        for item in origin.get("localStorage", [])
    ]
    print(f"wrote secure session: {auth_path}")
    print(f"  H-E-B cookies: {len(state.get('cookies', []))}")
    print(f"  localStorage keys: {local_storage_keys}")
    print(f"  captured hashes: {sorted(discovered)}")
    if not state.get("cookies"):
        print(
            "WARNING: no H-E-B cookies captured; confirm the browser is logged in.",
            file=sys.stderr,
        )
    if "reese84" not in local_storage_keys:
        print("WARNING: no reese84 localStorage token captured.", file=sys.stderr)


if __name__ == "__main__":
    main()
