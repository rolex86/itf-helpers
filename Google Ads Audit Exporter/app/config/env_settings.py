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


ENV_KEY_MAP = {
    "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
    "client_id": "GOOGLE_ADS_CLIENT_ID",
    "client_secret": "GOOGLE_ADS_CLIENT_SECRET",
    "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN",
    "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
}


def load_env_config(env_path: Path) -> GoogleAdsEnvConfig:
    if not env_path.exists():
        return GoogleAdsEnvConfig()

    values = dotenv_values(env_path)
    return GoogleAdsEnvConfig(
        developer_token=str(values.get(ENV_KEY_MAP["developer_token"], "") or ""),
        client_id=str(values.get(ENV_KEY_MAP["client_id"], "") or ""),
        client_secret=str(values.get(ENV_KEY_MAP["client_secret"], "") or ""),
        refresh_token=str(values.get(ENV_KEY_MAP["refresh_token"], "") or ""),
        login_customer_id=str(values.get(ENV_KEY_MAP["login_customer_id"], "") or ""),
    )


def save_env_config(env_path: Path, env_config: GoogleAdsEnvConfig) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for field_name, key in ENV_KEY_MAP.items():
        value = getattr(env_config, field_name)
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
