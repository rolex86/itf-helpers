from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LINKEDIN_API_VERSION = "202606"
DEFAULT_LINKEDIN_USER_AGENT = "ITFutureLinkedInAudit/1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
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
class LinkedInConnection:
    key: str
    label: str
    auth_type: str = "manual_token"
    client_id: str = ""
    linkedin_api_version: str = DEFAULT_LINKEDIN_API_VERSION
    granted_scopes: list[str] = field(default_factory=list)
    requested_scopes: list[str] = field(default_factory=list)
    token_expires_at: str = ""
    refresh_token_expires_at: str = ""
    status: str = "disabled"
    last_validated_at: str = ""
    last_error: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    notes: str = ""
    user_agent: str = DEFAULT_LINKEDIN_USER_AGENT
    enable_write_actions: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class LinkedInAccountContextMapping:
    context_key: str
    enabled: bool = False
    connection_key: str = ""
    ad_account_ids: list[str] = field(default_factory=list)
    organization_ids: list[str] = field(default_factory=list)
    expected_domains: list[str] = field(default_factory=list)
    expected_insight_tag_ids: list[str] = field(default_factory=list)
    expected_conversion_ids: list[str] = field(default_factory=list)
    expected_lead_form_ids: list[str] = field(default_factory=list)
    expected_utm_source: str = "linkedin"
    expected_utm_medium: str = "paid_social"
    expected_conversion_type: str = "lead"
    lead_sync_enabled: bool = True
    web_scan_enabled: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class LinkedInExportManifest:
    platform: str
    context_key: str
    connection_key: str
    started_at: str
    finished_at: str = ""
    status: str = "partial"
    date_range: dict[str, Any] = field(default_factory=dict)
    api_version: str = ""
    scopes_seen: list[str] = field(default_factory=list)
    ad_account_ids: list[str] = field(default_factory=list)
    organization_ids: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class LinkedInAuditFinding:
    severity: str
    category: str
    code: str
    title: str
    detail: str
    entity_type: str = ""
    entity_id: str = ""
    entity_name: str = ""
    recommendation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class LinkedInRuntimeConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:5000/linkedin/oauth/callback"
    api_version: str = DEFAULT_LINKEDIN_API_VERSION
    user_agent: str = DEFAULT_LINKEDIN_USER_AGENT
    enable_write_actions: bool = False
    default_date_range_days: int = 90
    request_timeout_seconds: int = 60
    max_retries: int = 3
    export_raw: bool = True
    enable_web_scan: bool = True
    enable_lead_sync: bool = True
    enable_conversions_api_audit: bool = True


@dataclass(slots=True)
class LinkedInDiscoverySnapshot:
    connection_key: str
    fetched_at: str = field(default_factory=utc_now_iso)
    status: str = "partial"
    ad_accounts: list[dict[str, Any]] = field(default_factory=list)
    ad_account_users: list[dict[str, Any]] = field(default_factory=list)
    ad_account_roles: list[dict[str, Any]] = field(default_factory=list)
    campaign_groups: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    creatives: list[dict[str, Any]] = field(default_factory=list)
    creative_content: list[dict[str, Any]] = field(default_factory=list)
    conversions: list[dict[str, Any]] = field(default_factory=list)
    campaign_conversions: list[dict[str, Any]] = field(default_factory=list)
    insight_tags: list[dict[str, Any]] = field(default_factory=list)
    insight_tag_domains: list[dict[str, Any]] = field(default_factory=list)
    organizations: list[dict[str, Any]] = field(default_factory=list)
    organization_roles: list[dict[str, Any]] = field(default_factory=list)
    lead_forms: list[dict[str, Any]] = field(default_factory=list)
    lead_form_questions: list[dict[str, Any]] = field(default_factory=list)
    lead_notifications: list[dict[str, Any]] = field(default_factory=list)
    raw_snapshots: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))