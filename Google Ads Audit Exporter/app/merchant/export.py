from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import FlagsConfig
from app.google_ads.report_definitions import get_report_definition
from app.merchant.client import MerchantApiClient, MerchantApiError


MERCHANT_REPORT_KEYS = [
    "merchant_products",
    "merchant_product_issues",
    "merchant_product_status_summary",
    "product_optimization",
    "product_feed_issues_with_spend",
    "product_custom_label_performance",
]

PRODUCT_FLAG_COLUMNS = [
    "entity_type",
    "entity_id",
    "entity_name",
    "parent_campaign",
    "flag_type",
    "severity",
    "metric_1",
    "metric_2",
    "note",
]


@dataclass(slots=True)
class MerchantExportResult:
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


def _safe_int(value: object) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _format_price(price: dict[str, Any] | None) -> str:
    if not price:
        return ""
    amount = price.get("amountMicros") or price.get("amount") or ""
    currency = price.get("currencyCode") or ""
    if amount and str(amount).isdigit():
        normalized_amount = f"{int(amount) / 1_000_000:.2f}"
    else:
        normalized_amount = str(amount)
    return " ".join(part for part in [normalized_amount, str(currency)] if part).strip()


def _join_list(values: list[Any] | None) -> str:
    if not values:
        return ""
    return " | ".join(str(value) for value in values if value not in (None, ""))


def _name_segments(product_name: str) -> tuple[str, str, str, str]:
    if "/products/" not in product_name:
        return "", "", "", ""
    identifier = product_name.split("/products/", 1)[1]
    parts = identifier.split("~")
    while len(parts) < 4:
        parts.append("")
    channel, content_language, feed_label, offer_id = parts[:4]
    return channel, content_language, feed_label, offer_id


def _first_destination_country(destination_statuses: list[dict[str, Any]] | None) -> str:
    if not destination_statuses:
        return ""
    countries: list[str] = []
    for status in destination_statuses:
        countries.extend(status.get("countries", []) or [])
    if not countries:
        return ""
    return " | ".join(sorted(set(countries)))


def _merchant_products_frame(products: list[dict[str, Any]], merchant_account_id: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for product in products:
        channel, content_language, feed_label, inferred_offer_id = _name_segments(product.get("name", ""))
        attributes = (
            product.get("productAttributes")
            or product.get("attributes")
            or product.get("product")
            or {}
        )
        status = product.get("productStatus", {}) or {}
        offer_id = (
            attributes.get("offerId")
            or attributes.get("itemId")
            or product.get("offerId")
            or inferred_offer_id
        )
        gtin_values = attributes.get("gtins") or []
        if not gtin_values and attributes.get("gtin"):
            gtin_values = [attributes.get("gtin")]
        row = {
            "merchant_account_id": merchant_account_id,
            "offer_id": offer_id,
            "item_id": offer_id,
            "title": attributes.get("title") or product.get("title") or "",
            "description": attributes.get("description") or product.get("description") or "",
            "brand": attributes.get("brand") or "",
            "gtin": _join_list(gtin_values),
            "mpn": attributes.get("mpn") or "",
            "link": attributes.get("link") or "",
            "image_link": attributes.get("imageLink") or "",
            "price": _format_price(attributes.get("price")),
            "sale_price": _format_price(attributes.get("salePrice")),
            "availability": attributes.get("availability") or "",
            "condition": attributes.get("condition") or "",
            "google_product_category": attributes.get("googleProductCategory") or "",
            "product_type": _join_list(attributes.get("productTypes")) or attributes.get("productType") or "",
            "custom_label_0": attributes.get("customLabel0") or "",
            "custom_label_1": attributes.get("customLabel1") or "",
            "custom_label_2": attributes.get("customLabel2") or "",
            "custom_label_3": attributes.get("customLabel3") or "",
            "custom_label_4": attributes.get("customLabel4") or "",
            "channel": channel or ("local" if attributes.get("local") else "online"),
            "content_language": content_language or attributes.get("contentLanguage") or "",
            "target_country": _first_destination_country(status.get("destinationStatuses")),
            "feed_label": feed_label or attributes.get("feedLabel") or "",
            "last_update_time": status.get("lastUpdateDate") or product.get("lastUpdateTime") or "",
        }
        rows.append(row)
    return pd.DataFrame(rows, columns=get_report_definition("merchant_products").aliases)


def _issue_status(issue: dict[str, Any]) -> str:
    if issue.get("severity") == "DISAPPROVED":
        return "disapproved"
    if issue.get("severity") == "DEMOTED":
        return "limited"
    return "warning"


def _merchant_issues_frame(
    products: list[dict[str, Any]],
    merchant_account_id: str,
    shopping_summary: pd.DataFrame,
) -> pd.DataFrame:
    spend_map = _shopping_spend_map(shopping_summary)
    rows: list[dict[str, Any]] = []

    for product in products:
        channel, _, _, inferred_offer_id = _name_segments(product.get("name", ""))
        attributes = (
            product.get("productAttributes")
            or product.get("attributes")
            or product.get("product")
            or {}
        )
        status = product.get("productStatus", {}) or {}
        offer_id = (
            attributes.get("offerId")
            or attributes.get("itemId")
            or product.get("offerId")
            or inferred_offer_id
        )
        title = attributes.get("title") or product.get("title") or ""
        for issue in status.get("itemLevelIssues", []) or []:
            spend = spend_map.get(str(offer_id), {})
            rows.append(
                {
                    "merchant_account_id": merchant_account_id,
                    "item_id": offer_id,
                    "title": title,
                    "destination": issue.get("reportingContext") or channel or "",
                    "country": _join_list(issue.get("applicableCountries")),
                    "issue_code": issue.get("code") or "",
                    "issue_severity": issue.get("severity") or "",
                    "issue_title": issue.get("description") or "",
                    "issue_description": issue.get("detail") or "",
                    "documentation_link": issue.get("documentation") or "",
                    "affected_destination": issue.get("reportingContext") or "",
                    "status": _issue_status(issue),
                    "cost_micros": spend.get("cost_micros", 0),
                    "clicks": spend.get("clicks", 0),
                    "conversions": spend.get("conversions", 0),
                    "conversions_value": spend.get("conversions_value", 0),
                }
            )
    return pd.DataFrame(rows, columns=get_report_definition("merchant_product_issues").aliases)


def _merchant_status_summary_frame(
    aggregate_statuses: list[dict[str, Any]],
    merchant_account_id: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in aggregate_statuses:
        stats = item.get("stats", {}) or {}
        rows.append(
            {
                "merchant_account_id": merchant_account_id,
                "reporting_context": item.get("reportingContext") or "",
                "country": item.get("country") or "",
                "approved": _safe_int(stats.get("activeCount")),
                "pending": _safe_int(stats.get("pendingCount")),
                "disapproved": _safe_int(stats.get("disapprovedCount")),
                "expiring": _safe_int(stats.get("expiringCount")),
            }
        )
    return pd.DataFrame(rows, columns=get_report_definition("merchant_product_status_summary").aliases)


def _shopping_spend_map(shopping_summary: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if shopping_summary.empty or "product_item_id" not in shopping_summary.columns:
        return {}
    spend_map: dict[str, dict[str, Any]] = {}
    for _, row in shopping_summary.iterrows():
        spend_map[str(row.get("product_item_id") or "")] = row.to_dict()
    return spend_map


def _apply_merchant_match(
    shopping_products: pd.DataFrame,
    shopping_summary: pd.DataFrame,
    merchant_products: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if merchant_products.empty:
        if not shopping_products.empty:
            shopping_products = shopping_products.copy()
            shopping_products["missing_in_merchant_export"] = True
        if not shopping_summary.empty:
            shopping_summary = shopping_summary.copy()
            shopping_summary["missing_in_merchant_export"] = True
        return shopping_products, shopping_summary

    merchant_lookup = merchant_products.rename(columns={"item_id": "product_item_id"})
    merchant_fields = [
        "product_item_id",
        "title",
        "availability",
        "custom_label_0",
        "custom_label_1",
        "custom_label_2",
        "custom_label_3",
        "custom_label_4",
    ]
    merchant_lookup = merchant_lookup.loc[:, merchant_fields].drop_duplicates(subset=["product_item_id"])

    def _merge(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "product_item_id" not in frame.columns:
            return frame
        merged = frame.merge(
            merchant_lookup,
            on="product_item_id",
            how="left",
            suffixes=("", "_merchant"),
        )
        merged["missing_in_merchant_export"] = merged["title_merchant"].isna()
        return merged

    return _merge(shopping_products), _merge(shopping_summary)


def _product_feed_issues_with_spend(
    issues: pd.DataFrame,
    shopping_summary: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    if issues.empty:
        return _empty_report("product_feed_issues_with_spend")

    spend_map = _shopping_spend_map(shopping_summary)
    rows: list[dict[str, Any]] = []
    for _, row in issues.iterrows():
        item_id = str(row.get("item_id") or "")
        perf = spend_map.get(item_id, {})
        cost_micros = _safe_float(row.get("cost_micros") or perf.get("cost_micros"))
        clicks = _safe_float(row.get("clicks") or perf.get("clicks"))
        if cost_micros < flags_config.min_spend_micros and clicks < flags_config.min_clicks:
            continue
        rows.append(
            {
                "item_id": item_id,
                "title": row.get("title", ""),
                "issue_code": row.get("issue_code", ""),
                "issue_severity": row.get("issue_severity", ""),
                "status": row.get("status", ""),
                "cost_micros": cost_micros,
                "clicks": _safe_float(row.get("clicks") or perf.get("clicks")),
                "conversions": _safe_float(row.get("conversions") or perf.get("conversions")),
                "conversions_value": _safe_float(
                    row.get("conversions_value") or perf.get("conversions_value")
                ),
                "custom_label_0": perf.get("custom_label_0", ""),
                "custom_label_1": perf.get("custom_label_1", ""),
                "custom_label_2": perf.get("custom_label_2", ""),
                "custom_label_3": perf.get("custom_label_3", ""),
                "custom_label_4": perf.get("custom_label_4", ""),
                "note": "Feed issue on product with relevant spend.",
            }
        )
    frame = pd.DataFrame(rows, columns=get_report_definition("product_feed_issues_with_spend").aliases)
    if frame.empty:
        return frame
    return frame.sort_values(by="cost_micros", ascending=False).reset_index(drop=True)


def _product_custom_label_performance(shopping_summary: pd.DataFrame) -> pd.DataFrame:
    if shopping_summary.empty:
        return _empty_report("product_custom_label_performance")

    rows: list[dict[str, Any]] = []
    for label_index in range(5):
        label_name = f"custom_label_{label_index}"
        if label_name not in shopping_summary.columns:
            continue
        subset = shopping_summary[shopping_summary[label_name].fillna("").astype(str) != ""].copy()
        if subset.empty:
            continue
        grouped = (
            subset.groupby(label_name, dropna=False, as_index=False)
            .agg(
                {
                    "product_item_id": "nunique",
                    "impressions": "sum",
                    "clicks": "sum",
                    "cost_micros": "sum",
                    "conversions": "sum",
                    "conversions_value": "sum",
                }
            )
            .rename(columns={label_name: "label_value", "product_item_id": "item_count"})
        )
        for _, row in grouped.iterrows():
            spend = _safe_float(row.get("cost_micros")) / 1_000_000
            conversions = _safe_float(row.get("conversions"))
            clicks = _safe_float(row.get("clicks"))
            conv_value = _safe_float(row.get("conversions_value"))
            rows.append(
                {
                    "label_name": label_name,
                    "label_value": row.get("label_value", ""),
                    "item_count": row.get("item_count", 0),
                    "impressions": row.get("impressions", 0),
                    "clicks": clicks,
                    "cost_micros": row.get("cost_micros", 0),
                    "conversions": conversions,
                    "conversions_value": conv_value,
                    "average_cpc": (_safe_float(row.get("cost_micros")) / clicks) if clicks else 0,
                    "conversion_rate": conversions / clicks if clicks else 0,
                    "cost_per_conversion": (_safe_float(row.get("cost_micros")) / conversions)
                    if conversions
                    else 0,
                    "value_per_conversion": conv_value / conversions if conversions else 0,
                    "roas": conv_value / spend if spend else 0,
                }
            )
    frame = pd.DataFrame(rows, columns=get_report_definition("product_custom_label_performance").aliases)
    if frame.empty:
        return frame
    return frame.sort_values(by="cost_micros", ascending=False).reset_index(drop=True)


def _append_product_flag(target: list[dict[str, Any]], **kwargs: Any) -> None:
    target.append({column: kwargs.get(column, "") for column in PRODUCT_FLAG_COLUMNS})


def _build_product_optimization(
    shopping_summary: pd.DataFrame,
    merchant_products: pd.DataFrame,
    issues: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    if shopping_summary.empty:
        return _empty_report("product_optimization")

    flags: list[dict[str, Any]] = []
    issue_items = set(issues["item_id"].astype(str)) if not issues.empty and "item_id" in issues.columns else set()
    merchant_lookup = (
        merchant_products.set_index("item_id").to_dict("index")
        if not merchant_products.empty and "item_id" in merchant_products.columns
        else {}
    )

    median_impressions = (
        float(shopping_summary["impressions"].median())
        if "impressions" in shopping_summary.columns and not shopping_summary.empty
        else 0
    )

    for _, row in shopping_summary.iterrows():
        item_id = str(row.get("product_item_id") or "")
        title = row.get("product_title") or row.get("title") or item_id
        campaign = row.get("campaign_name", "")
        cost_micros = _safe_float(row.get("cost_micros"))
        clicks = _safe_float(row.get("clicks"))
        conversions = _safe_float(row.get("conversions"))
        conv_value = _safe_float(row.get("conversions_value"))
        impressions = _safe_float(row.get("impressions"))
        spend = cost_micros / 1_000_000
        roas = conv_value / spend if spend else 0
        availability = _normalize_text(row.get("availability") or merchant_lookup.get(item_id, {}).get("availability"))

        if cost_micros >= flags_config.min_spend_micros and clicks >= flags_config.min_clicks and conversions <= 0:
            _append_product_flag(
                flags,
                entity_type="product",
                entity_id=item_id,
                entity_name=title,
                parent_campaign=campaign,
                flag_type="high_spend_no_conversion_product",
                severity="high",
                metric_1=cost_micros,
                metric_2=conversions,
                note="Produkt ma relevantni spend bez konverzi.",
            )

        if flags_config.target_roas and cost_micros >= flags_config.min_spend_micros and roas < flags_config.target_roas:
            _append_product_flag(
                flags,
                entity_type="product",
                entity_id=item_id,
                entity_name=title,
                parent_campaign=campaign,
                flag_type="high_spend_low_roas_product",
                severity="high",
                metric_1=roas,
                metric_2=flags_config.target_roas,
                note="Produkt ma spend, ale ROAS je pod cilovou hranici.",
            )

        if flags_config.target_roas and roas >= flags_config.target_roas and impressions < median_impressions:
            _append_product_flag(
                flags,
                entity_type="product",
                entity_id=item_id,
                entity_name=title,
                parent_campaign=campaign,
                flag_type="good_roas_low_impression_product",
                severity="medium",
                metric_1=roas,
                metric_2=impressions,
                note="Produkt vypada efektivne, ale ma malo zobrazeni pro dalsi skalu.",
            )

        if item_id in issue_items and cost_micros >= flags_config.min_spend_micros:
            _append_product_flag(
                flags,
                entity_type="product",
                entity_id=item_id,
                entity_name=title,
                parent_campaign=campaign,
                flag_type="merchant_issue_with_spend",
                severity="high",
                metric_1=cost_micros,
                metric_2=roas,
                note="Produkt ma Merchant issue a zaroven relevantni spend.",
            )

        if availability.lower() in {"out of stock", "out_of_stock"} and cost_micros >= flags_config.min_spend_micros:
            _append_product_flag(
                flags,
                entity_type="product",
                entity_id=item_id,
                entity_name=title,
                parent_campaign=campaign,
                flag_type="product_out_of_stock_with_spend",
                severity="high",
                metric_1=cost_micros,
                metric_2=availability,
                note="Produkt je mimo skladovou dostupnost, ale stale se v datech objevuje spend.",
            )

        custom_labels = [row.get(f"custom_label_{index}") for index in range(5)]
        if not any(_normalize_text(value) for value in custom_labels):
            _append_product_flag(
                flags,
                entity_type="product",
                entity_id=item_id,
                entity_name=title,
                parent_campaign=campaign,
                flag_type="missing_custom_labels",
                severity="medium",
                metric_1="0",
                metric_2=cost_micros,
                note="Produkt nema vyplnene zadne custom labels.",
            )

    label_performance = _product_custom_label_performance(shopping_summary)
    if not label_performance.empty and flags_config.target_roas:
        for _, row in label_performance.iterrows():
            if _safe_float(row.get("cost_micros")) < flags_config.min_spend_micros:
                continue
            if _safe_float(row.get("roas")) >= flags_config.target_roas:
                continue
            _append_product_flag(
                flags,
                entity_type="custom_label",
                entity_id=f"{row.get('label_name')}:{row.get('label_value')}",
                entity_name=row.get("label_value", ""),
                parent_campaign="",
                flag_type="custom_label_underperforming",
                severity="medium",
                metric_1=row.get("roas", 0),
                metric_2=flags_config.target_roas,
                note="Skupina produktu podle custom labelu je pod cilovym ROAS.",
            )

    return pd.DataFrame(flags, columns=get_report_definition("product_optimization").aliases)


def build_merchant_exports(
    *,
    env_config: GoogleAdsEnvConfig,
    datasets: dict[str, pd.DataFrame],
    reports_enabled: dict[str, bool],
    flags_config: FlagsConfig,
) -> MerchantExportResult:
    result = MerchantExportResult()
    enabled_report_keys = [key for key in MERCHANT_REPORT_KEYS if reports_enabled.get(key, False)]
    if not enabled_report_keys:
        return result

    if not env_config.merchant_enabled:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Merchant API modul je vypnuty v .env."]
            result.report_warning_keys.add(key)
        return result

    if not env_config.merchant_account_id:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Chybi MERCHANT_CENTER_ACCOUNT_ID v .env."]
            result.report_warning_keys.add(key)
        return result

    client = MerchantApiClient.from_env_config(env_config)
    try:
        products = client.list_products()
        aggregate_statuses = client.list_aggregate_product_statuses()
    except MerchantApiError as exc:
        result.errors.append(
            {
                "report": "merchant_api",
                "message": exc.message,
                "details": exc.details,
                "timestamp": _timestamp(),
            }
        )
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Merchant API dotaz selhal, ale Google Ads export pokracoval dal."]
            result.report_warning_keys.add(key)
        return result

    merchant_products = _merchant_products_frame(products, env_config.merchant_account_id)
    shopping_products = datasets.get("shopping_products", _empty_report("shopping_products"))
    shopping_summary = datasets.get("shopping_products_summary", _empty_report("shopping_products_summary"))
    updated_shopping_products, updated_shopping_summary = _apply_merchant_match(
        shopping_products=shopping_products,
        shopping_summary=shopping_summary,
        merchant_products=merchant_products,
    )
    datasets["shopping_products"] = updated_shopping_products
    datasets["shopping_products_summary"] = updated_shopping_summary
    if reports_enabled.get("shopping_products", False):
        result.datasets["shopping_products"] = updated_shopping_products
    if reports_enabled.get("shopping_products_summary", False):
        result.datasets["shopping_products_summary"] = updated_shopping_summary

    merchant_issues = _merchant_issues_frame(products, env_config.merchant_account_id, updated_shopping_summary)
    status_summary = _merchant_status_summary_frame(aggregate_statuses, env_config.merchant_account_id)
    feed_issues_with_spend = _product_feed_issues_with_spend(
        merchant_issues,
        updated_shopping_summary,
        flags_config,
    )
    custom_label_performance = _product_custom_label_performance(updated_shopping_summary)
    product_optimization = _build_product_optimization(
        updated_shopping_summary,
        merchant_products,
        merchant_issues,
        flags_config,
    )

    result.report_notes["merchant_products"] = [
        "Merchant feed produkty jsou propojeny na Google Ads Shopping vykon pres product_item_id = offer_id."
    ]
    result.report_notes["merchant_product_issues"] = [
        "Issue list je rozbaleny z processed product status a doplneny o spend z Ads, pokud je k dispozici."
    ]
    result.report_notes["merchant_product_status_summary"] = [
        "Souhrn vychazi z aggregateProductStatuses v Merchant API."
    ]
    result.report_notes["product_optimization"] = [
        "Produktova pravidla kombinuji vykon v Ads a feedovy stav v Merchant Center."
    ]
    result.report_notes["product_feed_issues_with_spend"] = [
        "Tady jsou feedove problemy, ktere uz maji relevantni spend v Google Ads."
    ]
    result.report_notes["product_custom_label_performance"] = [
        "Souhrn podle custom labelu pomaha rychle najit slabe nebo silne skupiny produktu."
    ]

    if reports_enabled.get("merchant_products", False):
        result.datasets["merchant_products"] = merchant_products
    if reports_enabled.get("merchant_product_issues", False):
        result.datasets["merchant_product_issues"] = merchant_issues
    if reports_enabled.get("merchant_product_status_summary", False):
        result.datasets["merchant_product_status_summary"] = status_summary
    if reports_enabled.get("product_feed_issues_with_spend", False):
        result.datasets["product_feed_issues_with_spend"] = feed_issues_with_spend
    if reports_enabled.get("product_custom_label_performance", False):
        result.datasets["product_custom_label_performance"] = custom_label_performance
    if reports_enabled.get("product_optimization", False):
        result.datasets["product_optimization"] = product_optimization

    return result
