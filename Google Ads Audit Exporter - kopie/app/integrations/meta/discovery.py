from __future__ import annotations

from typing import Any

from app.integrations.meta.client import MetaGraphClient
from app.integrations.meta.models import MetaConnection, MetaDiscoverySnapshot


def _string(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_by_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []

    for row in rows:
        row_id = _string(row.get("id"))
        if row_id and row_id in seen:
            continue
        if row_id:
            seen.add(row_id)
        deduped.append(row)

    return deduped


def _safe_get(
    *,
    client: MetaGraphClient,
    snapshot: MetaDiscoverySnapshot,
    endpoint: str,
    params: dict[str, Any],
    warning_context: str,
    raw_key: str | None = None,
) -> dict[str, Any]:
    try:
        row = client.get(endpoint, params=params)
    except Exception as exc:
        snapshot.warnings.append(f"{warning_context} failed: {exc}")
        return {}

    if raw_key:
        snapshot.raw_snapshots[raw_key] = row

    return row


def _safe_paginate(
    *,
    client: MetaGraphClient,
    snapshot: MetaDiscoverySnapshot,
    endpoint: str,
    params: dict[str, Any],
    warning_context: str,
    raw_key: str | None = None,
) -> list[dict[str, Any]]:
    try:
        rows = list(client.paginate(endpoint, params=params))
    except Exception as exc:
        snapshot.warnings.append(f"{warning_context} failed: {exc}")
        return []

    if raw_key:
        snapshot.raw_snapshots[raw_key] = rows

    return rows


def _append_catalog_children_raw(
    snapshot: MetaDiscoverySnapshot,
    *,
    catalog_id: str,
    product_sets: list[dict[str, Any]],
    product_feeds: list[dict[str, Any]],
) -> None:
    product_sets_by_catalog = snapshot.raw_snapshots.setdefault("product_sets_by_catalog", {})
    if isinstance(product_sets_by_catalog, dict):
        product_sets_by_catalog[catalog_id] = product_sets

    product_feeds_by_catalog = snapshot.raw_snapshots.setdefault("product_feeds_by_catalog", {})
    if isinstance(product_feeds_by_catalog, dict):
        product_feeds_by_catalog[catalog_id] = product_feeds


def _append_account_pixels_raw(
    snapshot: MetaDiscoverySnapshot,
    *,
    ad_account_id: str,
    pixels: list[dict[str, Any]],
) -> None:
    pixels_by_account = snapshot.raw_snapshots.setdefault("pixels_by_ad_account", {})
    if isinstance(pixels_by_account, dict):
        pixels_by_account[ad_account_id] = pixels


def run_meta_discovery(connection: MetaConnection) -> MetaDiscoverySnapshot:
    client = MetaGraphClient(connection)
    snapshot = MetaDiscoverySnapshot(connection_key=connection.key)

    if not connection.business_id:
        snapshot.warnings.append(
            "Meta Business ID neni vyplneny. Discovery Business assetu, ad accountu, pixelu a katalogu bylo preskoceno."
        )
        return snapshot

    business_id = _string(connection.business_id)

    business = _safe_get(
        client=client,
        snapshot=snapshot,
        endpoint=business_id,
        params={"fields": "id,name,created_time,verification_status"},
        warning_context=f"Business discovery for {business_id}",
        raw_key="business",
    )
    if business:
        snapshot.businesses.append(business)

    owned_ad_accounts = _safe_paginate(
        client=client,
        snapshot=snapshot,
        endpoint=f"{business_id}/owned_ad_accounts",
        params={
            "fields": (
                "id,name,account_id,account_status,currency,timezone_name,"
                "business,campaigns.limit(1)"
            )
        },
        warning_context=f"Owned ad accounts discovery for business {business_id}",
        raw_key="owned_ad_accounts",
    )

    client_ad_accounts = _safe_paginate(
        client=client,
        snapshot=snapshot,
        endpoint=f"{business_id}/client_ad_accounts",
        params={"fields": "id,name,account_id,account_status,currency,timezone_name,business"},
        warning_context=f"Client ad accounts discovery for business {business_id}",
        raw_key="client_ad_accounts",
    )

    snapshot.ad_accounts = _dedupe_by_id(owned_ad_accounts + client_ad_accounts)

    catalogs = _safe_paginate(
        client=client,
        snapshot=snapshot,
        endpoint=f"{business_id}/owned_product_catalogs",
        params={"fields": "id,name,vertical,product_count"},
        warning_context=f"Catalog discovery for business {business_id}",
        raw_key="catalogs",
    )
    snapshot.catalogs = _dedupe_by_id(catalogs)

    # Business-level pixel discovery.
    # Do not request nested adaccounts here: Meta Graph may require an additional
    # business parameter for that nested edge and fail the whole pixel read.
    business_pixels = _safe_paginate(
        client=client,
        snapshot=snapshot,
        endpoint=f"{business_id}/adspixels",
        params={"fields": "id,name,owner_business"},
        warning_context=f"Pixels/Datasets discovery for business {business_id}",
        raw_key="pixels_business",
    )

    # Ad-account fallback. Some setups expose pixels/datasets through the ad account
    # even when the business-level adspixels endpoint returns nothing.
    account_pixels: list[dict[str, Any]] = []

    for ad_account in snapshot.ad_accounts:
        ad_account_id = _string(ad_account.get("id"))
        if not ad_account_id:
            continue

        rows = _safe_paginate(
            client=client,
            snapshot=snapshot,
            endpoint=f"{ad_account_id}/adspixels",
            params={"fields": "id,name,owner_business"},
            warning_context=f"Pixels/Datasets discovery for ad account {ad_account_id}",
            raw_key=None,
        )

        _append_account_pixels_raw(
            snapshot,
            ad_account_id=ad_account_id,
            pixels=rows,
        )

        account_pixels.extend(rows)

    snapshot.pixels = _dedupe_by_id(business_pixels + account_pixels)
    snapshot.raw_snapshots["pixels"] = snapshot.pixels

    if not snapshot.pixels:
        snapshot.warnings.append(
            "Pixel/Dataset nebyl nalezen pres business ani pres ad account endpoint. "
            "Pokud je dataset v Business Manageru videt, dopln jeho ID rucne v Meta Mappingu."
        )

    all_product_sets: list[dict[str, Any]] = []
    all_product_feeds: list[dict[str, Any]] = []

    for catalog in snapshot.catalogs:
        catalog_id = _string(catalog.get("id"))
        if not catalog_id:
            continue

        product_sets = _safe_paginate(
            client=client,
            snapshot=snapshot,
            endpoint=f"{catalog_id}/product_sets",
            params={"fields": "id,name,filter,product_count"},
            warning_context=f"Product sets discovery for catalog {catalog_id}",
            raw_key=None,
        )

        product_feeds = _safe_paginate(
            client=client,
            snapshot=snapshot,
            endpoint=f"{catalog_id}/product_feeds",
            params={"fields": "id,name,schedule,latest_upload,created_time,update_schedule"},
            warning_context=f"Product feeds discovery for catalog {catalog_id}",
            raw_key=None,
        )

        _append_catalog_children_raw(
            snapshot,
            catalog_id=catalog_id,
            product_sets=product_sets,
            product_feeds=product_feeds,
        )

        all_product_sets.extend(product_sets)
        all_product_feeds.extend(product_feeds)

    snapshot.product_sets = _dedupe_by_id(all_product_sets)
    snapshot.product_feeds = _dedupe_by_id(all_product_feeds)
    snapshot.raw_snapshots["product_sets"] = snapshot.product_sets
    snapshot.raw_snapshots["product_feeds"] = snapshot.product_feeds

    # Custom conversions are useful in mapping/audit even though they are ad-account scoped.
    custom_conversions: list[dict[str, Any]] = []
    custom_conversions_by_account: dict[str, list[dict[str, Any]]] = {}

    for ad_account in snapshot.ad_accounts:
        ad_account_id = _string(ad_account.get("id"))
        if not ad_account_id:
            continue

        rows = _safe_paginate(
            client=client,
            snapshot=snapshot,
            endpoint=f"{ad_account_id}/customconversions",
            params={"fields": "id,name,event_source_type,rule,status"},
            warning_context=f"Custom conversions discovery for ad account {ad_account_id}",
            raw_key=None,
        )

        if rows:
            custom_conversions_by_account[ad_account_id] = rows
            custom_conversions.extend(rows)

    snapshot.custom_conversions = _dedupe_by_id(custom_conversions)
    snapshot.raw_snapshots["custom_conversions"] = snapshot.custom_conversions
    snapshot.raw_snapshots["custom_conversions_by_account"] = custom_conversions_by_account

    return snapshot