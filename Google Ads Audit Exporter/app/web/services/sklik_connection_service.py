from __future__ import annotations

from pathlib import Path
from typing import Any

from app.integrations.sklik.auth import (
    build_drak_token_env_key,
    build_fenix_refresh_token_env_key,
    delete_secret,
    secret_from_payload_or_store,
    set_secret,
)
from app.integrations.sklik.connections import (
    delete_sklik_connection,
    load_sklik_connections,
    sanitize_connection,
    upsert_sklik_connection,
)
from app.integrations.sklik.models import SklikConnection
from app.integrations.sklik.validators import normalize_connection_key, validate_connection
from app.web.services.sklik_runtime import load_sklik_runtime_config


MASKED_SECRET_VALUES = {
    "***",
    "****",
    "*****",
    "********",
    "••••",
    "••••••••",
}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _connection_from_payload(payload: dict[str, Any]) -> SklikConnection:
    key = normalize_connection_key(payload.get("key"))
    return SklikConnection(
        key=key,
        label=_string(payload.get("label")),
        auth_type="token",
        drak_enabled=bool(payload.get("drak_enabled", True)),
        fenix_enabled=bool(payload.get("fenix_enabled", True)),
        drak_token_env_key=_string(payload.get("drak_token_env_key")) or build_drak_token_env_key(key),
        fenix_refresh_token_env_key=_string(payload.get("fenix_refresh_token_env_key")) or build_fenix_refresh_token_env_key(key),
        default_user_id=_string(payload.get("default_user_id")),
        status=_string(payload.get("status")) or "active",
        notes=_string(payload.get("notes")),
    )


def list_sklik_connections(project_root: Path) -> list[dict[str, Any]]:
    return [sanitize_connection(item) for item in load_sklik_connections(project_root)]


def save_sklik_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    connection = _connection_from_payload(payload)
    errors = validate_connection(connection)
    if errors:
        raise ValueError(" | ".join(errors))

    drak_token = secret_from_payload_or_store(project_root, payload, "drak_token", connection.drak_token_env_key)
    fenix_refresh_token = secret_from_payload_or_store(
        project_root,
        payload,
        "fenix_refresh_token",
        connection.fenix_refresh_token_env_key,
    )

    if connection.drak_enabled and not drak_token:
        raise ValueError("Pro Drak-enabled connection je povinný Drak token.")

    upsert_sklik_connection(project_root, connection)

    if drak_token:
        set_secret(project_root, connection.drak_token_env_key, drak_token)
    if fenix_refresh_token:
        set_secret(project_root, connection.fenix_refresh_token_env_key, fenix_refresh_token)

    return sanitize_connection(connection)


def delete_sklik_connection_locally(project_root: Path, connection_key: str) -> dict[str, Any]:
    connections = {item.key: item for item in load_sklik_connections(project_root)}
    connection = connections.get(normalize_connection_key(connection_key))
    delete_sklik_connection(project_root, connection_key)
    if connection is not None:
        delete_secret(project_root, connection.drak_token_env_key)
        delete_secret(project_root, connection.fenix_refresh_token_env_key)
    return {"ok": True, "connection_key": connection_key}


def test_sklik_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.integrations.sklik.client_drak import SklikDrakClient
    from app.integrations.sklik.client_fenix import SklikFenixClient

    runtime_config = load_sklik_runtime_config(project_root)
    connection = _connection_from_payload(payload)
    errors = validate_connection(connection)
    if errors:
        raise ValueError(" | ".join(errors))

    drak_token = secret_from_payload_or_store(project_root, payload, "drak_token", connection.drak_token_env_key)
    fenix_refresh_token = secret_from_payload_or_store(
        project_root,
        payload,
        "fenix_refresh_token",
        connection.fenix_refresh_token_env_key,
    )

    result: dict[str, Any] = {
        "ok": True,
        "message": "Sklik connection byla ověřena.",
        "connection": sanitize_connection(connection),
        "drak": {"ok": not connection.drak_enabled, "message": "Drak je vypnutý."},
        "fenix": {"ok": not connection.fenix_enabled, "message": "Fénix je vypnutý."},
    }

    if connection.drak_enabled:
        if not drak_token:
            raise ValueError("Chybí Drak token.")
        drak_client = SklikDrakClient(
            token=drak_token,
            base_url=runtime_config.drak_base_url,
            timeout=runtime_config.request_timeout_seconds,
            max_retries=runtime_config.max_retries,
            user_agent=runtime_config.user_agent,
        )
        session_id = drak_client.login_by_token()
        limits = drak_client.api_limits()
        client_info = drak_client.client_get()
        result["drak"] = {
            "ok": True,
            "message": "Drak loginByToken, api.limits a client.get proběhly.",
            "session_seen": bool(session_id),
            "limits_status": limits.get("_status", "success"),
            "client_status": client_info.get("_status", "success"),
        }
        try:
            drak_client.logout()
        except Exception:
            pass

    if connection.fenix_enabled and fenix_refresh_token:
        fenix_client = SklikFenixClient(
            refresh_token=fenix_refresh_token,
            base_url=runtime_config.fenix_base_url,
            timeout=runtime_config.request_timeout_seconds,
            max_retries=runtime_config.max_retries,
            user_agent=runtime_config.user_agent,
        )
        fenix_client.refresh_access_token()
        api_home = fenix_client.get_api_home()
        user_me = fenix_client.get_user_me()
        result["fenix"] = {
            "ok": True,
            "message": "Fenix refresh token, API home a user/me probehly. Premise IDs je potreba vyplnit rucne v mappingu.",
            "api_home_keys": list(api_home.keys())[:10] if isinstance(api_home, dict) else [],
            "user_keys": list(user_me.keys())[:10] if isinstance(user_me, dict) else [],
            "manual_premise_ids_required": True,
        }

    upsert_sklik_connection(project_root, connection)
    if drak_token:
        set_secret(project_root, connection.drak_token_env_key, drak_token)
    if fenix_refresh_token:
        set_secret(project_root, connection.fenix_refresh_token_env_key, fenix_refresh_token)

    return result
