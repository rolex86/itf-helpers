from __future__ import annotations

from pathlib import Path
from typing import Any

from app.integrations.meta.auth import (
    catalog_scope_warnings,
    ensure_required_scopes,
    infer_scopes_from_debug,
    recommended_scope_warnings,
    update_connection_validation,
    validate_meta_connection_config,
)
from app.integrations.meta.client import MetaGraphClient
from app.integrations.meta.connections import (
    load_meta_connections,
    sanitize_connection,
    upsert_meta_connection,
)
from app.integrations.meta.models import MetaConnection


MASKED_SECRET_VALUES = {"*", "**", "***", "****", "*****", "********", "masked", "__masked__"}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _connection_from_payload(payload: dict[str, Any]) -> MetaConnection:
    return MetaConnection(
        key=_string(payload.get("key")),
        label=_string(payload.get("label")),
        business_id=_string(payload.get("business_id")),
        business_name=_string(payload.get("business_name")),
        auth_type=_string(payload.get("auth_type") or "system_user") or "system_user",
        access_token=_string(payload.get("access_token")),
        token_expires_at=_string(payload.get("token_expires_at")),
        granted_scopes=[
            _string(item)
            for item in (payload.get("granted_scopes", []) or [])
            if _string(item)
        ],
        status=_string(payload.get("status") or "active") or "active",
        meta_api_version=_string(payload.get("meta_api_version") or "v25.0") or "v25.0",
        app_id=_string(payload.get("app_id")),
        app_secret=_string(payload.get("app_secret")),
        user_agent=_string(payload.get("user_agent") or "ITFutureMetaAudit/1.0") or "ITFutureMetaAudit/1.0",
        notes=_string(payload.get("notes")),
    )


def _is_masked_secret(value: Any) -> bool:
    text = _string(value)
    if not text:
        return False
    if text.lower() in MASKED_SECRET_VALUES:
        return True
    return set(text) == {"*"}


def _normalize_secret_value(value: Any) -> str:
    text = _string(value)
    return "" if _is_masked_secret(text) else text


def _hydrate_with_stored_secrets(project_root: Path, connection: MetaConnection) -> MetaConnection:
    stored = next(
        (item for item in load_meta_connections(project_root) if item.key == connection.key),
        None,
    )

    connection.access_token = _normalize_secret_value(connection.access_token)
    connection.app_secret = _normalize_secret_value(connection.app_secret)

    if stored is None:
        return connection

    if not connection.access_token:
        connection.access_token = stored.access_token

    if not connection.app_secret:
        connection.app_secret = stored.app_secret

    if not connection.created_at:
        connection.created_at = stored.created_at

    if not connection.last_validated_at:
        connection.last_validated_at = stored.last_validated_at

    if not connection.granted_scopes:
        connection.granted_scopes = list(stored.granted_scopes)

    return connection


def _safe_business_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _string(payload.get("id")),
        "name": _string(payload.get("name")),
    }


def list_meta_connections(project_root: Path) -> list[dict[str, Any]]:
    return [sanitize_connection(item) for item in load_meta_connections(project_root)]


def save_meta_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    connection = _hydrate_with_stored_secrets(project_root, _connection_from_payload(payload))

    errors = validate_meta_connection_config(connection)
    if errors:
        raise ValueError(" | ".join(errors))

    upsert_meta_connection(project_root, connection)
    return sanitize_connection(connection)


def test_meta_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    connection = _hydrate_with_stored_secrets(project_root, _connection_from_payload(payload))

    errors = validate_meta_connection_config(connection)
    if errors:
        raise ValueError(" | ".join(errors))

    client = MetaGraphClient(connection)

    app_access_token = (
        f"{connection.app_id}|{connection.app_secret}"
        if connection.app_id and connection.app_secret
        else connection.access_token
    )

    debug_token = client.get(
        "debug_token",
        params={
            "input_token": connection.access_token,
            "access_token": app_access_token,
        },
    )

    scopes = infer_scopes_from_debug(debug_token)
    ensure_required_scopes(scopes)

    scope_warnings = recommended_scope_warnings(scopes) + catalog_scope_warnings(scopes)

    business: dict[str, Any] = {}
    if connection.business_id:
        business = client.get(connection.business_id, params={"fields": "id,name"})
        safe_business = _safe_business_payload(business)

        if safe_business.get("id"):
            connection.business_id = safe_business["id"]

        if safe_business.get("name"):
            connection.business_name = safe_business["name"]

        business = safe_business

    update_connection_validation(connection, granted_scopes=scopes, status="active")
    upsert_meta_connection(project_root, connection)

    message = "Meta connection byla overena."
    if scope_warnings:
        message += " Nektera doporucena opravneni chybi, cast auditu muze byt omezena."

    return {
        "ok": True,
        "message": message,
        "connection": sanitize_connection(connection),
        "granted_scopes": scopes,
        "scope_warnings": scope_warnings,
        "business": business,
    }