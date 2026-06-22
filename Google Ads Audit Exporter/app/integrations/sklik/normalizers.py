from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import pandas as pd


SANITIZED_KEYS = {
    "session",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "set-cookie",
}

DRAK_ROW_KEYS = (
    "campaigns",
    "groups",
    "ads",
    "banners",
    "keywords",
    "negativeKeywords",
    "budgets",
    "sitelinks",
    "lists",
    "retargetingLists",
    "retargetingCombinations",
    "combinations",
    "lookalikes",
    "conversions",
    "conversionTypes",
    "searchServices",
    "predefinedRegions",
    "campaignTypes",
    "history",
    "report",
    "foreignAccounts",
)

HALER_STAT_FIELDS = {
    "avgCpc",
    "avgCpt",
    "clickMoney",
    "impressionMoney",
    "totalMoney",
    "conversionPrice",
}

CZK_STAT_FIELDS = {
    "conversionValue",
}

HALER_FIELDS = {
    "dayBudget",
    "totalBudget",
    "exhaustedTotalBudget",
    "maxCpc",
    "maxCpt",
    "clickMoney",
    "impressionMoney",
    "totalMoney",
    "avgCpc",
    "value",
    "walletCredit",
    "walletCreditWithVat",
    "accountLimit",
    "dayBudgetSum",
}

CZK_VALUE_FIELDS = {
    "conversionValue",
}


def normalize_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlsplit(text).netloc.lower()
    text = text.split("/")[0].split(":")[0].strip()
    return text.removeprefix("www.")


def normalize_domains(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        candidate = normalize_domain(value)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def normalize_halers_to_czk(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def normalize_czk_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_sklik_metric(name: str, value: Any) -> float | int | None | Any:
    if value in (None, ""):
        return None

    if name in HALER_STAT_FIELDS or name in HALER_FIELDS:
        return normalize_halers_to_czk(value)

    if name in CZK_STAT_FIELDS or name in CZK_VALUE_FIELDS:
        return normalize_czk_value(value)

    return value


def normalize_iso_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def sanitize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        sanitized: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = str(key or "").strip().lower()
            if lowered in SANITIZED_KEYS or any(secret in lowered for secret in SANITIZED_KEYS):
                sanitized[str(key)] = "***"
            else:
                sanitized[str(key)] = sanitize_payload(value)
        return sanitized
    if isinstance(payload, list):
        return [sanitize_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [sanitize_payload(item) for item in payload]
    return payload


def records_to_frame(records: list[dict[str, Any]] | list[Any] | None):
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for record in records or []:
        if isinstance(record, dict):
            rows.append(record)
        else:
            rows.append({"value": record})
    return pd.DataFrame(rows)


def dataframe_to_records(dataframe: Any) -> list[dict[str, Any]]:
    if dataframe.empty:
        return []
    return json.loads(dataframe.to_json(orient="records", date_format="iso"))


def flatten_dict(value: dict[str, Any], *, prefix: str = "", sep: str = "_") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, nested_value in value.items():
        clean_key = str(key or "")
        target_key = f"{prefix}{sep}{clean_key}" if prefix else clean_key
        if isinstance(nested_value, dict):
            flattened.update(flatten_dict(nested_value, prefix=target_key, sep=sep))
        elif isinstance(nested_value, list):
            flattened[target_key] = json.dumps(nested_value, ensure_ascii=False)
        else:
            flattened[target_key] = nested_value
    return flattened


def _list_to_rows(value: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append({"value": item})
    return rows


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _list_to_rows(payload)

    if not isinstance(payload, dict):
        return []

    for key in (
        *DRAK_ROW_KEYS,
        "result",
        "results",
        "items",
        "rows",
        "data",
        "elements",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return _list_to_rows(value)

    return []


def flatten_report_rows(report_rows: list[dict[str, Any]], *, user_id: int, entity: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    for row in report_rows:
        stats = row.get("stats")
        stat_rows = stats if isinstance(stats, list) and stats else [{}]

        base = {key: value for key, value in row.items() if key != "stats"}
        flat_base = flatten_dict(base)

        for stat in stat_rows:
            flat_stat = flatten_dict(stat if isinstance(stat, dict) else {})
            record = {
                "user_id": str(user_id),
                "entity": entity,
                **flat_base,
                **flat_stat,
            }

            for field in HALER_STAT_FIELDS:
                if field in record:
                    record[f"{field}_halers"] = record[field]
                    record[f"{field}_czk"] = normalize_halers_to_czk(record[field])

            for field in CZK_STAT_FIELDS:
                if field in record:
                    record[f"{field}_raw"] = record[field]
                    record[f"{field}_czk"] = normalize_czk_value(record[field])

            if "clickMoney" in record:
                record["cost_raw"] = record["clickMoney"]
                record["cost_czk"] = normalize_halers_to_czk(record["clickMoney"])

            if "conversionValue" in record:
                record["conversion_value_raw"] = record["conversionValue"]
                record["conversion_value_czk"] = normalize_czk_value(record["conversionValue"])

            pno_ratio = normalize_czk_value(record.get("pno"))
            if pno_ratio is not None:
                record["pno_ratio"] = pno_ratio
                record["pno_percent"] = round(pno_ratio * 100.0, 2)

            output.append(record)

    return output


def normalize_campaign(row: dict[str, Any], user_id: int) -> dict[str, Any]:
    budget = row.get("budget") if isinstance(row.get("budget"), dict) else {}
    devices = row.get("devicesPriceRatio") if isinstance(row.get("devicesPriceRatio"), dict) else {}
    premises = row.get("premises") if isinstance(row.get("premises"), dict) else {}
    phone = row.get("phone") if isinstance(row.get("phone"), dict) else {}

    return {
        "user_id": str(user_id),
        "campaign_id": row.get("id"),
        "name": row.get("name"),
        "status": row.get("status"),
        "status_id": row.get("statusId"),
        "type": row.get("type"),
        "fulltext": row.get("fulltext"),
        "context": row.get("context"),
        "context_network": row.get("contextNetwork"),
        "deleted": row.get("deleted"),
        "create_date": row.get("createDate"),
        "delete_date": row.get("deleteDate"),
        "start_date": row.get("startDate"),
        "end_date": row.get("endDate"),
        "budget_id": budget.get("id") or row.get("budgetId") or row.get("defaultBudgetId"),
        "budget_name": budget.get("name"),
        "day_budget_halers": budget.get("dayBudget"),
        "day_budget_czk": normalize_halers_to_czk(budget.get("dayBudget")),
        "total_budget_halers": row.get("totalBudget"),
        "total_budget_czk": normalize_halers_to_czk(row.get("totalBudget")),
        "exhausted_total_budget_halers": row.get("exhaustedTotalBudget"),
        "exhausted_total_budget_czk": normalize_halers_to_czk(row.get("exhaustedTotalBudget")),
        "actual_clicks": row.get("actualClicks"),
        "total_clicks": row.get("totalClicks"),
        "payment_method": row.get("paymentMethod"),
        "ad_selection": row.get("adSelection"),
        "automatic_location": row.get("automaticLocation"),
        "devices_price_ratio_desktop": devices.get("desktop"),
        "devices_price_ratio_mobile": devices.get("mobile"),
        "devices_price_ratio_tablet": devices.get("tablet"),
        "devices_price_ratio_other": devices.get("other"),
        "excluded_search_services_json": json.dumps(row.get("excludedSearchServices") or [], ensure_ascii=False),
        "excluded_urls_json": json.dumps(row.get("excludedUrls") or [], ensure_ascii=False),
        "regions_json": json.dumps(row.get("regions") or [], ensure_ascii=False),
        "schedule_enabled": row.get("scheduleEnabled"),
        "schedule_json": json.dumps(row.get("schedule") or [], ensure_ascii=False),
        "phone_number": phone.get("number"),
        "phone_status": phone.get("status"),
        "phone_status_id": phone.get("statusId"),
        "premises_id": premises.get("id"),
        "default_zbozi": row.get("defaultZbozi"),
        "zbozi_bidding_type": row.get("zboziBiddingType"),
        "zbozi_premise_id": row.get("zboziPremiseId"),
    }


def normalize_conversion(row: dict[str, Any], user_id: int) -> dict[str, Any]:
    return {
        "user_id": str(user_id),
        "conversion_id": row.get("id"),
        "name": row.get("name"),
        "conversion_type_id": row.get("conversionTypeId"),
        "conversion_type_name": row.get("conversionTypeName"),
        "removed": row.get("removed"),
        "proto": row.get("proto"),
        "color": row.get("color"),
        "value_halers": row.get("value"),
        "value_czk": normalize_halers_to_czk(row.get("value")),
    }


def normalize_generic_entity(row: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    record = flatten_dict(row)
    record["user_id"] = str(user_id)

    for field in HALER_FIELDS:
        if field not in record:
            continue
        record[f"{field}_minor_units"] = record[field]
        record[f"{field}_czk"] = normalize_halers_to_czk(record[field])

    for field in CZK_VALUE_FIELDS:
        if field not in record:
            continue
        record[f"{field}_raw"] = record[field]
        record[f"{field}_czk"] = normalize_czk_value(record[field])

    if "clickMoney" in record:
        record["cost_raw"] = record["clickMoney"]
        record["cost_czk"] = normalize_halers_to_czk(record["clickMoney"])

    if "conversionValue" in record:
        record["conversion_value_raw"] = record["conversionValue"]
        record["conversion_value_czk"] = normalize_czk_value(record["conversionValue"])

    pno_ratio = normalize_czk_value(record.get("pno"))
    if pno_ratio is not None:
        record["pno_ratio"] = pno_ratio
        record["pno_percent"] = round(pno_ratio * 100.0, 2)

    if "dayBudgetSum" in record:
        record["day_budget_sum_minor_units"] = record["dayBudgetSum"]
        record["day_budget_sum_czk"] = normalize_halers_to_czk(record["dayBudgetSum"])

    return record


def normalize_fenix_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _list_to_rows(payload)
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return _list_to_rows(value)
    return []


def normalize_numeric(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number.is_integer():
        return int(number)
    return number if math.isfinite(number) else None
