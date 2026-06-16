from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from app.integrations.linkedin.auth import (
    DEFAULT_REQUESTED_SCOPES,
    ensure_refresh_possible,
    token_expires_within,
    token_is_expired,
    update_connection_after_token,
)
from app.integrations.linkedin.connections import (
    load_linkedin_connections,
    sanitize_connection,
    upsert_linkedin_connection,
)
from app.integrations.linkedin.models import LinkedInConnection, utc_now_iso
from app.integrations.linkedin.oauth import build_authorize_url, exchange_code_for_token, refresh_access_token
from app.integrations.linkedin.token_store import load_token_payload, revoke_local_tokens, save_token_payload
from app.web.services.linkedin_connection_service import _connection_from_payload, _hydrate_secrets
from app.web.services.linkedin_runtime import load_linkedin_runtime_config


def _string(value: Any) -> str:
    return str(value or "").strip()


def _scopes_from_token_payload(token_payload: dict[str, Any], fallback: list[str]) -> list[str]:
    raw_scope = token_payload.get("scope") or token_payload.get("scopes") or ""

    if isinstance(raw_scope, list):
        scopes = [_string(item) for item in raw_scope if _string(item)]
    else:
        scopes = [_string(item) for item in str(raw_scope or "").replace(",", " ").split() if _string(item)]

    if not scopes:
        scopes = list(fallback or DEFAULT_REQUESTED_SCOPES)

    normalized: list[str] = []
    for scope in scopes:
        if scope and scope not in normalized:
            normalized.append(scope)

    return normalized


def _find_connection(project_root: Path, connection_key: str) -> LinkedInConnection:
    connection = next(
        (item for item in load_linkedin_connections(project_root) if item.key == connection_key),
        None,
    )

    if connection is None:
        raise ValueError(f"LinkedIn connection '{connection_key}' nebyla nalezena.")

    return connection


def _mark_needs_reauth(project_root: Path, connection: LinkedInConnection, message: str) -> None:
    connection.status = "needs_reauth"
    connection.last_error = message
    connection.updated_at = utc_now_iso()
    upsert_linkedin_connection(project_root, connection)


def build_oauth_start(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    runtime_config = load_linkedin_runtime_config(project_root)
    connection = _connection_from_payload(payload)
    connection.auth_type = "oauth"

    state = secrets.token_urlsafe(24)

    authorize_url = build_authorize_url(
        config=runtime_config,
        client_id=connection.client_id or runtime_config.client_id,
        state=state,
        scopes=connection.requested_scopes or list(DEFAULT_REQUESTED_SCOPES),
    )

    return {
        "state": state,
        "authorize_url": authorize_url,
        "connection": sanitize_connection(connection),
    }


def handle_oauth_callback(
    project_root: Path,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    runtime_config = load_linkedin_runtime_config(project_root)
    connection = _connection_from_payload(payload)
    connection.auth_type = "oauth"

    _, _, client_secret = _hydrate_secrets(project_root, connection, payload)
    if not client_secret and runtime_config.client_secret:
        client_secret = runtime_config.client_secret

    code = _string(payload.get("code"))
    if not code:
        raise ValueError("Chybí LinkedIn authorization code.")

    token_payload = exchange_code_for_token(
        config=runtime_config,
        client_id=connection.client_id or runtime_config.client_id,
        client_secret=client_secret,
        code=code,
    )

    access_token = _string(token_payload.get("access_token"))
    refresh_token = _string(token_payload.get("refresh_token"))

    if not access_token:
        connection.status = "needs_reauth"
        connection.last_error = "LinkedIn OAuth nevrátil access token."
        connection.updated_at = utc_now_iso()
        upsert_linkedin_connection(project_root, connection)
        raise ValueError("LinkedIn OAuth nevrátil access token.")

    granted_scopes = _scopes_from_token_payload(
        token_payload,
        connection.requested_scopes or list(DEFAULT_REQUESTED_SCOPES),
    )

    update_connection_after_token(
        connection,
        granted_scopes=granted_scopes,
        access_token_expires_in=int(token_payload.get("expires_in", 0) or 0),
        refresh_token_expires_in=int(token_payload.get("refresh_token_expires_in", 0) or 0),
        status="active",
    )

    upsert_linkedin_connection(project_root, connection)

    save_token_payload(
        project_root,
        connection.key,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "client_secret": client_secret,
            "manual_token": "",
        },
    )

    return {
        "ok": True,
        "message": "LinkedIn OAuth byl dokončen.",
        "connection": sanitize_connection(connection),
    }


def refresh_linkedin_connection_token(project_root: Path, connection_key: str) -> dict[str, Any]:
    runtime_config = load_linkedin_runtime_config(project_root)
    connection = _find_connection(project_root, connection_key)

    secrets_payload = load_token_payload(project_root, connection.key)
    refresh_token = _string(secrets_payload.get("refresh_token"))
    client_secret = _string(secrets_payload.get("client_secret")) or runtime_config.client_secret

    ensure_refresh_possible(refresh_token)

    try:
        token_payload = refresh_access_token(
            config=runtime_config,
            client_id=connection.client_id or runtime_config.client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
    except Exception as exc:
        _mark_needs_reauth(project_root, connection, str(exc))
        raise

    access_token = _string(token_payload.get("access_token"))
    next_refresh_token = _string(token_payload.get("refresh_token")) or refresh_token

    if not access_token:
        _mark_needs_reauth(project_root, connection, "LinkedIn refresh nevrátil access token.")
        raise ValueError("LinkedIn refresh nevrátil access token.")

    granted_scopes = _scopes_from_token_payload(
        token_payload,
        connection.granted_scopes or connection.requested_scopes or list(DEFAULT_REQUESTED_SCOPES),
    )

    update_connection_after_token(
        connection,
        granted_scopes=granted_scopes,
        access_token_expires_in=int(token_payload.get("expires_in", 0) or 0),
        refresh_token_expires_in=int(token_payload.get("refresh_token_expires_in", 0) or 0),
        status="active",
    )

    upsert_linkedin_connection(project_root, connection)

    save_token_payload(
        project_root,
        connection.key,
        {
            "access_token": access_token,
            "refresh_token": next_refresh_token,
            "client_secret": client_secret,
            "manual_token": "",
        },
    )

    return {
        "ok": True,
        "message": "LinkedIn token byl obnoven.",
        "connection": sanitize_connection(connection),
    }


def ensure_connection_access_token(project_root: Path, connection: LinkedInConnection) -> str:
    secrets_payload = load_token_payload(project_root, connection.key)
    access_token = _string(secrets_payload.get("access_token") or secrets_payload.get("manual_token"))

    if not access_token:
        _mark_needs_reauth(project_root, connection, "Chybí access token.")
        raise ValueError("Pro LinkedIn connection chybí access token.")

    token_expired = token_is_expired(connection.token_expires_at)
    token_expires_soon = token_expires_within(connection.token_expires_at, days=7)

    if token_expired or token_expires_soon:
        if _string(secrets_payload.get("refresh_token")):
            refresh_linkedin_connection_token(project_root, connection.key)

            secrets_payload = load_token_payload(project_root, connection.key)
            access_token = _string(secrets_payload.get("access_token") or secrets_payload.get("manual_token"))

            refreshed = next(
                (item for item in load_linkedin_connections(project_root) if item.key == connection.key),
                None,
            )
            if refreshed is not None:
                connection.status = refreshed.status
                connection.last_error = refreshed.last_error
                connection.token_expires_at = refreshed.token_expires_at
                connection.refresh_token_expires_at = refreshed.refresh_token_expires_at
                connection.granted_scopes = refreshed.granted_scopes
                connection.updated_at = refreshed.updated_at

            if not access_token:
                _mark_needs_reauth(project_root, connection, "Po refreshi chybí access token.")
                raise ValueError("Po refreshi LinkedIn connection chybí access token.")

        elif connection.token_expires_at:
            _mark_needs_reauth(
                project_root,
                connection,
                "Access token expiroval nebo expiruje a není k dispozici refresh token.",
            )
            raise ValueError("LinkedIn token expiroval nebo expiruje a connection vyžaduje reautorizaci.")

    return access_token


def revoke_local_linkedin_connection(project_root: Path, connection_key: str) -> dict[str, Any]:
    connection = next(
        (item for item in load_linkedin_connections(project_root) if item.key == connection_key),
        None,
    )

    revoke_local_tokens(project_root, connection_key)

    if connection is not None:
        connection.status = "needs_reauth"
        connection.last_error = "Lokální tokeny byly smazány."
        connection.updated_at = utc_now_iso()
        upsert_linkedin_connection(project_root, connection)

    return {
        "ok": True,
        "message": "Lokální LinkedIn tokeny byly smazány.",
        "connection_key": connection_key,
    }