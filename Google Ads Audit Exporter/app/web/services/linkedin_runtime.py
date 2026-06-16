from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from app.integrations.linkedin.models import (
    DEFAULT_LINKEDIN_API_VERSION,
    DEFAULT_LINKEDIN_USER_AGENT,
    LinkedInRuntimeConfig,
)


def _to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default

    text = str(value).strip().lower()
    if not text:
        return default

    if text in {"1", "true", "yes", "on", "y"}:
        return True

    if text in {"0", "false", "no", "off", "n"}:
        return False

    return default


def _to_int(value: object, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _env_str(values: dict[str, object], key: str, default: str = "") -> str:
    return str(values.get(key, default) or default).strip()


def load_linkedin_runtime_config(project_root: Path) -> LinkedInRuntimeConfig:
    env_path = project_root / ".env"
    values = dotenv_values(env_path) if env_path.exists() else {}

    return LinkedInRuntimeConfig(
        client_id=_env_str(values, "LINKEDIN_CLIENT_ID"),
        client_secret=_env_str(values, "LINKEDIN_CLIENT_SECRET"),
        redirect_uri=_env_str(
            values,
            "LINKEDIN_REDIRECT_URI",
            "http://localhost:5000/linkedin/oauth/callback",
        ),
        api_version=_env_str(
            values,
            "LINKEDIN_API_VERSION",
            DEFAULT_LINKEDIN_API_VERSION,
        ) or DEFAULT_LINKEDIN_API_VERSION,
        user_agent=_env_str(
            values,
            "LINKEDIN_USER_AGENT",
            DEFAULT_LINKEDIN_USER_AGENT,
        ) or DEFAULT_LINKEDIN_USER_AGENT,
        enable_write_actions=_to_bool(
            values.get("LINKEDIN_ENABLE_WRITE_ACTIONS"),
            default=False,
        ),
        default_date_range_days=_to_int(
            values.get("LINKEDIN_DEFAULT_DATE_RANGE_DAYS", 90),
            90,
        ),
        request_timeout_seconds=_to_int(
            values.get("LINKEDIN_REQUEST_TIMEOUT_SECONDS", 60),
            60,
        ),
        max_retries=_to_int(
            values.get("LINKEDIN_MAX_RETRIES", 3),
            3,
        ),
        export_raw=_to_bool(
            values.get("LINKEDIN_EXPORT_RAW"),
            default=True,
        ),
        enable_web_scan=_to_bool(
            values.get("LINKEDIN_ENABLE_WEB_SCAN"),
            default=True,
        ),
        enable_lead_sync=_to_bool(
            values.get("LINKEDIN_ENABLE_LEAD_SYNC"),
            default=True,
        ),
        enable_conversions_api_audit=_to_bool(
            values.get("LINKEDIN_ENABLE_CONVERSIONS_API_AUDIT"),
            default=True,
        ),
    )