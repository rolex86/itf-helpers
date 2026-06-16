from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from app.integrations.linkedin.models import LinkedInRuntimeConfig


def _to_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: object, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def load_linkedin_runtime_config(project_root: Path) -> LinkedInRuntimeConfig:
    env_path = project_root / ".env"
    values = dotenv_values(env_path) if env_path.exists() else {}
    return LinkedInRuntimeConfig(
        client_id=str(values.get("LINKEDIN_CLIENT_ID", "") or "").strip(),
        client_secret=str(values.get("LINKEDIN_CLIENT_SECRET", "") or "").strip(),
        redirect_uri=str(values.get("LINKEDIN_REDIRECT_URI", "http://localhost:5000/linkedin/oauth/callback") or "").strip(),
        api_version=str(values.get("LINKEDIN_API_VERSION", "202605") or "202605").strip(),
        user_agent=str(values.get("LINKEDIN_USER_AGENT", "ITFutureLinkedInAudit/1.0") or "ITFutureLinkedInAudit/1.0").strip(),
        enable_write_actions=_to_bool(values.get("LINKEDIN_ENABLE_WRITE_ACTIONS", "")),
        default_date_range_days=_to_int(values.get("LINKEDIN_DEFAULT_DATE_RANGE_DAYS", 90), 90),
        request_timeout_seconds=_to_int(values.get("LINKEDIN_REQUEST_TIMEOUT_SECONDS", 60), 60),
        max_retries=_to_int(values.get("LINKEDIN_MAX_RETRIES", 3), 3),
        export_raw=_to_bool(values.get("LINKEDIN_EXPORT_RAW", True)),
        enable_web_scan=_to_bool(values.get("LINKEDIN_ENABLE_WEB_SCAN", True)),
        enable_lead_sync=_to_bool(values.get("LINKEDIN_ENABLE_LEAD_SYNC", True)),
        enable_conversions_api_audit=_to_bool(values.get("LINKEDIN_ENABLE_CONVERSIONS_API_AUDIT", True)),
    )

