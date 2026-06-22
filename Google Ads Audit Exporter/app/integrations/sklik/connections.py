from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.integrations.sklik.auth import build_drak_token_env_key, build_fenix_refresh_token_env_key
from app.integrations.sklik.models import SklikConnection, utc_now_iso


def connections_path(project_root: Path) -> Path:
    return project_root / "app_state" / "sklik_connections.json"


def _clean_str(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _connection_from_payload(payload: dict[str, Any]) -> SklikConnection:
    key = _clean_str(payload.get("key"))
    return SklikConnection(
        key=key,
        label=_clean_str(payload.get("label")),
        auth_type=_clean_str(payload.get("auth_type"), "token") or "token",
        drak_enabled=bool(payload.get("drak_enabled", True)),
        fenix_enabled=bool(payload.get("fenix_enabled", True)),
        drak_token_env_key=_clean_str(payload.get("drak_token_env_key")) or build_drak_token_env_key(key),
        fenix_refresh_token_env_key=_clean_str(payload.get("fenix_refresh_token_env_key")) or build_fenix_refresh_token_env_key(key),
        default_user_id=_clean_str(payload.get("default_user_id")),
        status=_clean_str(payload.get("status"), "active") or "active",
        last_validated_at=_clean_str(payload.get("last_validated_at")),
        last_discovery_at=_clean_str(payload.get("last_discovery_at")),
        last_error=_clean_str(payload.get("last_error")),
        notes=_clean_str(payload.get("notes")),
        created_at=_clean_str(payload.get("created_at"), utc_now_iso()) or utc_now_iso(),
        updated_at=_clean_str(payload.get("updated_at"), utc_now_iso()) or utc_now_iso(),
    )


def load_sklik_connections(project_root: Path) -> list[SklikConnection]:
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


def save_sklik_connections(project_root: Path, connections: list[SklikConnection]) -> None:
    path = connections_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"connections": [connection.to_dict() for connection in connections]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_sklik_connection(project_root: Path, connection: SklikConnection) -> list[SklikConnection]:
    existing = load_sklik_connections(project_root)
    updated: list[SklikConnection] = []
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

    save_sklik_connections(project_root, updated)
    return updated


def delete_sklik_connection(project_root: Path, connection_key: str) -> list[SklikConnection]:
    normalized = _clean_str(connection_key)
    remaining = [item for item in load_sklik_connections(project_root) if item.key != normalized]
    save_sklik_connections(project_root, remaining)
    return remaining


def sanitize_connection(connection: SklikConnection) -> dict[str, Any]:
    return connection.to_dict()
