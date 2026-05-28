from __future__ import annotations

from pathlib import Path
from typing import Any

from app.accounts.context_config import (
    AccountContext,
    context_from_mapping,
    context_to_mapping,
    load_account_contexts,
    save_account_contexts,
)
from app.accounts.context_runner import run_context_export, run_multi_context_export, test_account_context
from app.config.env_settings import load_env_config
from app.config.settings import load_settings
from app.web.services.discovery_service import load_discovery_tables


def accounts_config_path(project_root: Path) -> Path:
    return project_root / "config.accounts.yaml"


def load_mapping_state(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(accounts_config_path(project_root))
    return {
        "contexts": contexts,
        "contexts_payload": [context_to_mapping(context) for context in contexts],
        "discovery_tables": load_discovery_tables(project_root),
    }


def save_mapping(project_root: Path, contexts: list[AccountContext]) -> None:
    save_account_contexts(accounts_config_path(project_root), contexts)


def parse_contexts_payload(payload: dict[str, Any]) -> list[AccountContext]:
    raw_contexts = payload.get("account_contexts", []) or []
    contexts: list[AccountContext] = []
    for item in raw_contexts:
        if not isinstance(item, dict):
            continue
        context = context_from_mapping(item)
        if not context.key:
            continue
        contexts.append(context)
    return contexts


def test_context_from_payload(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    context = context_from_mapping(payload)
    env_config = load_env_config(project_root / ".env")
    result = test_account_context(context=context, base_env_config=env_config)
    return {
        "ok": result.ok,
        "context_key": result.context_key,
        "context_label": result.context_label,
        "services": {
            key: {
                "status": value.status,
                "details": value.details,
            }
            for key, value in result.services.items()
        },
    }


def run_selected_context_export(
    project_root: Path,
    *,
    context_key: str,
) -> dict[str, Any]:
    contexts = load_account_contexts(accounts_config_path(project_root))
    selected = next((context for context in contexts if context.key == context_key), None)
    if selected is None:
        raise ValueError(f"Kontext '{context_key}' nebyl nalezen v config.accounts.yaml.")

    env_config = load_env_config(project_root / ".env")
    settings = load_settings(project_root / "config.yaml")
    result = run_context_export(
        project_root=project_root,
        settings=settings,
        config_path=project_root / "config.yaml",
        base_env_config=env_config,
        context=selected,
        export_base_name_override=f"{result_date_prefix(settings, project_root)}_{selected.key}_{selected.google_ads_customer_id}",
        export_mode="selected_context",
    )
    return {
        "ok": result.exit_code == 0,
        "context_key": selected.key,
        "context_label": selected.label,
        "export_path": str(result.export_paths.base_dir),
        "error_count": len(result.errors),
        "xlsx_path": str(result.export_paths.xlsx_path),
    }


def run_all_context_exports(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(accounts_config_path(project_root))
    env_config = load_env_config(project_root / ".env")
    settings = load_settings(project_root / "config.yaml")
    result = run_multi_context_export(
        project_root=project_root,
        settings=settings,
        config_path=project_root / "config.yaml",
        base_env_config=env_config,
        contexts=contexts,
    )
    return {
        "ok": True,
        "mode": result.mode,
        "cross_account_dir": result.cross_account_dir,
        "context_results": [
            {
                "context_key": bundle.context.key,
                "context_label": bundle.context.label,
                "export_path": str(bundle.result.export_paths.base_dir),
                "error_count": len(bundle.result.errors),
            }
            for bundle in result.context_results
        ],
        "context_errors": result.context_errors,
    }


def result_date_prefix(settings, project_root: Path) -> str:
    from app.utils.dates import resolve_date_range

    return resolve_date_range(settings.date_range).export_date.isoformat()
