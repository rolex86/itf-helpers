from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from app.integrations.linkedin.auth import DEFAULT_REQUESTED_SCOPES, ensure_refresh_possible, update_connection_after_token
from app.integrations.linkedin.connections import load_linkedin_connections, sanitize_connection, upsert_linkedin_connection
from app.integrations.linkedin.models import LinkedInConnection
from app.integrations.linkedin.oauth import build_authorize_url, exchange_code_for_token, refresh_access_token
from app.integrations.linkedin.token_store import load_token_payload, revoke_local_tokens, save_token_payload
from app.web.services.linkedin_connection_service import _connection_from_payload, _hydrate_secrets
from app.web.services.linkedin_runtime import load_linkedin_runtime_config


def build_oauth_start(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    runtime_config = load_linkedin_runtime_config(project_root)
    connection = _connection_from_payload(payload)
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
    _, _, client_secret = _hydrate_secrets(project_root, connection, payload)
    if not client_secret and runtime_config.client_secret:
        client_secret = runtime_config.client_secret
    token_payload = exchange_code_for_token(
        config=runtime_config,
        client_id=connection.client_id or runtime_config.client_id,
        client_secret=client_secret,
        code=str(payload.get("code") or "").strip(),
    )
    granted_scopes = connection.requested_scopes or list(DEFAULT_REQUESTED_SCOPES)
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
            "access_token": str(token_payload.get("access_token", "") or "").strip(),
            "refresh_token": str(token_payload.get("refresh_token", "") or "").strip(),
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
    connection = next((item for item in load_linkedin_connections(project_root) if item.key == connection_key), None)
    if connection is None:
        raise ValueError(f"LinkedIn connection '{connection_key}' nebyla nalezena.")
    secrets_payload = load_token_payload(project_root, connection.key)
    refresh_token = secrets_payload.get("refresh_token", "")
    client_secret = secrets_payload.get("client_secret", "") or runtime_config.client_secret
    ensure_refresh_possible(refresh_token)
    token_payload = refresh_access_token(
        config=runtime_config,
        client_id=connection.client_id or runtime_config.client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )
    update_connection_after_token(
        connection,
        granted_scopes=connection.granted_scopes or connection.requested_scopes or list(DEFAULT_REQUESTED_SCOPES),
        access_token_expires_in=int(token_payload.get("expires_in", 0) or 0),
        refresh_token_expires_in=int(token_payload.get("refresh_token_expires_in", 0) or 0),
        status="active",
    )
    upsert_linkedin_connection(project_root, connection)
    save_token_payload(
        project_root,
        connection.key,
        {
            "access_token": str(token_payload.get("access_token", "") or "").strip(),
            "refresh_token": str(token_payload.get("refresh_token", "") or refresh_token).strip(),
            "client_secret": client_secret,
            "manual_token": "",
        },
    )
    return {"ok": True, "message": "LinkedIn token byl obnoven.", "connection": sanitize_connection(connection)}


def revoke_local_linkedin_connection(project_root: Path, connection_key: str) -> dict[str, Any]:
    connection = next((item for item in load_linkedin_connections(project_root) if item.key == connection_key), None)
    revoke_local_tokens(project_root, connection_key)
    if connection is not None:
        connection.status = "needs_reauth"
        connection.last_error = "Lokální tokeny byly smazány."
        upsert_linkedin_connection(project_root, connection)
    return {"ok": True, "message": "Lokální LinkedIn tokeny byly smazány.", "connection_key": connection_key}

