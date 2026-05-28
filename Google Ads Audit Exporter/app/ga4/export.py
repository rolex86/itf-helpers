from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import FlagsConfig
from app.ga4.client import Ga4ApiClient, Ga4ApiError
from app.google_ads.report_definitions import get_report_definition
from app.utils.dates import ResolvedDateRange


GA4_REPORT_KEYS = [
    "ga4_landing_pages",
    "landing_page_diagnostics",
    "ga4_ecommerce_funnel",
]


@dataclass(slots=True)
class Ga4ExportResult:
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


def _ga4_landing_pages_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        records.append(
            {
                "landing_page": row.get("landingPage", ""),
                "session_source_medium": row.get("sessionSourceMedium", ""),
                "session_campaign_name": row.get("sessionCampaignName", ""),
                "device_category": row.get("deviceCategory", ""),
                "country": row.get("country", ""),
                "sessions": _safe_float(row.get("sessions")),
                "engaged_sessions": _safe_float(row.get("engagedSessions")),
                "engagement_rate": _safe_float(row.get("engagementRate")),
                "average_session_duration": _safe_float(row.get("averageSessionDuration")),
                "event_count": _safe_float(row.get("eventCount")),
                "key_events": _safe_float(row.get("keyEvents")),
                "total_revenue": _safe_float(row.get("totalRevenue")),
                "transactions": _safe_float(row.get("transactions")),
            }
        )
    return pd.DataFrame(records, columns=get_report_definition("ga4_landing_pages").aliases)


def _landing_page_diagnostics_frame(
    landing_pages_ads: pd.DataFrame,
    ga4_landing_pages: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    report = get_report_definition("landing_page_diagnostics")
    if landing_pages_ads.empty:
        return pd.DataFrame(columns=report.aliases)

    ga4_grouped = pd.DataFrame(columns=[])
    if not ga4_landing_pages.empty:
        normalized = ga4_landing_pages.copy()
        normalized["landing_page_path"] = normalized["landing_page"].apply(_safe_path)
        ga4_grouped = (
            normalized.groupby("landing_page_path", dropna=False, as_index=False)
            .agg(
                {
                    "sessions": "sum",
                    "engaged_sessions": "sum",
                    "event_count": "sum",
                    "key_events": "sum",
                    "total_revenue": "sum",
                    "transactions": "sum",
                    "average_session_duration": "mean",
                }
            )
        )
        ga4_grouped["engagement_rate"] = ga4_grouped.apply(
            lambda row: row["engaged_sessions"] / row["sessions"] if row["sessions"] else 0,
            axis=1,
        )

    ads = landing_pages_ads.copy()
    source_column = "expanded_final_url" if "expanded_final_url" in ads.columns else "landing_page_url"
    ads["landing_page_url"] = ads[source_column]
    ads["landing_page_path"] = ads["landing_page_url"].apply(_safe_path)
    for column in ["impressions", "clicks", "cost_micros", "conversions", "conversions_value"]:
        if column in ads.columns:
            ads[column] = pd.to_numeric(ads[column], errors="coerce").fillna(0)
    if "campaign_name" not in ads.columns:
        ads["campaign_name"] = ""
    else:
        ads["campaign_name"] = ads["campaign_name"].fillna("")
    ads = (
        ads.groupby(["landing_page_url", "landing_page_path", "campaign_name"], dropna=False, as_index=False)
        .agg(
            {
                "impressions": "sum",
                "clicks": "sum",
                "cost_micros": "sum",
                "conversions": "sum",
                "conversions_value": "sum",
            }
        )
    )
    ads = ads[
        (ads["cost_micros"] >= flags_config.min_spend_micros)
        | (ads["clicks"] >= flags_config.min_clicks)
    ].copy()

    if not ga4_grouped.empty:
        merged = ads.merge(ga4_grouped, on="landing_page_path", how="left")
    else:
        merged = ads.copy()
        for column in [
            "sessions",
            "engaged_sessions",
            "engagement_rate",
            "average_session_duration",
            "transactions",
            "total_revenue",
        ]:
            merged[column] = 0

    if "campaign_name" not in merged.columns:
        merged["campaign_name"] = ""

    diagnostics: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        cost_micros = _safe_float(row.get("cost_micros"))
        clicks = _safe_float(row.get("clicks"))
        conversions = _safe_float(row.get("conversions"))
        engagement_rate = _safe_float(row.get("engagement_rate"))
        sessions = _safe_float(row.get("sessions"))
        transactions = _safe_float(row.get("transactions"))
        diagnosis = "ok"
        severity = "info"
        note = ""

        if cost_micros >= flags_config.min_spend_micros and conversions <= 0 and engagement_rate < 0.5:
            diagnosis = "web_ux_problem"
            severity = "high"
            note = "Low conversions and low engagement suggest a landing page or UX issue."
        elif cost_micros >= flags_config.min_spend_micros and sessions <= 0:
            diagnosis = "tracking_or_match_problem"
            severity = "high"
            note = "Ad spend exists but no matching GA4 landing page sessions were found."
        elif clicks >= flags_config.min_clicks and transactions <= 0 and engagement_rate >= 0.5:
            diagnosis = "checkout_or_conversion_problem"
            severity = "medium"
            note = "Users engage with the page, but transactions do not follow."

        diagnostics.append(
            {
                "landing_page_url": row.get("landing_page_url", ""),
                "landing_page_path": row.get("landing_page_path", ""),
                "campaign_name": row.get("campaign_name", ""),
                "cost_micros": cost_micros,
                "clicks": clicks,
                "conversions": conversions,
                "conversions_value": _safe_float(row.get("conversions_value")),
                "sessions": sessions,
                "engaged_sessions": _safe_float(row.get("engaged_sessions")),
                "engagement_rate": engagement_rate,
                "average_session_duration": _safe_float(row.get("average_session_duration")),
                "transactions": transactions,
                "total_revenue": _safe_float(row.get("total_revenue")),
                "diagnosis": diagnosis,
                "severity": severity,
                "note": note,
            }
        )
    frame = pd.DataFrame(diagnostics, columns=report.aliases)
    if frame.empty:
        return frame
    return frame.sort_values(by="cost_micros", ascending=False).reset_index(drop=True)


def _event_filter(events: list[str]) -> dict[str, Any]:
    return {
        "filter": {
            "fieldName": "eventName",
            "inListFilter": {"values": events},
        }
    }


def _reshape_funnel_rows(
    rows: list[dict[str, Any]],
    *,
    breakdown_type: str,
    key_dimensions: list[str],
    label_dimension: str | None = None,
    secondary_dimension: str | None = None,
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=get_report_definition("ga4_ecommerce_funnel").aliases)

    source = pd.DataFrame(rows)
    for metric in ["eventCount", "totalRevenue", "transactions"]:
        if metric not in source.columns:
            source[metric] = 0
        source[metric] = pd.to_numeric(source[metric], errors="coerce").fillna(0)

    event_names = ["view_item", "add_to_cart", "begin_checkout", "purchase"]
    pivot = (
        source.pivot_table(
            index=key_dimensions,
            columns="eventName",
            values="eventCount",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    for event_name in event_names:
        if event_name not in pivot.columns:
            pivot[event_name] = 0

    revenue_group = (
        source.groupby(key_dimensions, dropna=False, as_index=False)[["totalRevenue", "transactions"]].sum()
    )
    merged = pivot.merge(revenue_group, on=key_dimensions, how="left").fillna(0)

    records: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        view_item = _safe_float(row.get("view_item"))
        add_to_cart = _safe_float(row.get("add_to_cart"))
        begin_checkout = _safe_float(row.get("begin_checkout"))
        purchase = _safe_float(row.get("purchase"))
        dropoff_after_view = max(view_item - add_to_cart, 0)
        dropoff_after_cart = max(add_to_cart - begin_checkout, 0)
        dropoff_after_checkout = max(begin_checkout - purchase, 0)
        purchase_rate = purchase / view_item if view_item else 0

        diagnosis = "ok"
        if view_item > 0 and add_to_cart / view_item < 0.2:
            diagnosis = "product_page_or_offer_problem"
        elif add_to_cart > 0 and begin_checkout / add_to_cart < 0.35:
            diagnosis = "cart_friction_problem"
        elif begin_checkout > 0 and purchase / begin_checkout < 0.4:
            diagnosis = "checkout_problem"

        breakdown_key = key_dimensions[1] if len(key_dimensions) > 1 else key_dimensions[0]
        records.append(
            {
                "breakdown_type": breakdown_type,
                "parent_campaign": row.get("sessionCampaignName", ""),
                "breakdown_value": row.get(breakdown_key, ""),
                "breakdown_label": row.get(label_dimension, "") if label_dimension else row.get(breakdown_key, ""),
                "secondary_value": row.get(secondary_dimension, "") if secondary_dimension else "",
                "view_item_count": view_item,
                "add_to_cart_count": add_to_cart,
                "begin_checkout_count": begin_checkout,
                "purchase_count": purchase,
                "dropoff_after_view_item": dropoff_after_view,
                "dropoff_after_add_to_cart": dropoff_after_cart,
                "dropoff_after_begin_checkout": dropoff_after_checkout,
                "purchase_rate_from_view_item": purchase_rate,
                "total_revenue": _safe_float(row.get("totalRevenue")),
                "transactions": _safe_float(row.get("transactions")),
                "diagnosis": diagnosis,
            }
        )

    frame = pd.DataFrame(records, columns=get_report_definition("ga4_ecommerce_funnel").aliases)
    return frame.sort_values(by="view_item_count", ascending=False).reset_index(drop=True)


def _reshape_product_metric_rows(
    rows: list[dict[str, Any]],
    *,
    breakdown_type: str,
    key_dimensions: list[str],
    label_dimension: str | None = None,
    secondary_dimension: str | None = None,
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=get_report_definition("ga4_ecommerce_funnel").aliases)

    records: list[dict[str, Any]] = []
    for row in rows:
        view_item = _safe_float(row.get("itemsViewed"))
        add_to_cart = _safe_float(row.get("itemsAddedToCart"))
        begin_checkout = _safe_float(row.get("itemsCheckedOut"))
        purchase = _safe_float(row.get("itemsPurchased"))
        total_revenue = _safe_float(row.get("itemRevenue"))

        dropoff_after_view = max(view_item - add_to_cart, 0)
        dropoff_after_cart = max(add_to_cart - begin_checkout, 0)
        dropoff_after_checkout = max(begin_checkout - purchase, 0)
        purchase_rate = purchase / view_item if view_item else 0

        diagnosis = "ok"
        if view_item > 0 and add_to_cart / view_item < 0.2:
            diagnosis = "product_page_or_offer_problem"
        elif add_to_cart > 0 and begin_checkout / add_to_cart < 0.35:
            diagnosis = "cart_friction_problem"
        elif begin_checkout > 0 and purchase / begin_checkout < 0.4:
            diagnosis = "checkout_problem"

        breakdown_key = key_dimensions[1] if len(key_dimensions) > 1 else key_dimensions[0]
        records.append(
            {
                "breakdown_type": breakdown_type,
                "parent_campaign": row.get("sessionCampaignName", ""),
                "breakdown_value": row.get(breakdown_key, ""),
                "breakdown_label": row.get(label_dimension, "") if label_dimension else row.get(breakdown_key, ""),
                "secondary_value": row.get(secondary_dimension, "") if secondary_dimension else "",
                "view_item_count": view_item,
                "add_to_cart_count": add_to_cart,
                "begin_checkout_count": begin_checkout,
                "purchase_count": purchase,
                "dropoff_after_view_item": dropoff_after_view,
                "dropoff_after_add_to_cart": dropoff_after_cart,
                "dropoff_after_begin_checkout": dropoff_after_checkout,
                "purchase_rate_from_view_item": purchase_rate,
                "total_revenue": total_revenue,
                "transactions": purchase,
                "diagnosis": diagnosis,
            }
        )

    frame = pd.DataFrame(records, columns=get_report_definition("ga4_ecommerce_funnel").aliases)
    return frame.sort_values(by="view_item_count", ascending=False).reset_index(drop=True)


def _run_ga4_report_or_empty(
    *,
    result: Ga4ExportResult,
    client: Ga4ApiClient,
    label: str,
    dimensions: list[str],
    metrics: list[str],
    resolved_range: ResolvedDateRange,
    dimension_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        return client.run_report(
            dimensions=dimensions,
            metrics=metrics,
            date_from=resolved_range.date_from.isoformat(),
            date_to=resolved_range.date_to.isoformat(),
            dimension_filter=dimension_filter,
        )
    except Ga4ApiError as exc:
        result.errors.append(
            {
                "report": f"ga4_{label}",
                "message": exc.message,
                "details": exc.details,
                "timestamp": _timestamp(),
            }
        )
        return []


def build_ga4_exports(
    *,
    env_config: GoogleAdsEnvConfig,
    datasets: dict[str, pd.DataFrame],
    reports_enabled: dict[str, bool],
    resolved_range: ResolvedDateRange,
    flags_config: FlagsConfig,
) -> Ga4ExportResult:
    result = Ga4ExportResult()
    enabled_report_keys = [key for key in GA4_REPORT_KEYS if reports_enabled.get(key, False)]
    if not enabled_report_keys:
        return result

    if not env_config.ga4_enabled:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["GA4 modul je vypnuty v .env."]
            result.report_warning_keys.add(key)
        return result

    if not env_config.ga4_property_id:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Chybi GA4_PROPERTY_ID v .env."]
            result.report_warning_keys.add(key)
        return result

    client = Ga4ApiClient.from_env_config(env_config)
    funnel_event_names = ["view_item", "add_to_cart", "begin_checkout", "purchase"]

    ga4_landing_rows = _run_ga4_report_or_empty(
        result=result,
        client=client,
        label="landing_pages",
        dimensions=[
            "landingPage",
            "sessionSourceMedium",
            "sessionCampaignName",
            "deviceCategory",
            "country",
        ],
        metrics=[
            "sessions",
            "engagedSessions",
            "engagementRate",
            "averageSessionDuration",
            "keyEvents",
            "totalRevenue",
        ],
        resolved_range=resolved_range,
    )

    campaign_rows = _run_ga4_report_or_empty(
        result=result,
        client=client,
        label="funnel_campaign",
        dimensions=["sessionCampaignName", "eventName"],
        metrics=["eventCount"],
        resolved_range=resolved_range,
        dimension_filter=_event_filter(funnel_event_names),
    )

    product_rows = _run_ga4_report_or_empty(
        result=result,
        client=client,
        label="funnel_product",
        dimensions=["itemId", "itemName", "itemCategory"],
        metrics=[
            "itemsViewed",
            "itemsAddedToCart",
            "itemsCheckedOut",
            "itemsPurchased",
            "itemRevenue",
        ],
        resolved_range=resolved_range,
    )

    landing_rows = _run_ga4_report_or_empty(
        result=result,
        client=client,
        label="funnel_page_path",
        dimensions=["pagePath", "eventName"],
        metrics=["eventCount"],
        resolved_range=resolved_range,
        dimension_filter=_event_filter(funnel_event_names),
    )

    device_rows = _run_ga4_report_or_empty(
        result=result,
        client=client,
        label="funnel_device",
        dimensions=["deviceCategory", "eventName"],
        metrics=["eventCount"],
        resolved_range=resolved_range,
        dimension_filter=_event_filter(funnel_event_names),
    )

    ga4_landing_pages = _ga4_landing_pages_frame(ga4_landing_rows)
    if reports_enabled.get("ga4_landing_pages", False):
        result.datasets["ga4_landing_pages"] = ga4_landing_pages
        result.report_notes["ga4_landing_pages"] = [
            "GA4 landing pages doplnuji engagement, sessions a revenue pro dalsi diagnostiku webu."
        ]
        if not ga4_landing_rows and any(error["report"] == "ga4_landing_pages" for error in result.errors):
            result.report_notes["ga4_landing_pages"] = [
                "GA4 landing pages dotaz selhal, ale ostatni GA4 dotazy mohly pokracovat dal."
            ]
            result.report_warning_keys.add("ga4_landing_pages")

    if reports_enabled.get("landing_page_diagnostics", False):
        landing_page_summary = datasets.get("landing_pages", pd.DataFrame())
        diagnostics = _landing_page_diagnostics_frame(
            landing_pages_ads=landing_page_summary,
            ga4_landing_pages=ga4_landing_pages,
            flags_config=flags_config,
        )
        result.datasets["landing_page_diagnostics"] = diagnostics
        result.report_notes["landing_page_diagnostics"] = [
            "Diagnostika spojuje spend a konverze z Ads s engagementem a transakcemi z GA4."
        ]
        if not ga4_landing_rows and any(error["report"] == "ga4_landing_pages" for error in result.errors):
            result.report_notes["landing_page_diagnostics"] = [
                "Diagnostika cilovych stranek nema GA4 landing page data, proto pracuje jen s dostupnymi Ads signaly."
            ]
            result.report_warning_keys.add("landing_page_diagnostics")

    if reports_enabled.get("ga4_ecommerce_funnel", False):
        campaign_funnel = _reshape_funnel_rows(
            campaign_rows,
            breakdown_type="campaign",
            key_dimensions=["sessionCampaignName"],
        )

        product_funnel = _reshape_product_metric_rows(
            product_rows,
            breakdown_type="product",
            key_dimensions=["itemId", "itemName", "itemCategory"],
            label_dimension="itemName",
            secondary_dimension="itemCategory",
        )

        landing_funnel = _reshape_funnel_rows(
            landing_rows,
            breakdown_type="page_path",
            key_dimensions=["pagePath"],
        )

        device_funnel = _reshape_funnel_rows(
            device_rows,
            breakdown_type="device",
            key_dimensions=["deviceCategory"],
        )

        funnel_frames = [
            frame
            for frame in [campaign_funnel, product_funnel, landing_funnel, device_funnel]
            if not frame.empty
        ]
        funnel = (
            pd.concat(funnel_frames, ignore_index=True)
            if funnel_frames
            else pd.DataFrame(columns=get_report_definition("ga4_ecommerce_funnel").aliases)
        )
        if not funnel.empty:
            funnel = funnel.sort_values(
                by=["breakdown_type", "view_item_count"],
                ascending=[True, False],
            ).reset_index(drop=True)
        result.datasets["ga4_ecommerce_funnel"] = funnel
        result.report_notes["ga4_ecommerce_funnel"] = [
            "Funnel rozlisuje propad mezi view_item, add_to_cart, begin_checkout a purchase."
        ]

        funnel_error_reports = {
            "ga4_funnel_campaign",
            "ga4_funnel_product",
            "ga4_funnel_page_path",
            "ga4_funnel_device",
        }
        if any(error["report"] in funnel_error_reports for error in result.errors):
            result.report_notes["ga4_ecommerce_funnel"].append(
                "Nektery GA4 funnel breakdown selhal, export ale zachoval ostatni dostupne GA4 funnel breakdowny."
            )
            result.report_warning_keys.add("ga4_ecommerce_funnel")

    return result
