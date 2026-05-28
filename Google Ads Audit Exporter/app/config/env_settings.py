from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(slots=True)
class GoogleAdsEnvConfig:
    developer_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    login_customer_id: str = ""
    merchant_account_id: str = ""
    merchant_enabled: bool = False


ENV_KEY_MAP = {
    "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
    "client_id": "GOOGLE_ADS_CLIENT_ID",
    "client_secret": "GOOGLE_ADS_CLIENT_SECRET",
    "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN",
    "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "merchant_account_id": "MERCHANT_CENTER_ACCOUNT_ID",
    "merchant_enabled": "GOOGLE_MERCHANT_ENABLED",
}


def _to_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_id(value: object) -> str:
    return str(value or "").replace("-", "").strip()


def load_env_config(env_path: Path) -> GoogleAdsEnvConfig:
    if not env_path.exists():
        return GoogleAdsEnvConfig()

    values = dotenv_values(env_path)
    return GoogleAdsEnvConfig(
        developer_token=str(values.get(ENV_KEY_MAP["developer_token"], "") or ""),
        client_id=str(values.get(ENV_KEY_MAP["client_id"], "") or ""),
        client_secret=str(values.get(ENV_KEY_MAP["client_secret"], "") or ""),
        refresh_token=str(values.get(ENV_KEY_MAP["refresh_token"], "") or ""),
        login_customer_id=_normalize_id(values.get(ENV_KEY_MAP["login_customer_id"], "")),
        merchant_account_id=_normalize_id(values.get(ENV_KEY_MAP["merchant_account_id"], "")),
        merchant_enabled=_to_bool(values.get(ENV_KEY_MAP["merchant_enabled"], "")),
    )


def env_config_from_mapping(values: dict[str, object]) -> GoogleAdsEnvConfig:
    return GoogleAdsEnvConfig(
        developer_token=str(values.get("developer_token", "") or "").strip(),
        client_id=str(values.get("client_id", "") or "").strip(),
        client_secret=str(values.get("client_secret", "") or "").strip(),
        refresh_token=str(values.get("refresh_token", "") or "").strip(),
        login_customer_id=_normalize_id(values.get("login_customer_id", "")),
        merchant_account_id=_normalize_id(values.get("merchant_account_id", "")),
        merchant_enabled=_to_bool(values.get("merchant_enabled", "")),
    )


def save_env_config(env_path: Path, env_config: GoogleAdsEnvConfig) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for field_name, key in ENV_KEY_MAP.items():
        value = getattr(env_config, field_name)
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
