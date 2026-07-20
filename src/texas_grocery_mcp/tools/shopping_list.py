"""Shopping list MCP tools with human-in-the-loop confirmation."""

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, ValidationError

from texas_grocery_mcp.auth.session import (
    check_auth,
    ensure_session,
    get_auth_instructions,
    is_authenticated,
)
from texas_grocery_mcp.models.shopping_list import (
    GetShoppingListsV2Response,
    GetShoppingListV2Response,
    ShoppingListItem,
)
from texas_grocery_mcp.state import StateManager

if TYPE_CHECKING:
    from texas_grocery_mcp.clients.graphql import HEBGraphQLClient


def _get_client() -> "HEBGraphQLClient":
    """Get or create GraphQL client."""
    return StateManager.get_graphql_client_sync()


def _parse_shopping_lists(result: dict[str, Any]) -> GetShoppingListsV2Response | None:
    """Parse the getShoppingListsV2 response into a typed model.

    Returns None if the response cannot be parsed.
    """
    raw = result.get("getShoppingListsV2")
    if not isinstance(raw, dict):
        return None
    try:
        return GetShoppingListsV2Response.model_validate(raw)
    except ValidationError:
        return None


def _resolve_list_id(
    result: dict[str, Any], list_name: str | None = None
) -> tuple[str | None, str | None]:
    """Resolve a list ID from a getShoppingListsV2 response.

    Resolution order:
      1. Explicit list_name parameter
      2. Configured default (StateManager.get_default_shopping_list_name())
      3. First list (fallback when no name preference is set)

    Returns (list_id, error_message). If a name is requested but not found,
    returns (None, error_message) so the caller can surface a clear error.
    """
    parsed = _parse_shopping_lists(result)
    if not parsed or not parsed.lists:
        return None, None

    target_name = list_name or StateManager.get_default_shopping_list_name()
    if target_name:
        for lst in parsed.lists:
            if lst.name.lower() == target_name.lower():
                return lst.id, None
        available = [lst.name for lst in parsed.lists]
        return None, (f"Shopping list '{target_name}' not found. Available lists: {available}")

    # No name preference — use first list
    return parsed.lists[0].id, None


def _parse_shopping_list_items(result: dict[str, Any]) -> list[ShoppingListItem]:
    """Parse the getShoppingListV2 response into a list of ShoppingListItems.

    Items are nested under itemPage.items in the response.
    Returns an empty list if the response cannot be parsed.
    """
    try:
        raw = result.get("getShoppingListV2")
        if not isinstance(raw, dict):
            return []
        return GetShoppingListV2Response.model_validate(raw).item_page.items
    except Exception:
        return []


def _find_item_by_product_id(
    items: list[ShoppingListItem], product_id: str
) -> ShoppingListItem | None:
    """Find a ShoppingListItem by product ID."""
    for item in items:
        if str(item.product.id) == str(product_id):
            return item
    return None


def shopping_list_check_auth() -> dict[str, Any]:
    """Check if authenticated for shopping list operations.

    Returns authentication status and instructions if not authenticated.
    Use this before attempting shopping list operations.
    """
    return check_auth()


@ensure_session
async def shopping_list_get(
    list_name: Annotated[
        str | None,
        Field(
            description=(
                "Shopping list name to view. Omit to use the configured default "
                "(HEB_DEFAULT_SHOPPING_LIST) or the first list."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Get the current shopping list contents.

    Returns the default shopping list with its name, item count, and store.
    Specify list_name to view a specific list by name.
    """
    if not is_authenticated():
        return {
            "auth_required": True,
            "message": "Login required to view shopping list",
            "instructions": get_auth_instructions(),
        }

    client = _get_client()
    try:
        # Fetch list metadata (name, store, item count)
        lists_result = await client.get_shopping_lists()
        if lists_result.get("error"):
            return lists_result

        list_id, err = _resolve_list_id(lists_result, list_name)
        if not list_id:
            if err:
                return {"error": True, "code": "LIST_NOT_FOUND", "message": err}
            return {"list_id": None, "message": "No shopping lists found"}

        # Re-parse to get the full preview object for metadata
        parsed = _parse_shopping_lists(lists_result)
        target_list = next(
            (lst for lst in (parsed.lists if parsed else []) if lst.id == list_id), None
        )
        store = None
        if target_list and target_list.fulfillment and target_list.fulfillment.store:
            s = target_list.fulfillment.store
            store = {"store_number": s.store_number, "name": s.name}

        # Fetch full item details so Claude can resolve product names to IDs
        items_result = await client.get_shopping_list_items(list_id=list_id)
        items = []
        if not items_result.get("error"):
            for item in _parse_shopping_list_items(items_result):
                items.append(
                    {
                        "product_id": item.product.id,
                        "name": item.product.full_display_name,
                        "quantity": item.quantity,
                        "category": item.group_header,
                        "unit_price": item.item_price.sale_price,
                        "total_price": item.item_price.total_amount,
                        "on_sale": item.item_price.on_sale,
                    }
                )

        list_display_name = target_list.name if target_list else list_id
        total_count = target_list.total_item_count if target_list else len(items)
        return {
            "list_id": list_id,
            "name": list_display_name,
            "total_item_count": total_count,
            "is_active": target_list.is_active if target_list else None,
            "store": store,
            "updated": target_list.updated.isoformat() if target_list else None,
            "items": items,
            "message": f"Shopping list '{list_display_name}' has {total_count} item(s)",
        }
    except Exception as e:
        return {
            "error": True,
            "code": "SHOPPING_LIST_GET_FAILED",
            "message": f"Failed to get shopping list: {e!s}",
        }


@ensure_session
async def shopping_list_add(
    product_id: Annotated[
        str,
        Field(
            description="HEB product ID (short numeric ID from search results)",
            min_length=1,
        ),
    ],
    quantity: Annotated[
        int,
        Field(description="Desired quantity for this product on the list", ge=1, le=20),
    ] = 1,
    confirm: Annotated[bool, Field(description="Set to true to confirm the action")] = False,
    list_name: Annotated[
        str | None,
        Field(
            description=(
                "Shopping list name to add to. Omit to use the configured default "
                "(HEB_DEFAULT_SHOPPING_LIST) or the first list."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Add an item to the shopping list with an explicit target quantity.

    Without confirm=true, returns a preview of the action.
    With confirm=true, adds a product that is not yet present and verifies the
    resulting minimum quantity with a fresh list read. H-E-B's add mutation does
    not reliably increase an existing list line, so that case stops before writing.
    """
    # Validate product_id
    product_id = product_id.strip()
    if not product_id:
        return {
            "error": True,
            "code": "INVALID_PRODUCT_ID",
            "message": "Product ID cannot be empty or whitespace.",
        }

    # Check authentication first
    if not is_authenticated():
        return {
            "auth_required": True,
            "message": "Login required for shopping list operations",
            "instructions": get_auth_instructions(),
        }

    # If not confirmed, return preview
    if not confirm:
        return {
            "preview": True,
            "action": "add_to_shopping_list",
            "product_id": product_id,
            "quantity": quantity,
            "message": (
                f"Set confirm=true to add {quantity}x product {product_id} to the shopping list"
            ),
        }

    client = _get_client()
    try:
        # Resolve the default list ID
        lists_result = await client.get_shopping_lists()
        if lists_result.get("error"):
            return lists_result

        list_id, err = _resolve_list_id(lists_result, list_name)
        if not list_id:
            if err:
                return {"error": True, "code": "LIST_NOT_FOUND", "message": err}
            return {
                "error": True,
                "code": "NO_SHOPPING_LIST",
                "message": "No shopping list found. Create one on HEB.com first.",
            }

        # Snapshot before writing. This makes the result honest when the item was
        # already present and prevents a blind mutation when list reads are broken.
        items_before_result = await client.get_shopping_list_items(list_id=list_id)
        if items_before_result.get("error"):
            return {
                "error": True,
                "code": "SHOPPING_LIST_PRECHECK_FAILED",
                "message": (
                    "Could not read the shopping list before adding; no mutation was attempted."
                ),
                "mutation_attempted": False,
            }
        before_item = _find_item_by_product_id(
            _parse_shopping_list_items(items_before_result), product_id
        )
        before_quantity = before_item.quantity if before_item else None
        # Preserve the add-only boundary. The requested quantity is a minimum;
        # an add call must never lower a larger quantity already on the list.
        if before_quantity is not None and before_quantity >= quantity:
            return {
                "success": True,
                "verified": True,
                "action": "add_to_shopping_list",
                "product_id": product_id,
                "list_id": list_id,
                "quantity": quantity,
                "requested_minimum_quantity": quantity,
                "before_quantity": before_quantity,
                "actual_quantity": before_quantity,
                "changed": False,
                "already_satisfied": True,
                "mutation_attempted": False,
                "message": (
                    f"Product {product_id} is already on the shopping list with "
                    f"quantity {before_quantity}, satisfying the requested minimum "
                    f"of {quantity}; no mutation was needed."
                ),
            }

        if before_quantity is not None:
            return {
                "success": False,
                "error": True,
                "code": "EXISTING_QUANTITY_UPDATE_UNSUPPORTED",
                "message": (
                    f"Product {product_id} is already on the shopping list with "
                    f"quantity {before_quantity}. H-E-B's add mutation does not "
                    f"reliably increase it to {quantity}; no mutation was attempted."
                ),
                "product_id": product_id,
                "quantity": quantity,
                "requested_minimum_quantity": quantity,
                "before_quantity": before_quantity,
                "actual_quantity": before_quantity,
                "mutation_attempted": False,
                "do_not_retry_automatically": True,
            }

        # Execute the add via GraphQL API
        result = await client.add_to_shopping_list(
            list_id=list_id, product_id=product_id, quantity=quantity
        )
        if result.get("error"):
            return result

        # VERIFY: Fetch list items and require the exact requested quantity.
        items_result = await client.get_shopping_list_items(list_id=list_id)
        if items_result.get("error"):
            return {
                "success": False,
                "error": True,
                "code": "SHOPPING_LIST_VERIFICATION_UNAVAILABLE",
                "message": (
                    "H-E-B accepted the mutation request, but the list could not be read back. "
                    "The item may have been added; inspect the list before retrying."
                ),
                "product_id": product_id,
                "quantity": quantity,
                "before_quantity": before_quantity,
                "mutation_attempted": True,
                "mutation_may_have_succeeded": True,
                "do_not_retry_automatically": True,
            }

        items = _parse_shopping_list_items(items_result)
        after_item = _find_item_by_product_id(items, product_id)
        if not after_item:
            return {
                "error": True,
                "code": "SHOPPING_LIST_ADD_NOT_VERIFIED",
                "message": "H-E-B returned success, but the product is not on the list.",
                "product_id": product_id,
                "quantity": quantity,
                "before_quantity": before_quantity,
            }

        actual_quantity = after_item.quantity
        if actual_quantity != quantity:
            return {
                "error": True,
                "code": "SHOPPING_LIST_QUANTITY_MISMATCH",
                "message": (
                    f"Product {product_id} is on the list with quantity {actual_quantity}; "
                    f"the requested quantity was {quantity}."
                ),
                "product_id": product_id,
                "quantity": quantity,
                "before_quantity": before_quantity,
                "actual_quantity": actual_quantity,
                "do_not_retry_automatically": True,
            }

        return {
            "success": True,
            "verified": True,
            "action": "add_to_shopping_list",
            "product_id": product_id,
            "list_id": list_id,
            "quantity": quantity,
            "before_quantity": before_quantity,
            "actual_quantity": actual_quantity,
            "changed": before_quantity != actual_quantity,
            "already_satisfied": False,
            "mutation_attempted": True,
            "message": (
                f"Shopping-list quantity for product {product_id} is {actual_quantity} (verified)."
            ),
        }
    except Exception as e:
        return {
            "error": True,
            "code": "SHOPPING_LIST_ADD_FAILED",
            "message": f"Failed to add item to shopping list: {e!s}",
        }


@ensure_session
async def shopping_list_add_with_retry(
    product_id: Annotated[str, Field(description="HEB product ID", min_length=1)],
    quantity: Annotated[int, Field(description="Number of units to add", ge=1)] = 1,
    confirm: Annotated[bool, Field(description="Set to true to confirm")] = False,
    auto_correct_id: Annotated[
        bool, Field(description="Search for the product and retry with corrected ID if add fails")
    ] = True,
    list_name: Annotated[
        str | None,
        Field(
            description=(
                "Shopping list name to add to. Omit to use the configured default "
                "(HEB_DEFAULT_SHOPPING_LIST) or the first list."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Add item to shopping list with automatic ID correction on failure.

    If the initial add fails and auto_correct_id=True, this will search for
    the product by name/ID and retry with the correct product_id from search results.

    This is a more resilient version of shopping_list_add that can recover from
    incorrect product IDs by looking up the product.
    """
    # First attempt with provided ID
    result = await shopping_list_add(
        product_id=product_id,
        quantity=quantity,
        confirm=confirm,
        list_name=list_name,
    )

    # If not confirming or if it succeeded or is a preview, return as-is
    if not confirm or result.get("success") or result.get("preview"):
        return result

    # If auth is required, surface that immediately
    if result.get("auth_required"):
        return result

    # If failed with SHOPPING_LIST_ADD_NOT_VERIFIED and auto-correct is enabled, search and retry
    if result.get("code") == "SHOPPING_LIST_ADD_NOT_VERIFIED" and auto_correct_id:
        from texas_grocery_mcp.tools.product import product_search
        from texas_grocery_mcp.tools.store import get_default_store_id

        store_id = get_default_store_id()
        if not store_id:
            result["auto_correct_attempted"] = False
            result["auto_correct_reason"] = "No default store set"
            return result

        try:
            # Search using the product_id as the query to find matching products
            search_result = await product_search(
                query=product_id,
                store_id=store_id,
                limit=5,
            )

            products = search_result.get("products", [])
            if not products:
                result["auto_correct_attempted"] = True
                result["auto_correct_reason"] = f"No products found for '{product_id}'"
                return result

            # Try each product until one succeeds
            for product in products:
                correct_product_id = product.get("product_id")

                # Skip placeholder or invalid IDs
                if not correct_product_id or str(correct_product_id).startswith("suggestion-"):
                    continue

                retry_result = await shopping_list_add(
                    product_id=correct_product_id,
                    quantity=quantity,
                    confirm=True,
                    list_name=list_name,
                )

                if retry_result.get("success"):
                    retry_result["auto_corrected"] = True
                    retry_result["original_product_id"] = product_id
                    retry_result["corrected_product_id"] = correct_product_id
                    return retry_result

            result["auto_correct_attempted"] = True
            result["auto_correct_reason"] = "Found products but retry still failed"

        except Exception as e:
            result["auto_correct_attempted"] = True
            result["auto_correct_reason"] = f"Search failed: {e!s}"

    return result


@ensure_session
async def shopping_list_add_many(
    items: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "List of items to add. Each item must have: "
                "product_id (short numeric ID from search results), target quantity (>=1). "
                "Maximum 100 items per call."
            ),
        ),
    ],
    confirm: Annotated[
        bool,
        Field(
            description=(
                "Set to True to execute the bulk add. "
                "Default False shows a preview of items to be added."
            )
        ),
    ] = False,
    list_name: Annotated[
        str | None,
        Field(
            description=(
                "Shopping list name to add to. Omit to use the configured default "
                "(HEB_DEFAULT_SHOPPING_LIST) or the first list."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Add multiple items to the shopping list with a single confirmation.

    More efficient than calling shopping_list_add multiple times and provides
    a single confirmation gate for the entire batch.

    IMPORTANT: This operation uses STRICT success semantics. If ANY item
    fails to add, the entire operation is reported as a FAILURE. Items that
    were successfully added will remain on the list, but you'll receive
    a clear list of which items failed.

    Args:
        items: List of items, each with product_id and quantity
        confirm: Must be True to actually add items (human-in-the-loop safety)

    Returns:
        On success: All items added with details
        On failure: List of failed items with reasons (successful items stay on the list)
    """
    import structlog

    logger = structlog.get_logger()

    # Validate item count
    if not items:
        return {
            "error": True,
            "code": "NO_ITEMS",
            "message": "No items provided. Provide a list of items to add.",
        }

    if len(items) > 100:
        return {
            "error": True,
            "code": "TOO_MANY_ITEMS",
            "message": f"Maximum 100 items per call. You provided {len(items)}.",
        }

    # Check authentication
    if not is_authenticated():
        return {
            "auth_required": True,
            "message": "Login required for shopping list operations",
            "instructions": get_auth_instructions(),
        }

    # Validate each item and build normalized list
    validated_items = []
    validation_errors = []

    for idx, item in enumerate(items):
        item_errors = []

        # Check required fields
        product_id = item.get("product_id")
        quantity = item.get("quantity", 1)

        if not product_id:
            item_errors.append("missing product_id")
        elif not str(product_id).strip():
            item_errors.append("product_id is empty")

        if quantity is None:
            item_errors.append("missing quantity")
        elif not isinstance(quantity, int) or quantity < 1:
            item_errors.append("quantity must be integer >= 1")
        elif quantity > 20:
            item_errors.append("quantity must be <= 20")

        if item_errors:
            validation_errors.append(
                {
                    "index": idx,
                    "item": item,
                    "errors": item_errors,
                }
            )
        else:
            validated_items.append(
                {
                    "product_id": str(product_id).strip(),
                    "quantity": quantity,
                }
            )

    # Return validation errors before doing anything
    if validation_errors:
        return {
            "error": True,
            "code": "VALIDATION_ERROR",
            "message": f"{len(validation_errors)} item(s) have validation errors.",
            "validation_errors": validation_errors,
            "valid_items": len(validated_items),
        }

    # Preview mode - return what would be added
    if not confirm:
        return {
            "preview": True,
            "items_to_add": validated_items,
            "count": len(validated_items),
            "message": (
                f"Review {len(validated_items)} item(s) above. Call with confirm=True "
                "to add all to shopping list."
            ),
        }

    # Execute mode - resolve list ID then add all items
    client = _get_client()

    # Resolve the default list ID once for the entire batch
    lists_result = await client.get_shopping_lists()
    if lists_result.get("error"):
        return lists_result

    list_id, err = _resolve_list_id(lists_result, list_name)
    if not list_id:
        if err:
            return {"error": True, "code": "LIST_NOT_FOUND", "message": err}
        return {
            "error": True,
            "code": "NO_SHOPPING_LIST",
            "message": "No shopping list found. Create one on HEB.com first.",
        }

    items_before_result = await client.get_shopping_list_items(list_id=list_id)
    if items_before_result.get("error"):
        return {
            "error": True,
            "code": "SHOPPING_LIST_PRECHECK_FAILED",
            "message": (
                "Could not read the shopping list before adding; no mutations were attempted."
            ),
            "mutation_attempted": False,
        }
    items_before = {
        str(item.product.id): item for item in _parse_shopping_list_items(items_before_result)
    }

    unsupported_updates = []
    for item in validated_items:
        before_item = items_before.get(item["product_id"])
        if before_item is not None and before_item.quantity < item["quantity"]:
            unsupported_updates.append(
                {
                    "product_id": item["product_id"],
                    "quantity": item["quantity"],
                    "requested_minimum_quantity": item["quantity"],
                    "before_quantity": before_item.quantity,
                    "actual_quantity": before_item.quantity,
                    "code": "EXISTING_QUANTITY_UPDATE_UNSUPPORTED",
                }
            )
    if unsupported_updates:
        return {
            "success": False,
            "error": True,
            "code": "EXISTING_QUANTITY_UPDATE_UNSUPPORTED",
            "message": (
                "One or more products already exist below the requested minimum. "
                "H-E-B's add mutation does not reliably increase existing list lines; "
                "no mutations were attempted."
            ),
            "failed": unsupported_updates,
            "mutation_attempted": False,
            "do_not_retry_automatically": True,
        }

    # Track results
    added_items = []
    failed_items = []

    # Add items one by one
    for item in validated_items:
        product_id = item["product_id"]
        quantity = item["quantity"]
        before_item = items_before.get(product_id)
        before_quantity = before_item.quantity if before_item else None

        # The mutation sets an absolute quantity, so skip it whenever the
        # existing quantity already satisfies the requested minimum.
        if before_quantity is not None and before_quantity >= quantity:
            added_items.append(
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "requested_minimum_quantity": quantity,
                    "before_quantity": before_quantity,
                    "mutation_attempted": False,
                }
            )
            continue

        try:
            result = await client.add_to_shopping_list(
                list_id=list_id,
                product_id=product_id,
                quantity=quantity,
            )

            if result.get("error"):
                failed_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "error": result.get("message", "Add failed"),
                        "code": result.get("code", "ADD_FAILED"),
                    }
                )
            else:
                added_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "before_quantity": (before_quantity),
                        "mutation_attempted": True,
                    }
                )

        except Exception as e:
            logger.warning(
                "shopping_list_add_many item failed",
                product_id=product_id,
                error=str(e),
            )
            failed_items.append(
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "error": str(e),
                    "code": "EXCEPTION",
                }
            )

    # Verify items in list after all adds and enrich with name/price data
    items_after_result = await client.get_shopping_list_items(list_id=list_id)
    if items_after_result.get("error"):
        return {
            "success": False,
            "error": True,
            "code": "SHOPPING_LIST_VERIFICATION_UNAVAILABLE",
            "message": (
                "H-E-B accepted one or more mutation requests, but the list could not be "
                "read back. Inspect the list before retrying."
            ),
            "requested": validated_items,
            "mutation_results": {"accepted": added_items, "failed": failed_items},
            "mutation_may_have_succeeded": any(item["mutation_attempted"] for item in added_items),
            "do_not_retry_automatically": True,
        }

    items_after = {
        str(item.product.id): item for item in _parse_shopping_list_items(items_after_result)
    }
    verified_added = []
    for item in added_items:
        list_item = items_after.get(item["product_id"])
        if list_item and list_item.quantity >= item["quantity"]:
            verified_added.append(
                {
                    "product_id": item["product_id"],
                    "quantity": item["quantity"],
                    "requested_minimum_quantity": item["quantity"],
                    "before_quantity": item["before_quantity"],
                    "actual_quantity": list_item.quantity,
                    "changed": item["before_quantity"] != list_item.quantity,
                    "already_satisfied": (
                        item["before_quantity"] is not None
                        and item["before_quantity"] >= item["quantity"]
                    ),
                    "mutation_attempted": item["mutation_attempted"],
                    "name": list_item.product.full_display_name,
                    "unit_price": list_item.item_price.sale_price,
                    "total_price": list_item.item_price.total_amount,
                    "on_sale": list_item.item_price.on_sale,
                }
            )
        else:
            failed_items.append(
                {
                    "product_id": item["product_id"],
                    "quantity": item["quantity"],
                    "actual_quantity": list_item.quantity if list_item else None,
                    "error": "Requested quantity not found in list after add",
                    "code": "VERIFICATION_FAILED",
                    "do_not_retry_automatically": True,
                }
            )
    added_items = verified_added

    # Calculate total cost of verified items
    total_cost = sum(item.get("total_price", 0.0) for item in added_items)

    # Build summary
    summary = {
        "requested": len(validated_items),
        "added": len(added_items),
        "failed": len(failed_items),
        "total_cost": round(total_cost, 2),
    }

    # Strict semantics: any failure = operation failure
    if failed_items:
        return {
            "success": False,
            "error": True,
            "code": "PARTIAL_FAILURE",
            "message": (
                f"{len(failed_items)} of {len(validated_items)} item(s) could not be added "
                "to the shopping list."
            ),
            "added": added_items,
            "failed": failed_items,
            "summary": summary,
            "note": (
                "Successfully added items remain on the list. Review failed items and retry "
                "if needed."
            ),
        }

    # All items added successfully
    return {
        "success": True,
        "added": added_items,
        "summary": summary,
        "message": f"All {len(added_items)} item(s) added to shopping list successfully.",
    }


@ensure_session
async def shopping_list_remove(
    product_id: Annotated[
        str,
        Field(
            description="HEB product ID to remove from the shopping list",
            min_length=1,
        ),
    ],
    confirm: Annotated[bool, Field(description="Set to true to confirm the action")] = False,
    list_name: Annotated[
        str | None,
        Field(
            description=(
                "Shopping list name to remove from. Omit to use the configured default "
                "(HEB_DEFAULT_SHOPPING_LIST) or the first list."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Remove an item from the shopping list by product ID.

    Without confirm=true, fetches the list items and returns a preview showing
    the item name (so the user can confirm they have the right product).
    With confirm=true, resolves both listId and itemId (UUID), then deletes.

    Returns an error if the product is not on the shopping list.
    """
    # Validate product_id
    product_id = product_id.strip()
    if not product_id:
        return {
            "error": True,
            "code": "INVALID_PRODUCT_ID",
            "message": "Product ID cannot be empty or whitespace.",
        }

    # Check authentication first
    if not is_authenticated():
        return {
            "auth_required": True,
            "message": "Login required for shopping list operations",
            "instructions": get_auth_instructions(),
        }

    client = _get_client()
    try:
        # Resolve the default list ID
        lists_result = await client.get_shopping_lists()
        if lists_result.get("error"):
            return lists_result

        list_id, err = _resolve_list_id(lists_result, list_name)
        if not list_id:
            if err:
                return {"error": True, "code": "LIST_NOT_FOUND", "message": err}
            return {
                "error": True,
                "code": "NO_SHOPPING_LIST",
                "message": "No shopping list found.",
            }

        # Fetch list items to resolve the item UUID for this product
        items_result = await client.get_shopping_list_items(list_id=list_id)
        if items_result.get("error"):
            return items_result

        items = _parse_shopping_list_items(items_result)
        matched_item = _find_item_by_product_id(items, product_id)
        if not matched_item:
            return {
                "error": True,
                "code": "PRODUCT_NOT_ON_LIST",
                "message": f"Product {product_id} is not on the shopping list.",
            }

        item_id = matched_item.id  # UUID for deleteShoppingListItems
        item_name = matched_item.product.full_display_name

        # If not confirmed, return preview with item details
        if not confirm:
            return {
                "preview": True,
                "action": "remove_from_shopping_list",
                "product_id": product_id,
                "item_id": item_id,
                "name": item_name,
                "message": "Set confirm=true to remove this item from the shopping list",
            }

        # Execute the removal via GraphQL API using the item UUID
        result = await client.delete_shopping_list_items(list_id=list_id, item_ids=[item_id])
        if result.get("error"):
            return result

        return {
            "success": True,
            "action": "remove_from_shopping_list",
            "product_id": product_id,
            "item_id": item_id,
            "name": item_name,
            "message": f"Removed '{item_name or product_id}' from shopping list",
        }
    except Exception as e:
        return {
            "error": True,
            "code": "SHOPPING_LIST_REMOVE_FAILED",
            "message": f"Failed to remove item from shopping list: {e!s}",
        }
