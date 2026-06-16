from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.accounts.context_config import load_account_contexts, resolve_context_env_config
from app.config.env_settings import load_env_config
from app.gtm.export import build_gtm_exports
from app.integrations.meta.connections import load_meta_connections
from app.integrations.meta.sync import run_meta_context_sync


@dataclass(slots=True)
class GtmTagsLoadResult:
    tags: pd.DataFrame
    warnings: list[str]


def _empty_gtm_tags_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "tag_id",
            "name",
            "type",
            "parameter_json",
            "notes",
            "firing_trigger_ids",
            "consent_settings",
        ]
    )


def _load_context_gtm_tags(project_root: Path, context) -> GtmTagsLoadResult:
    warnings: list[str] = []

    try:
        env_config = load_env_config(project_root / ".env")
    except Exception as exc:
        return GtmTagsLoadResult(
            tags=_empty_gtm_tags_frame(),
            warnings=[f"GTM env config load failed for context '{context.key}': {exc}"],
        )

    try:
        context_env = resolve_context_env_config(env_config, context)
    except Exception as exc:
        return GtmTagsLoadResult(
            tags=_empty_gtm_tags_frame(),
            warnings=[f"GTM context env resolve failed for context '{context.key}': {exc}"],
        )

    try:
        export_result = build_gtm_exports(
            env_config=context_env,
            reports_enabled={"gtm_tags": True},
        )
    except Exception as exc:
        return GtmTagsLoadResult(
            tags=_empty_gtm_tags_frame(),
            warnings=[f"GTM tags export failed for context '{context.key}': {exc}"],
        )

    gtm_tags = export_result.datasets.get("gtm_tags")
    if gtm_tags is None:
        warnings.append(f"GTM tags dataset missing for context '{context.key}'.")
        return GtmTagsLoadResult(tags=_empty_gtm_tags_frame(), warnings=warnings)

    if not isinstance(gtm_tags, pd.DataFrame):
        warnings.append(
            f"GTM tags dataset has unexpected type for context '{context.key}': {type(gtm_tags).__name__}."
        )
        return GtmTagsLoadResult(tags=_empty_gtm_tags_frame(), warnings=warnings)

    return GtmTagsLoadResult(tags=gtm_tags, warnings=warnings)


def run_meta_export_for_context(project_root: Path, context_key: str) -> dict[str, Any]:
    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    context = next((item for item in contexts if item.key == context_key), None)

    if context is None:
        raise ValueError(f"Kontext '{context_key}' nebyl nalezen.")

    if not context.meta.enabled:
        raise ValueError(f"Kontext '{context_key}' nema Meta modul zapnuty.")

    connection = next(
        (item for item in load_meta_connections(project_root) if item.key == context.meta.connection_key),
        None,
    )
    if connection is None:
        raise ValueError(f"Meta connection '{context.meta.connection_key}' nebyla nalezena.")

    gtm_load_result = _load_context_gtm_tags(project_root, context)

    result = run_meta_context_sync(
        project_root=project_root,
        context=context,
        connection=connection,
        gtm_tags=gtm_load_result.tags,
    )

    # These warnings are service-level warnings from loading GTM context data.
    # The Meta export itself should still run so audit_rules can surface missing GTM/Pixel findings.
    combined_warnings = list(gtm_load_result.warnings) + list(result.warnings)

    return {
        "ok": True,
        "context_key": context.key,
        "context_label": context.label,
        "export_dir": result.export_dir,
        "warning_count": len(combined_warnings),
        "finding_count": len(result.findings),
        "warnings": combined_warnings,
    }


def run_meta_export_for_all_enabled_contexts(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for context in contexts:
        if not context.enabled or not context.meta.enabled:
            continue

        try:
            results.append(run_meta_export_for_context(project_root, context.key))
        except Exception as exc:
            errors.append(
                {
                    "context_key": context.key,
                    "context_label": context.label,
                    "message": str(exc),
                }
            )

    return {
        "ok": True,
        "mode": "all_enabled_contexts",
        "results": results,
        "errors": errors,
    }