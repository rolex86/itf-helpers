from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd


PARTNER_ID_PATTERNS = [
    re.compile(r"_linkedin_partner_id[^0-9]*(\d+)", re.IGNORECASE),
    re.compile(r"linkedin_partner_id[^0-9]*(\d+)", re.IGNORECASE),
    re.compile(r"partnerId[^0-9]*(\d+)", re.IGNORECASE),
    re.compile(r"partner[_-]?id[^0-9]*(\d+)", re.IGNORECASE),
]

CONVERSION_ID_PATTERNS = [
    re.compile(r"conversion[_-]?id[^0-9]*(\d+)", re.IGNORECASE),
    re.compile(r"conversionId[^0-9]*(\d+)", re.IGNORECASE),
    re.compile(r"lintrk\([^)]*conversion[_-]?id[^0-9]*(\d+)", re.IGNORECASE | re.DOTALL),
]

LINKEDIN_MARKERS = (
    "linkedin",
    "lintrk",
    "_linkedin_partner_id",
    "linkedin_partner_id",
    "window.lintrk",
    "snap.licdn.com",
    "licdn.com/li.lms-analytics",
)


def _parse_parameter_json(value: object) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _extract_match(patterns: list[re.Pattern[str]], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _extract_all(patterns: list[re.Pattern[str]], text: str) -> list[str]:
    found: list[str] = []

    for pattern in patterns:
        for match in pattern.finditer(text):
            value = str(match.group(1) or "").strip()
            if value and value not in found:
                found.append(value)

    return found


def _normalize_id_list(values: list[str]) -> list[str]:
    normalized: list[str] = []

    for value in values or []:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)

    return normalized


def _row_text(row: pd.Series) -> str:
    values: list[str] = []

    for key in (
        "tag_id",
        "name",
        "tag_name",
        "type",
        "tag_type",
        "parameter_json",
        "notes",
        "firing_trigger_ids",
        "trigger_name",
        "consent_settings",
        "custom_html",
        "html",
        "script",
    ):
        if key in row and row.get(key) not in (None, ""):
            values.append(str(row.get(key)))

    return "\n".join(values)


def _has_consent_settings(row: pd.Series, text: str) -> bool:
    consent_text = str(row.get("consent_settings") or "").lower()
    combined = f"{consent_text}\n{text.lower()}"

    return any(
        marker in combined
        for marker in (
            "ad_storage",
            "ad_user_data",
            "ad_personalization",
            "consent",
        )
    )


def _extract_trigger_name(row: pd.Series) -> str:
    return str(
        row.get("trigger_name")
        or row.get("firing_trigger_ids")
        or row.get("firingTriggerIds")
        or ""
    )


def parse_linkedin_tags_from_gtm(gtm_tags: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "tag_id",
        "tag_name",
        "tag_type",
        "partner_id",
        "conversion_id",
        "all_partner_ids",
        "all_conversion_ids",
        "trigger_name",
        "consent_settings",
        "has_consent",
        "is_insight_tag",
        "is_conversion_tag",
        "custom_html",
    ]

    if gtm_tags is None or gtm_tags.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []

    for _, row in gtm_tags.iterrows():
        name = str(row.get("name") or row.get("tag_name") or "")
        tag_type = str(row.get("type") or row.get("tag_type") or "")
        parameter_json = str(row.get("parameter_json") or "")
        notes = str(row.get("notes") or "")
        combined = _row_text(row)
        lowered = combined.lower()

        if not any(marker in lowered for marker in LINKEDIN_MARKERS):
            continue

        parameter_payload = _parse_parameter_json(parameter_json)
        parameter_text = _json_dumps(parameter_payload) if parameter_payload else combined

        all_partner_ids = _extract_all(PARTNER_ID_PATTERNS, parameter_text)
        all_conversion_ids = _extract_all(CONVERSION_ID_PATTERNS, parameter_text)

        is_insight_tag = bool(all_partner_ids) or "_linkedin_partner_id" in lowered or "insight.min.js" in lowered
        is_conversion_tag = bool(all_conversion_ids) or "lintrk(" in lowered or "window.lintrk" in lowered

        rows.append(
            {
                "tag_id": row.get("tag_id", ""),
                "tag_name": name,
                "tag_type": tag_type,
                "partner_id": all_partner_ids[0] if all_partner_ids else "",
                "conversion_id": all_conversion_ids[0] if all_conversion_ids else "",
                "all_partner_ids": ",".join(all_partner_ids),
                "all_conversion_ids": ",".join(all_conversion_ids),
                "trigger_name": _extract_trigger_name(row),
                "consent_settings": row.get("consent_settings", ""),
                "has_consent": _has_consent_settings(row, combined),
                "is_insight_tag": is_insight_tag,
                "is_conversion_tag": is_conversion_tag,
                "custom_html": combined or "\n".join([name, tag_type, parameter_json, notes]),
            }
        )

    return pd.DataFrame(rows, columns=columns)


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
            partner_id
            for _, row in parsed.iterrows()
            for partner_id in _normalize_id_list(str(row.get("all_partner_ids") or "").split(","))
            if partner_id
        }
    )
    found_conversion_ids = sorted(
        {
            conversion_id
            for _, row in parsed.iterrows()
            for conversion_id in _normalize_id_list(str(row.get("all_conversion_ids") or "").split(","))
            if conversion_id
        }
    )

    expected_partner_ids = set(_normalize_id_list(expected_insight_tag_ids))
    expected_conversions = set(_normalize_id_list(expected_conversion_ids))
    warnings: list[str] = []
    errors: list[str] = []

    if expected_partner_ids and not expected_partner_ids.intersection(found_partner_ids):
        warnings.append("V GTM nebyl nalezen očekávaný LinkedIn Insight Tag / partner ID.")

    if expected_conversions and not expected_conversions.intersection(found_conversion_ids):
        warnings.append("V GTM nebyl nalezen očekávaný LinkedIn conversion_id.")

    if not parsed.empty and "has_consent" in parsed.columns:
        missing_consent_tags = [
            str(row.get("tag_name") or row.get("tag_id") or "")
            for _, row in parsed.iterrows()
            if not bool(row.get("has_consent"))
        ]
        if missing_consent_tags:
            warnings.append(
                "Některé LinkedIn GTM tagy nemají detekované consent nastavení: "
                + ", ".join(missing_consent_tags)
            )

    matched = not warnings and not errors

    return {
        "context_key": context_key,
        "expected_domains": list(expected_domains),
        "expected_conversion_ids": list(expected_conversion_ids),
        "expected_insight_tag_ids": list(expected_insight_tag_ids),
        "found_insight_tags": [
            row
            for row in parsed.to_dict(orient="records")
            if row.get("is_insight_tag")
        ],
        "found_conversion_tags": [
            row
            for row in parsed.to_dict(orient="records")
            if row.get("is_conversion_tag")
        ],
        "found_partner_ids": found_partner_ids,
        "found_conversion_ids": found_conversion_ids,
        "matched": matched,
        "warnings": warnings,
        "errors": errors,
    }