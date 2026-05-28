from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.config_store import load_config_payload, save_config_payload
from app.config.env_settings import GoogleAdsEnvConfig, load_env_config, save_env_config
from app.config.settings import DEFAULT_REPORTS, load_settings
from app.export.workflow import ExportExecutionResult, execute_export
from app.web.services.export_history import ExportHistoryItem, list_export_history


PRESET_OPTIONS = ("LAST_30_DAYS", "LAST_90_DAYS", "LAST_365_DAYS", "CUSTOM")
PRESET_LABELS = {
    "LAST_30_DAYS": "Poslednich 30 dni",
    "LAST_90_DAYS": "Poslednich 90 dni",
    "LAST_365_DAYS": "Poslednich 365 dni",
    "CUSTOM": "Vlastni obdobi",
}

REPORT_LABELS = {
    "account": "Ucet",
    "account_diagnostics": "Diagnostika uctu",
    "linked_accounts": "Propojene sluzby",
    "campaigns": "Kampane",
    "campaigns_monthly": "Kampane po mesicich",
    "ad_groups": "Sestavy",
    "keywords": "Klicova slova",
    "search_terms": "Vyhledavaci dotazy",
    "ads": "Reklamy",
    "assets": "Assety",
    "devices": "Zarizeni",
    "locations": "Lokality",
    "landing_pages": "Cilove stranky",
    "shopping_products": "Shopping produkty",
    "shopping_products_summary": "Shopping produkty souhrn",
    "google_ads_recommendations": "Google Ads doporuceni",
    "conversion_actions": "Konverze",
    "pmax_campaigns": "PMax kampane",
    "pmax_asset_groups": "PMax asset groupy",
    "change_history": "Historie zmen",
}


@dataclass(slots=True)
class DashboardViewModel:
    env_config: GoogleAdsEnvConfig
    config_payload: dict[str, Any]
    export_history: list[ExportHistoryItem]
    report_labels: dict[str, str]
    preset_options: tuple[str, ...]
    preset_labels: dict[str, str]


def _config_paths(project_root: Path) -> tuple[Path, Path]:
    return project_root / ".env", project_root / "config.yaml"


def load_dashboard_state(project_root: Path) -> DashboardViewModel:
    env_path, config_path = _config_paths(project_root)
    return DashboardViewModel(
        env_config=load_env_config(env_path),
        config_payload=load_config_payload(config_path),
        export_history=list_export_history(project_root / "exports"),
        report_labels=REPORT_LABELS,
        preset_options=PRESET_OPTIONS,
        preset_labels=PRESET_LABELS,
    )


def save_dashboard_configuration(project_root: Path, payload: dict[str, Any]) -> None:
    env_path, config_path = _config_paths(project_root)
    save_env_config(env_path, payload["env_config"])
    save_config_payload(config_path, payload["config_payload"])


def run_export_from_dashboard(project_root: Path, payload: dict[str, Any]) -> ExportExecutionResult:
    save_dashboard_configuration(project_root, payload)
    _, config_path = _config_paths(project_root)
    settings = load_settings(config_path=config_path)
    return execute_export(settings=settings, project_root=project_root, config_path=config_path)


def default_reports_payload() -> dict[str, bool]:
    return dict(DEFAULT_REPORTS)
