# H-E-B Planner MCP

A local MCP server for finding H-E-B products, checking current prices, reading
shopping lists, and staging reviewed products into a shopping list.

This is an experimental, personal-use fork of
[`mgwalkerjr95/texas-grocery-mcp`](https://github.com/mgwalkerjr95/texas-grocery-mcp).
It is not affiliated with H-E-B and relies on unofficial web interfaces that can
change without notice.

## Safety Profile

Run `heb-planner-mcp`, not the broader upstream `texas-grocery-mcp` entry point.
The planner server:

- uses manual login in an isolated real-Chrome profile;
- never receives or stores an H-E-B password;
- is read-only unless `HEB_WRITE_SCOPE=shopping-list` is explicitly enabled;
- only exposes add-only shopping-list writes, each requiring `confirm=true`;
- snapshots before writes, skips already-satisfied quantities, and verifies exact
  quantities afterward rather than silently retrying uncertain mutations;
- does not expose cart additions, removal/deletion, coupon clipping, store
  mutation, checkout, cancellation, payment, or account-management tools;
- stores session tokens locally with owner-only file permissions.

There is no checkout implementation anywhere in the planner-safe server.

## What Works

| Capability | State |
|---|---|
| Store discovery | Live-tested against H-E-B's public store search |
| Product search and current prices | Implemented; requires a captured session |
| Product nutrition/details | Implemented; requires a captured session |
| Planner review-pack read | Implemented; local and read-only |
| Shopping-list read | Implemented; live verification still required |
| Shopping-list add | Implemented behind opt-in scope and confirmation |
| Cart read | Exposed read-only for final comparison |
| Coupons | Search/read only |
| Checkout and destructive actions | Deliberately unavailable |

As of 2026-07-19, the upstream unauthenticated typeahead hash is stale. That
fallback is not treated as a working product-search path. The intended path is a
manual real-browser session followed by authenticated, low-volume requests.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Playwright's Python package attaches to the real browser for initial capture. It
does not need to download or launch its own browser for the supported workflow.

## First Login

1. Launch an isolated ordinary Chrome profile:

   ```bash
   .venv/bin/python scripts/launch_real_chrome.py
   ```

2. Log in to H-E-B manually in the opened window. Credentials remain in Chrome.

3. Capture the session:

   ```bash
   .venv/bin/python scripts/capture_session.py
   ```

4. To learn current GraphQL hashes from normal browser activity, run a short
   observation window:

   ```bash
   .venv/bin/python scripts/capture_session.py --watch-seconds 60
   ```

   During that minute, visit your shopping list and search for a product. To
   capture the add-to-list mutation hash, manually add one item you actually want.
   The script observes request metadata only and leaves Chrome open.

The private files are written under `~/.texas-grocery-mcp/` by default:

- `auth.json`: H-E-B cookies and localStorage;
- `browser_ua.txt`: the exact User-Agent bound to the session;
- `hash_overrides.json`: current persisted-query hashes observed in Chrome;
- `chrome-profile/`: the isolated browser profile.

## Configure

Copy `planner.env.example` to `.env` and review it. Find your store's numeric ID
with `store_search`, then set it locally.

Keep the server read-only while testing:

```dotenv
HEB_DEFAULT_STORE=123
HEB_WRITE_SCOPE=read-only
PLANNER_IMPORT_PACK_PATH=/absolute/path/to/heb_list_import_pack.json
```

After product and list reads pass, enable add-only list staging:

```dotenv
HEB_WRITE_SCOPE=shopping-list
```

Background browser traffic and automatic hash rediscovery are disabled by
default. They can be enabled explicitly later if the low-volume manual path is
not reliable enough.

`AUTO_REFRESH_ENABLED=false` prevents ordinary read tools from quietly launching
a browser. Renew the session by running `scripts/capture_session.py` against the
dedicated browser explicitly.

## Run

```bash
.venv/bin/heb-planner-mcp
```

Before wiring an MCP client, run the read-only smoke check:

```bash
.venv/bin/python scripts/smoke_test_read_only.py
```

Generic stdio MCP configuration:

```json
{
  "mcpServers": {
    "heb-planner": {
      "command": "/absolute/path/to/texas-grocery-mcp/.venv/bin/heb-planner-mcp",
      "cwd": "/absolute/path/to/texas-grocery-mcp"
    }
  }
}
```

## Planner Workflow

1. Load the grocery planner's generated review pack.
2. Use `planner_queue_get` to read `ready` rows from the configured pack.
3. Use `product_search` for each approved search term at the configured store.
4. Compare exact item, package size, unit price, sale state, and availability.
5. Keep ambiguous products in review; never select the first result blindly.
6. Preview the exact product IDs and quantities with `shopping_list_add_many`.
7. After explicit review, repeat with `confirm=true`.
8. Read the H-E-B list back and compare names, quantities, prices, and failures.
9. Use H-E-B's normal list-to-cart flow and checkout manually.

The source planner currently produces
`outputs/heb_list_import_pack_real_week.json`, including `ready` versus
`review_first` groups, search terms, quantities, warnings, and historical price
estimates.

## Tool Surface

Always available:

- `store_search`, `store_get_default`
- `product_search`, `product_search_batch`, `product_get`
- `planner_queue_get`
- `shopping_list_check_auth`, `shopping_list_get`
- `cart_check_auth`, `cart_get`
- `coupon_list`, `coupon_search`, `coupon_categories`, `coupon_clipped`
- `session_status`
- `health_live`, `health_ready`

Only with `HEB_WRITE_SCOPE=shopping-list`:

- `shopping_list_add`
- `shopping_list_add_many`

## Test

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src
```

Live tests are opt-in and must remain read-only until the session and current
hashes have been verified.

## Sources And Limits

- [H-E-B shopping-list help](https://www.heb.com/help/shopping-lists)
- [H-E-B terms](https://www.heb.com/terms)
- [Original MCP project](https://github.com/mgwalkerjr95/texas-grocery-mcp)
- [Shopping-list contribution](https://github.com/mgwalkerjr95/texas-grocery-mcp/pull/13)
- [Real-Chrome session work](https://github.com/mgwalkerjr95/texas-grocery-mcp/pull/20)

Use low request volumes and review H-E-B's current terms before use. A browser-UI
fallback remains the escape hatch when private interfaces change.
