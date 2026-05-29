from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.accounts.context_config import AccountContext, has_multi_account_config, load_account_contexts
from app.config.config_store import load_config_payload, save_config_payload
from app.config.env_settings import GoogleAdsEnvConfig, load_env_config, save_env_config
from app.config.settings import DEFAULT_REPORTS, load_settings
from app.export.workflow import ExportExecutionResult, execute_export
from app.web.services.mapping_service import run_all_context_exports, run_selected_context_export
from app.web.services.export_history import ExportHistoryItem, list_export_history


PRESET_OPTIONS = ("LAST_30_DAYS", "LAST_90_DAYS", "LAST_365_DAYS", "CUSTOM")
PRESET_LABELS = {
    "LAST_30_DAYS": "Posledních 30 dní",
    "LAST_90_DAYS": "Posledních 90 dní",
    "LAST_365_DAYS": "Posledních 365 dní",
    "CUSTOM": "Vlastní období",
}

REPORT_LABELS = {
    "account": "Účet",
    "account_diagnostics": "Diagnostika účtu",
    "linked_accounts": "Propojené služby",
    "campaigns": "Kampaně",
    "campaigns_monthly": "Kampaně po měsících",
    "ad_groups": "Sestavy",
    "keywords": "Klíčová slova",
    "search_terms": "Vyhledávací dotazy",
    "ads": "Reklamy",
    "assets": "Assety",
    "devices": "Zařízení",
    "locations": "Lokality",
    "landing_pages": "Cílové stránky",
    "shopping_products": "Shopping produkty",
    "shopping_products_summary": "Shopping produkty souhrn",
    "merchant_products": "Merchant produkty",
    "merchant_product_issues": "Merchant problémy produktů",
    "merchant_product_status_summary": "Merchant status summary",
    "product_optimization": "Produktová optimalizace",
    "product_feed_issues_with_spend": "Feed problémy se spendem",
    "product_custom_label_performance": "Výkon custom labelů",
    "ga4_landing_pages": "GA4 landing pages",
    "landing_page_diagnostics": "Diagnostika cílových stránek",
    "ga4_ecommerce_funnel": "GA4 ecommerce funnel",
    "gsc_queries": "GSC queries",
    "gsc_pages": "GSC pages",
    "gsc_page_query": "GSC page-query",
    "gsc_opportunities": "GSC příležitosti",
    "pagespeed_landing_pages": "PageSpeed landing pages",
    "gtm_tags": "GTM tagy",
    "gtm_triggers": "GTM triggery",
    "gtm_variables": "GTM proměnné",
    "gtm_versions": "GTM verze",
    "measurement_diagnostics": "Diagnostika měření",
    "google_ads_recommendations": "Google Ads doporučení",
    "conversion_actions": "Konverze",
    "pmax_campaigns": "PMax kampaně",
    "pmax_asset_groups": "PMax asset groupy",
    "change_history": "Historie změn",
}


@dataclass(slots=True)
class DashboardViewModel:
    env_config: GoogleAdsEnvConfig
    config_payload: dict[str, Any]
    export_history: list[ExportHistoryItem]
    report_labels: dict[str, str]
    preset_options: tuple[str, ...]
    preset_labels: dict[str, str]
    account_contexts: list[AccountContext]
    has_multi_account_config: bool


def _config_paths(project_root: Path) -> tuple[Path, Path]:
    return project_root / ".env", project_root / "config.yaml"


def load_dashboard_state(project_root: Path) -> DashboardViewModel:
    env_path, config_path = _config_paths(project_root)
    accounts_path = project_root / "config.accounts.yaml"
    return DashboardViewModel(
        env_config=load_env_config(env_path),
        config_payload=load_config_payload(config_path),
        export_history=list_export_history(project_root / "exports"),
        report_labels=REPORT_LABELS,
        preset_options=PRESET_OPTIONS,
        preset_labels=PRESET_LABELS,
        account_contexts=load_account_contexts(accounts_path),
        has_multi_account_config=has_multi_account_config(accounts_path),
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


def run_multi_mode_export_from_dashboard(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    save_dashboard_configuration(project_root, payload)
    export_mode = payload.get("ui_state", {}).get("export_mode", "single_account")
    selected_context_key = payload.get("ui_state", {}).get("selected_context_key", "")

    if export_mode == "selected_context":
        if not selected_context_key:
            raise ValueError("Není vybraný žádný kontext pro export vybraného kontextu.")
        return run_selected_context_export(project_root, context_key=selected_context_key)
    if export_mode == "all_enabled_contexts":
        return run_all_context_exports(project_root)
    raise ValueError(f"Neznámý režim exportu: {export_mode}")


def default_reports_payload() -> dict[str, bool]:
    return dict(DEFAULT_REPORTS)
