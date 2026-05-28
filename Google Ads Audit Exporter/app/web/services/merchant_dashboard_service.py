from __future__ import annotations

from typing import Any

from app.config.env_settings import env_config_from_mapping
from app.merchant.client import MerchantApiClient, MerchantApiError


def build_env_config_from_payload(payload: dict[str, Any]) -> Any:
    return env_config_from_mapping(
        {
            "developer_token": payload.get("developer_token", ""),
            "client_id": payload.get("client_id", ""),
            "client_secret": payload.get("client_secret", ""),
            "refresh_token": payload.get("refresh_token", ""),
            "login_customer_id": payload.get("login_customer_id", ""),
            "merchant_account_id": payload.get("merchant_account_id", ""),
            "merchant_enabled": payload.get("merchant_enabled", False),
        }
    )


def merchant_test_connection(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = build_env_config_from_payload(payload)
    client = MerchantApiClient.from_env_config(env_config)
    result = client.test_connection()
    return {
        "ok": result.ok,
        "message": result.message,
        "instructions": result.instructions,
        "selected_account": result.selected_account,
        "available_accounts": result.available_accounts,
        "service_count": result.service_count,
    }


def merchant_list_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = build_env_config_from_payload(payload)
    client = MerchantApiClient.from_env_config(env_config)
    if not client.is_enabled():
        return {
            "ok": False,
            "message": "Merchant Center modul je vypnuty.",
            "accounts": [],
        }

    try:
        accounts = client.list_accessible_accounts()
        return {
            "ok": True,
            "message": f"Nalezeno {len(accounts)} dostupnych Merchant uctu.",
            "accounts": accounts,
        }
    except MerchantApiError as exc:
        return {
            "ok": False,
            "message": exc.message,
            "accounts": [],
        }
