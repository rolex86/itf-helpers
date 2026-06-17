from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd


PII_KEY_PATTERN = re.compile(
    r"(email|e-mail|mail|phone|mobile|telephone|tel|first[_-]?name|last[_-]?name|full[_-]?name|name|company|job[_-]?title|title|address)",
    re.IGNORECASE,
)


URN_PREFIX_TO_FIELD_PREFIX = {
    "sponsoredAccount": "account",
    "sponsoredCampaign": "campaign",
    "sponsoredCampaignGroup": "campaign_group",
    "sponsoredCreative": "creative",
    "organization": "organization",
    "leadGenForm": "lead_form",
    "versionedLeadGenForm": "lead_form",
    "conversion": "conversion",
    "insightTag": "insight_tag",
    "leadGenFormResponse": "lead_form_response",
}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _split_urn(value: object) -> list[str]:
    text = _string(value)
    return text.split(":") if text.startswith("urn:li:") else []


def urn_to_id(value: object) -> str:
    text = _string(value)

    if not text:
        return ""

    if text.startswith("act_"):
        return text.replace("act_", "", 1).strip()

    parts = _split_urn(text)
    if not parts:
        return text

    entity_type = parts[2] if len(parts) >= 3 else ""

    if entity_type == "versionedLeadGenForm":
        # LinkedIn may return values like:
        # urn:li:versionedLeadGenForm:(urn:li:leadGenForm:123,2)
        match = re.search(r"leadGenForm:(\d+)", text)
        if match:
            return match.group(1)

    if entity_type == "leadGenForm" and len(parts) >= 5:
        # Versioned lead form URNs may look like urn:li:leadGenForm:123:2.
        # For entity matching we want the base lead form id, not the version.
        return parts[-2].strip()

    if len(parts) >= 4:
        return parts[-1].strip("() ")

    return text


def urn_to_entity_type(value: object) -> str:
    parts = _split_urn(value)
    return parts[2] if len(parts) >= 3 else ""


def urn_to_field_prefix(value: object) -> str:
    return URN_PREFIX_TO_FIELD_PREFIX.get(urn_to_entity_type(value), "")


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(records) if records else pd.DataFrame()


def iso_from_epoch_millis(value: object) -> str:
    if value in (None, ""):
        return ""

    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _copy_urn_and_id(
    normalized: dict[str, Any],
    *,
    prefix: str,
    value: Any,
) -> None:
    text = _string(value)
    if not text:
        return

    urn_key = f"{prefix}_urn"
    id_key = f"{prefix}_id"

    if text.startswith("urn:li:") and urn_key not in normalized:
        normalized[urn_key] = text

    if id_key not in normalized:
        normalized[id_key] = urn_to_id(text)


def _copy_generic_urn_id(
    normalized: dict[str, Any],
    *,
    key: str,
    value: Any,
) -> None:
    text = _string(value)
    if not text.startswith("urn:li:"):
        return

    generic_id_key = f"{key}_id"
    if generic_id_key not in normalized:
        normalized[generic_id_key] = urn_to_id(text)


def normalize_entity_identifiers(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)

    direct_id = _string(normalized.get("id"))
    direct_prefix = urn_to_field_prefix(direct_id)
    if direct_prefix:
        _copy_urn_and_id(normalized, prefix=direct_prefix, value=direct_id)

    for prefix, source_key in (
        ("account", "account"),
        ("account", "account_urn"),
        ("account", "sponsoredAccount"),
        ("account", "sponsored_account"),
        ("account", "sponsoredAccountUrn"),
        ("campaign", "campaign"),
        ("campaign", "campaign_urn"),
        ("campaign", "sponsoredCampaign"),
        ("campaign", "sponsored_campaign"),
        ("campaign", "sponsoredCampaignUrn"),
        ("campaign_group", "campaignGroup"),
        ("campaign_group", "campaign_group"),
        ("campaign_group", "campaign_group_urn"),
        ("campaign_group", "campaignGroupUrn"),
        ("creative", "creative"),
        ("creative", "creative_urn"),
        ("creative", "sponsoredCreative"),
        ("creative", "sponsored_creative"),
        ("creative", "sponsoredCreativeUrn"),
        ("organization", "organization"),
        ("organization", "organization_urn"),
        ("organization", "organizationUrn"),
        ("lead_form", "lead_form_urn"),
        ("lead_form", "leadGenForm"),
        ("lead_form", "leadGenFormUrn"),
        ("lead_form", "versionedLeadGenForm"),
        ("lead_form", "versionedLeadGenFormUrn"),
        ("conversion", "conversion"),
        ("conversion", "conversion_urn"),
        ("insight_tag", "insightTag"),
        ("insight_tag", "insight_tag_urn"),
        ("lead_form_response", "leadGenFormResponse"),
        ("lead_form_response", "lead_form_response_urn"),
    ):
        value = normalized.get(source_key)
        _copy_urn_and_id(normalized, prefix=prefix, value=value)

    for key, value in list(normalized.items()):
        if isinstance(value, str) and value.startswith("urn:li:"):
            _copy_generic_urn_id(normalized, key=key, value=value)

            detected_prefix = urn_to_field_prefix(value)
            if detected_prefix:
                _copy_urn_and_id(normalized, prefix=detected_prefix, value=value)

    return normalized


def sanitize_pii_for_report(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}

    for key, value in payload.items():
        if PII_KEY_PATTERN.search(str(key)):
            sanitized[key] = "***"
        elif isinstance(value, dict):
            sanitized[key] = sanitize_pii_for_report(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_pii_for_report(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value

    return sanitized


def json_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)