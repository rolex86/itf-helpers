from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd


PARTNER_ID_PATTERNS = [
    re.compile(r"_linkedin_partner_id[^0-9]*(\d+)", re.IGNORECASE),
    re.compile(r"partnerId[^0-9]*(\d+)", re.IGNORECASE),
]
CONVERSION_ID_PATTERNS = [
    re.compile(r"conversion[_-]?id[^0-9]*(\d+)", re.IGNORECASE),
]


def _parse_parameter_json(value: object) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_match(patterns: list[re.Pattern[str]], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def parse_linkedin_tags_from_gtm(gtm_tags: pd.DataFrame) -> pd.DataFrame:
    if gtm_tags.empty:
        return pd.DataFrame(
            columns=[
                "tag_id",
                "tag_name",
                "tag_type",
                "partner_id",
                "conversion_id",
                "trigger_name",
                "consent_settings",
                "custom_html",
            ]
        )

    rows: list[dict[str, Any]] = []
    for _, row in gtm_tags.iterrows():
        name = str(row.get("name") or "")
        tag_type = str(row.get("type") or "")
        parameter_json = str(row.get("parameter_json") or "")
        notes = str(row.get("notes") or "")
        combined = "\n".join([name, tag_type, parameter_json, notes])
        lowered = combined.lower()
        if not any(marker in lowered for marker in ("linkedin", "lintrk", "_linkedin_partner_id", "window.lintrk")):
            continue
        parameter_payload = _parse_parameter_json(parameter_json)
        parameter_text = json.dumps(parameter_payload, ensure_ascii=False) if parameter_payload else combined
        rows.append(
            {
                "tag_id": row.get("tag_id", ""),
                "tag_name": name,
                "tag_type": tag_type,
                "partner_id": _extract_match(PARTNER_ID_PATTERNS, parameter_text),
                "conversion_id": _extract_match(CONVERSION_ID_PATTERNS, parameter_text),
                "trigger_name": row.get("firing_trigger_ids", ""),
                "consent_settings": row.get("consent_settings", ""),
                "custom_html": combined,
            }
        )
    return pd.DataFrame(rows)


def build_gtm_crosscheck(
    *,
    context_key: str,
    expected_domains: list[str],
    expected_conversion_ids: list[str],
    expected_insight_tag_ids: list[str],
    gtm_tags: pd.DataFrame | None,
) -> dict[str, Any]:
    parsed = parse_linkedin_tags_from_gtm(gtm_tags if gtm_tags is not None else pd.DataFrame())
    found_partner_ids = sorted(
        {
            str(row.get("partner_id") or "").strip()
            for _, row in parsed.iterrows()
            if str(row.get("partner_id") or "").strip()
        }
    )
    found_conversion_ids = sorted(
        {
            str(row.get("conversion_id") or "").strip()
            for _, row in parsed.iterrows()
            if str(row.get("conversion_id") or "").strip()
        }
    )
    warnings: list[str] = []
    if expected_insight_tag_ids and not set(expected_insight_tag_ids).intersection(found_partner_ids):
        warnings.append("V GTM nebyl nalezen očekávaný LinkedIn Insight Tag / partner ID.")
    if expected_conversion_ids and not set(expected_conversion_ids).intersection(found_conversion_ids):
        warnings.append("V GTM nebyl nalezen očekávaný LinkedIn conversion_id.")
    return {
        "context_key": context_key,
        "expected_domains": list(expected_domains),
        "expected_conversion_ids": list(expected_conversion_ids),
        "expected_insight_tag_ids": list(expected_insight_tag_ids),
        "found_insight_tags": parsed.to_dict(orient="records"),
        "found_conversion_tags": [
            row for row in parsed.to_dict(orient="records") if row.get("conversion_id")
        ],
        "found_partner_ids": found_partner_ids,
        "matched": not warnings,
        "warnings": warnings,
        "errors": [],
    }

