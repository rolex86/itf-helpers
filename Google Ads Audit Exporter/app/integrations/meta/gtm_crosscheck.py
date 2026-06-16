from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd


PIXEL_ID_PATTERNS = [
    re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d+)['\"]", re.IGNORECASE),
    re.compile(r"facebook\.com/tr\?id=(\d+)", re.IGNORECASE),
    re.compile(
        r"['\"](?:pixelId|pixel_id|facebookPixelId|facebook_pixel_id|metaPixelId|meta_pixel_id|datasetId|dataset_id)['\"]\s*[:=]\s*['\"]?(\d+)",
        re.IGNORECASE,
    ),
]

EVENT_NAME_PATTERNS = [
    re.compile(r"fbq\(\s*['\"]track(?:Custom)?['\"]\s*,\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r'"eventName"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"event_name"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"standardEventName"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"customEventName"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"event"\s*:\s*"([^"]+)"', re.IGNORECASE),
]

META_MARKERS = [
    "fbq(",
    "facebook pixel",
    "meta pixel",
    "connect.facebook.net",
    "facebook.com/tr",
    "capi",
    "conversions api",
    "facebook conversions",
    "meta conversions",
]

PIXEL_ID_KEYS = {
    "pixelId",
    "pixel_id",
    "facebookPixelId",
    "facebook_pixel_id",
    "fbPixelId",
    "fb_pixel_id",
    "metaPixelId",
    "meta_pixel_id",
    "datasetId",
    "dataset_id",
}

EVENT_NAME_KEYS = {
    "eventName",
    "event_name",
    "standardEventName",
    "standard_event_name",
    "customEventName",
    "custom_event_name",
    "event",
    "eventType",
    "event_type",
}

VALUE_KEYS = {
    "value",
    "revenue",
    "conversionValue",
    "conversion_value",
}

CURRENCY_KEYS = {
    "currency",
    "currencyCode",
    "currency_code",
}

CONTENT_IDS_KEYS = {
    "content_ids",
    "contentIds",
    "contentId",
    "content_id",
    "contents",
    "ids",
    "product_ids",
    "productIds",
}

EVENT_ID_KEYS = {
    "event_id",
    "eventId",
    "eventID",
    "deduplicationEventId",
    "deduplication_event_id",
}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _string(value).lower())


def _normalized_keys(keys: set[str]) -> set[str]:
    return {_normalize_key(key) for key in keys}


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return _string(value)


def extract_meta_pixel_id(value: object) -> str:
    text = _string(value)
    for pattern in PIXEL_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return _string(match.group(1))
    return ""


def extract_meta_event_name(value: object) -> str:
    text = _string(value)
    for pattern in EVENT_NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            return _string(match.group(1))
    return ""


def _parse_parameter_json(value: object) -> Any:
    if isinstance(value, (dict, list)):
        return value

    text = _string(value)
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _contains_any(text: str, markers: list[str] | set[str]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _first_scalar(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (str, int, float, bool)):
        return _string(value)

    if isinstance(value, dict):
        for preferred_key in ("value", "string", "template", "displayName", "name"):
            if preferred_key in value:
                found = _first_scalar(value.get(preferred_key))
                if found:
                    return found

        for nested in value.values():
            found = _first_scalar(nested)
            if found:
                return found

    if isinstance(value, list):
        for nested in value:
            found = _first_scalar(nested)
            if found:
                return found

    return ""


def _extract_nested_value(payload: Any, keys: set[str]) -> str:
    normalized_targets = _normalized_keys(keys)

    def walk(value: Any) -> str:
        if isinstance(value, dict):
            # GTM export parameters are often stored as:
            # {"key": "pixelId", "value": "123456789"}
            gtm_parameter_key = _normalize_key(value.get("key"))
            if gtm_parameter_key in normalized_targets:
                for candidate_key in ("value", "string", "template", "list", "map"):
                    if candidate_key in value:
                        found = _first_scalar(value.get(candidate_key))
                        if found:
                            return found

            # Generic nested object form:
            # {"pixelId": "123456789"} or {"eventName": "Purchase"}
            for key, nested in value.items():
                if _normalize_key(key) in normalized_targets:
                    found = _first_scalar(nested)
                    if found:
                        return found

            for nested in value.values():
                found = walk(nested)
                if found:
                    return found

        if isinstance(value, list):
            for nested in value:
                found = walk(nested)
                if found:
                    return found

        return ""

    return walk(payload)


def _nested_key_exists(payload: Any, keys: set[str]) -> bool:
    normalized_targets = _normalized_keys(keys)

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            gtm_parameter_key = _normalize_key(value.get("key"))
            if gtm_parameter_key in normalized_targets:
                return True

            for key, nested in value.items():
                if _normalize_key(key) in normalized_targets:
                    return True
                if walk(nested):
                    return True

        if isinstance(value, list):
            return any(walk(nested) for nested in value)

        return False

    return walk(payload)


def _parameter_has_signal(parameter_payload: Any, parameter_text: str, keys: set[str], markers: list[str] | set[str]) -> bool:
    if parameter_payload is not None and _nested_key_exists(parameter_payload, keys):
        return True
    return _contains_any(parameter_text, markers)


def _is_meta_tag(*, combined_text: str, parameter_payload: Any, pixel_id: str, event_name: str) -> bool:
    lowered = combined_text.lower()

    if _contains_any(lowered, META_MARKERS):
        return True

    if pixel_id:
        return True

    # CAPI / server-side templates can sometimes expose dataset/pixel id under nested
    # parameters without any fbq text, so nested key detection is also enough.
    if parameter_payload is not None and _nested_key_exists(parameter_payload, PIXEL_ID_KEYS):
        return True

    # event_name alone is too generic because GA/Ads templates can also contain it.
    # Use it only when the tag name/type text also points to Meta/Facebook.
    if event_name and _contains_any(lowered, ["facebook", "meta", "capi"]):
        return True

    return False


def parse_meta_tags_from_gtm(gtm_tags: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "tag_id",
        "tag_name",
        "tag_type",
        "pixel_id",
        "event_name",
        "trigger_name",
        "custom_html",
        "consent_settings",
        "value_present",
        "currency_present",
        "content_ids_present",
        "event_id_present",
    ]

    if gtm_tags.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []

    for _, row in gtm_tags.iterrows():
        name = _string(row.get("name"))
        tag_type = _string(row.get("type"))
        parameter_json = row.get("parameter_json") or ""
        notes = _string(row.get("notes"))

        parameter_payload = _parse_parameter_json(parameter_json)
        parameter_raw_text = _json_dumps(parameter_payload) if parameter_payload is not None else _string(parameter_json)

        combined = "\n".join([name, tag_type, parameter_raw_text, notes])
        parameter_text = parameter_raw_text.lower()

        pixel_id = extract_meta_pixel_id(combined)
        if not pixel_id and parameter_payload is not None:
            pixel_id = _extract_nested_value(parameter_payload, PIXEL_ID_KEYS)

        event_name = extract_meta_event_name(combined)
        if not event_name and parameter_payload is not None:
            event_name = _extract_nested_value(parameter_payload, EVENT_NAME_KEYS)

        if not _is_meta_tag(
            combined_text=combined,
            parameter_payload=parameter_payload,
            pixel_id=pixel_id,
            event_name=event_name,
        ):
            continue

        rows.append(
            {
                "tag_id": row.get("tag_id", ""),
                "tag_name": name,
                "tag_type": tag_type,
                "pixel_id": pixel_id,
                "event_name": event_name,
                "trigger_name": row.get("firing_trigger_ids", ""),
                "custom_html": combined,
                "consent_settings": row.get("consent_settings", ""),
                "value_present": _parameter_has_signal(
                    parameter_payload,
                    parameter_text,
                    VALUE_KEYS,
                    ["value", "revenue", "conversionvalue", "conversion_value"],
                ),
                "currency_present": _parameter_has_signal(
                    parameter_payload,
                    parameter_text,
                    CURRENCY_KEYS,
                    ["currency", "currencycode", "currency_code"],
                ),
                "content_ids_present": _parameter_has_signal(
                    parameter_payload,
                    parameter_text,
                    CONTENT_IDS_KEYS,
                    ["content_ids", "contentids", "contentid", "content_id", "contents", "product_ids", "productids"],
                ),
                "event_id_present": _parameter_has_signal(
                    parameter_payload,
                    parameter_text,
                    EVENT_ID_KEYS,
                    ["event_id", "eventid", "deduplicationeventid", "deduplication_event_id"],
                ),
            }
        )

    return pd.DataFrame(rows, columns=columns)