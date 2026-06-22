from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.integrations.sklik.client_drak import SklikDrakClient
from app.integrations.sklik.errors import SklikApiError
from app.integrations.sklik.normalizers import extract_rows, flatten_report_rows


LOGGER = logging.getLogger("google_ads_audit_exporter")

CAMPAIGN_REPORT_COLUMNS = [
    "actualClicks",
    "adSelection",
    "automaticLocation",
    "budget.colorCodeId",
    "budget.dayBudget",
    "budget.deleted",
    "budget.deleteDate",
    "budget.id",
    "budget.name",
    "context",
    "contextNetwork",
    "createDate",
    "deleted",
    "deleteDate",
    "defaultBudgetId",
    "devicesPriceRatio",
    "endDate",
    "excludedSearchServices",
    "excludedUrls",
    "exhaustedTotalBudget",
    "fulltext",
    "id",
    "name",
    "paymentMethod",
    "phone.number",
    "phone.statusId",
    "phone.status",
    "premises.id",
    "schedule",
    "scheduleEnabled",
    "startDate",
    "status",
    "regions.id",
    "regions.parentId",
    "regions.name",
    "totalClicks",
    "totalClicksFrom",
    "totalBudgetFrom",
    "type",
    "totalBudget",
    "videoFormat",
    "avgCpc",
    "avgPos",
    "clickMoney",
    "clicks",
    "conversions",
    "conversionValue",
    "impressionMoney",
    "impressions",
    "totalMoney",
    "transactions",
    "missImpressions",
    "underLowerThreshold",
    "exhaustedBudget",
    "stoppedBySchedule",
    "underForestThreshold",
    "exhaustedBudgetShare",
    "ctr",
    "pno",
    "ish",
    "ishContext",
    "ishSum",
    "avgCpt",
]

ADS_REPORT_COLUMNS = [
    "id",
    "adStatus",
    "adType",
    "createDate",
    "deleteDate",
    "deleted",
    "creative1",
    "creative2",
    "creative3",
    "description",
    "description2",
    "finalUrl",
    "headline1",
    "headline2",
    "headline3",
    "path1",
    "path2",
    "longLine",
    "shortLine",
    "name",
    "companyName",
    "clickthruText",
    "clickthruUrl",
    "image.id",
    "image.url",
    "image.width",
    "image.height",
    "image.size",
    "imageLogo.id",
    "imageLogo.url",
    "imageSquare.id",
    "imageSquare.url",
    "group.id",
    "group.name",
    "campaign.id",
    "campaign.name",
]

BANNERS_REPORT_COLUMNS = [
    "id",
    "bannerName",
    "adStatus",
    "adType",
    "createDate",
    "deleteDate",
    "deleted",
    "description",
    "clickthruUrl",
    "mobileFinalUrl",
    "height",
    "width",
    "image.id",
    "image.url",
    "image.width",
    "image.height",
    "image.size",
    "imageType",
    "premiseId",
    "premiseModeId",
    "premiseMode",
    "status",
    "sensitivity",
    "schedule",
    "scheduleEnabled",
    "group.id",
    "group.name",
    "group.deleted",
    "group.createDate",
    "group.deleteDate",
    "group.maxCpc",
    "group.maxCpt",
    "group.status",
    "campaign.id",
    "campaign.name",
    "campaign.actualClicks",
    "campaign.createDate",
    "campaign.deleteDate",
    "campaign.deleted",
    "campaign.endDate",
    "campaign.startDate",
    "campaign.totalBudgetFrom",
    "campaign.totalClicksFrom",
    "campaign.totalClicks",
    "campaign.status",
    "avgCpc",
    "avgPos",
    "clickMoney",
    "clicks",
    "conversions",
    "conversionValue",
    "impressionMoney",
    "impressions",
    "totalMoney",
    "transactions",
    "missImpressions",
    "underLowerThreshold",
    "exhaustedBudget",
    "stoppedBySchedule",
    "underForestThreshold",
    "exhaustedBudgetShare",
    "ctr",
    "pno",
    "ish",
    "ishContext",
    "ishSum",
]

QUERIES_REPORT_COLUMNS = [
    "query",
    "keyword.id",
    "keyword.name",
    "keyword.matchType",
    "keyword.matchTypeId",
    "keyword.url",
    "keyword.maxCpc",
    "keyword.cpc",
    "keyword.status",
    "keyword.statusId",
    "keyword.disabled",
    "group.id",
    "group.name",
    "group.maxCpc",
    "group.maxCpt",
    "group.statusId",
    "campaign.id",
    "campaign.name",
    "campaign.statusId",
    "campaign.context",
    "campaign.fulltext",
    "campaign.paymentMethod",
    "campaign.automaticLocation",
    "campaign.budgetId",
    "campaign.defaultBudgetId",
    "avgCpc",
    "avgPos",
    "clickMoney",
    "clicks",
    "conversions",
    "conversionValue",
    "impressionMoney",
    "impressions",
    "totalMoney",
    "transactions",
]

SITELINKS_REPORT_COLUMNS = [
    "id",
    "createDate",
    "deleted",
    "deletedSitelinkInGroup",
    "status",
    "statusId",
    "name",
    "urlId",
    "url",
    "indexDate",
    "group.id",
    "group.name",
    "group.deleted",
    "group.createDate",
    "group.deleteDate",
    "group.maxCpc",
    "group.maxCpt",
    "campaign.actualClicks",
    "campaign.createDate",
    "campaign.deleteDate",
    "campaign.deleted",
    "campaign.endDate",
    "campaign.id",
    "campaign.name",
    "campaign.startDate",
    "campaign.totalBudgetFrom",
    "campaign.totalClicksFrom",
    "campaign.totalClicks",
    "user.id",
    "clicks",
    "impressions",
    "clickMoney",
    "impressionMoney",
    "totalMoney",
    "avgPos",
    "conversions",
    "transactions",
    "missImpressions",
    "underLowerThreshold",
    "exhaustedBudget",
    "stoppedBySchedule",
    "underForestThreshold",
    "exhaustedBudgetShare",
    "ctr",
    "pno",
    "ish",
    "ishContext",
    "ishSum",
    "avgCpt",
]

RETARGETING_REPORT_COLUMNS = [
    "id",
    "retargetingId",
    "active",
    "cpc",
    "cpt",
    "isCombination",
    "name",
    "membership",
    "users",
    "useHistoricData",
    "description",
    "createDate",
    "takeAllUsers",
    "indexDate",
    "isDynamic",
    "deleteDate",
    "listDeleted",
    "deleted",
    "suspendDate",
    "status",
    "retargetingConditions.id",
    "retargetingConditions.groupId",
    "retargetingConditions.value",
    "retargetingConditions.type",
    "retargetingConditions.key",
    "campaign.actualClicks",
    "campaign.createDate",
    "campaign.deleteDate",
    "campaign.deleted",
    "campaign.endDate",
    "campaign.id",
    "campaign.name",
    "campaign.startDate",
    "campaign.totalBudgetFrom",
    "campaign.totalClicksFrom",
    "campaign.totalClicks",
    "group.id",
    "group.name",
    "group.deleted",
    "group.createDate",
    "group.deleteDate",
    "group.maxCpc",
    "group.maxCpt",
    "clicks",
    "impressions",
    "clickMoney",
    "impressionMoney",
    "totalMoney",
    "avgCpc",
    "avgPos",
    "conversions",
    "conversionValue",
    "transactions",
    "missImpressions",
    "underLowerThreshold",
    "exhaustedBudget",
    "stoppedBySchedule",
    "underForestThreshold",
    "exhaustedBudgetShare",
    "ctr",
    "pno",
    "ish",
    "ishContext",
    "ishSum",
]

REPORT_DISPLAY_COLUMNS: dict[str, list[str]] = {
    "campaigns": CAMPAIGN_REPORT_COLUMNS,
    "ads": ADS_REPORT_COLUMNS,
    "banners": BANNERS_REPORT_COLUMNS,
    "queries": QUERIES_REPORT_COLUMNS,
    "sitelinks": SITELINKS_REPORT_COLUMNS,
    "retargeting": RETARGETING_REPORT_COLUMNS,
}


@dataclass(slots=True)
class SklikDateRange:
    start: date
    end: date


def resolve_date_range(
    *,
    preset: str = "last_90_days",
    date_from: str = "",
    date_to: str = "",
    default_days: int = 90,
) -> SklikDateRange:
    if date_from and date_to:
        return SklikDateRange(start=date.fromisoformat(date_from), end=date.fromisoformat(date_to))

    normalized = str(preset or "").strip().lower()
    today = datetime.utcnow().date()

    if normalized == "today":
        return SklikDateRange(start=today, end=today)

    days_lookup = {
        "last_1_day": 1,
        "last_7_days": 7,
        "last_30_days": 30,
        "last_90_days": 90,
        "last_180_days": 180,
        "last_365_days": 365,
    }
    days = days_lookup.get(normalized, default_days)

    end = today - timedelta(days=1)
    start = end - timedelta(days=max(1, days) - 1)
    return SklikDateRange(start=start, end=end)


def _days_between(date_from: str, date_to: str) -> int:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    return max(1, (end - start).days + 1)


def _split_window(date_from: str, date_to: str) -> list[tuple[str, str]]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if start >= end:
        return [(date_from, date_to)]
    middle = start + timedelta(days=max(1, (end - start).days // 2))
    first_end = middle.isoformat()
    second_start = (middle + timedelta(days=1)).isoformat()
    return [(start.isoformat(), first_end), (second_start, end.isoformat())]


def should_include_current_day_stats(date_to: str, today: date | None = None) -> bool:
    today = today or datetime.utcnow().date()
    try:
        return date.fromisoformat(date_to) >= today
    except ValueError:
        return False


def build_report_create_params(
    *,
    date_from: str,
    date_to: str,
    stat_granularity: str,
    restriction_filter_extra: dict[str, Any] | None = None,
    include_current_day_stats: bool | None = None,
) -> list[dict[str, Any]]:
    restriction_filter = {
        "dateFrom": date_from,
        "dateTo": date_to,
    }
    if restriction_filter_extra:
        restriction_filter.update(restriction_filter_extra)

    if include_current_day_stats is None:
        include_current_day_stats = should_include_current_day_stats(date_to)

    display_options = {
        "statGranularity": stat_granularity,
        "includeCurrentDayStats": bool(include_current_day_stats),
    }

    return [restriction_filter, display_options]


def build_report_read_params(
    *,
    report_id: str,
    offset: int,
    limit: int,
    allow_empty_statistics: bool,
    display_columns: list[str] | None,
) -> list[Any]:
    display_options: dict[str, Any] = {
        "offset": int(offset),
        "limit": int(limit),
        "allowEmptyStatistics": bool(allow_empty_statistics),
    }
    if display_columns:
        display_options["displayColumns"] = display_columns
    return [report_id, display_options]


def estimate_periods(date_from: str, date_to: str, granularity: str) -> int:
    days = _days_between(date_from, date_to)
    normalized = str(granularity or "").strip().lower()
    if normalized == "total":
        return 1
    if normalized == "daily":
        return days
    if normalized == "weekly":
        return max(1, math.ceil(days / 7))
    if normalized == "monthly":
        return max(1, math.ceil(days / 31))
    if normalized == "quarterly":
        return max(1, math.ceil(days / 92))
    if normalized == "yearly":
        return max(1, math.ceil(days / 366))
    return days


def estimate_report_units(entity_count: int, date_from: str, date_to: str, granularity: str) -> int:
    periods = estimate_periods(date_from, date_to, granularity)
    return max(1, entity_count) * max(1, periods)


def _recommended_chunk_days(entity_count: int, stats_data_limit: int, granularity: str) -> int | None:
    normalized = str(granularity or "").strip().lower()
    if normalized == "total":
        return None
    periods_per_window = max(1, stats_data_limit // max(1, entity_count))
    if normalized == "daily":
        return periods_per_window
    if normalized == "weekly":
        return periods_per_window * 7
    if normalized == "monthly":
        return periods_per_window * 31
    if normalized == "quarterly":
        return periods_per_window * 92
    if normalized == "yearly":
        return periods_per_window * 366
    return periods_per_window


def _split_range_by_days(date_from: str, date_to: str, chunk_days: int) -> list[tuple[str, str]]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if chunk_days <= 0 or start >= end:
        return [(date_from, date_to)]

    windows: list[tuple[str, str]] = []
    current = start
    while current <= end:
        window_end = min(end, current + timedelta(days=chunk_days - 1))
        windows.append((current.isoformat(), window_end.isoformat()))
        current = window_end + timedelta(days=1)
    return windows


def split_date_range_to_days(date_from: str, date_to: str) -> list[tuple[str, str]]:
    return _split_range_by_days(date_from, date_to, 1)


def _is_too_much_data_error(exc: SklikApiError) -> bool:
    message = str(exc).lower()
    if exc.status_code == 413:
        return True
    return (
        "too many" in message
        or "too much" in message
        or "requiring" in message
        or "statsdatalimit" in message
        or ("data" in message and "limit" in message)
    )


def _is_current_day_stats_error(exc: SklikApiError) -> bool:
    message = str(exc).lower()
    return "includecurrentdaystats" in message or "current day" in message


def _create_report(
    *,
    client: SklikDrakClient,
    entity: str,
    user_id: int | None,
    date_from: str,
    date_to: str,
    granularity: str,
    restriction_filter_extra: dict[str, Any] | None,
    include_current_day_stats: bool | None = None,
) -> dict[str, Any]:
    create_params = build_report_create_params(
        date_from=date_from,
        date_to=date_to,
        stat_granularity=granularity,
        restriction_filter_extra=restriction_filter_extra,
        include_current_day_stats=include_current_day_stats,
    )
    return client.call(f"{entity}.createReport", create_params, user_id=user_id)


def fetch_report_rows(
    *,
    client: SklikDrakClient,
    entity: str,
    user_id: int | None,
    date_from: str,
    date_to: str,
    granularity: str,
    include_empty_statistics: bool = False,
    limit: int = 5000,
    restriction_filter_extra: dict[str, Any] | None = None,
    display_columns: list[str] | None = None,
    max_splits: int = 4,
    entity_count: int = 1,
    stats_data_limit: int | None = None,
) -> list[dict[str, Any]]:
    if stats_data_limit:
        estimated_units = estimate_report_units(entity_count, date_from, date_to, granularity)
        if estimated_units > stats_data_limit:
            chunk_days = _recommended_chunk_days(entity_count, stats_data_limit, granularity)
            if chunk_days and _days_between(date_from, date_to) > chunk_days:
                rows: list[dict[str, Any]] = []
                for window_start, window_end in _split_range_by_days(date_from, date_to, chunk_days):
                    rows.extend(
                        fetch_report_rows(
                            client=client,
                            entity=entity,
                            user_id=user_id,
                            date_from=window_start,
                            date_to=window_end,
                            granularity=granularity,
                            include_empty_statistics=include_empty_statistics,
                            limit=limit,
                            restriction_filter_extra=restriction_filter_extra,
                            display_columns=display_columns,
                            max_splits=max_splits,
                            entity_count=entity_count,
                            stats_data_limit=stats_data_limit,
                        )
                    )
                return rows

    try:
        create_payload = _create_report(
            client=client,
            entity=entity,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
            restriction_filter_extra=restriction_filter_extra,
        )
    except SklikApiError as exc:
        if _is_current_day_stats_error(exc):
            LOGGER.warning(
                "Sklik report current-day stats required entity=%s user_id=%s date_from=%s date_to=%s granularity=%s; retrying with includeCurrentDayStats=true",
                entity,
                user_id,
                date_from,
                date_to,
                granularity,
            )
            create_payload = _create_report(
                client=client,
                entity=entity,
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                granularity=granularity,
                restriction_filter_extra=restriction_filter_extra,
                include_current_day_stats=True,
            )
        elif _is_too_much_data_error(exc) and _days_between(date_from, date_to) > 1:
            LOGGER.warning(
                "Sklik report too much data entity=%s user_id=%s date_from=%s date_to=%s granularity=%s; retrying daily fallback",
                entity,
                user_id,
                date_from,
                date_to,
                granularity,
            )
            rows: list[dict[str, Any]] = []
            for window_start, window_end in split_date_range_to_days(date_from, date_to):
                rows.extend(
                    fetch_report_rows(
                        client=client,
                        entity=entity,
                        user_id=user_id,
                        date_from=window_start,
                        date_to=window_end,
                        granularity="daily",
                        include_empty_statistics=include_empty_statistics,
                        limit=limit,
                        restriction_filter_extra=restriction_filter_extra,
                        display_columns=display_columns,
                        max_splits=0,
                        entity_count=entity_count,
                        stats_data_limit=stats_data_limit,
                    )
                )
            return rows
        else:
            raise

    report_id = str(create_payload.get("reportId") or create_payload.get("result") or "").strip()
    if not report_id:
        rows = extract_rows(create_payload)
        return flatten_report_rows(rows, user_id=int(user_id or 0), entity=entity)

    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        try:
            read_payload = client.call(
                f"{entity}.readReport",
                build_report_read_params(
                    report_id=report_id,
                    offset=offset,
                    limit=limit,
                    allow_empty_statistics=include_empty_statistics,
                    display_columns=display_columns,
                ),
                user_id=user_id,
            )
        except SklikApiError as exc:
            if _is_too_much_data_error(exc) and _days_between(date_from, date_to) > 1:
                LOGGER.warning(
                    "Sklik readReport too much data entity=%s user_id=%s date_from=%s date_to=%s granularity=%s; retrying daily fallback",
                    entity,
                    user_id,
                    date_from,
                    date_to,
                    granularity,
                )
                fallback_rows: list[dict[str, Any]] = []
                for window_start, window_end in split_date_range_to_days(date_from, date_to):
                    fallback_rows.extend(
                        fetch_report_rows(
                            client=client,
                            entity=entity,
                            user_id=user_id,
                            date_from=window_start,
                            date_to=window_end,
                            granularity="daily",
                            include_empty_statistics=include_empty_statistics,
                            limit=limit,
                            restriction_filter_extra=restriction_filter_extra,
                            display_columns=display_columns,
                            max_splits=0,
                            entity_count=entity_count,
                            stats_data_limit=stats_data_limit,
                        )
                    )
                return fallback_rows
            raise

        batch = extract_rows(read_payload)
        if not batch:
            break

        rows.extend(flatten_report_rows(batch, user_id=int(user_id or 0), entity=entity))

        if len(batch) < limit:
            break

        offset += limit

    return rows