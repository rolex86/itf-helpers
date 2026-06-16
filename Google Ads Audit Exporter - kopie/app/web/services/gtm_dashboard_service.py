from __future__ import annotations

from typing import Any

from app.config.env_settings import env_config_from_mapping
from app.gtm.client import GtmApiClient, GtmApiError


def build_env_config_from_payload(payload: dict[str, Any]) -> Any:
    return env_config_from_mapping(
        {
            "developer_token": payload.get("developer_token", ""),
            "client_id": payload.get("client_id", ""),
            "client_secret": payload.get("client_secret", ""),
            "refresh_token": payload.get("refresh_token", ""),
            "gtm_account_id": payload.get("gtm_account_id", ""),
            "gtm_container_id": payload.get("gtm_container_id", ""),
            "gtm_enabled": payload.get("gtm_enabled", False),
        }
    )


def gtm_test_connection(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = build_env_config_from_payload(payload)
    client = GtmApiClient.from_env_config(env_config)
    result = client.test_connection()
    return {
        "ok": result.ok,
        "message": result.message,
        "instructions": result.instructions,
        "selected_account": result.selected_account,
        "selected_container": result.selected_container,
        "available_accounts": result.available_accounts,
    }


def gtm_list_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    env_config = build_env_config_from_payload(payload)
    client = GtmApiClient.from_env_config(env_config)
    if not client.is_enabled():
        return {
            "ok": False,
            "message": "GTM modul je vypnutý.",
            "accounts": [],
        }

    try:
        accounts = client.list_accounts()
        enriched_accounts: list[dict[str, Any]] = []
        for account in accounts:
            account_id = str(account.get("account_id") or "")
            containers = client.list_containers(account_id) if account_id else []
            enriched_accounts.append(
                {
                    **account,
                    "containers": containers,
                }
            )
        return {
            "ok": True,
            "message": f"Nalezeno {len(enriched_accounts)} GTM účtů.",
            "accounts": enriched_accounts,
        }
    except GtmApiError as exc:
        return {
            "ok": False,
            "message": exc.message,
            "accounts": [],
        }
