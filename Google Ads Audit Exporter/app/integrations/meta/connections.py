from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.integrations.meta.models import MetaConnection, utc_now_iso


MASKED_SECRET_VALUES = {"*", "**", "***", "****", "*****", "********", "masked", "__masked__"}
MANAGED_SECRET_PREFIX = "META_CONNECTION_"


def connections_path(project_root: Path) -> Path:
    return project_root / "app_state" / "meta_connections.json"


def meta_secrets_path(project_root: Path) -> Path:
    return project_root / ".env.meta.local"


def _normalize_secret_key(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in value.upper())
    return "_".join(part for part in normalized.split("_") if part)


def _access_token_env_key(connection_key: str) -> str:
    return f"META_CONNECTION_{_normalize_secret_key(connection_key)}_ACCESS_TOKEN"


def _app_secret_env_key(connection_key: str) -> str:
    return f"META_CONNECTION_{_normalize_secret_key(connection_key)}_APP_SECRET"


def _is_masked_secret(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    if stripped.lower() in MASKED_SECRET_VALUES:
        return True
    return set(stripped) == {"*"}


def _secret_value(value: Any) -> str:
    text = str(value or "").strip()
    return "" if _is_masked_secret(text) else text


def _load_meta_secrets(project_root: Path) -> dict[str, str]:
    path = meta_secrets_path(project_root)
    if not path.exists():
        return {}

    values = dotenv_values(path)
    return {
        str(key or "").strip(): str(value or "").strip()
        for key, value in values.items()
        if str(key or "").strip()
    }


def _env_quote(value: str) -> str:
    # json.dumps creates a safe double-quoted dotenv value and escapes special chars.
    return json.dumps(str(value or ""), ensure_ascii=False)


def _write_meta_secrets(project_root: Path, values: dict[str, str]) -> None:
    path = meta_secrets_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    cleaned = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in values.items()
        if str(key or "").strip() and str(value or "").strip()
    }

    lines = [f"{key}={_env_quote(cleaned[key])}" for key in sorted(cleaned)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _connection_from_payload(payload: dict[str, Any], *, project_root: Path | None = None) -> MetaConnection:
    connection_key = str(payload.get("key", "") or "").strip()
    secrets = _load_meta_secrets(project_root) if project_root is not None else {}

    access_token = _secret_value(payload.get("access_token", ""))
    app_secret = _secret_value(payload.get("app_secret", ""))

    if not access_token and connection_key:
        access_token = secrets.get(_access_token_env_key(connection_key), "")

    if not app_secret and connection_key:
        app_secret = secrets.get(_app_secret_env_key(connection_key), "")

    return MetaConnection(
        key=connection_key,
        label=str(payload.get("label", "") or "").strip(),
        business_id=str(payload.get("business_id", "") or "").strip(),
        business_name=str(payload.get("business_name", "") or "").strip(),
        auth_type=str(payload.get("auth_type", "system_user") or "system_user").strip(),
        access_token=access_token,
        token_expires_at=str(payload.get("token_expires_at", "") or "").strip(),
        granted_scopes=[
            str(item or "").strip()
            for item in (payload.get("granted_scopes", []) or [])
            if str(item or "").strip()
        ],
        status=str(payload.get("status", "active") or "active").strip(),
        meta_api_version=str(payload.get("meta_api_version", "v25.0") or "v25.0").strip(),
        app_id=str(payload.get("app_id", "") or "").strip(),
        app_secret=app_secret,
        user_agent=str(payload.get("user_agent", "ITFutureMetaAudit/1.0") or "ITFutureMetaAudit/1.0").strip(),
        created_at=str(payload.get("created_at", "") or utc_now_iso()),
        updated_at=str(payload.get("updated_at", "") or utc_now_iso()),
        last_validated_at=str(payload.get("last_validated_at", "") or "").strip(),
        notes=str(payload.get("notes", "") or "").strip(),
    )


def load_meta_connections(project_root: Path) -> list[MetaConnection]:
    path = connections_path(project_root)
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    rows = payload.get("connections", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []

    connections: list[MetaConnection] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        connection = _connection_from_payload(row, project_root=project_root)
        if connection.key:
            connections.append(connection)

    return connections


def _managed_secret_keys_for_connections(connections: list[MetaConnection]) -> set[str]:
    keys: set[str] = set()
    for connection in connections:
        if not connection.key:
            continue
        keys.add(_access_token_env_key(connection.key))
        keys.add(_app_secret_env_key(connection.key))
    return keys


def save_meta_connections(project_root: Path, connections: list[MetaConnection]) -> None:
    path = connections_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    secret_values = _load_meta_secrets(project_root)
    allowed_managed_keys = _managed_secret_keys_for_connections(connections)

    # Remove stale managed keys for deleted/renamed connections, but keep any unrelated
    # custom values a user may have placed into .env.meta.local.
    for key in list(secret_values):
        if key.startswith(MANAGED_SECRET_PREFIX) and key not in allowed_managed_keys:
            secret_values.pop(key, None)

    serialized_connections: list[dict[str, Any]] = []

    for connection in connections:
        access_token_key = _access_token_env_key(connection.key)
        app_secret_key = _app_secret_env_key(connection.key)

        access_token = _secret_value(connection.access_token)
        app_secret = _secret_value(connection.app_secret)

        if access_token:
            secret_values[access_token_key] = access_token
        elif access_token_key in allowed_managed_keys and access_token_key not in secret_values:
            secret_values.pop(access_token_key, None)

        if app_secret:
            secret_values[app_secret_key] = app_secret
        elif app_secret_key in allowed_managed_keys and app_secret_key not in secret_values:
            secret_values.pop(app_secret_key, None)

        item = connection.to_dict()
        item["access_token"] = ""
        item["app_secret"] = ""
        serialized_connections.append(item)

    payload = {"connections": serialized_connections}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_meta_secrets(project_root, secret_values)


def upsert_meta_connection(project_root: Path, connection: MetaConnection) -> list[MetaConnection]:
    existing = load_meta_connections(project_root)
    updated: list[MetaConnection] = []
    found = False

    for item in existing:
        if item.key == connection.key:
            found = True
            connection.created_at = item.created_at or connection.created_at
            connection.updated_at = utc_now_iso()

            # If a UI submits empty or masked secret fields, keep already stored values.
            if not _secret_value(connection.access_token):
                connection.access_token = item.access_token
            if not _secret_value(connection.app_secret):
                connection.app_secret = item.app_secret

            updated.append(connection)
        else:
            updated.append(item)

    if not found:
        connection.created_at = connection.created_at or utc_now_iso()
        connection.updated_at = connection.updated_at or utc_now_iso()
        updated.append(connection)

    save_meta_connections(project_root, updated)
    return updated


def delete_meta_connection(project_root: Path, connection_key: str) -> list[MetaConnection]:
    remaining = [item for item in load_meta_connections(project_root) if item.key != connection_key]
    save_meta_connections(project_root, remaining)

    secret_values = _load_meta_secrets(project_root)
    secret_values.pop(_access_token_env_key(connection_key), None)
    secret_values.pop(_app_secret_env_key(connection_key), None)
    _write_meta_secrets(project_root, secret_values)

    return remaining


def sanitize_connection(connection: MetaConnection) -> dict[str, Any]:
    payload = connection.to_dict()
    payload["access_token"] = "***" if connection.access_token else ""
    payload["app_secret"] = "***" if connection.app_secret else ""
    return payload