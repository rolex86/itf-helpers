from __future__ import annotations

from pathlib import Path
from typing import Any

from app.integrations.linkedin.auth import DEFAULT_REQUESTED_SCOPES, ensure_required_scopes, token_expires_within, update_connection_after_token
from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.connections import (
    delete_linkedin_connection,
    load_linkedin_connections,
    sanitize_connection,
    upsert_linkedin_connection,
)
from app.integrations.linkedin.models import LinkedInConnection
from app.integrations.linkedin.token_store import load_token_payload, revoke_local_tokens, save_token_payload
from app.integrations.linkedin.validators import validate_connection
from app.web.services.linkedin_runtime import load_linkedin_runtime_config


def _string(value: Any) -> str:
    return str(value or "").strip()


def _split_scopes(value: Any) -> list[str]:
    text = _string(value)
    if not text:
        return list(DEFAULT_REQUESTED_SCOPES)
    normalized: list[str] = []
    for item in text.replace(",", " ").split():
        scope = str(item or "").strip()
        if scope and scope not in normalized:
            normalized.append(scope)
    return normalized or list(DEFAULT_REQUESTED_SCOPES)


def _connection_from_payload(payload: dict[str, Any]) -> LinkedInConnection:
    return LinkedInConnection(
        key=_string(payload.get("key")),
        label=_string(payload.get("label")),
        auth_type=_string(payload.get("auth_type")) or "manual_token",
        client_id=_string(payload.get("client_id")),
        linkedin_api_version=_string(payload.get("linkedin_api_version")) or "202605",
        requested_scopes=_split_scopes(payload.get("requested_scopes")),
        status=_string(payload.get("status")) or "disabled",
        notes=_string(payload.get("notes")),
        user_agent=_string(payload.get("user_agent")) or "ITFutureLinkedInAudit/1.0",
        enable_write_actions=bool(payload.get("enable_write_actions", False)),
    )


def _hydrate_secrets(project_root: Path, connection: LinkedInConnection, payload: dict[str, Any]) -> tuple[str, str, str]:
    secrets = load_token_payload(project_root, connection.key)
    access_token = _string(payload.get("access_token")) or secrets.get("access_token", "")
    refresh_token = _string(payload.get("refresh_token")) or secrets.get("refresh_token", "")
    client_secret = _string(payload.get("client_secret")) or secrets.get("client_secret", "")
    return access_token, refresh_token, client_secret


def list_linkedin_connections(project_root: Path) -> list[dict[str, Any]]:
    return [sanitize_connection(item) for item in load_linkedin_connections(project_root)]


def save_linkedin_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    connection = _connection_from_payload(payload)
    errors = validate_connection(connection)
    if errors:
        raise ValueError(" | ".join(errors))
    access_token, refresh_token, client_secret = _hydrate_secrets(project_root, connection, payload)
    if connection.auth_type == "manual_token" and not access_token:
        raise ValueError("Pro manual token režim je access token povinný.")
    upsert_linkedin_connection(project_root, connection)
    save_token_payload(
        project_root,
        connection.key,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "client_secret": client_secret,
            "manual_token": access_token if connection.auth_type == "manual_token" else "",
        },
    )
    return sanitize_connection(connection)


def delete_linkedin_connection_locally(project_root: Path, connection_key: str) -> dict[str, Any]:
    delete_linkedin_connection(project_root, connection_key)
    revoke_local_tokens(project_root, connection_key)
    return {"ok": True, "connection_key": connection_key}


def test_linkedin_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    runtime_config = load_linkedin_runtime_config(project_root)
    connection = _connection_from_payload(payload)
    errors = validate_connection(connection)
    if errors:
        raise ValueError(" | ".join(errors))
    access_token, refresh_token, client_secret = _hydrate_secrets(project_root, connection, payload)
    if not access_token:
        raise ValueError("Chybí LinkedIn access token.")
    client = LinkedInRestClient(connection=connection, runtime_config=runtime_config, access_token=access_token)
    ad_accounts_payload = client.get("adAccounts", params={"q": "search", "count": 1})
    scopes = list(connection.requested_scopes or DEFAULT_REQUESTED_SCOPES)
    ensure_required_scopes(scopes)
    update_connection_after_token(connection, granted_scopes=scopes, status="active")
    upsert_linkedin_connection(project_root, connection)
    save_token_payload(
        project_root,
        connection.key,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "client_secret": client_secret,
            "manual_token": access_token if connection.auth_type == "manual_token" else "",
        },
    )
    return {
        "ok": True,
        "message": "LinkedIn connection byla ověřena.",
        "connection": sanitize_connection(connection),
        "granted_scopes": scopes,
        "token_expires_soon": token_expires_within(connection.token_expires_at, days=7),
        "ad_accounts_preview": ad_accounts_payload.get("elements", [])[:5] if isinstance(ad_accounts_payload, dict) else [],
        "has_refresh_token": bool(refresh_token),
    }

