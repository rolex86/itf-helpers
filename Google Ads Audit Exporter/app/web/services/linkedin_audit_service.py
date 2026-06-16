from __future__ import annotations

from pathlib import Path
from typing import Any

from app.accounts.context_config import load_account_contexts, resolve_context_env_config
from app.config.env_settings import load_env_config
from app.integrations.linkedin.connections import load_linkedin_connections
from app.integrations.linkedin.reporting import resolve_date_range
from app.integrations.linkedin.sync import run_linkedin_context_sync
from app.integrations.linkedin.validators import validate_mapping
from app.web.services.linkedin_mapping_service import load_linkedin_mapping
from app.web.services.linkedin_oauth_service import ensure_connection_access_token
from app.web.services.linkedin_runtime import load_linkedin_runtime_config


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value != 0

    text = str(value).strip().lower()
    if not text:
        return default

    if text in {"1", "true", "yes", "on", "y"}:
        return True

    if text in {"0", "false", "no", "off", "n"}:
        return False

    return default


def _string(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "preset": _string(payload.get("preset"), "last_90_days") or "last_90_days",
        "date_from": _string(payload.get("date_from")),
        "date_to": _string(payload.get("date_to")),
        "include_raw": _to_bool(payload.get("include_raw"), default=True),
        "include_reporting": _to_bool(payload.get("include_reporting"), default=True),
        "include_professional_demographics": _to_bool(
            payload.get("include_professional_demographics"),
            default=True,
        ),
        "include_lead_sync": _to_bool(payload.get("include_lead_sync"), default=True),
        "include_web_scan": _to_bool(payload.get("include_web_scan"), default=True),
        "include_gtm_crosscheck": _to_bool(payload.get("include_gtm_crosscheck"), default=True),
        "limited_to_test_leads": _to_bool(payload.get("limited_to_test_leads"), default=True),
    }


def run_linkedin_export_for_context(
    project_root: Path,
    context_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}

    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    context = next((item for item in contexts if item.key == context_key), None)
    if context is None:
        raise ValueError(f"Kontext '{context_key}' nebyl nalezen.")

    mapping = load_linkedin_mapping(project_root).get(context_key)
    if mapping is None or not mapping.enabled:
        raise ValueError(f"Kontext '{context_key}' nemá LinkedIn mapping nebo není zapnutý.")

    mapping_errors = validate_mapping(mapping)
    if mapping_errors:
        raise ValueError(" | ".join(mapping_errors))

    connection = next(
        (item for item in load_linkedin_connections(project_root) if item.key == mapping.connection_key),
        None,
    )
    if connection is None:
        raise ValueError(f"LinkedIn connection '{mapping.connection_key}' nebyla nalezena.")

    runtime_config = load_linkedin_runtime_config(project_root)
    access_token = ensure_connection_access_token(project_root, connection)

    options = _options(payload)
    options["include_raw"] = bool(options["include_raw"] and runtime_config.export_raw)
    options["include_lead_sync"] = bool(
        options["include_lead_sync"]
        and runtime_config.enable_lead_sync
        and mapping.lead_sync_enabled
    )
    options["include_web_scan"] = bool(
        options["include_web_scan"]
        and runtime_config.enable_web_scan
        and mapping.web_scan_enabled
    )

    date_range = resolve_date_range(
        preset=options["preset"],
        date_from=options["date_from"],
        date_to=options["date_to"],
        default_days=runtime_config.default_date_range_days,
    )

    base_env = load_env_config(project_root / ".env")
    context_env = resolve_context_env_config(base_env, context)

    result = run_linkedin_context_sync(
        project_root=project_root,
        connection=connection,
        mapping=mapping,
        runtime_config=runtime_config,
        access_token=access_token,
        date_range=date_range,
        include_raw=options["include_raw"],
        include_reporting=options["include_reporting"],
        include_professional_demographics=options["include_professional_demographics"],
        include_lead_sync=options["include_lead_sync"],
        include_web_scan=options["include_web_scan"],
        include_gtm_crosscheck=options["include_gtm_crosscheck"],
        limited_to_test_leads=options["limited_to_test_leads"],
        gtm_env_config=context_env,
    )

    return {
        "ok": True,
        "context_key": context_key,
        "export_dir": result.export_dir,
        "status": result.manifest.status if result.manifest else "partial",
        "warning_count": len(result.warnings),
        "error_count": len(result.errors),
        "finding_count": len(result.findings),
    }


def run_linkedin_export_for_all_enabled_contexts(
    project_root: Path,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    mappings = load_linkedin_mapping(project_root)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for context_key, mapping in mappings.items():
        if not mapping.enabled:
            continue

        try:
            results.append(run_linkedin_export_for_context(project_root, context_key, payload))
        except Exception as exc:
            errors.append({"context_key": context_key, "message": str(exc)})

    return {
        "ok": True,
        "mode": "all_enabled_contexts",
        "results": results,
        "errors": errors,
    }