from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd


URN_ID_PATTERN = re.compile(r":([^:()]+)(?::\d+)?$")
PII_KEY_PATTERN = re.compile(
    r"(email|e-mail|mail|phone|mobile|telephone|tel|first[_-]?name|last[_-]?name|full[_-]?name|name|company|job[_-]?title|title|address)",
    re.IGNORECASE,
)


def urn_to_id(value: object) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    if text.startswith("act_"):
        return text.replace("act_", "", 1).strip()

    match = URN_ID_PATTERN.search(text)
    if match:
        return str(match.group(1) or "").strip()

    return text


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
    if not value:
        return

    urn_key = f"{prefix}_urn"
    id_key = f"{prefix}_id"

    if urn_key not in normalized:
        normalized[urn_key] = str(value)

    if id_key not in normalized:
        normalized[id_key] = urn_to_id(value)


def normalize_entity_identifiers(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)

    direct_id = normalized.get("id")
    if direct_id:
        text_id = str(direct_id)

        if "urn:li:sponsoredAccount:" in text_id:
            _copy_urn_and_id(normalized, prefix="account", value=text_id)
        elif "urn:li:sponsoredCampaign:" in text_id:
            _copy_urn_and_id(normalized, prefix="campaign", value=text_id)
        elif "urn:li:sponsoredCreative:" in text_id:
            _copy_urn_and_id(normalized, prefix="creative", value=text_id)
        elif "urn:li:organization:" in text_id:
            _copy_urn_and_id(normalized, prefix="organization", value=text_id)
        elif "urn:li:leadGenForm:" in text_id or "urn:li:versionedLeadGenForm:" in text_id:
            _copy_urn_and_id(normalized, prefix="lead_form", value=text_id)

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
    ):
        value = normalized.get(source_key)
        _copy_urn_and_id(normalized, prefix=prefix, value=value)

    for key, value in list(normalized.items()):
        if isinstance(value, str) and value.startswith("urn:li:"):
            normalized.setdefault(f"{key}_id", urn_to_id(value))

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