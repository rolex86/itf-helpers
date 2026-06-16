from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import dotenv_values


def token_store_path(project_root: Path) -> Path:
    return project_root / ".env.linkedin.local"


def _normalize_key(connection_key: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(connection_key or "").upper())


def _env_key(connection_key: str, field_name: str) -> str:
    return f"LINKEDIN_CONNECTION_{_normalize_key(connection_key)}_{field_name}"


SECRET_FIELDS = {
    "access_token": "ACCESS_TOKEN",
    "refresh_token": "REFRESH_TOKEN",
    "client_secret": "CLIENT_SECRET",
    "manual_token": "MANUAL_TOKEN",
}


def load_token_payload(project_root: Path, connection_key: str) -> dict[str, str]:
    path = token_store_path(project_root)
    if not path.exists():
        return {}
    values = dotenv_values(path)
    payload: dict[str, str] = {}
    for field_name, env_suffix in SECRET_FIELDS.items():
        payload[field_name] = str(values.get(_env_key(connection_key, env_suffix), "") or "").strip()
    return payload


def save_token_payload(project_root: Path, connection_key: str, payload: dict[str, Any]) -> None:
    path = token_store_path(project_root)
    existing = dotenv_values(path) if path.exists() else {}
    normalized = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in existing.items()
        if str(key or "").strip()
    }
    for field_name, env_suffix in SECRET_FIELDS.items():
        env_key = _env_key(connection_key, env_suffix)
        value = str(payload.get(field_name, "") or "").strip()
        if value:
            normalized[env_key] = value
        else:
            normalized.pop(env_key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={normalized[key]}" for key in sorted(normalized)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def revoke_local_tokens(project_root: Path, connection_key: str) -> None:
    path = token_store_path(project_root)
    if not path.exists():
        return
    existing = dotenv_values(path)
    normalized = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in existing.items()
        if str(key or "").strip()
    }
    for env_suffix in SECRET_FIELDS.values():
        normalized.pop(_env_key(connection_key, env_suffix), None)
    lines = [f"{key}={normalized[key]}" for key in sorted(normalized)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

