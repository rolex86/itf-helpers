from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.integrations.linkedin.models import LinkedInConnection, utc_now_iso


def connections_path(project_root: Path) -> Path:
    return project_root / "app_state" / "linkedin_connections.json"


def _connection_from_payload(payload: dict[str, Any]) -> LinkedInConnection:
    return LinkedInConnection(
        key=str(payload.get("key", "") or "").strip(),
        label=str(payload.get("label", "") or "").strip(),
        auth_type=str(payload.get("auth_type", "manual_token") or "manual_token").strip(),
        client_id=str(payload.get("client_id", "") or "").strip(),
        linkedin_api_version=str(payload.get("linkedin_api_version", "202605") or "202605").strip(),
        granted_scopes=[
            str(item or "").strip()
            for item in (payload.get("granted_scopes", []) or [])
            if str(item or "").strip()
        ],
        requested_scopes=[
            str(item or "").strip()
            for item in (payload.get("requested_scopes", []) or [])
            if str(item or "").strip()
        ],
        token_expires_at=str(payload.get("token_expires_at", "") or "").strip(),
        refresh_token_expires_at=str(payload.get("refresh_token_expires_at", "") or "").strip(),
        status=str(payload.get("status", "disabled") or "disabled").strip(),
        last_validated_at=str(payload.get("last_validated_at", "") or "").strip(),
        last_error=str(payload.get("last_error", "") or "").strip(),
        created_at=str(payload.get("created_at", "") or utc_now_iso()),
        updated_at=str(payload.get("updated_at", "") or utc_now_iso()),
        notes=str(payload.get("notes", "") or "").strip(),
        user_agent=str(payload.get("user_agent", "ITFutureLinkedInAudit/1.0") or "ITFutureLinkedInAudit/1.0").strip(),
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
        if isinstance(row, dict) and str(row.get("key", "") or "").strip()
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
            connection.updated_at = utc_now_iso()
            updated.append(connection)
        else:
            updated.append(item)
    if not found:
        updated.append(connection)
    save_linkedin_connections(project_root, updated)
    return updated


def delete_linkedin_connection(project_root: Path, connection_key: str) -> list[LinkedInConnection]:
    remaining = [item for item in load_linkedin_connections(project_root) if item.key != connection_key]
    save_linkedin_connections(project_root, remaining)
    return remaining


def sanitize_connection(connection: LinkedInConnection) -> dict[str, Any]:
    return connection.to_dict()

