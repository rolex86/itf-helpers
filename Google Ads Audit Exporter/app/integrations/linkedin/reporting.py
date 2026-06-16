from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers, records_to_frame
from app.integrations.linkedin.restli import date_range_param, restli_list, sponsored_account_urn


PRESET_TO_DAYS = {
    "last_30_days": 30,
    "last_90_days": 90,
    "last_180_days": 180,
    "last_365_days": 365,
}


@dataclass(slots=True)
class LinkedInDateRange:
    start: date
    end: date


def resolve_date_range(
    *,
    preset: str = "last_90_days",
    date_from: str = "",
    date_to: str = "",
    default_days: int = 90,
) -> LinkedInDateRange:
    if date_from and date_to:
        return LinkedInDateRange(
            start=datetime.strptime(date_from, "%Y-%m-%d").date(),
            end=datetime.strptime(date_to, "%Y-%m-%d").date(),
        )
    days = PRESET_TO_DAYS.get(str(preset or "").lower(), default_days)
    end = datetime.utcnow().date()
    start = end - timedelta(days=max(1, days) - 1)
    return LinkedInDateRange(start=start, end=end)


def _account_list_param(account_ids: list[str]) -> str:
    return restli_list([sponsored_account_urn(account_id) for account_id in account_ids])


def _analytics_rows(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    pivot: str,
    time_granularity: str,
    date_range: LinkedInDateRange,
) -> list[dict[str, Any]]:
    params = {
        "q": "analytics",
        "accounts": _account_list_param(account_ids),
        "pivot": pivot,
        "timeGranularity": time_granularity,
        "dateRange": date_range_param(date_range.start, date_range.end),
        "count": 100,
    }
    rows = list(client.paginate("adAnalytics", params=params, count=100))
    return [normalize_entity_identifiers(row) for row in rows]


def build_reporting_exports(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    date_range: LinkedInDateRange,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    warnings: list[str] = []
    raw_payloads: dict[str, list[dict[str, Any]]] = {}
    datasets: dict[str, Any] = {}
    report_specs = {
        "insights_account_daily": ("ACCOUNT", "DAILY"),
        "insights_campaign_daily": ("CAMPAIGN", "DAILY"),
        "insights_creative_daily": ("CREATIVE", "DAILY"),
        "insights_campaign_all": ("CAMPAIGN", "ALL"),
        "insights_creative_all": ("CREATIVE", "ALL"),
    }
    for dataset_key, (pivot, granularity) in report_specs.items():
        try:
            rows = _analytics_rows(
                client,
                account_ids=account_ids,
                pivot=pivot,
                time_granularity=granularity,
                date_range=date_range,
            )
            raw_payloads[f"{dataset_key}_raw"] = rows
            datasets[dataset_key] = records_to_frame(rows)
        except Exception as exc:
            warnings.append(f"{dataset_key} nebylo možné načíst: {exc}")
            raw_payloads[f"{dataset_key}_raw"] = []
            datasets[dataset_key] = records_to_frame([])

    for dataset_key, pivot in (
        ("professional_demographics_campaign", "CAMPAIGN"),
        ("professional_demographics_creative", "CREATIVE"),
    ):
        try:
            rows = list(
                client.paginate(
                    "adAnalytics",
                    params={
                        "q": "analytics",
                        "accounts": _account_list_param(account_ids),
                        "pivot": pivot,
                        "timeGranularity": "ALL",
                        "dateRange": date_range_param(date_range.start, date_range.end),
                        "fields": "companySize,jobFunction,seniority,industry",
                        "count": 100,
                    },
                )
            )
            rows = [normalize_entity_identifiers(row) for row in rows]
            raw_payloads[f"{dataset_key}_raw"] = rows
            datasets[dataset_key] = records_to_frame(rows)
        except Exception as exc:
            warnings.append(f"{dataset_key} není dostupné nebo vrátilo prázdná data: {exc}")
            raw_payloads[f"{dataset_key}_raw"] = []
            datasets[dataset_key] = records_to_frame([])

    return datasets, raw_payloads, warnings

