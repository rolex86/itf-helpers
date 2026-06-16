from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.utils.dates import ResolvedDateRange, parse_iso_date


DEFAULT_REPORTS = {
    "account": True,
    "account_diagnostics": True,
    "linked_accounts": True,
    "campaigns": True,
    "campaigns_monthly": True,
    "ad_groups": True,
    "keywords": True,
    "search_terms": True,
    "ads": True,
    "assets": True,
    "devices": True,
    "locations": True,
    "landing_pages": True,
    "shopping_products": True,
    "shopping_products_summary": True,
    "google_ads_recommendations": True,
    "merchant_products": False,
    "merchant_product_issues": False,
    "merchant_product_status_summary": False,
    "product_optimization": False,
    "product_feed_issues_with_spend": False,
    "product_custom_label_performance": False,
    "ga4_landing_pages": False,
    "landing_page_diagnostics": False,
    "ga4_ecommerce_funnel": False,
    "gsc_queries": False,
    "gsc_pages": False,
    "gsc_page_query": False,
    "gsc_opportunities": False,
    "pagespeed_landing_pages": False,
    "gtm_tags": False,
    "gtm_triggers": False,
    "gtm_variables": False,
    "gtm_versions": False,
    "measurement_diagnostics": False,
    "conversion_actions": True,
    "pmax_campaigns": True,
    "pmax_asset_groups": True,
    "change_history": True,
}


@dataclass(slots=True)
class DateRangeConfig:
    preset: str | None = "LAST_90_DAYS"
    date_from: date | None = None
    date_to: date | None = None


@dataclass(slots=True)
class OutputConfig:
    base_dir: str = "exports"
    xlsx_filename: str = "audit_export.xlsx"
    include_raw_csv: bool = True
    include_metadata: bool = True


@dataclass(slots=True)
class FlagsConfig:
    min_spend_micros: int = 100_000_000
    min_clicks: int = 50
    target_cpa_micros: int | None = None
    target_roas: float | None = None
    low_ctr_threshold: float = 0.01


@dataclass(slots=True)
class CostPolicyConfig:
    free_only: bool = True
    forbid_paid_cloud_resources: bool = True
    allow_local_storage_only: bool = True


@dataclass(slots=True)
class PageSpeedConfig:
    enabled: bool = True
    max_urls_per_export: int = 50
    source: str = "top_landing_pages_by_cost"
    strategies: list[str] = field(default_factory=lambda: ["mobile", "desktop"])
    cache_days: int = 30


@dataclass(slots=True)
class AppSettings:
    customer_id: str
    date_range: DateRangeConfig = field(default_factory=DateRangeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    reports: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_REPORTS))
    flags: FlagsConfig = field(default_factory=FlagsConfig)
    cost_policy: CostPolicyConfig = field(default_factory=CostPolicyConfig)
    pagespeed: PageSpeedConfig = field(default_factory=PageSpeedConfig)

    def to_metadata(self, resolved_range: ResolvedDateRange, config_path: Path) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "date_range": {
                "requested_preset": self.date_range.preset,
                "requested_date_from": self.date_range.date_from.isoformat()
                if self.date_range.date_from
                else None,
                "requested_date_to": self.date_range.date_to.isoformat()
                if self.date_range.date_to
                else None,
                "resolved_date_from": resolved_range.date_from.isoformat(),
                "resolved_date_to": resolved_range.date_to.isoformat(),
                "label": resolved_range.label,
                "warnings": resolved_range.warnings,
            },
            "output": asdict(self.output),
            "reports": dict(self.reports),
            "flags": asdict(self.flags),
            "cost_policy": asdict(self.cost_policy),
            "pagespeed": asdict(self.pagespeed),
            "config_path": str(config_path),
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Create it from config.example.yaml."
        )
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Top-level config.yaml structure must be a mapping.")
    return data


def _normalize_customer_id(value: str) -> str:
    normalized = value.replace("-", "").strip()
    if not normalized.isdigit():
        raise ValueError("customer_id must contain only digits.")
    return normalized


def load_settings(
    config_path: Path,
    customer_id_override: str | None = None,
    preset_override: str | None = None,
    date_from_override: str | None = None,
    date_to_override: str | None = None,
) -> AppSettings:
    raw = _load_yaml(config_path)

    customer_id = _normalize_customer_id(
        customer_id_override or str(raw.get("customer_id", "")).strip()
    )

    raw_date_range = raw.get("date_range", {}) or {}
    raw_output = raw.get("output", {}) or {}
    raw_reports = raw.get("reports", {}) or {}
    raw_flags = raw.get("flags", {}) or {}
    raw_cost_policy = raw.get("cost_policy", {}) or {}
    raw_pagespeed = raw.get("pagespeed", {}) or {}

    date_range = DateRangeConfig(
        preset=(preset_override or raw_date_range.get("preset") or "LAST_90_DAYS"),
        date_from=parse_iso_date(date_from_override)
        if date_from_override
        else parse_iso_date(raw_date_range.get("date_from")),
        date_to=parse_iso_date(date_to_override)
        if date_to_override
        else parse_iso_date(raw_date_range.get("date_to")),
    )

    output = OutputConfig(
        base_dir=str(raw_output.get("base_dir", "exports")),
        xlsx_filename=str(raw_output.get("xlsx_filename", "audit_export.xlsx")),
        include_raw_csv=bool(raw_output.get("include_raw_csv", True)),
        include_metadata=bool(raw_output.get("include_metadata", True)),
    )

    reports = dict(DEFAULT_REPORTS)
    reports.update({key: bool(value) for key, value in raw_reports.items()})

    flags = FlagsConfig(
        min_spend_micros=int(raw_flags.get("min_spend_micros", 100_000_000)),
        min_clicks=int(raw_flags.get("min_clicks", 50)),
        target_cpa_micros=(
            int(raw_flags["target_cpa_micros"])
            if raw_flags.get("target_cpa_micros") is not None
            else None
        ),
        target_roas=(
            float(raw_flags["target_roas"]) if raw_flags.get("target_roas") is not None else None
        ),
        low_ctr_threshold=float(raw_flags.get("low_ctr_threshold", 0.01)),
    )

    cost_policy = CostPolicyConfig(
        free_only=bool(raw_cost_policy.get("free_only", True)),
        forbid_paid_cloud_resources=bool(
            raw_cost_policy.get("forbid_paid_cloud_resources", True)
        ),
        allow_local_storage_only=bool(raw_cost_policy.get("allow_local_storage_only", True)),
    )

    pagespeed = PageSpeedConfig(
        enabled=bool(raw_pagespeed.get("enabled", True)),
        max_urls_per_export=int(raw_pagespeed.get("max_urls_per_export", 50)),
        source=str(raw_pagespeed.get("source", "top_landing_pages_by_cost")),
        strategies=[
            str(value).strip().lower()
            for value in list(raw_pagespeed.get("strategies", ["mobile", "desktop"]))
            if str(value).strip()
        ]
        or ["mobile", "desktop"],
        cache_days=int(raw_pagespeed.get("cache_days", 30)),
    )

    return AppSettings(
        customer_id=customer_id,
        date_range=date_range,
        output=output,
        reports=reports,
        flags=flags,
        cost_policy=cost_policy,
        pagespeed=pagespeed,
    )
