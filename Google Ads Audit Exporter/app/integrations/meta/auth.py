from __future__ import annotations

from typing import Any

from app.integrations.meta.errors import MetaPermissionError
from app.integrations.meta.models import MetaConnection, utc_now_iso


# Required for the first read-only Meta Ads audit.
# Meta's current app/use-case UI may not offer read_insights as a separate selectable scope.
# Insights export will be tested by real API calls in sync.py and will produce warnings if access is insufficient.
REQUIRED_META_SCOPES = [
    "ads_read",
]

# Strongly recommended for Business discovery and asset mapping.
RECOMMENDED_META_SCOPES = [
    "business_management",
]

# Required only for contexts where catalog/feed audit is enabled or catalog_ids are mapped.
CATALOG_META_SCOPES = [
    "catalog_management",
]


def validate_meta_connection_config(connection: MetaConnection) -> list[str]:
    errors: list[str] = []

    if not connection.key:
        errors.append("Connection key je povinny.")

    if not connection.label:
        errors.append("Nazev connection je povinny.")

    if not connection.access_token:
        errors.append("Access token je povinny.")

    if not connection.meta_api_version:
        errors.append("Meta API version je povinna.")

    if not str(connection.meta_api_version or "").strip().startswith("v"):
        errors.append("Meta API version ma byt ve formatu napr. v25.0.")

    return errors


def update_connection_validation(
    connection: MetaConnection,
    *,
    granted_scopes: list[str],
    status: str,
) -> MetaConnection:
    connection.granted_scopes = _dedupe_scopes(granted_scopes)
    connection.status = status
    connection.last_validated_at = utc_now_iso()
    connection.updated_at = utc_now_iso()
    return connection


def _dedupe_scopes(scopes: list[str]) -> list[str]:
    normalized: list[str] = []

    for scope in scopes:
        value = str(scope or "").strip()
        if value and value not in normalized:
            normalized.append(value)

    return normalized


def _missing_scopes(granted_scopes: list[str], required_scopes: list[str]) -> list[str]:
    granted = set(_dedupe_scopes(granted_scopes))
    return [scope for scope in required_scopes if scope not in granted]


def ensure_required_scopes(granted_scopes: list[str]) -> None:
    missing = _missing_scopes(granted_scopes, REQUIRED_META_SCOPES)

    if missing:
        raise MetaPermissionError(
            "Meta connection nema vsechna povinna read-only opravneni.",
            details="Missing scopes: " + ", ".join(missing),
        )


def ensure_catalog_scopes(granted_scopes: list[str]) -> None:
    missing = _missing_scopes(granted_scopes, CATALOG_META_SCOPES)

    if missing:
        raise MetaPermissionError(
            "Meta connection nema opravneni pro katalogovy audit.",
            details="Missing scopes: " + ", ".join(missing),
        )


def recommended_scope_warnings(granted_scopes: list[str]) -> list[str]:
    missing = _missing_scopes(granted_scopes, RECOMMENDED_META_SCOPES)
    if not missing:
        return []

    return [
        "Meta connection nema doporucena opravneni: "
        + ", ".join(missing)
        + ". Discovery Business assetu muze byt omezene."
    ]


def catalog_scope_warnings(granted_scopes: list[str]) -> list[str]:
    missing = _missing_scopes(granted_scopes, CATALOG_META_SCOPES)
    if not missing:
        return []

    return [
        "Meta connection nema catalog_management. Katalogy, product sety a feedy nemusi jit nacist."
    ]


def infer_scopes_from_debug(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    scopes = data.get("scopes", []) or []

    granular_scopes = data.get("granular_scopes", []) or []
    if granular_scopes:
        scopes = list(scopes) + list(granular_scopes)

    normalized: list[str] = []
    for scope in scopes:
        value = ""

        if isinstance(scope, dict):
            value = str(scope.get("scope", "") or "").strip()
        else:
            value = str(scope or "").strip()

        if value and value not in normalized:
            normalized.append(value)

    return normalized