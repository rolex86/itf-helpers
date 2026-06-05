from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from app.accounts.domain_filter import extract_domains_from_gsc_site_url, normalize_source_domains, source_domains_display
from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import AppSettings


@dataclass(slots=True)
class AccountContext:
    key: str
    label: str
    google_ads_customer_id: str
    google_ads_login_customer_id: str = ""
    merchant_account_id: str = ""
    ga4_property_id: str = ""
    gsc_site_url: str = ""
    gtm_account_id: str = ""
    gtm_container_id: str = ""
    pagespeed_enabled: bool = True
    enabled: bool = True
    source_domains: list[str] = field(default_factory=list)

    @property
    def source_domain(self) -> str:
        if self.source_domains:
            return self.source_domains[0]
        suggested = self.suggested_source_domains
        return suggested[0] if suggested else ""

    @property
    def effective_source_domains(self) -> list[str]:
        return list(self.source_domains or self.suggested_source_domains)

    @property
    def source_domains_text(self) -> str:
        return "\n".join(self.source_domains)

    @property
    def source_domains_display(self) -> str:
        return source_domains_display(self.effective_source_domains)

    @property
    def suggested_source_domains(self) -> list[str]:
        return extract_domains_from_gsc_site_url(self.gsc_site_url)


DEFAULT_ACCOUNTS_PAYLOAD = {
    "merchant_parent_account_id": "",
    "account_contexts": [],
}


def _normalize_id(value: object) -> str:
    return str(value or "").replace("-", "").strip()


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def context_from_mapping(raw: dict[str, Any]) -> AccountContext:
    return AccountContext(
        key=_normalize_text(raw.get("key")),
        label=_normalize_text(raw.get("label")),
        google_ads_customer_id=_normalize_id(raw.get("google_ads_customer_id")),
        google_ads_login_customer_id=_normalize_id(raw.get("google_ads_login_customer_id")),
        merchant_account_id=_normalize_id(raw.get("merchant_account_id")),
        ga4_property_id=_normalize_id(raw.get("ga4_property_id")),
        gsc_site_url=_normalize_text(raw.get("gsc_site_url")),
        gtm_account_id=_normalize_id(raw.get("gtm_account_id")),
        gtm_container_id=_normalize_id(raw.get("gtm_container_id")),
        pagespeed_enabled=bool(raw.get("pagespeed_enabled", True)),
        enabled=bool(raw.get("enabled", True)),
        source_domains=normalize_source_domains(raw.get("source_domains")),
    )


def context_to_mapping(context: AccountContext) -> dict[str, Any]:
    payload = asdict(context)
    payload["source_domains"] = list(context.source_domains)
    return payload


def load_accounts_config_payload(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return dict(DEFAULT_ACCOUNTS_PAYLOAD)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Top-level config.accounts.yaml structure must be a mapping.")
    payload = dict(DEFAULT_ACCOUNTS_PAYLOAD)
    payload["merchant_parent_account_id"] = _normalize_id(raw.get("merchant_parent_account_id"))
    payload["account_contexts"] = raw.get("account_contexts", []) or []
    return payload


def load_merchant_parent_account_id(config_path: Path) -> str:
    payload = load_accounts_config_payload(config_path)
    return _normalize_id(payload.get("merchant_parent_account_id"))


def load_account_contexts(config_path: Path) -> list[AccountContext]:
    payload = load_accounts_config_payload(config_path)
    contexts_raw = payload.get("account_contexts", []) or []
    if not isinstance(contexts_raw, list):
        raise ValueError("'account_contexts' in config.accounts.yaml must be a list.")
    contexts: list[AccountContext] = []
    for item in contexts_raw:
        if not isinstance(item, dict):
            continue
        context = context_from_mapping(item)
        if not context.key:
            continue
        contexts.append(context)
    return contexts


def save_account_contexts(
    config_path: Path,
    contexts: list[AccountContext],
    *,
    merchant_parent_account_id: str = "",
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "merchant_parent_account_id": _normalize_id(merchant_parent_account_id),
        "account_contexts": [context_to_mapping(context) for context in contexts],
    }
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)


def has_multi_account_config(config_path: Path) -> bool:
    return config_path.exists() and bool(load_account_contexts(config_path))


def resolve_context_env_config(base_env: GoogleAdsEnvConfig, context: AccountContext) -> GoogleAdsEnvConfig:
    return replace(
        base_env,
        login_customer_id=context.google_ads_login_customer_id or base_env.login_customer_id,
        merchant_account_id=context.merchant_account_id or base_env.merchant_account_id,
        merchant_enabled=bool(context.merchant_account_id) and base_env.merchant_enabled,
        ga4_property_id=context.ga4_property_id or base_env.ga4_property_id,
        ga4_enabled=bool(context.ga4_property_id) and base_env.ga4_enabled,
        gsc_site_url=context.gsc_site_url or base_env.gsc_site_url,
        gsc_enabled=bool(context.gsc_site_url) and base_env.gsc_enabled,
        gtm_account_id=context.gtm_account_id or base_env.gtm_account_id,
        gtm_container_id=context.gtm_container_id or base_env.gtm_container_id,
        gtm_enabled=bool(context.gtm_account_id and context.gtm_container_id) and base_env.gtm_enabled,
        pagespeed_enabled=bool(context.pagespeed_enabled) and base_env.pagespeed_enabled,
    )


def resolve_context_settings(base_settings: AppSettings, context: AccountContext) -> AppSettings:
    context_settings = replace(base_settings)
    context_settings.customer_id = context.google_ads_customer_id
    context_settings.pagespeed = replace(
        base_settings.pagespeed,
        enabled=bool(base_settings.pagespeed.enabled and context.pagespeed_enabled),
    )
    return context_settings


def contexts_to_payload(contexts: list[AccountContext]) -> dict[str, Any]:
    return {
        "account_contexts": [context_to_mapping(context) for context in contexts],
    }
