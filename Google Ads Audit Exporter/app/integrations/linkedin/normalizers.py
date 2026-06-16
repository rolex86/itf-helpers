from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd


URN_ID_PATTERN = re.compile(r":(\d+)(?::\d+)?$")


def urn_to_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = URN_ID_PATTERN.search(text)
    if match:
        return str(match.group(1) or "").strip()
    return text.replace("act_", "").strip()


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(records) if records else pd.DataFrame()


def iso_from_epoch_millis(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def normalize_entity_identifiers(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    for prefix, source_key in (
        ("account", "account"),
        ("account", "account_urn"),
        ("campaign", "campaign"),
        ("campaign", "campaign_urn"),
        ("creative", "creative"),
        ("creative", "creative_urn"),
        ("organization", "organization"),
        ("organization", "organization_urn"),
        ("lead_form", "lead_form_urn"),
    ):
        value = normalized.get(source_key)
        if value and f"{prefix}_id" not in normalized:
            normalized[f"{prefix}_id"] = urn_to_id(value)
        if value and f"{prefix}_urn" not in normalized:
            normalized[f"{prefix}_urn"] = str(value)
    return normalized


def sanitize_pii_for_report(payload: dict[str, Any]) -> dict[str, Any]:
    redacted_keys = {"email", "phone", "first_name", "last_name", "company", "job_title"}
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in redacted_keys:
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

