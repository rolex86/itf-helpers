from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.accounts.context_config import AccountContext, resolve_context_env_config, resolve_context_settings
from app.accounts.cross_account_exports import ContextExportBundle, write_cross_account_exports
from app.auth.google_ads_client import build_google_ads_client
from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import AppSettings
from app.export.workflow import ExportExecutionResult, execute_export_with_overrides
from app.ga4.client import Ga4ApiClient
from app.gtm.client import GtmApiClient
from app.merchant.client import MerchantApiClient
from app.pagespeed.client import PageSpeedApiClient, PageSpeedApiError, PageSpeedClientConfig
from app.search_console.client import SearchConsoleApiClient
from app.utils.dates import resolve_date_range
from app.utils.retry import is_retryable_google_ads_exception, run_with_retry


@dataclass(slots=True)
class ServiceTestResult:
    status: str
    details: str


@dataclass(slots=True)
class ContextTestResult:
    context_key: str
    context_label: str
    services: dict[str, ServiceTestResult] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(item.status == "ok" for item in self.services.values())


@dataclass(slots=True)
class MultiContextRunResult:
    mode: str
    context_results: list[ContextExportBundle] = field(default_factory=list)
    context_errors: list[dict[str, Any]] = field(default_factory=list)
    cross_account_dir: str = ""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pagespeed_test_url(context: AccountContext) -> str:
    site = str(context.gsc_site_url or "").strip()
    if site.startswith("http://") or site.startswith("https://"):
        return site
    if context.source_domain:
        return f"https://{context.source_domain}/"
    return ""


def _google_ads_context_test(customer_id: str, env_config: GoogleAdsEnvConfig) -> ServiceTestResult:
    try:
        client = build_google_ads_client(env_config=env_config)
        google_ads_service = client.get_service("GoogleAdsService")
        def _action():
            stream = google_ads_service.search_stream(
                customer_id=customer_id,
                query="SELECT customer.id, customer.descriptive_name FROM customer LIMIT 1",
            )
            for batch in stream:
                if batch.results:
                    return batch.results[0]
            return None

        first_row = run_with_retry(_action, should_retry=is_retryable_google_ads_exception)
        if first_row is None:
            return ServiceTestResult(status="problem", details="Google Ads customer nevratil zadna data.")
        return ServiceTestResult(
            status="ok",
            details=f"Customer {customer_id} je dostupny.",
        )
    except Exception as exc:
        return ServiceTestResult(status="problem", details=str(exc))


def test_account_context(
    *,
    context: AccountContext,
    base_env_config: GoogleAdsEnvConfig,
) -> ContextTestResult:
    env_config = resolve_context_env_config(base_env_config, context)
    result = ContextTestResult(context_key=context.key, context_label=context.label)
    result.services["google_ads"] = _google_ads_context_test(context.google_ads_customer_id, env_config)

    if context.ga4_property_id:
        ga4_result = Ga4ApiClient.from_env_config(env_config).test_connection()
        result.services["ga4"] = ServiceTestResult(
            status="ok" if ga4_result.ok else "problem",
            details=ga4_result.message,
        )
    else:
        result.services["ga4"] = ServiceTestResult(status="problem", details="GA4 property neni nastavena.")

    if context.gsc_site_url:
        gsc_result = SearchConsoleApiClient.from_env_config(env_config).test_connection()
        result.services["gsc"] = ServiceTestResult(
            status="ok" if gsc_result.ok else "problem",
            details=gsc_result.message,
        )
    else:
        result.services["gsc"] = ServiceTestResult(status="problem", details="GSC property neni nastavena.")

    if context.merchant_account_id:
        merchant_result = MerchantApiClient.from_env_config(env_config).test_connection(context.merchant_account_id)
        result.services["merchant"] = ServiceTestResult(
            status="ok" if merchant_result.ok else "problem",
            details=merchant_result.message,
        )
    else:
        result.services["merchant"] = ServiceTestResult(status="problem", details="Merchant ucet neni nastaven.")

    if context.gtm_account_id and context.gtm_container_id:
        gtm_result = GtmApiClient.from_env_config(env_config).test_connection()
        result.services["gtm"] = ServiceTestResult(
            status="ok" if gtm_result.ok else "problem",
            details=gtm_result.message,
        )
    else:
        result.services["gtm"] = ServiceTestResult(status="problem", details="GTM account nebo container neni nastaven.")

    pagespeed_url = _pagespeed_test_url(context)
    if env_config.pagespeed_enabled and pagespeed_url:
        try:
            client = PageSpeedApiClient(
                PageSpeedClientConfig(
                    api_key=env_config.pagespeed_api_key,
                    enabled=env_config.pagespeed_enabled,
                )
            )
            client.run_pagespeed(url=pagespeed_url, strategy="mobile")
            result.services["pagespeed"] = ServiceTestResult(
                status="ok",
                details=f"PageSpeed otestoval URL {pagespeed_url}.",
            )
        except PageSpeedApiError as exc:
            result.services["pagespeed"] = ServiceTestResult(status="problem", details=exc.message)
    else:
        result.services["pagespeed"] = ServiceTestResult(
            status="problem",
            details="PageSpeed neni zapnuty nebo neni z ceho odvodit testovaci URL.",
        )

    return result


def run_context_export(
    *,
    project_root: Path,
    settings: AppSettings,
    config_path: Path,
    base_env_config: GoogleAdsEnvConfig,
    context: AccountContext,
    export_parent_dir_override: Path | None = None,
    export_base_name_override: str | None = None,
    export_mode: str = "selected_context",
) -> ExportExecutionResult:
    context_settings = resolve_context_settings(settings, context)
    context_env_config = resolve_context_env_config(base_env_config, context)
    return execute_export_with_overrides(
        settings=context_settings,
        project_root=project_root,
        config_path=config_path,
        env_config_override=context_env_config,
        export_base_name_override=export_base_name_override,
        export_parent_dir_override=export_parent_dir_override,
        context_metadata={
            "context_key": context.key,
            "context_label": context.label,
            "source_domain": context.source_domain,
            "export_mode": export_mode,
        },
    )


def run_multi_context_export(
    *,
    project_root: Path,
    settings: AppSettings,
    config_path: Path,
    base_env_config: GoogleAdsEnvConfig,
    contexts: list[AccountContext],
) -> MultiContextRunResult:
    result = MultiContextRunResult(mode="all_enabled_contexts")
    resolved_settings = settings
    run_date = resolve_date_range(resolved_settings.date_range).export_date
    parent_dir = project_root / resolved_settings.output.base_dir / f"{run_date.isoformat()}_multi"
    parent_dir.mkdir(parents=True, exist_ok=True)

    enabled_contexts = [context for context in contexts if context.enabled]
    for context in enabled_contexts:
        try:
            export_result = run_context_export(
                project_root=project_root,
                settings=resolved_settings,
                config_path=config_path,
                base_env_config=base_env_config,
                context=context,
                export_parent_dir_override=parent_dir,
                export_base_name_override=f"{context.key}_{context.google_ads_customer_id}",
                export_mode="all_enabled_contexts",
            )
            result.context_results.append(ContextExportBundle(context=context, result=export_result))
        except Exception as exc:
            result.context_errors.append(
                {
                    "context_key": context.key,
                    "context_label": context.label,
                    "message": str(exc),
                    "timestamp": _timestamp(),
                }
            )

    if result.context_results:
        result.cross_account_dir = str(
            write_cross_account_exports(
                parent_dir=parent_dir,
                bundles=result.context_results,
            )
        )
    return result
