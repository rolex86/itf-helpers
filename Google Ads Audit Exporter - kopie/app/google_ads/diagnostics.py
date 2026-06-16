from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config.settings import FlagsConfig
from app.export.derived_summaries import build_shopping_products_summary
from app.google_ads.fetcher import GoogleAdsFetcher
from app.google_ads.report_definitions import FieldSpec, ReportDefinition
from app.utils.dates import ResolvedDateRange


MERCHANT_LINKS_REPORT = ReportDefinition(
    key="_merchant_links",
    sheet_name="_merchant_links",
    query_file="merchant_links.sql",
    fields=(
        FieldSpec("product_link.product_link_id", "product_link_id"),
        FieldSpec("product_link.merchant_center.merchant_center_id", "merchant_center_id"),
        FieldSpec("product_link.resource_name", "resource_name"),
    ),
)

APP_ANALYTICS_LINKS_REPORT = ReportDefinition(
    key="_app_analytics_links",
    sheet_name="_app_analytics_links",
    query_file="account_links.sql",
    fields=(
        FieldSpec("account_link.account_link_id", "account_link_id"),
        FieldSpec("account_link.status", "status"),
        FieldSpec("account_link.type", "type"),
        FieldSpec(
            "account_link.third_party_app_analytics.app_analytics_provider_id",
            "app_analytics_provider_id",
            optional=True,
        ),
        FieldSpec("account_link.third_party_app_analytics.app_id", "app_id", optional=True),
        FieldSpec("account_link.third_party_app_analytics.app_vendor", "app_vendor", optional=True),
    ),
)

VIDEO_DATA_LINKS_REPORT = ReportDefinition(
    key="_video_data_links",
    sheet_name="_video_data_links",
    query_file="data_links.sql",
    fields=(
        FieldSpec("data_link.data_link_id", "data_link_id"),
        FieldSpec("data_link.product_link_id", "product_link_id"),
        FieldSpec("data_link.status", "status"),
        FieldSpec("data_link.type", "type"),
        FieldSpec("data_link.youtube_video.video_id", "youtube_video_id", optional=True),
    ),
)

RECOMMENDATION_UPLIFT_REPORT = ReportDefinition(
    key="_recommendation_uplift",
    sheet_name="_recommendation_uplift",
    query_file="recommendation_uplift.sql",
    fields=(
        FieldSpec("segments.recommendation_type", "recommendation_type"),
        FieldSpec("metrics.optimization_score_uplift", "optimization_score_uplift"),
    ),
)

GA4_CONVERSION_TYPES = {
    "GOOGLE_ANALYTICS_4_CUSTOM",
    "GOOGLE_ANALYTICS_4_PURCHASE",
}


@dataclass(slots=True)
class SupplementalReportsResult:
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    query_attempts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    report_notes: dict[str, list[str]] = field(default_factory=dict)
    report_warning_keys: set[str] = field(default_factory=set)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _safe_fetch(
    fetcher: GoogleAdsFetcher,
    report: ReportDefinition,
    customer_id: str,
    resolved_range: ResolvedDateRange,
    result: SupplementalReportsResult,
) -> pd.DataFrame:
    try:
        fetch_result = fetcher.fetch_report(
            report=report,
            customer_id=customer_id,
            resolved_range=resolved_range,
        )
        result.query_attempts.extend(fetch_result.query_attempts)
        return fetch_result.dataframe
    except Exception as exc:
        result.errors.append(
            {
                "report": report.key,
                "message": str(exc),
                "details": "Supporting read-only diagnostics query failed.",
                "timestamp": _timestamp(),
            }
        )
        return _empty_frame(report.aliases)


def _normalize_status(value: object, detected: bool) -> str:
    normalized = str(value or "").strip()
    if normalized:
        return normalized.lower()
    return "detected" if detected else "not_detected"


def _resource_name_suffix(value: object) -> str:
    if value in (None, ""):
        return ""
    parts = str(value).rstrip("/").split("/")
    return parts[-1] if parts else ""


def _series_value(frame: pd.DataFrame, column: str, default: object = "") -> object:
    if frame.empty or column not in frame.columns:
        return default
    return frame.iloc[0].get(column, default)


def _bool_string(value: object) -> str:
    if value in (True, "True", "true", 1, "1"):
        return "ano"
    if value in (False, "False", "false", 0, "0"):
        return "ne"
    return ""


def _unique_campaign_count(frame: pd.DataFrame) -> int:
    if frame.empty or "campaign_id" not in frame.columns:
        return 0
    normalized = frame["campaign_id"].replace("", pd.NA).dropna()
    return int(normalized.astype(str).nunique())


def _build_conversion_source_details(conversion_actions: pd.DataFrame) -> str:
    if conversion_actions.empty or "type" not in conversion_actions.columns:
        return "Nenalezeny zadne konverzni akce."

    type_counts = (
        conversion_actions["type"]
        .fillna("(neznamy typ)")
        .astype(str)
        .value_counts()
        .sort_values(ascending=False)
    )
    return ", ".join(f"{key}: {value}" for key, value in type_counts.items())


def _build_linked_accounts_report(
    merchant_links: pd.DataFrame,
    app_links: pd.DataFrame,
    video_links: pd.DataFrame,
    assets: pd.DataFrame,
    conversion_actions: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    if merchant_links.empty:
        rows.append(
            {
                "service_key": "merchant_center",
                "service_name": "Merchant Center",
                "status": "not_detected",
                "linked_account_id": "",
                "linked_account_name": "",
                "evidence_source": "product_link",
                "details": "Nenalezen zadny Merchant Center product link.",
            }
        )
    else:
        for row in merchant_links.to_dict("records"):
            merchant_center_id = row.get("merchant_center_id", "")
            rows.append(
                {
                    "service_key": "merchant_center",
                    "service_name": "Merchant Center",
                    "status": "detected",
                    "linked_account_id": merchant_center_id,
                    "linked_account_name": f"Merchant Center {merchant_center_id}",
                    "evidence_source": "product_link",
                    "details": f"Propojeni pres product_link {row.get('product_link_id', '')}.",
                }
            )

    ga4_count = 0
    if not conversion_actions.empty and "type" in conversion_actions.columns:
        ga4_count = int(conversion_actions["type"].isin(GA4_CONVERSION_TYPES).sum())
    rows.append(
        {
            "service_key": "ga4",
            "service_name": "Google Analytics 4",
            "status": "detected" if ga4_count else "not_detected",
            "linked_account_id": "",
            "linked_account_name": "",
            "evidence_source": "conversion_actions",
            "details": (
                f"Nalezeno {ga4_count} GA4 konverznich akci."
                if ga4_count
                else "Nenalezeny GA4 konverzni akce, propojeni tedy nebylo potvrzeno."
            ),
        }
    )

    if app_links.empty:
        rows.append(
            {
                "service_key": "third_party_app_analytics",
                "service_name": "App analytics",
                "status": "not_detected",
                "linked_account_id": "",
                "linked_account_name": "",
                "evidence_source": "account_link",
                "details": "Nenalezen zadny account_link pro third-party app analytics.",
            }
        )
    else:
        for row in app_links.to_dict("records"):
            rows.append(
                {
                    "service_key": "third_party_app_analytics",
                    "service_name": "App analytics",
                    "status": _normalize_status(row.get("status"), detected=True),
                    "linked_account_id": row.get("account_link_id", ""),
                    "linked_account_name": row.get("app_id", ""),
                    "evidence_source": "account_link",
                    "details": (
                        f"Provider {row.get('app_analytics_provider_id', '')}, "
                        f"vendor {row.get('app_vendor', '')}."
                    ).strip(),
                }
            )

    location_assets_count = 0
    if not assets.empty and "asset_type" in assets.columns:
        location_assets_count = int((assets["asset_type"] == "LOCATION").sum())
    rows.append(
        {
            "service_key": "business_profile",
            "service_name": "Business Profile",
            "status": "detected" if location_assets_count else "not_detected",
            "linked_account_id": "",
            "linked_account_name": "",
            "evidence_source": "assets",
            "details": (
                f"Nalezeno {location_assets_count} location assetu, bereme to jako signal napojeni."
                if location_assets_count
                else "Nenalezeny location assety, propojeni Business Profile nebylo potvrzeno."
            ),
        }
    )

    youtube_detected = False
    if not video_links.empty:
        youtube_detected = True
        for row in video_links.to_dict("records"):
            rows.append(
                {
                    "service_key": "youtube",
                    "service_name": "YouTube",
                    "status": _normalize_status(row.get("status"), detected=True),
                    "linked_account_id": row.get("youtube_video_id", "") or row.get("data_link_id", ""),
                    "linked_account_name": row.get("type", ""),
                    "evidence_source": "data_link",
                    "details": "Video data link dostupny pres data_link resource.",
                }
            )

    if not youtube_detected:
        youtube_video_assets = 0
        if not assets.empty and "asset_type" in assets.columns:
            youtube_video_assets = int((assets["asset_type"] == "YOUTUBE_VIDEO").sum())
        rows.append(
            {
                "service_key": "youtube",
                "service_name": "YouTube",
                "status": "detected" if youtube_video_assets else "not_detected",
                "linked_account_id": "",
                "linked_account_name": "",
                "evidence_source": "assets",
                "details": (
                    f"Nalezeno {youtube_video_assets} YouTube video assetu."
                    if youtube_video_assets
                    else "Nenalezeny data_link ani YouTube video assety."
                ),
            }
        )

    rows.append(
        {
            "service_key": "conversion_sources",
            "service_name": "Zdroje konverzi",
            "status": "detected" if not conversion_actions.empty else "not_detected",
            "linked_account_id": "",
            "linked_account_name": "",
            "evidence_source": "conversion_actions",
            "details": _build_conversion_source_details(conversion_actions),
        }
    )

    return pd.DataFrame(rows)


def _build_account_diagnostics_report(
    account: pd.DataFrame,
    campaigns: pd.DataFrame,
    pmax_campaigns: pd.DataFrame,
    conversion_actions: pd.DataFrame,
    linked_accounts: pd.DataFrame,
) -> pd.DataFrame:
    merchant_detected = False
    ga4_detected = False
    youtube_detected = False
    business_profile_detected = False
    if not linked_accounts.empty:
        merchant_detected = bool(
            (
                (linked_accounts["service_key"] == "merchant_center")
                & (linked_accounts["status"] != "not_detected")
            ).any()
        )
        ga4_detected = bool(
            ((linked_accounts["service_key"] == "ga4") & (linked_accounts["status"] != "not_detected")).any()
        )
        youtube_detected = bool(
            (
                (linked_accounts["service_key"] == "youtube")
                & (linked_accounts["status"] != "not_detected")
            ).any()
        )
        business_profile_detected = bool(
            (
                (linked_accounts["service_key"] == "business_profile")
                & (linked_accounts["status"] != "not_detected")
            ).any()
        )

    primary_count = 0
    if not conversion_actions.empty and "primary_for_goal" in conversion_actions.columns:
        primary_count = int(
            conversion_actions["primary_for_goal"].isin([True, "True", "true", 1, "1"]).sum()
        )

    shopping_campaigns_count = 0
    if not campaigns.empty and "advertising_channel_type" in campaigns.columns:
        shopping_campaigns_count = int(
            campaigns.loc[campaigns["advertising_channel_type"] == "SHOPPING", "campaign_id"]
            .astype(str)
            .nunique()
        )

    rows = [
        {
            "diagnostic_key": "auto_tagging_enabled",
            "label": "Auto-tagging",
            "value": _bool_string(_series_value(account, "auto_tagging_enabled")),
            "status": "ok" if _series_value(account, "auto_tagging_enabled") else "warning",
            "details": "Vypnute auto-tagging muze komplikovat mereni a rozpad zdroju.",
        },
        {
            "diagnostic_key": "currency_code",
            "label": "Mena uctu",
            "value": _series_value(account, "currency_code"),
            "status": "info",
            "details": "",
        },
        {
            "diagnostic_key": "time_zone",
            "label": "Casova zona uctu",
            "value": _series_value(account, "time_zone"),
            "status": "warning"
            if _series_value(account, "time_zone") not in ("Europe/Prague", "Europe/Bratislava")
            else "ok",
            "details": "Denni hranice a mesicni rezy vychazi z casove zony Google Ads uctu.",
        },
        {
            "diagnostic_key": "merchant_center_linked",
            "label": "Merchant Center",
            "value": "ano" if merchant_detected else "ne",
            "status": "ok" if merchant_detected else "warning",
            "details": (
                "Merchant Center link byl potvrzen."
                if merchant_detected
                else "Nebyl potvrzen product_link do Merchant Center."
            ),
        },
        {
            "diagnostic_key": "ga4_detected",
            "label": "GA4 signal",
            "value": "ano" if ga4_detected else "ne",
            "status": "ok" if ga4_detected else "warning",
            "details": (
                "GA4 bylo rozpoznano pres typy konverznich akci."
                if ga4_detected
                else "Nebyla nalezena GA4 konverzni akce."
            ),
        },
        {
            "diagnostic_key": "business_profile_detected",
            "label": "Business Profile signal",
            "value": "ano" if business_profile_detected else "ne",
            "status": "ok" if business_profile_detected else "info",
            "details": (
                "Detekovany location assety."
                if business_profile_detected
                else "Nenalezeny location assety, Business Profile nebyl potvrzen."
            ),
        },
        {
            "diagnostic_key": "youtube_signal",
            "label": "YouTube signal",
            "value": "ano" if youtube_detected else "ne",
            "status": "ok" if youtube_detected else "info",
            "details": (
                "Detekovan data_link nebo YouTube assety."
                if youtube_detected
                else "Nenalezen YouTube data link ani video assety."
            ),
        },
        {
            "diagnostic_key": "conversion_actions_count",
            "label": "Pocet konverznich akci",
            "value": int(len(conversion_actions)),
            "status": "ok" if not conversion_actions.empty else "warning",
            "details": "",
        },
        {
            "diagnostic_key": "primary_conversion_actions_count",
            "label": "Primarni konverzni akce",
            "value": primary_count,
            "status": "ok" if primary_count else "warning",
            "details": "",
        },
        {
            "diagnostic_key": "pmax_campaigns_count",
            "label": "PMax kampane",
            "value": _unique_campaign_count(pmax_campaigns),
            "status": "info",
            "details": "",
        },
        {
            "diagnostic_key": "shopping_campaigns_count",
            "label": "Shopping kampane",
            "value": shopping_campaigns_count,
            "status": "info",
            "details": "",
        },
    ]
    return pd.DataFrame(rows)


def build_supplemental_reports(
    *,
    fetcher: GoogleAdsFetcher,
    customer_id: str,
    resolved_range: ResolvedDateRange,
    datasets: dict[str, pd.DataFrame],
    enabled_reports: dict[str, bool],
    flags_config: FlagsConfig,
) -> SupplementalReportsResult:
    result = SupplementalReportsResult()

    merchant_links = _empty_frame(MERCHANT_LINKS_REPORT.aliases)
    app_links = _empty_frame(APP_ANALYTICS_LINKS_REPORT.aliases)
    video_links = _empty_frame(VIDEO_DATA_LINKS_REPORT.aliases)

    needs_link_diagnostics = enabled_reports.get("linked_accounts", False) or enabled_reports.get(
        "account_diagnostics", False
    )
    if needs_link_diagnostics:
        merchant_links = _safe_fetch(
            fetcher=fetcher,
            report=MERCHANT_LINKS_REPORT,
            customer_id=customer_id,
            resolved_range=resolved_range,
            result=result,
        )
        app_links = _safe_fetch(
            fetcher=fetcher,
            report=APP_ANALYTICS_LINKS_REPORT,
            customer_id=customer_id,
            resolved_range=resolved_range,
            result=result,
        )
        video_links = _safe_fetch(
            fetcher=fetcher,
            report=VIDEO_DATA_LINKS_REPORT,
            customer_id=customer_id,
            resolved_range=resolved_range,
            result=result,
        )

    if enabled_reports.get("shopping_products_summary", False):
        result.datasets["shopping_products_summary"] = build_shopping_products_summary(
            shopping_products=datasets.get("shopping_products", pd.DataFrame()),
            flags_config=flags_config,
        )
        result.report_notes["shopping_products_summary"] = [
            "Souhrn agreguje produktovy vykon podle product_item_id a custom labelu."
        ]

    if enabled_reports.get("linked_accounts", False):
        linked_accounts = _build_linked_accounts_report(
            merchant_links=merchant_links,
            app_links=app_links,
            video_links=video_links,
            assets=datasets.get("assets", pd.DataFrame()),
            conversion_actions=datasets.get("conversion_actions", pd.DataFrame()),
        )
        result.datasets["linked_accounts"] = linked_accounts
        if result.errors:
            result.report_notes["linked_accounts"] = [
                "Cast diagnostiky je best-effort a nektere read-only link resources nemusely byt dostupne."
            ]
            result.report_warning_keys.add("linked_accounts")

    if enabled_reports.get("account_diagnostics", False):
        linked_accounts = result.datasets.get("linked_accounts")
        if linked_accounts is None:
            linked_accounts = _build_linked_accounts_report(
                merchant_links=merchant_links,
                app_links=app_links,
                video_links=video_links,
                assets=datasets.get("assets", pd.DataFrame()),
                conversion_actions=datasets.get("conversion_actions", pd.DataFrame()),
            )
        result.datasets["account_diagnostics"] = _build_account_diagnostics_report(
            account=datasets.get("account", pd.DataFrame()),
            campaigns=datasets.get("campaigns", pd.DataFrame()),
            pmax_campaigns=datasets.get("pmax_campaigns", pd.DataFrame()),
            conversion_actions=datasets.get("conversion_actions", pd.DataFrame()),
            linked_accounts=linked_accounts,
        )
        result.report_notes["account_diagnostics"] = [
            "Diagnostika kombinuje prime Google Ads link resources a odvozene signaly z exportovanych dat."
        ]
        if result.errors:
            result.report_warning_keys.add("account_diagnostics")

    if enabled_reports.get("google_ads_recommendations", False):
        uplift_map = _safe_fetch(
            fetcher=fetcher,
            report=RECOMMENDATION_UPLIFT_REPORT,
            customer_id=customer_id,
            resolved_range=resolved_range,
            result=result,
        )
        recommendations = datasets.get("google_ads_recommendations", pd.DataFrame())
        if not recommendations.empty and not uplift_map.empty:
            normalized = uplift_map.loc[:, ["recommendation_type", "optimization_score_uplift"]].copy()
            normalized["recommendation_type"] = normalized["recommendation_type"].astype(str)
            recommendations = recommendations.copy()
            recommendations["recommendation_type"] = recommendations["recommendation_type"].astype(str)
            recommendations = recommendations.merge(
                normalized.drop_duplicates(subset=["recommendation_type"]),
                on="recommendation_type",
                how="left",
            )
            datasets["google_ads_recommendations"] = recommendations
            result.datasets["google_ads_recommendations"] = recommendations
        elif "google_ads_recommendations" in datasets and "optimization_score_uplift" not in datasets[
            "google_ads_recommendations"
        ].columns:
            enriched = datasets["google_ads_recommendations"].assign(
                optimization_score_uplift=None
            )
            datasets["google_ads_recommendations"] = enriched
            result.datasets["google_ads_recommendations"] = enriched

        result.report_notes["google_ads_recommendations"] = [
            "Optimization score uplift je mapovany z customer reportu segmentovaneho podle recommendation type."
        ]
        if any(error["report"] == RECOMMENDATION_UPLIFT_REPORT.key for error in result.errors):
            result.report_warning_keys.add("google_ads_recommendations")

    return result
