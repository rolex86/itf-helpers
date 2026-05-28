from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from app.accounts.context_config import AccountContext
from app.export.csv_exporter import export_csv
from app.export.metadata_exporter import write_json
from app.export.workflow import ExportExecutionResult
from app.export.xlsx_exporter import export_workbook


@dataclass(slots=True)
class ContextExportBundle:
    context: AccountContext
    result: ExportExecutionResult


def _normalize_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/"):
        return text.split("?", 1)[0]
    parsed = urlparse(text)
    return (parsed.path or text).split("?", 1)[0]


def _safe_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _context_columns(context: AccountContext) -> dict[str, object]:
    return {
        "context_key": context.key,
        "context_label": context.label,
        "google_ads_customer_id": context.google_ads_customer_id,
        "source_domain": context.source_domain,
    }


def _append_context(df: pd.DataFrame, context: AccountContext) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    enriched = df.copy()
    for key, value in _context_columns(context).items():
        enriched[key] = value
    return enriched


def build_cross_account_summary(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for bundle in bundles:
        campaigns = bundle.result.datasets.get("campaigns", pd.DataFrame())
        landing_pages = bundle.result.datasets.get("landing_pages", pd.DataFrame())
        rows.append(
            {
                **_context_columns(bundle.context),
                "campaign_count": int(campaigns["campaign_id"].astype(str).nunique())
                if not campaigns.empty and "campaign_id" in campaigns.columns
                else 0,
                "landing_page_count": int(len(landing_pages)),
                "error_count": len(bundle.result.errors),
                "fallback_report_count": bundle.result.fallback_report_count,
                "date_from": bundle.result.resolved_range.date_from.isoformat(),
                "date_to": bundle.result.resolved_range.date_to.isoformat(),
                "export_path": str(bundle.result.export_paths.base_dir),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "context_key",
            "context_label",
            "google_ads_customer_id",
            "source_domain",
            "campaign_count",
            "landing_page_count",
            "error_count",
            "fallback_report_count",
            "date_from",
            "date_to",
            "export_path",
        ],
    )


def build_cross_campaigns(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    frames = [_append_context(bundle.result.datasets.get("campaigns", pd.DataFrame()), bundle.context) for bundle in bundles]
    if any(not frame.empty for frame in frames):
        return pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    return pd.DataFrame(columns=["context_key", "context_label", "google_ads_customer_id", "source_domain"])


def build_cross_landing_pages(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for bundle in bundles:
        context = bundle.context
        diagnostics = bundle.result.datasets.get("landing_page_diagnostics", pd.DataFrame()).copy()
        if diagnostics.empty:
            diagnostics = bundle.result.datasets.get("landing_pages", pd.DataFrame()).copy()
            if diagnostics.empty:
                continue
            diagnostics["landing_page_url"] = diagnostics.get("expanded_final_url", "")
            diagnostics["landing_page_path"] = diagnostics["landing_page_url"].apply(_normalize_path)
            diagnostics["diagnosis"] = "ok"
            diagnostics["sessions"] = 0
            diagnostics["engagement_rate"] = 0

        gsc_pages = bundle.result.datasets.get("gsc_pages", pd.DataFrame()).copy()
        if not gsc_pages.empty:
            gsc_pages["landing_page_path"] = gsc_pages["page"].apply(_normalize_path)
            gsc_grouped = gsc_pages.groupby("landing_page_path", as_index=False).agg(
                {"clicks": "sum", "impressions": "sum"}
            )
        else:
            gsc_grouped = pd.DataFrame(columns=["landing_page_path", "clicks", "impressions"])

        pagespeed = bundle.result.datasets.get("pagespeed_landing_pages", pd.DataFrame()).copy()
        if not pagespeed.empty:
            pagespeed_pivot = (
                pagespeed.pivot_table(
                    index="url",
                    columns="strategy",
                    values="performance_score",
                    aggfunc="first",
                )
                .reset_index()
                .rename(
                    columns={
                        "url": "landing_page_url",
                        "mobile": "pagespeed_mobile_score",
                        "desktop": "pagespeed_desktop_score",
                    }
                )
            )
            pagespeed_pivot["landing_page_path"] = pagespeed_pivot["landing_page_url"].apply(_normalize_path)
        else:
            pagespeed_pivot = pd.DataFrame(
                columns=["landing_page_url", "landing_page_path", "pagespeed_mobile_score", "pagespeed_desktop_score"]
            )

        base = diagnostics.merge(gsc_grouped, on="landing_page_path", how="left", suffixes=("", "_gsc"))
        base = base.merge(
            pagespeed_pivot[["landing_page_path", "pagespeed_mobile_score", "pagespeed_desktop_score"]],
            on="landing_page_path",
            how="left",
        )

        for _, row in base.iterrows():
            cost_micros = _safe_float(row.get("cost_micros"))
            conversions = _safe_float(row.get("conversions"))
            diagnosis = str(row.get("diagnosis") or row.get("note") or "ok")
            priority = "low"
            if cost_micros >= 100_000_000 and (conversions <= 0 or diagnosis != "ok"):
                priority = "high"
            elif cost_micros > 0:
                priority = "medium"

            rows.append(
                {
                    **_context_columns(context),
                    "landing_page_url": row.get("landing_page_url") or row.get("expanded_final_url") or "",
                    "landing_page_path": row.get("landing_page_path", ""),
                    "cost_micros": cost_micros,
                    "clicks": _safe_float(row.get("clicks")),
                    "conversions": conversions,
                    "conversions_value": _safe_float(row.get("conversions_value")),
                    "ga4_sessions": _safe_float(row.get("sessions")),
                    "ga4_engagement_rate": _safe_float(row.get("engagement_rate")),
                    "gsc_clicks": _safe_float(row.get("clicks_gsc")),
                    "gsc_impressions": _safe_float(row.get("impressions_gsc")),
                    "pagespeed_mobile_score": row.get("pagespeed_mobile_score", None),
                    "pagespeed_desktop_score": row.get("pagespeed_desktop_score", None),
                    "diagnosis": diagnosis,
                    "priority": priority,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "context_key",
            "context_label",
            "google_ads_customer_id",
            "source_domain",
            "landing_page_url",
            "landing_page_path",
            "cost_micros",
            "clicks",
            "conversions",
            "conversions_value",
            "ga4_sessions",
            "ga4_engagement_rate",
            "gsc_clicks",
            "gsc_impressions",
            "pagespeed_mobile_score",
            "pagespeed_desktop_score",
            "diagnosis",
            "priority",
        ],
    )


def build_cross_product_optimization(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for bundle in bundles:
        context = bundle.context
        products = bundle.result.datasets.get("shopping_products_summary", pd.DataFrame()).copy()
        if products.empty:
            continue
        merchant_products = bundle.result.datasets.get("merchant_products", pd.DataFrame()).copy()
        if not merchant_products.empty:
            merchant_lookup = merchant_products.rename(columns={"item_id": "product_item_id"})[
                ["product_item_id", "title", "availability"]
            ].drop_duplicates(subset=["product_item_id"])
            products = products.merge(merchant_lookup, on="product_item_id", how="left")
        issues = bundle.result.datasets.get("merchant_product_issues", pd.DataFrame()).copy()
        issue_counts: dict[str, int] = {}
        if not issues.empty and "item_id" in issues.columns:
            issue_counts = issues["item_id"].astype(str).value_counts().to_dict()
        flags = bundle.result.datasets.get("product_optimization", pd.DataFrame()).copy()
        flag_lookup: dict[str, dict[str, object]] = {}
        if not flags.empty and "entity_type" in flags.columns:
            product_flags = flags[flags["entity_type"] == "product"].copy()
            for _, row in product_flags.iterrows():
                item_id = str(row.get("entity_id") or "")
                if not item_id or item_id in flag_lookup:
                    continue
                flag_lookup[item_id] = row.to_dict()

        for _, row in products.iterrows():
            item_id = str(row.get("product_item_id") or "")
            flag = flag_lookup.get(item_id, {})
            rows.append(
                {
                    **_context_columns(context),
                    "merchant_account_id": context.merchant_account_id,
                    "product_item_id": item_id,
                    "product_title": row.get("product_title") or row.get("title") or "",
                    "custom_label_0": row.get("custom_label_0", ""),
                    "cost_micros": _safe_float(row.get("cost_micros")),
                    "clicks": _safe_float(row.get("clicks")),
                    "conversions": _safe_float(row.get("conversions")),
                    "conversions_value": _safe_float(row.get("conversions_value")),
                    "merchant_issue_count": issue_counts.get(item_id, 0),
                    "availability": row.get("availability", ""),
                    "optimization_flag": flag.get("flag_type", ""),
                    "severity": flag.get("severity", ""),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "context_key",
            "context_label",
            "google_ads_customer_id",
            "source_domain",
            "merchant_account_id",
            "product_item_id",
            "product_title",
            "custom_label_0",
            "cost_micros",
            "clicks",
            "conversions",
            "conversions_value",
            "merchant_issue_count",
            "availability",
            "optimization_flag",
            "severity",
        ],
    )


def build_cross_feed_issues(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for bundle in bundles:
        frame = bundle.result.datasets.get("product_feed_issues_with_spend", pd.DataFrame())
        if frame.empty:
            frame = bundle.result.datasets.get("merchant_product_issues", pd.DataFrame())
        frame = _append_context(frame, bundle.context)
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["context_key", "context_label", "google_ads_customer_id", "source_domain"])


def build_cross_ga4_funnel(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    frames = [_append_context(bundle.result.datasets.get("ga4_ecommerce_funnel", pd.DataFrame()), bundle.context) for bundle in bundles]
    if any(not frame.empty for frame in frames):
        return pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    return pd.DataFrame(columns=["context_key", "context_label", "google_ads_customer_id", "source_domain"])


def build_cross_gsc_opportunities(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    frames = [_append_context(bundle.result.datasets.get("gsc_opportunities", pd.DataFrame()), bundle.context) for bundle in bundles]
    if any(not frame.empty for frame in frames):
        return pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    return pd.DataFrame(columns=["context_key", "context_label", "google_ads_customer_id", "source_domain"])


def build_cross_pagespeed(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    frames = [_append_context(bundle.result.datasets.get("pagespeed_landing_pages", pd.DataFrame()), bundle.context) for bundle in bundles]
    if any(not frame.empty for frame in frames):
        return pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    return pd.DataFrame(columns=["context_key", "context_label", "google_ads_customer_id", "source_domain"])


def build_cross_measurement_diagnostics(bundles: list[ContextExportBundle]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for bundle in bundles:
        frame = bundle.result.datasets.get("measurement_diagnostics", pd.DataFrame())
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            rows.append(
                {
                    "context_key": bundle.context.key,
                    "context_label": bundle.context.label,
                    "google_ads_customer_id": bundle.context.google_ads_customer_id,
                    "source_domain": bundle.context.source_domain,
                    "domain": bundle.context.source_domain,
                    "gtm_container_id": bundle.context.gtm_container_id,
                    "issue_key": row.get("diagnostic_key", ""),
                    "status": row.get("status", ""),
                    "details": row.get("details", ""),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "context_key",
            "context_label",
            "google_ads_customer_id",
            "source_domain",
            "domain",
            "gtm_container_id",
            "issue_key",
            "status",
            "details",
        ],
    )


def write_cross_account_exports(
    *,
    parent_dir: Path,
    bundles: list[ContextExportBundle],
) -> Path:
    cross_root = parent_dir / "_cross_account"
    raw_dir = cross_root / "raw"
    metadata_dir = cross_root / "metadata"
    raw_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    datasets = {
        "cross_account_summary": build_cross_account_summary(bundles),
        "cross_campaigns": build_cross_campaigns(bundles),
        "cross_landing_pages": build_cross_landing_pages(bundles),
        "cross_product_optimization": build_cross_product_optimization(bundles),
        "cross_feed_issues": build_cross_feed_issues(bundles),
        "cross_ga4_funnel": build_cross_ga4_funnel(bundles),
        "cross_gsc_opportunities": build_cross_gsc_opportunities(bundles),
        "cross_pagespeed": build_cross_pagespeed(bundles),
        "cross_measurement_diagnostics": build_cross_measurement_diagnostics(bundles),
    }

    for key, dataframe in datasets.items():
        export_csv(dataframe, raw_dir / f"{key}.csv")

    summary_rows = [
        {
            "section": "multi_export",
            "name": "context_count",
            "value": len(bundles),
            "details": "",
            "status": "ok",
            "rows": "",
        },
        {
            "section": "multi_export",
            "name": "cross_output_dir",
            "value": str(cross_root),
            "details": "",
            "status": "ok",
            "rows": "",
        },
    ]
    for bundle in bundles:
        summary_rows.append(
            {
                "section": "context",
                "name": bundle.context.key,
                "value": bundle.context.label,
                "details": str(bundle.result.export_paths.base_dir),
                "status": "error" if bundle.result.errors else "ok",
                "rows": "",
            }
        )

    export_workbook(
        xlsx_path=cross_root / "cross_account_export.xlsx",
        summary_rows=summary_rows,
        datasets={},
        flags_df=None,
        derived_sheets=[
            ("Cross summary", datasets["cross_account_summary"]),
            ("Cross campaigns", datasets["cross_campaigns"]),
            ("Cross landing pages", datasets["cross_landing_pages"]),
            ("Cross product optimization", datasets["cross_product_optimization"]),
            ("Cross feed issues", datasets["cross_feed_issues"]),
            ("Cross GA4 funnel", datasets["cross_ga4_funnel"]),
            ("Cross GSC opportunities", datasets["cross_gsc_opportunities"]),
            ("Cross PageSpeed", datasets["cross_pagespeed"]),
            ("Cross measurement diagnostics", datasets["cross_measurement_diagnostics"]),
        ],
    )

    write_json(
        metadata_dir / "cross_account_contexts.json",
        [
            {
                "context": {
                    "key": bundle.context.key,
                    "label": bundle.context.label,
                    "google_ads_customer_id": bundle.context.google_ads_customer_id,
                    "source_domain": bundle.context.source_domain,
                },
                "export_path": str(bundle.result.export_paths.base_dir),
                "error_count": len(bundle.result.errors),
            }
            for bundle in bundles
        ],
    )
    write_json(
        metadata_dir / "cross_account_summary.json",
        json.loads(datasets["cross_account_summary"].to_json(orient="records"))
        if not datasets["cross_account_summary"].empty
        else [],
    )
    return cross_root
