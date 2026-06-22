from __future__ import annotations

from pathlib import Path
from typing import Any

from app.integrations.sklik.reporting import resolve_date_range
from app.integrations.sklik.sync import export_all_enabled_sklik_contexts, export_sklik_context, load_sklik_mapping
from app.web.services.sklik_runtime import load_sklik_runtime


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
        "granularity": _string(payload.get("granularity"), "daily") or "daily",
        "include_raw": _to_bool(payload.get("include_raw"), default=True),
        "include_empty_statistics": _to_bool(payload.get("include_empty_statistics"), default=False),
        "enable_fenix": _to_bool(payload.get("enable_fenix"), default=True),
        "enable_gtm_crosscheck": _to_bool(payload.get("enable_gtm_crosscheck"), default=True),
        "enable_web_scan": _to_bool(payload.get("enable_web_scan"), default=True),
    }


def run_sklik_export_for_context(project_root: Path, context_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    mapping = load_sklik_mapping(project_root).get(context_key)
    if mapping is None or not mapping.enabled:
        raise ValueError(f"Kontext '{context_key}' nemá Sklik mapping nebo není zapnutý.")

    runtime_config = load_sklik_runtime(project_root)
    options = _options(payload)
    date_range = resolve_date_range(
        preset=options["preset"],
        date_from=options["date_from"],
        date_to=options["date_to"],
        default_days=runtime_config.default_date_range_days,
    )

    export_path = export_sklik_context(
        project_root=project_root,
        context_key=context_key,
        date_from=date_range.start.isoformat(),
        date_to=date_range.end.isoformat(),
        granularity=options["granularity"],
        include_raw=options["include_raw"],
        include_empty_statistics=options["include_empty_statistics"],
        enable_fenix=options["enable_fenix"],
        enable_gtm_crosscheck=options["enable_gtm_crosscheck"],
        enable_web_scan=options["enable_web_scan"],
    )

    return {
        "ok": True,
        "context_key": context_key,
        "export_dir": str(export_path),
        "status": "success",
    }


def run_sklik_export_for_all_enabled_contexts(project_root: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    runtime_config = load_sklik_runtime(project_root)
    options = _options(payload)
    date_range = resolve_date_range(
        preset=options["preset"],
        date_from=options["date_from"],
        date_to=options["date_to"],
        default_days=runtime_config.default_date_range_days,
    )

    outputs = export_all_enabled_sklik_contexts(
        project_root=project_root,
        date_from=date_range.start.isoformat(),
        date_to=date_range.end.isoformat(),
        granularity=options["granularity"],
        include_raw=options["include_raw"],
        include_empty_statistics=options["include_empty_statistics"],
        enable_fenix=options["enable_fenix"],
        enable_gtm_crosscheck=options["enable_gtm_crosscheck"],
        enable_web_scan=options["enable_web_scan"],
    )

    return {
        "ok": True,
        "mode": "all_enabled_contexts",
        "results": [str(path) for path in outputs],
    }

