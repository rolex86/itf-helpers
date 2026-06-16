from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Return a JSON-safe representation for exported audit payloads.

    Meta/Google/GTM data can contain datetime objects, pathlib Paths, NaN/NA values
    or other SDK-specific values. The audit exporter should not fail because of a
    non-serializable evidence value.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(key): _json_safe(nested) for key, nested in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(nested) for nested in value]

    # Handle pandas/numpy scalar-like objects without importing pandas/numpy here.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass

    return str(value)


def _dict_json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in payload.items()}


@dataclass(slots=True)
class MetaConnection:
    key: str
    label: str
    business_id: str = ""
    business_name: str = ""
    auth_type: str = "system_user"
    access_token: str = ""
    token_expires_at: str = ""
    granted_scopes: list[str] = field(default_factory=list)
    status: str = "active"
    meta_api_version: str = "v25.0"
    app_id: str = ""
    app_secret: str = ""
    user_agent: str = "ITFutureMetaAudit/1.0"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    last_validated_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class MetaContextMapping:
    enabled: bool = False
    connection_key: str = ""
    business_id: str = ""
    ad_account_ids: list[str] = field(default_factory=list)
    pixel_ids: list[str] = field(default_factory=list)
    catalog_ids: list[str] = field(default_factory=list)
    product_set_ids: list[str] = field(default_factory=list)
    expected_conversion_event: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class MetaAuditFinding:
    source: str
    severity: str
    rule_code: str
    title: str
    description: str
    affected_object_type: str
    affected_object_id: str
    affected_object_name: str
    business_id: str = ""
    ad_account_id: str = ""
    campaign_id: str = ""
    adset_id: str = ""
    ad_id: str = ""
    pixel_id: str = ""
    catalog_id: str = ""
    evidence_json: dict[str, Any] = field(default_factory=dict)
    recommended_fix: str = ""
    can_autofix: bool = False
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class MetaDiscoverySnapshot:
    connection_key: str
    businesses: list[dict[str, Any]] = field(default_factory=list)
    ad_accounts: list[dict[str, Any]] = field(default_factory=list)
    pixels: list[dict[str, Any]] = field(default_factory=list)
    catalogs: list[dict[str, Any]] = field(default_factory=list)
    product_sets: list[dict[str, Any]] = field(default_factory=list)
    product_feeds: list[dict[str, Any]] = field(default_factory=list)
    custom_conversions: list[dict[str, Any]] = field(default_factory=list)
    raw_snapshots: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))