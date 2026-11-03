"""Virtual kits ("combos"): read-only planning helpers. Nothing here writes to the account.

Source: official page "Kits virtuales" (Mercado Libre developers, updated 17/09/2026). Facts used here:
a kit has 2 to 6 distinct User Products, at most 10 units of each, all new; its stock is computed from the
components; its composition cannot be edited after publishing; the price is manual or synchronized with the
components through `automatic_price.discount` (same decimal 0-1 for every component); the thumbnail is a
picture id obtained from POST /pictures/items/upload. The kit itself is published with POST /items/kits.
"""
from __future__ import annotations

from typing import Any

MIN_COMPONENTS = 2
MAX_COMPONENTS = 6
MAX_UNITS = 10
THUMBNAIL_PLACEHOLDER = "<PICTURE_ID devuelto por meli_upload_picture>"


def item_summary(item: dict[str, Any]) -> dict[str, Any]:
    pictures = [p.get("secure_url") or p.get("url") for p in item.get("pictures", []) if isinstance(p, dict)]
    return {
        "item_id": item.get("id"),
        "title": item.get("title"),
        "price": item.get("price"),
        "currency_id": item.get("currency_id"),
        "status": item.get("status"),
        "condition": item.get("condition"),
        "available_quantity": item.get("available_quantity"),
        "listing_type_id": item.get("listing_type_id"),
        "category_id": item.get("category_id"),
        "user_product_id": item.get("user_product_id"),
        "thumbnail": item.get("secure_thumbnail") or item.get("thumbnail"),
        "pictures": [u for u in pictures if u][:4],
    }


def suggest_title(titles: list[str], quantities: list[int]) -> str:
    parts = [f"{q} x {t}" if q > 1 else t for t, q in zip(titles, quantities)]
    return "Combo: " + " + ".join(parts)


def kit_body(*, family_name: str, components: list[dict[str, Any]], currency_id: str, listing_type_id: str, price: float | None, discount_pct: float | None) -> dict[str, Any]:
    """Body of POST /items/kits as documented. Manual `price`, or `discount_pct` to synchronize with the components."""
    automatic = {"discount": round(discount_pct / 100, 4)} if discount_pct else None
    body: dict[str, Any] = {
        "family_name": family_name,
        "channels": ["marketplace"],
        "thumbnail": {"id": THUMBNAIL_PLACEHOLDER},
        "currency_id": currency_id,
        "listing_type_id": listing_type_id,
        "official_store_id": None,
        "bundle": {"type": "kit", "components": [{"type": "user_product", "user_product_id": c["user_product_id"], "quantity": c["quantity"], "automatic_price": automatic} for c in components]},
    }
    if automatic is None and price is not None:
        body["price"] = price
    return body
