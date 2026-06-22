from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SKLIK_DRAK_BASE_URL = "https://api.sklik.cz/drak/json/v5"
DEFAULT_SKLIK_FENIX_BASE_URL = "https://api.sklik.cz/v1"
DEFAULT_SKLIK_USER_AGENT = "ITFutureSklikAudit/1.0"


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
class SklikConnection:
    key: str
    label: str
    auth_type: str = "token"
    drak_enabled: bool = True
    fenix_enabled: bool = True
    drak_token_env_key: str = ""
    fenix_refresh_token_env_key: str = ""
    default_user_id: str = ""
    status: str = "active"
    last_validated_at: str = ""
    last_discovery_at: str = ""
    last_error: str = ""
    notes: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class SklikAccountContextMapping:
    context_key: str
    enabled: bool = False
    connection_key: str = ""
    drak_user_ids: list[str] = field(default_factory=list)
    fenix_user_ids: list[str] = field(default_factory=list)
    fenix_premise_ids: list[str] = field(default_factory=list)
    expected_domains: list[str] = field(default_factory=list)
    expected_utm_source: list[str] = field(default_factory=lambda: ["sklik", "seznam"])
    expected_utm_medium: list[str] = field(default_factory=lambda: ["cpc", "ppc"])
    expected_sem: bool = True
    expected_sklik_conversions: list[str] = field(default_factory=list)
    expected_retargeting_lists: list[str] = field(default_factory=list)
    enable_reporting: bool = True
    enable_fenix: bool = True
    enable_gtm_crosscheck: bool = True
    enable_web_scan: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class SklikAuditFinding:
    severity: str
    category: str
    code: str
    title: str
    message: str
    entity_type: str | None = None
    entity_id: str | None = None
    context_key: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class SklikRuntimeConfig:
    drak_base_url: str = DEFAULT_SKLIK_DRAK_BASE_URL
    fenix_base_url: str = DEFAULT_SKLIK_FENIX_BASE_URL
    request_timeout_seconds: int = 60
    max_retries: int = 3
    export_raw: bool = True
    export_pii: bool = False
    enable_web_scan: bool = True
    enable_gtm_crosscheck: bool = True
    enable_fenix: bool = True
    default_date_range_days: int = 90
    fenix_export_items: bool = False
    fenix_max_items: int = 5000
    user_agent: str = DEFAULT_SKLIK_USER_AGENT


@dataclass(slots=True)
class SklikDiscoverySnapshot:
    connection_key: str
    fetched_at: str = field(default_factory=utc_now_iso)
    status: str = "partial"
    drak: dict[str, Any] = field(default_factory=dict)
    fenix: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))


@dataclass(slots=True)
class SklikExportManifest:
    platform: str
    context_key: str
    export_started_at: str
    export_finished_at: str = ""
    date_from: str = ""
    date_to: str = ""
    connection_key: str = ""
    drak_enabled: bool = True
    fenix_enabled: bool = True
    user_ids: list[str] = field(default_factory=list)
    premise_ids: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    infos: list[dict[str, Any]] = field(default_factory=list)
    pii_files: list[str] = field(default_factory=list)
    raw_export_enabled: bool = True
    status: str = "partial"
    counts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _dict_json_safe(asdict(self))

