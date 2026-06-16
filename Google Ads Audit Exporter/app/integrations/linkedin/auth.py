from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.integrations.linkedin.errors import LinkedInAuthError, LinkedInPermissionError
from app.integrations.linkedin.models import LinkedInConnection, utc_now_iso
from app.integrations.linkedin.validators import REQUIRED_CORE_SCOPES


DEFAULT_REQUESTED_SCOPES = [
    "r_ads",
    "r_ads_reporting",
    "r_marketing_leadgen_automation",
    "r_organization_lookup",
    "rw_organization_admin",
]


def _dedupe_scopes(scopes: list[str]) -> list[str]:
    normalized: list[str] = []

    for scope in scopes or []:
        value = str(scope or "").strip()
        if value and value not in normalized:
            normalized.append(value)

    return normalized


def _utc_datetime_from_iso(iso_value: str) -> datetime | None:
    text = str(iso_value or "").strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def update_connection_after_token(
    connection: LinkedInConnection,
    *,
    granted_scopes: list[str],
    access_token_expires_in: int | None = None,
    refresh_token_expires_in: int | None = None,
    status: str = "active",
    last_error: str = "",
) -> LinkedInConnection:
    now = datetime.now(timezone.utc)

    connection.granted_scopes = _dedupe_scopes(granted_scopes)
    connection.status = str(status or "active").strip() or "active"
    connection.last_error = str(last_error or "").strip()
    connection.last_validated_at = utc_now_iso()
    connection.updated_at = utc_now_iso()

    if access_token_expires_in:
        connection.token_expires_at = (now + timedelta(seconds=int(access_token_expires_in))).isoformat()

    if refresh_token_expires_in:
        connection.refresh_token_expires_at = (now + timedelta(seconds=int(refresh_token_expires_in))).isoformat()

    return connection


def ensure_required_scopes(scopes: list[str]) -> None:
    normalized = set(_dedupe_scopes(scopes))
    missing = [scope for scope in REQUIRED_CORE_SCOPES if scope not in normalized]

    if missing:
        raise LinkedInPermissionError(
            "LinkedIn connection nemá všechna minimální oprávnění.",
            details="Missing scopes: " + ", ".join(missing),
        )


def token_expires_within(iso_value: str, *, days: int) -> bool:
    expires_at = _utc_datetime_from_iso(iso_value)
    if expires_at is None:
        return False

    return expires_at <= datetime.now(timezone.utc) + timedelta(days=days)


def token_is_expired(iso_value: str) -> bool:
    expires_at = _utc_datetime_from_iso(iso_value)
    if expires_at is None:
        return False

    return expires_at <= datetime.now(timezone.utc)


def ensure_refresh_possible(refresh_token: str) -> None:
    if not str(refresh_token or "").strip():
        raise LinkedInAuthError("Chybí refresh token, je potřeba znovu autorizovat LinkedIn connection.")