from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from dotenv import dotenv_values, set_key, unset_key
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal local environments
    def dotenv_values(path: Path | str) -> dict[str, str]:
        file_path = Path(path)
        if not file_path.exists():
            return {}
        values: dict[str, str] = {}
        for line in file_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            values[key.strip()] = raw_value.strip().strip('"').strip("'")
        return values

    def _write_values(path: Path, values: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f'{key}="{value}"' for key, value in values.items()]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def set_key(path: str, key: str, value: str, quote_mode: str = "always") -> None:
        file_path = Path(path)
        values = dotenv_values(file_path)
        values[str(key)] = str(value)
        _write_values(file_path, values)

    def unset_key(path: str, key: str) -> None:
        file_path = Path(path)
        values = dotenv_values(file_path)
        values.pop(str(key), None)
        _write_values(file_path, values)

from app.integrations.sklik.models import (
    DEFAULT_SKLIK_DRAK_BASE_URL,
    DEFAULT_SKLIK_FENIX_BASE_URL,
    DEFAULT_SKLIK_USER_AGENT,
    SklikRuntimeConfig,
)
from app.integrations.sklik.validators import normalize_env_key


MASKED_SECRET_VALUES = {
    "***",
    "****",
    "*****",
    "********",
    "••••",
    "••••••••",
}


def sklik_env_path(project_root: Path) -> Path:
    return project_root / ".env.sklik.local"


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


def load_sklik_runtime_config(project_root: Path) -> SklikRuntimeConfig:
    env_path = sklik_env_path(project_root)
    values = dotenv_values(env_path) if env_path.exists() else {}

    return SklikRuntimeConfig(
        drak_base_url=_env_str(values, "SKLIK_DRAK_BASE_URL", DEFAULT_SKLIK_DRAK_BASE_URL) or DEFAULT_SKLIK_DRAK_BASE_URL,
        fenix_base_url=_env_str(values, "SKLIK_FENIX_BASE_URL", DEFAULT_SKLIK_FENIX_BASE_URL) or DEFAULT_SKLIK_FENIX_BASE_URL,
        request_timeout_seconds=_to_int(values.get("SKLIK_REQUEST_TIMEOUT_SECONDS"), 60),
        max_retries=_to_int(values.get("SKLIK_MAX_RETRIES"), 3),
        export_raw=_to_bool(values.get("SKLIK_EXPORT_RAW"), default=True),
        export_pii=_to_bool(values.get("SKLIK_EXPORT_PII"), default=False),
        enable_web_scan=_to_bool(values.get("SKLIK_ENABLE_WEB_SCAN"), default=True),
        enable_gtm_crosscheck=_to_bool(values.get("SKLIK_ENABLE_GTM_CROSSCHECK"), default=True),
        enable_fenix=_to_bool(values.get("SKLIK_ENABLE_FENIX"), default=True),
        default_date_range_days=_to_int(values.get("SKLIK_DEFAULT_DATE_RANGE_DAYS"), 90),
        fenix_export_items=_to_bool(values.get("SKLIK_FENIX_EXPORT_ITEMS"), default=False),
        fenix_max_items=_to_int(values.get("SKLIK_FENIX_MAX_ITEMS"), 5000),
        user_agent=_env_str(values, "SKLIK_USER_AGENT", DEFAULT_SKLIK_USER_AGENT) or DEFAULT_SKLIK_USER_AGENT,
    )


def build_drak_token_env_key(connection_key: str) -> str:
    return f"SKLIK_DRAK_TOKEN__{normalize_env_key(connection_key)}"


def build_fenix_refresh_token_env_key(connection_key: str) -> str:
    return f"SKLIK_FENIX_REFRESH_TOKEN__{normalize_env_key(connection_key)}"


def _is_masked_secret(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text in MASKED_SECRET_VALUES or set(text) <= {"*", "•"}


def load_sklik_env_values(project_root: Path) -> dict[str, str]:
    env_path = sklik_env_path(project_root)
    if not env_path.exists():
        return {}
    return {str(key): str(value or "") for key, value in dotenv_values(env_path).items()}


def get_secret(project_root: Path, env_key: str) -> str:
    return str(load_sklik_env_values(project_root).get(env_key, "") or "").strip()


def set_secret(project_root: Path, env_key: str, value: str) -> None:
    env_path = sklik_env_path(project_root)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    set_key(str(env_path), env_key, value, quote_mode="always")


def delete_secret(project_root: Path, env_key: str) -> None:
    env_path = sklik_env_path(project_root)
    if not env_path.exists():
        return
    unset_key(str(env_path), env_key)


def secret_from_payload_or_store(project_root: Path, payload: dict[str, Any], field_name: str, env_key: str) -> str:
    raw_value = payload.get(field_name)
    if raw_value is None:
        return get_secret(project_root, env_key)
    text = str(raw_value or "").strip()
    if not text or _is_masked_secret(text):
        return get_secret(project_root, env_key)
    return text
