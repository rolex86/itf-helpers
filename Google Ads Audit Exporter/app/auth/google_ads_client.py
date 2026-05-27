from __future__ import annotations

import os
from typing import Any


def _normalize_customer_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("-", "").strip()


def build_google_ads_client() -> Any:
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError(
            "Missing dependency 'google-ads'. Install requirements.txt first."
        ) from exc

    required_env = {
        "developer_token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
    }

    missing = [key for key, value in required_env.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    credentials: dict[str, Any] = dict(required_env)
    login_customer_id = _normalize_customer_id(os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
    if login_customer_id:
        credentials["login_customer_id"] = login_customer_id

    credentials["use_proto_plus"] = True
    return GoogleAdsClient.load_from_dict(credentials)
