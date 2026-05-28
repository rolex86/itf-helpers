from __future__ import annotations

from typing import Any

from app.config.env_settings import env_config_from_mapping
from app.search_console.client import SearchConsoleApiClient, SearchConsoleApiError


def _env_config_from_payload(payload: dict[str, Any]) -> Any:
    return env_config_from_mapping(
        {
            "client_id": payload.get("client_id", ""),
            "client_secret": payload.get("client_secret", ""),
            "refresh_token": payload.get("refresh_token", ""),
            "gsc_site_url": payload.get("gsc_site_url", ""),
            "gsc_enabled": payload.get("gsc_enabled", False),
        }
    )


def gsc_test_connection(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = _env_config_from_payload(payload)
    client = SearchConsoleApiClient.from_env_config(env_config)
    result = client.test_connection()
    return {
        "ok": result.ok,
        "message": result.message,
        "instructions": result.instructions,
        "selected_property": result.selected_property,
        "available_properties": result.available_properties,
    }


def gsc_list_properties(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = _env_config_from_payload(payload)
    client = SearchConsoleApiClient.from_env_config(env_config)
    if not client.is_enabled():
        return {"ok": False, "message": "Search Console modul je vypnuty.", "properties": []}

    try:
        properties = client.list_sites()
        return {
            "ok": True,
            "message": f"Nalezeno {len(properties)} dostupnych Search Console properties.",
            "properties": properties,
        }
    except SearchConsoleApiError as exc:
        return {"ok": False, "message": exc.message, "properties": []}
