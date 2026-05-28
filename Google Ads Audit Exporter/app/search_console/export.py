from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import FlagsConfig
from app.google_ads.report_definitions import get_report_definition
from app.search_console.cache import SearchConsoleCache
from app.search_console.client import SearchConsoleApiClient, SearchConsoleApiError
from app.utils.dates import ResolvedDateRange


GSC_REPORT_KEYS = [
    "gsc_queries",
    "gsc_pages",
    "gsc_page_query",
    "gsc_opportunities",
]


@dataclass(slots=True)
class SearchConsoleExportResult:
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    report_notes: dict[str, list[str]] = field(default_factory=dict)
    report_warning_keys: set[str] = field(default_factory=set)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_report(report_key: str) -> pd.DataFrame:
    report = get_report_definition(report_key)
    return pd.DataFrame(columns=report.aliases)


def _safe_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/"):
        return text.split("?", 1)[0]
    parsed = urlparse(text)
    return (parsed.path or text).split("?", 1)[0]


def _normalized_gsc_frame(rows: list[dict[str, Any]], report_key: str) -> pd.DataFrame:
    report = get_report_definition(report_key)
    records: list[dict[str, Any]] = []
    for row in rows:
        record = {alias: row.get(alias, row.get(alias.replace("_", ""), "")) for alias in report.aliases}
        record["clicks"] = _safe_float(row.get("clicks"))
        record["impressions"] = _safe_float(row.get("impressions"))
        record["ctr"] = _safe_float(row.get("ctr"))
        record["position"] = _safe_float(row.get("position"))
        records.append(record)
    return pd.DataFrame(records, columns=report.aliases)


def _cached_query(
    client: SearchConsoleApiClient,
    cache: SearchConsoleCache,
    *,
    namespace: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    row_limit: int = 25000,
) -> list[dict[str, Any]]:
    payload = {
        "site_url": client.config.site_url,
        "start_date": start_date,
        "end_date": end_date,
        "dimensions": dimensions,
        "row_limit": row_limit,
    }
    cached = cache.load(namespace=namespace, payload=payload)
    if cached is not None:
        return cached
    rows = client.query_search_analytics(
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
        row_limit=row_limit,
    )
    cache.save(namespace=namespace, payload=payload, rows=rows)
    return rows


def _ads_queries_set(search_terms: pd.DataFrame) -> set[str]:
    if search_terms.empty or "search_term" not in search_terms.columns:
        return set()
    return {
        str(value).strip().lower()
        for value in search_terms["search_term"].fillna("")
        if str(value).strip()
    }


def _landing_page_paths(frame: pd.DataFrame, column_name: str) -> dict[str, dict[str, Any]]:
    if frame.empty or column_name not in frame.columns:
        return {}
    records: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        path = _safe_path(row.get(column_name))
        if not path:
            continue
        existing = records.get(path)
        current_cost = _safe_float(row.get("cost_micros"))
        if existing is None or current_cost > _safe_float(existing.get("cost_micros")):
            records[path] = row.to_dict()
    return records


def _build_gsc_opportunities(
    gsc_queries: pd.DataFrame,
    gsc_pages: pd.DataFrame,
    gsc_page_query: pd.DataFrame,
    search_terms: pd.DataFrame,
    landing_pages: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    paid_queries = _ads_queries_set(search_terms)
    landing_pages_by_path = _landing_page_paths(landing_pages, "expanded_final_url")

    if not gsc_queries.empty:
        query_grouped = (
            gsc_queries.groupby("query", dropna=False, as_index=False)[["clicks", "impressions"]]
            .sum()
        )
        query_position = (
            gsc_queries.groupby("query", dropna=False, as_index=False)[["position"]]
            .mean()
            .rename(columns={"position": "avg_position"})
        )
        query_grouped = query_grouped.merge(query_position, on="query", how="left")
        for _, row in query_grouped.iterrows():
            query = str(row.get("query") or "").strip()
            if not query:
                continue
            clicks = _safe_float(row.get("clicks"))
            impressions = _safe_float(row.get("impressions"))
            if clicks >= 10 and impressions >= 100 and query.lower() not in paid_queries:
                rows.append(
                    {
                        "entity_type": "query",
                        "entity_id": query,
                        "entity_name": query,
                        "parent_page": "",
                        "flag_type": "organic_winner_missing_in_ads",
                        "severity": "medium",
                        "metric_1": clicks,
                        "metric_2": impressions,
                        "note": "Organicky dotaz ma vykon, ale neni videt v placenych search terms.",
                    }
                )

    if not search_terms.empty:
        for _, row in search_terms.iterrows():
            query = str(row.get("search_term") or "").strip().lower()
            if not query:
                continue
            cost_micros = _safe_float(row.get("cost_micros"))
            organic_match = gsc_queries[gsc_queries["query"].fillna("").str.lower() == query] if not gsc_queries.empty else pd.DataFrame()
            organic_clicks = _safe_float(organic_match["clicks"].sum()) if not organic_match.empty else 0
            organic_position = _safe_float(organic_match["position"].mean()) if not organic_match.empty else 0

            if cost_micros >= flags_config.min_spend_micros and organic_clicks > 20 and organic_position <= 10:
                rows.append(
                    {
                        "entity_type": "search_term",
                        "entity_id": row.get("search_term", ""),
                        "entity_name": row.get("search_term", ""),
                        "parent_page": row.get("campaign_name", ""),
                        "flag_type": "paid_high_spend_query_with_good_organic",
                        "severity": "medium",
                        "metric_1": cost_micros,
                        "metric_2": organic_position,
                        "note": "Dotaz ma uz silnou organiku, stojí za kontrolu překryv SEO/PPC.",
                    }
                )

            if cost_micros >= flags_config.min_spend_micros and organic_clicks <= 0:
                rows.append(
                    {
                        "entity_type": "search_term",
                        "entity_id": row.get("search_term", ""),
                        "entity_name": row.get("search_term", ""),
                        "parent_page": row.get("campaign_name", ""),
                        "flag_type": "paid_search_term_has_no_organic_support",
                        "severity": "medium",
                        "metric_1": cost_micros,
                        "metric_2": organic_clicks,
                        "note": "Placený dotaz nema zadnou organickou podporu.",
                    }
                )

    if not gsc_pages.empty and not landing_pages.empty:
        page_grouped = (
            gsc_pages.groupby("page", dropna=False, as_index=False)[["clicks", "impressions"]]
            .sum()
        )
        page_position = (
            gsc_pages.groupby("page", dropna=False, as_index=False)[["position"]]
            .mean()
            .rename(columns={"position": "avg_position"})
        )
        page_grouped = page_grouped.merge(page_position, on="page", how="left")
        for _, row in page_grouped.iterrows():
            path = _safe_path(row.get("page"))
            ads_row = landing_pages_by_path.get(path)
            if not ads_row:
                continue
            cost_micros = _safe_float(ads_row.get("cost_micros"))
            conversions = _safe_float(ads_row.get("conversions"))
            organic_position = _safe_float(row.get("avg_position"))
            organic_clicks = _safe_float(row.get("clicks"))
            if (
                cost_micros >= flags_config.min_spend_micros
                and conversions <= 0
                and organic_clicks > 10
                and organic_position <= 10
            ):
                rows.append(
                    {
                        "entity_type": "landing_page",
                        "entity_id": ads_row.get("expanded_final_url", ""),
                        "entity_name": path,
                        "parent_page": ads_row.get("campaign_name", ""),
                        "flag_type": "landing_page_paid_bad_organic_good",
                        "severity": "high",
                        "metric_1": cost_micros,
                        "metric_2": organic_position,
                        "note": "Stranka ma dobry organicky signal, ale slaby placeny vykon.",
                    }
                )

    return pd.DataFrame(rows, columns=get_report_definition("gsc_opportunities").aliases)


def build_search_console_exports(
    *,
    env_config: GoogleAdsEnvConfig,
    datasets: dict[str, pd.DataFrame],
    reports_enabled: dict[str, bool],
    resolved_range: ResolvedDateRange,
    flags_config: FlagsConfig,
    cache_dir: Path,
) -> SearchConsoleExportResult:
    result = SearchConsoleExportResult()
    enabled_report_keys = [key for key in GSC_REPORT_KEYS if reports_enabled.get(key, False)]
    if not enabled_report_keys:
        return result

    if not env_config.gsc_enabled:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Search Console modul je vypnuty v .env."]
            result.report_warning_keys.add(key)
        return result

    if not env_config.gsc_site_url:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Chybi GSC_SITE_URL v .env."]
            result.report_warning_keys.add(key)
        return result

    client = SearchConsoleApiClient.from_env_config(env_config)
    cache = SearchConsoleCache(base_dir=cache_dir)

    try:
        query_rows = _cached_query(
            client,
            cache,
            namespace="queries",
            start_date=resolved_range.date_from.isoformat(),
            end_date=resolved_range.date_to.isoformat(),
            dimensions=["date", "query", "country", "device"],
        )
        page_rows = _cached_query(
            client,
            cache,
            namespace="pages",
            start_date=resolved_range.date_from.isoformat(),
            end_date=resolved_range.date_to.isoformat(),
            dimensions=["date", "page", "country", "device"],
        )
        page_query_rows = _cached_query(
            client,
            cache,
            namespace="page_query",
            start_date=resolved_range.date_from.isoformat(),
            end_date=resolved_range.date_to.isoformat(),
            dimensions=["date", "query", "page", "country", "device"],
            row_limit=25000,
        )
    except SearchConsoleApiError as exc:
        result.errors.append(
            {
                "report": "search_console_api",
                "message": exc.message,
                "details": exc.details,
                "timestamp": _timestamp(),
            }
        )
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Search Console API dotaz selhal, ale Ads export pokracoval dal."]
            result.report_warning_keys.add(key)
        return result

    gsc_queries = _normalized_gsc_frame(query_rows, "gsc_queries")
    gsc_pages = _normalized_gsc_frame(page_rows, "gsc_pages")
    gsc_page_query = _normalized_gsc_frame(page_query_rows, "gsc_page_query")

    if reports_enabled.get("gsc_queries", False):
        result.datasets["gsc_queries"] = gsc_queries
    if reports_enabled.get("gsc_pages", False):
        result.datasets["gsc_pages"] = gsc_pages
    if reports_enabled.get("gsc_page_query", False):
        result.datasets["gsc_page_query"] = gsc_page_query

    result.report_notes["gsc_queries"] = [
        "Search Console queries jsou cachovane lokalne, aby se nevolaly stejne dotazy porad dokola."
    ]
    result.report_notes["gsc_pages"] = [
        "GSC pages pomahaji porovnat organicky signal se spendem na landing pages."
    ]
    result.report_notes["gsc_page_query"] = [
        "Page-query je nejnarocnejsi kombinace, proto ma nejvetsi smysl cache a rozumne intervaly exportu."
    ]

    if reports_enabled.get("gsc_opportunities", False):
        opportunities = _build_gsc_opportunities(
            gsc_queries=gsc_queries,
            gsc_pages=gsc_pages,
            gsc_page_query=gsc_page_query,
            search_terms=datasets.get("search_terms", pd.DataFrame()),
            landing_pages=datasets.get("landing_pages", pd.DataFrame()),
            flags_config=flags_config,
        )
        result.datasets["gsc_opportunities"] = opportunities
        result.report_notes["gsc_opportunities"] = [
            "Pravidla hledaji keyword prilezitosti z organiky i prekryv mezi SEO a PPC."
        ]

    return result
