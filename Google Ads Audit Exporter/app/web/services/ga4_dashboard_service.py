from __future__ import annotations

from typing import Any

from app.config.env_settings import env_config_from_mapping
from app.ga4.client import Ga4ApiClient, Ga4ApiError


def _env_config_from_payload(payload: dict[str, Any]) -> Any:
    return env_config_from_mapping(
        {
            "client_id": payload.get("client_id", ""),
            "client_secret": payload.get("client_secret", ""),
            "refresh_token": payload.get("refresh_token", ""),
            "ga4_property_id": payload.get("ga4_property_id", ""),
            "ga4_enabled": payload.get("ga4_enabled", False),
        }
    )


def ga4_test_connection(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = _env_config_from_payload(payload)
    client = Ga4ApiClient.from_env_config(env_config)
    result = client.test_connection()
    return {
        "ok": result.ok,
        "message": result.message,
        "instructions": result.instructions,
        "selected_property": result.selected_property,
        "available_properties": result.available_properties,
    }


def ga4_list_properties(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = _env_config_from_payload(payload)
    client = Ga4ApiClient.from_env_config(env_config)
    if not client.is_enabled():
        return {"ok": False, "message": "GA4 modul je vypnutý.", "properties": []}

    try:
        properties = client.list_accessible_properties()
        return {
            "ok": True,
            "message": f"Nalezeno {len(properties)} dostupných GA4 properties.",
            "properties": properties,
        }
    except Ga4ApiError as exc:
        return {"ok": False, "message": exc.message, "properties": []}
