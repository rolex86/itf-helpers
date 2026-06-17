from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers, records_to_frame, urn_to_id
from app.integrations.linkedin.restli import campaign_urn, creative_urn, date_range_param, sponsored_account_urn


PRESET_TO_DAYS = {
    "last_30_days": 30,
    "last_90_days": 90,
    "last_180_days": 180,
    "last_365_days": 365,
}

MAX_ANALYTICS_ROWS = 15000
DAILY_WINDOW_DAYS = 31

PERFORMANCE_FIELDS = (
    "dateRange",
    "pivotValues",
    "impressions",
    "clicks",
    "landingPageClicks",
    "costInLocalCurrency",
    "costInUsd",
    "externalWebsiteConversions",
    "externalWebsitePostClickConversions",
    "externalWebsitePostViewConversions",
    "oneClickLeadFormOpens",
    "oneClickLeads",
    "opens",
    "sends",
    "totalEngagements",
    "likes",
    "comments",
    "shares",
    "videoViews",
    "approximateMemberReach",
)

PERFORMANCE_FIELDS_FALLBACK = (
    "dateRange",
    "pivotValues",
    "impressions",
    "clicks",
    "landingPageClicks",
    "costInLocalCurrency",
    "externalWebsiteConversions",
    "oneClickLeads",
    "totalEngagements",
)

DEMOGRAPHIC_FIELDS = (
    "dateRange",
    "pivotValues",
    "impressions",
    "clicks",
)

MEMBER_DEMOGRAPHIC_PIVOTS = (
    "MEMBER_COMPANY_SIZE",
    "MEMBER_INDUSTRY",
    "MEMBER_SENIORITY",
    "MEMBER_JOB_FUNCTION",
    "MEMBER_JOB_TITLE",
    "MEMBER_COUNTRY_V2",
    "MEMBER_REGION_V2",
)


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


def _date_windows(date_range: LinkedInDateRange, *, window_days: int) -> list[LinkedInDateRange]:
    windows: list[LinkedInDateRange] = []
    current_start = date_range.start

    while current_start <= date_range.end:
        current_end = min(date_range.end, current_start + timedelta(days=window_days - 1))
        windows.append(LinkedInDateRange(start=current_start, end=current_end))
        current_start = current_end + timedelta(days=1)

    return windows


def _restli_list(values: list[str] | tuple[str, ...]) -> str:
    cleaned: list[str] = []

    for value in values or []:
        text = str(value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)

    return "List(" + ",".join(cleaned) + ")"


def _fields_param(fields: tuple[str, ...] | list[str]) -> str:
    cleaned: list[str] = []

    for field in fields or []:
        text = str(field or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)

    return ",".join(cleaned)


def _to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_urn_id(value: Any) -> str:
    return urn_to_id(value)


def _linkedin_date_to_iso(value: Any) -> str:
    if isinstance(value, dict):
        year = value.get("year")
        month = value.get("month")
        day = value.get("day")

        if year and month and day:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    return ""


def _apply_primary_pivot_identifiers(
    normalized: dict[str, Any],
    *,
    requested_pivot: str,
    pivot_values: list[Any],
    account_id: str,
    account_urn: str,
) -> None:
    normalized["account_id"] = str(account_id)
    normalized["account_urn"] = account_urn

    if not pivot_values:
        return

    first_pivot = str(pivot_values[0] or "").strip()
    first_pivot_id = urn_to_id(first_pivot)

    if requested_pivot == "ACCOUNT":
        normalized["account_id"] = first_pivot_id or str(account_id)
        normalized["account_urn"] = first_pivot or account_urn

    elif requested_pivot == "CAMPAIGN":
        normalized["campaign_id"] = first_pivot_id
        normalized["campaign_urn"] = first_pivot or campaign_urn(first_pivot_id)

    elif requested_pivot == "CREATIVE":
        normalized["creative_id"] = first_pivot_id
        normalized["creative_urn"] = first_pivot or creative_urn(first_pivot_id)

    elif requested_pivot.startswith("CAMPAIGN,"):
        normalized["campaign_id"] = first_pivot_id
        normalized["campaign_urn"] = first_pivot or campaign_urn(first_pivot_id)

    elif requested_pivot.startswith("CREATIVE,"):
        normalized["creative_id"] = first_pivot_id
        normalized["creative_urn"] = first_pivot or creative_urn(first_pivot_id)

    elif requested_pivot.startswith("MEMBER_"):
        normalized["member_demographic_id"] = first_pivot_id
        normalized["member_demographic_urn"] = first_pivot


def _flatten_ad_analytics_row(
    row: dict[str, Any],
    *,
    account_id: str,
    account_urn: str,
    requested_pivot: str,
    time_granularity: str,
) -> dict[str, Any]:
    normalized = normalize_entity_identifiers(dict(row))

    normalized["account_id"] = str(account_id)
    normalized["account_urn"] = account_urn
    normalized["requested_pivot"] = requested_pivot
    normalized["time_granularity"] = time_granularity

    date_range = row.get("dateRange") or {}
    normalized["date_start"] = _linkedin_date_to_iso(date_range.get("start") if isinstance(date_range, dict) else {})
    normalized["date_end"] = _linkedin_date_to_iso(date_range.get("end") if isinstance(date_range, dict) else {})

    pivot_values = row.get("pivotValues") or []
    if not isinstance(pivot_values, list):
        pivot_values = []

    for index, pivot_value in enumerate(pivot_values, start=1):
        normalized[f"pivot_value_{index}"] = pivot_value
        normalized[f"pivot_value_{index}_id"] = _extract_urn_id(pivot_value)

    _apply_primary_pivot_identifiers(
        normalized,
        requested_pivot=requested_pivot,
        pivot_values=pivot_values,
        account_id=account_id,
        account_urn=account_urn,
    )

    impressions = _to_number(row.get("impressions"))
    clicks = _to_number(row.get("clicks"))
    spend = _to_number(row.get("costInLocalCurrency"))
    website_conversions = _to_number(row.get("externalWebsiteConversions")) or 0.0
    post_click_conversions = _to_number(row.get("externalWebsitePostClickConversions")) or 0.0
    post_view_conversions = _to_number(row.get("externalWebsitePostViewConversions")) or 0.0
    one_click_leads = _to_number(row.get("oneClickLeads")) or 0.0

    leads_or_conversions = max(
        website_conversions,
        post_click_conversions + post_view_conversions,
    ) + one_click_leads

    if impressions and impressions > 0:
        normalized["ctr"] = (clicks or 0.0) / impressions
        if spend is not None:
            normalized["cpm"] = spend / impressions * 1000

    if clicks and clicks > 0 and spend is not None:
        normalized["cpc"] = spend / clicks

    if leads_or_conversions > 0 and spend is not None:
        normalized["cpl_or_cpa"] = spend / leads_or_conversions

    normalized["leads_or_conversions"] = leads_or_conversions

    return normalized


def _call_ad_analytics(
    client: LinkedInRestClient,
    *,
    params: dict[str, Any],
    fields: tuple[str, ...],
    fallback_fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    request_params = dict(params)
    request_params["fields"] = _fields_param(fields)

    try:
        payload = client.get("adAnalytics", params=request_params)
        return payload if isinstance(payload, dict) else {"elements": []}
    except Exception:
        if fallback_fields:
            fallback_params = dict(params)
            fallback_params["fields"] = _fields_param(fallback_fields)

            try:
                payload = client.get("adAnalytics", params=fallback_params)
                return payload if isinstance(payload, dict) else {"elements": []}
            except Exception:
                pass

        payload = client.get("adAnalytics", params=dict(params))
        return payload if isinstance(payload, dict) else {"elements": []}


def _analytics_request(
    client: LinkedInRestClient,
    *,
    account_id: str,
    pivot: str,
    time_granularity: str,
    date_range: LinkedInDateRange,
    fields: tuple[str, ...] = PERFORMANCE_FIELDS,
    fallback_fields: tuple[str, ...] | None = PERFORMANCE_FIELDS_FALLBACK,
) -> list[dict[str, Any]]:
    account_urn = sponsored_account_urn(account_id)
    payload = _call_ad_analytics(
        client,
        params={
            "q": "analytics",
            "accounts": _restli_list([account_urn]),
            "pivot": pivot,
            "timeGranularity": time_granularity,
            "dateRange": date_range_param(date_range.start, date_range.end),
        },
        fields=fields,
        fallback_fields=fallback_fields,
    )

    rows = payload.get("elements", []) or []
    if not isinstance(rows, list):
        return []

    return [
        _flatten_ad_analytics_row(
            row,
            account_id=account_id,
            account_urn=account_urn,
            requested_pivot=pivot,
            time_granularity=time_granularity,
        )
        for row in rows
        if isinstance(row, dict)
    ]


def _statistics_request(
    client: LinkedInRestClient,
    *,
    account_id: str,
    pivots: list[str],
    time_granularity: str,
    date_range: LinkedInDateRange,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    account_urn = sponsored_account_urn(account_id)
    requested_pivot = ",".join(pivots)

    payload = _call_ad_analytics(
        client,
        params={
            "q": "statistics",
            "accounts": _restli_list([account_urn]),
            "pivots": _restli_list(pivots),
            "timeGranularity": time_granularity,
            "dateRange": date_range_param(date_range.start, date_range.end),
        },
        fields=fields,
        fallback_fields=DEMOGRAPHIC_FIELDS,
    )

    rows = payload.get("elements", []) or []
    if not isinstance(rows, list):
        return []

    return [
        _flatten_ad_analytics_row(
            row,
            account_id=account_id,
            account_urn=account_urn,
            requested_pivot=requested_pivot,
            time_granularity=time_granularity,
        )
        for row in rows
        if isinstance(row, dict)
    ]


def _analytics_rows(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    pivot: str,
    time_granularity: str,
    date_range: LinkedInDateRange,
    fields: tuple[str, ...] = PERFORMANCE_FIELDS,
    fallback_fields: tuple[str, ...] | None = PERFORMANCE_FIELDS_FALLBACK,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    window_days = (
        DAILY_WINDOW_DAYS
        if time_granularity == "DAILY"
        else max(1, (date_range.end - date_range.start).days + 1)
    )

    for account_id in account_ids:
        windows = _date_windows(date_range, window_days=window_days)

        for window in windows:
            batch = _analytics_request(
                client,
                account_id=account_id,
                pivot=pivot,
                time_granularity=time_granularity,
                date_range=window,
                fields=fields,
                fallback_fields=fallback_fields,
            )

            if len(batch) >= MAX_ANALYTICS_ROWS and window.start < window.end:
                midpoint = window.start + timedelta(days=max(1, (window.end - window.start).days // 2))
                rows.extend(
                    _analytics_rows(
                        client,
                        account_ids=[account_id],
                        pivot=pivot,
                        time_granularity=time_granularity,
                        date_range=LinkedInDateRange(start=window.start, end=midpoint),
                        fields=fields,
                        fallback_fields=fallback_fields,
                    )
                )
                rows.extend(
                    _analytics_rows(
                        client,
                        account_ids=[account_id],
                        pivot=pivot,
                        time_granularity=time_granularity,
                        date_range=LinkedInDateRange(start=midpoint + timedelta(days=1), end=window.end),
                        fields=fields,
                        fallback_fields=fallback_fields,
                    )
                )
                continue

            rows.extend(batch)

    return rows


def _statistics_rows(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    pivots: list[str],
    time_granularity: str,
    date_range: LinkedInDateRange,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    window_days = (
        DAILY_WINDOW_DAYS
        if time_granularity == "DAILY"
        else max(1, (date_range.end - date_range.start).days + 1)
    )

    for account_id in account_ids:
        windows = _date_windows(date_range, window_days=window_days)

        for window in windows:
            batch = _statistics_request(
                client,
                account_id=account_id,
                pivots=pivots,
                time_granularity=time_granularity,
                date_range=window,
                fields=fields,
            )

            if len(batch) >= MAX_ANALYTICS_ROWS and window.start < window.end:
                midpoint = window.start + timedelta(days=max(1, (window.end - window.start).days // 2))
                rows.extend(
                    _statistics_rows(
                        client,
                        account_ids=[account_id],
                        pivots=pivots,
                        time_granularity=time_granularity,
                        date_range=LinkedInDateRange(start=window.start, end=midpoint),
                        fields=fields,
                    )
                )
                rows.extend(
                    _statistics_rows(
                        client,
                        account_ids=[account_id],
                        pivots=pivots,
                        time_granularity=time_granularity,
                        date_range=LinkedInDateRange(start=midpoint + timedelta(days=1), end=window.end),
                        fields=fields,
                    )
                )
                continue

            rows.extend(batch)

    return rows


def _professional_demographic_rows(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    date_range: LinkedInDateRange,
    member_pivot: str,
    entity_pivot: str | None = None,
) -> list[dict[str, Any]]:
    if not entity_pivot:
        rows = _analytics_rows(
            client,
            account_ids=account_ids,
            pivot=member_pivot,
            time_granularity="ALL",
            date_range=date_range,
            fields=DEMOGRAPHIC_FIELDS,
            fallback_fields=None,
        )
    else:
        rows = _statistics_rows(
            client,
            account_ids=account_ids,
            pivots=[entity_pivot, member_pivot],
            time_granularity="ALL",
            date_range=date_range,
            fields=DEMOGRAPHIC_FIELDS,
        )

    for row in rows:
        row["demographic_pivot"] = member_pivot
        row["entity_pivot"] = entity_pivot or "ACCOUNT"

    return rows


def _empty_professional_demographic_datasets(datasets: dict[str, Any], raw_payloads: dict[str, list[dict[str, Any]]]) -> None:
    for dataset_key in (
        "professional_demographics_account",
        "professional_demographics_campaign",
        "professional_demographics_creative",
    ):
        raw_payloads[f"{dataset_key}_raw"] = []
        datasets[dataset_key] = records_to_frame([])


def build_reporting_exports(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    date_range: LinkedInDateRange,
    include_professional_demographics: bool = True,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    warnings: list[str] = []
    raw_payloads: dict[str, list[dict[str, Any]]] = {}
    datasets: dict[str, Any] = {}

    report_specs = {
        "insights_account_daily": ("ACCOUNT", "DAILY"),
        "insights_campaign_daily": ("CAMPAIGN", "DAILY"),
        "insights_creative_daily": ("CREATIVE", "DAILY"),
        "insights_account_all": ("ACCOUNT", "ALL"),
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

            if not rows:
                warnings.append(
                    f"{dataset_key} vrátilo prázdná data. Může jít o nulovou aktivitu, chybějící r_ads_reporting access nebo omezení LinkedIn API."
                )
        except Exception as exc:
            warnings.append(f"{dataset_key} nebylo možné načíst: {exc}")
            raw_payloads[f"{dataset_key}_raw"] = []
            datasets[dataset_key] = records_to_frame([])

    if not include_professional_demographics:
        _empty_professional_demographic_datasets(datasets, raw_payloads)
        return datasets, raw_payloads, warnings

    demographic_specs = {
        "professional_demographics_account": None,
        "professional_demographics_campaign": "CAMPAIGN",
        "professional_demographics_creative": "CREATIVE",
    }

    for dataset_key, entity_pivot in demographic_specs.items():
        demographic_rows: list[dict[str, Any]] = []

        for member_pivot in MEMBER_DEMOGRAPHIC_PIVOTS:
            try:
                demographic_rows.extend(
                    _professional_demographic_rows(
                        client,
                        account_ids=account_ids,
                        date_range=date_range,
                        member_pivot=member_pivot,
                        entity_pivot=entity_pivot,
                    )
                )
            except Exception as exc:
                warnings.append(
                    f"{dataset_key} / {member_pivot} není dostupné nebo vrátilo chybu: {exc}"
                )

        raw_payloads[f"{dataset_key}_raw"] = demographic_rows
        datasets[dataset_key] = records_to_frame(demographic_rows)

        if not demographic_rows:
            warnings.append(
                f"{dataset_key} vrátilo prázdná data. U LinkedIn professional demographics to může být kvůli nízkému počtu událostí, zpoždění dat nebo permission limitu."
            )

    return datasets, raw_payloads, warnings