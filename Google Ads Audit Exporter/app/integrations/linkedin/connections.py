from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.integrations.linkedin.models import (
    DEFAULT_LINKEDIN_API_VERSION,
    DEFAULT_LINKEDIN_USER_AGENT,
    LinkedInConnection,
    utc_now_iso,
)


def connections_path(project_root: Path) -> Path:
    return project_root / "app_state" / "linkedin_connections.json"


def _clean_str(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _connection_from_payload(payload: dict[str, Any]) -> LinkedInConnection:
    api_version = _clean_str(
        payload.get("linkedin_api_version"),
        DEFAULT_LINKEDIN_API_VERSION,
    ) or DEFAULT_LINKEDIN_API_VERSION

    user_agent = _clean_str(
        payload.get("user_agent"),
        DEFAULT_LINKEDIN_USER_AGENT,
    ) or DEFAULT_LINKEDIN_USER_AGENT

    return LinkedInConnection(
        key=_clean_str(payload.get("key")),
        label=_clean_str(payload.get("label")),
        auth_type=_clean_str(payload.get("auth_type"), "manual_token") or "manual_token",
        client_id=_clean_str(payload.get("client_id")),
        linkedin_api_version=api_version,
        granted_scopes=_clean_list(payload.get("granted_scopes")),
        requested_scopes=_clean_list(payload.get("requested_scopes")),
        token_expires_at=_clean_str(payload.get("token_expires_at")),
        refresh_token_expires_at=_clean_str(payload.get("refresh_token_expires_at")),
        status=_clean_str(payload.get("status"), "disabled") or "disabled",
        last_validated_at=_clean_str(payload.get("last_validated_at")),
        last_error=_clean_str(payload.get("last_error")),
        created_at=_clean_str(payload.get("created_at"), utc_now_iso()) or utc_now_iso(),
        updated_at=_clean_str(payload.get("updated_at"), utc_now_iso()) or utc_now_iso(),
        notes=_clean_str(payload.get("notes")),
        user_agent=user_agent,
        enable_write_actions=bool(payload.get("enable_write_actions", False)),
    )


def load_linkedin_connections(project_root: Path) -> list[LinkedInConnection]:
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

    return [
        _connection_from_payload(row)
        for row in rows
        if isinstance(row, dict) and _clean_str(row.get("key"))
    ]


def save_linkedin_connections(project_root: Path, connections: list[LinkedInConnection]) -> None:
    path = connections_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {"connections": [connection.to_dict() for connection in connections]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_linkedin_connection(project_root: Path, connection: LinkedInConnection) -> list[LinkedInConnection]:
    existing = load_linkedin_connections(project_root)
    updated: list[LinkedInConnection] = []
    found = False

    for item in existing:
        if item.key == connection.key:
            found = True
            connection.created_at = item.created_at or connection.created_at
            connection.updated_at = utc_now_iso()
            updated.append(connection)
        else:
            updated.append(item)

    if not found:
        connection.created_at = connection.created_at or utc_now_iso()
        connection.updated_at = utc_now_iso()
        updated.append(connection)

    save_linkedin_connections(project_root, updated)
    return updated


def delete_linkedin_connection(project_root: Path, connection_key: str) -> list[LinkedInConnection]:
    normalized_key = _clean_str(connection_key)
    remaining = [
        item
        for item in load_linkedin_connections(project_root)
        if item.key != normalized_key
    ]
    save_linkedin_connections(project_root, remaining)
    return remaining


def sanitize_connection(connection: LinkedInConnection) -> dict[str, Any]:
    return connection.to_dict()