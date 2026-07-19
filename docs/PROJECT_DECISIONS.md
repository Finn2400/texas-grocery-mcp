# Project Decisions And Verified State

Last updated: 2026-07-19

## Goal

Connect the existing local grocery planner to H-E-B with the least fragile
workflow that still provides live product selection and meaningful automation.

The target workflow is:

`planner review pack -> exact H-E-B product review -> H-E-B shopping list -> manual list-to-cart -> manual checkout`

## Decisions

1. Use H-E-B shopping lists as the automation boundary. Do not automate checkout.
2. Use an isolated real-Chrome profile for manual login. Do not store credentials.
3. Capture only H-E-B cookies, localStorage, User-Agent, and observed operation hashes.
4. Run the restricted `heb-planner-mcp` entry point. The broad upstream server is
   retained for comparison but is not the supported planner entry point.
5. Default to read-only. Add-only shopping-list tools require the explicit
   `HEB_WRITE_SCOPE=shopping-list` setting and per-call confirmation.
6. Do not expose remove/delete, cart-add, coupon-clip, store-change, credential,
   cancellation, payment, account, or checkout tools.
7. Keep background refresh and browser-based hash rediscovery disabled by default.
8. Keep the user's preferred store ID and planner-pack path in ignored local config.

## Verified

- Fork created at `Finn2400/texas-grocery-mcp` and cloned locally.
- Upstream baseline tests passed: 296 tests.
- Session-reliability branch and shopping-list contribution were merged locally.
- Combined suite passed before planner-specific edits: 347 tests, 8 live tests skipped.
- Current planner-safe suite: 356 passed, 8 live tests skipped; lint and strict typing pass.
- Live store search succeeded on 2026-07-19.
- Live unauthenticated product fallback failed because `typeaheadContent` had a stale
  persisted-query hash.
- Live unauthenticated product details hit H-E-B's security challenge.
- Manual real-Chrome capture succeeded without credential storage.
- Authenticated SSR product search succeeded, but its requested store ID is not
  applied to the SSR request; results now expose this as unverified session-context pricing.
- Current shopping-list query and add hashes were captured from normal browser activity.
- Shopping-list read, dry-run preview, exact add, idempotent skip, and independent
  read-back all passed in a three-item live acceptance test.
- The local Codex installation has an enabled `heb-planner` stdio MCP registration.

## Not Yet Verified

- End-to-end import of the planner's six currently ready items.
- Exact target-store candidate and price review for those six items.
- Long-duration session behavior across a normal weekly planning run.

## Next Tests

1. Resolve the planner's six ready items to exact candidates.
2. Verify target-list prices and package semantics, especially weighted products.
3. Present one reviewed batch with exact IDs, quantities, and expected list prices.
4. With explicit approval, stage that batch and verify it by reading the list back.

## Fallbacks

- If direct product SSR fails, use the dedicated real browser for visible search and
  capture the resulting product IDs/prices.
- If persisted hashes rotate, observe fresh hashes from normal browser activity and
  write them to `hash_overrides.json`; automated rediscovery remains opt-in.
- If list mutation stops working, generate H-E-B search links and use normal browser
  list additions rather than escalating to checkout automation.
- If H-E-B blocks all programmatic interfaces, retain the local planner, review pack,
  and receipt analytics; only the final H-E-B handoff becomes manual.
