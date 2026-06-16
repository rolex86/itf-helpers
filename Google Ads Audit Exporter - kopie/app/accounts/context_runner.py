from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.accounts.context_config import (
    AccountContext,
    load_account_contexts,
    resolve_context_env_config,
    resolve_context_settings,
)
from app.accounts.domain_filter import (
    normalize_domain,
    source_domains_display,
    url_matches_source_domains,
)
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


LOGGER = logging.getLogger("google_ads_audit_exporter")


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


def _source_domains(context: AccountContext) -> list[str]:
    return list(context.effective_source_domains)


def _domains_match_context(context: AccountContext, raw_domain_or_url: object) -> bool:
    domains = _source_domains(context)
    if not domains:
        return True
    value = str(raw_domain_or_url or "").strip()
    if not value:
        return False
    if "://" in value:
        return url_matches_source_domains(value, domains)
    return normalize_domain(value) in set(domains)


def _warning_result(details: str) -> ServiceTestResult:
    return ServiceTestResult(status="problem", details=details)


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
                query="""
                    SELECT
                      customer.id,
                      customer.descriptive_name,
                      customer.manager,
                      customer.currency_code,
                      customer.time_zone
                    FROM customer
                    LIMIT 1
                """,
            )
            for batch in stream:
                if batch.results:
                    return batch.results[0]
            return None

        first_row = run_with_retry(_action, should_retry=is_retryable_google_ads_exception)
        if first_row is None:
            return ServiceTestResult(status="problem", details="Google Ads customer nevratil zadna data.")
        customer = getattr(first_row, "customer", None)
        if bool(getattr(customer, "manager", False)):
            return _warning_result(
                "Vybrany Google Ads customer ID je manager/MCC ucet. Vykonove metriky z manager uctu nelze exportovat. "
                "Jako Google Ads customer ID vyberte klientsky ucet. Manager/MCC ID pouzijte pouze jako login customer ID."
            )
        return ServiceTestResult(
            status="ok",
            details=f"Customer {customer_id} je dostupny jako klientsky ucet.",
        )
    except Exception as exc:
        return ServiceTestResult(status="problem", details=str(exc))


def _report_progress(
    progress_callback: Callable[[str, str, str], None] | None,
    service_key: str,
    phase: str,
    details: str,
) -> None:
    if progress_callback is not None:
        progress_callback(service_key, phase, details)


def _ga4_context_test(context: AccountContext, env_config: GoogleAdsEnvConfig) -> ServiceTestResult:
    result = Ga4ApiClient.from_env_config(env_config).test_connection()
    if not result.ok:
        return ServiceTestResult(status="problem", details=result.message)

    property_name = ""
    if result.selected_property:
        property_name = str(result.selected_property.get("property_name") or "")
    details = [result.message]
    if property_name:
        details.append(f"Property: {property_name}")

    try:
        streams = Ga4ApiClient.from_env_config(env_config).list_property_data_streams(context.ga4_property_id)
    except Exception:
        streams = []

    stream_urls = [str(stream.get("default_uri") or "").strip() for stream in streams if stream.get("default_uri")]
    if stream_urls:
        details.append("Stream URL: " + ", ".join(stream_urls))
        if not any(_domains_match_context(context, stream_url) for stream_url in stream_urls):
            return _warning_result(
                "GA4 property stream URL neodpovida source_domains kontextu. Overte, zda je vybrana spravna GA4 property."
            )
    elif property_name and _source_domains(context):
        normalized_domains = _source_domains(context)
        if not any(domain in property_name.lower() for domain in normalized_domains):
            details.append("Varovani: property display name neobsahuje zadnou source domain.")

    return ServiceTestResult(status="ok", details=" | ".join(details))


def _gsc_context_test(context: AccountContext, env_config: GoogleAdsEnvConfig) -> ServiceTestResult:
    result = SearchConsoleApiClient.from_env_config(env_config).test_connection()
    if not result.ok:
        return ServiceTestResult(status="problem", details=result.message)
    selected = result.selected_property or {}
    site_url = str(selected.get("site_url") or context.gsc_site_url or "")
    if _source_domains(context):
        if site_url.startswith("sc-domain:"):
            domain = site_url.replace("sc-domain:", "", 1).strip()
            if not _domains_match_context(context, domain):
                return _warning_result("GSC property neodpovida source_domains kontextu.")
        elif site_url and not _domains_match_context(context, site_url):
            return _warning_result("GSC property neodpovida source_domains kontextu.")
    return ServiceTestResult(status="ok", details=result.message)


def _merchant_context_test(context: AccountContext, env_config: GoogleAdsEnvConfig) -> ServiceTestResult:
    result = MerchantApiClient.from_env_config(env_config).test_connection(context.merchant_account_id)
    if result.ok:
        return ServiceTestResult(status="ok", details=result.message)
    message = result.message
    if "This method can only be accessed by subaccounts and standalone accounts." in message:
        message = (
            "Zadany Merchant account ID je pravdepodobne parent/advanced ucet. "
            "Pro produktove reporty vyberte konkretni subucet nebo standalone Merchant ucet."
        )
    return ServiceTestResult(status="problem", details=message)


def _gtm_context_test(
    context: AccountContext,
    env_config: GoogleAdsEnvConfig,
    *,
    sibling_contexts: list[AccountContext] | None = None,
) -> ServiceTestResult:
    result = GtmApiClient.from_env_config(env_config).test_connection()
    if not result.ok:
        return ServiceTestResult(status="problem", details=result.message)

    details = [result.message]
    selected_container = result.selected_container or {}
    domain_name = str(selected_container.get("domain_name") or "")
    if domain_name:
        details.append(f"Container domain: {domain_name}")
        if _source_domains(context) and not _domains_match_context(context, domain_name):
            return _warning_result("GTM container domain neodpovida source_domains kontextu.")

    if sibling_contexts:
        shared_with = [
            sibling.key
            for sibling in sibling_contexts
            if sibling.key != context.key
            and sibling.gtm_container_id
            and sibling.gtm_container_id == context.gtm_container_id
        ]
        if shared_with:
            details.append(
                "Tento GTM container je pouzity ve vice kontextech. Pokud weby opravdu sdili jeden GTM kontejner, je to v poradku. "
                f"Jinak zkontrolujte mapovani. Sdileno s: {', '.join(shared_with)}"
            )

    return ServiceTestResult(status="ok", details=" | ".join(details))


def _run_service_test(
    *,
    service_key: str,
    start_message: str,
    test_fn: Callable[[], ServiceTestResult],
    progress_callback: Callable[[str, str, str], None] | None,
) -> ServiceTestResult:
    LOGGER.info("Context test service start service=%s", service_key)
    _report_progress(progress_callback, service_key, "running", start_message)
    result = test_fn()
    LOGGER.info(
        "Context test service finished service=%s status=%s",
        service_key,
        result.status,
    )
    _report_progress(progress_callback, service_key, "finished", result.details)
    return result


def test_account_context(
    *,
    context: AccountContext,
    base_env_config: GoogleAdsEnvConfig,
    sibling_contexts: list[AccountContext] | None = None,
    progress_callback: Callable[[str, str, str], None] | None = None,
) -> ContextTestResult:
    env_config = resolve_context_env_config(base_env_config, context)
    result = ContextTestResult(context_key=context.key, context_label=context.label)

    if not _source_domains(context):
        result.services["source_domains"] = ServiceTestResult(
            status="problem",
            details="Kontext nema nastavene source_domains. Google Ads / PageSpeed data nemusi byt domenove oddelena.",
        )
        _report_progress(progress_callback, "source_domains", "finished", result.services["source_domains"].details)
    else:
        result.services["source_domains"] = ServiceTestResult(
            status="ok",
            details=f"Source domains: {source_domains_display(_source_domains(context))}",
        )
        _report_progress(progress_callback, "source_domains", "finished", result.services["source_domains"].details)

    result.services["google_ads"] = _run_service_test(
        service_key="google_ads",
        start_message="Overuji dostupnost Google Ads customer.",
        test_fn=lambda: _google_ads_context_test(context.google_ads_customer_id, env_config),
        progress_callback=progress_callback,
    )

    if context.ga4_property_id:
        result.services["ga4"] = _run_service_test(
            service_key="ga4",
            start_message="Overuji pristup ke GA4 property.",
            test_fn=lambda: _ga4_context_test(context, env_config),
            progress_callback=progress_callback,
        )
    else:
        result.services["ga4"] = ServiceTestResult(status="problem", details="GA4 property neni nastavena.")
        _report_progress(progress_callback, "ga4", "finished", result.services["ga4"].details)

    if context.gsc_site_url:
        result.services["gsc"] = _run_service_test(
            service_key="gsc",
            start_message="Overuji pristup k Search Console property.",
            test_fn=lambda: _gsc_context_test(context, env_config),
            progress_callback=progress_callback,
        )
    else:
        result.services["gsc"] = ServiceTestResult(status="problem", details="GSC property neni nastavena.")
        _report_progress(progress_callback, "gsc", "finished", result.services["gsc"].details)

    if context.merchant_account_id:
        result.services["merchant"] = _run_service_test(
            service_key="merchant",
            start_message="Overuji pristup k Merchant Center uctu.",
            test_fn=lambda: _merchant_context_test(context, env_config),
            progress_callback=progress_callback,
        )
    else:
        result.services["merchant"] = ServiceTestResult(status="problem", details="Merchant účet není nastaven.")
        _report_progress(progress_callback, "merchant", "finished", result.services["merchant"].details)

    if context.gtm_account_id and context.gtm_container_id:
        result.services["gtm"] = _run_service_test(
            service_key="gtm",
            start_message="Overuji pristup ke GTM containeru.",
            test_fn=lambda: _gtm_context_test(
                context,
                env_config,
                sibling_contexts=sibling_contexts,
            ),
            progress_callback=progress_callback,
        )
    else:
        result.services["gtm"] = ServiceTestResult(status="problem", details="GTM account nebo container není nastaven.")
        _report_progress(progress_callback, "gtm", "finished", result.services["gtm"].details)

    pagespeed_url = _pagespeed_test_url(context)
    if env_config.pagespeed_enabled and pagespeed_url:
        def _pagespeed_test() -> ServiceTestResult:
            try:
                client = PageSpeedApiClient(
                    PageSpeedClientConfig(
                        api_key=env_config.pagespeed_api_key,
                        enabled=env_config.pagespeed_enabled,
                    )
                )
                client.run_pagespeed(url=pagespeed_url, strategy="mobile")
                return ServiceTestResult(
                    status="ok",
                    details=f"PageSpeed otestoval URL {pagespeed_url}.",
                )
            except PageSpeedApiError as exc:
                return ServiceTestResult(status="problem", details=exc.message)

        result.services["pagespeed"] = _run_service_test(
            service_key="pagespeed",
            start_message=f"Overuji PageSpeed na URL {pagespeed_url}.",
            test_fn=_pagespeed_test,
            progress_callback=progress_callback,
        )
    else:
        result.services["pagespeed"] = ServiceTestResult(
            status="problem",
            details="PageSpeed není zapnutý nebo není z čeho odvodit testovací URL.",
        )
        _report_progress(progress_callback, "pagespeed", "finished", result.services["pagespeed"].details)

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
            "source_domains": list(context.effective_source_domains),
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
