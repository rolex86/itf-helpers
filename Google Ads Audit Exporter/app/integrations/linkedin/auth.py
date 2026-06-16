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
    connection.granted_scopes = list(granted_scopes)
    connection.status = status
    connection.last_error = last_error
    connection.last_validated_at = utc_now_iso()
    connection.updated_at = utc_now_iso()
    if access_token_expires_in:
        connection.token_expires_at = (now + timedelta(seconds=int(access_token_expires_in))).isoformat()
    if refresh_token_expires_in:
        connection.refresh_token_expires_at = (now + timedelta(seconds=int(refresh_token_expires_in))).isoformat()
    return connection


def ensure_required_scopes(scopes: list[str]) -> None:
    missing = [scope for scope in REQUIRED_CORE_SCOPES if scope not in scopes]
    if missing:
        raise LinkedInPermissionError(
            "LinkedIn connection nemá všechna minimální oprávnění.",
            details="Missing scopes: " + ", ".join(missing),
        )


def token_expires_within(iso_value: str, *, days: int) -> bool:
    if not iso_value:
        return False
    try:
        expires_at = datetime.fromisoformat(iso_value)
    except ValueError:
        return False
    return expires_at <= datetime.now(timezone.utc) + timedelta(days=days)


def ensure_refresh_possible(refresh_token: str) -> None:
    if not str(refresh_token or "").strip():
        raise LinkedInAuthError("Chybí refresh token, je potřeba znovu autorizovat LinkedIn connection.")

