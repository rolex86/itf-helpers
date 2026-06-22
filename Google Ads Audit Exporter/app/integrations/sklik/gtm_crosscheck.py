from __future__ import annotations

import re
from typing import Any

import pandas as pd


# Strong Sklik/Seznam signals only.
# Do not include generic words like conversion, purchase, lead, retargeting or sem,
# because those also appear in Google Ads / LinkedIn / Meta tags.
SKLIK_MARKERS = (
    "sklik",
    "seznam",
    "sul.js",
    "seznam_event_measurement",
    "seznam event measurement",
    "c.seznam.cz",
    "h.seznam.cz",
    "c.imedia.cz",
    "imedia.cz",
)

SEZNAM_SCRIPT_HOSTS = (
    "c.seznam.cz",
    "h.seznam.cz",
    "c.imedia.cz",
    "imedia.cz",
)

# Known Sklik/Seznam custom template IDs seen in GTM exports.
# Keep this list narrow so we do not mark unrelated custom templates as Sklik.
SKLIK_CUSTOM_TEMPLATE_IDS = {
    "cvt_8168974_152",
}


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


def _tag_type(row: pd.Series) -> str:
    return str(row.get("type") or row.get("tag_type") or "").strip()


def _tag_name(row: pd.Series) -> str:
    return str(row.get("name") or row.get("tag_name") or "").strip()


def _has_consent_signal(text: str) -> bool:
    lowered = text.lower()
    return (
        "consent" in lowered
        or "ad_storage" in lowered
        or "ad_user_data" in lowered
        or "ad_personalization" in lowered
    )


def _tag_names(rows: pd.DataFrame, *, limit: int = 10) -> str:
    names = [
        str(value or "").strip()
        for value in rows.get("tag_name", pd.Series(dtype=str)).tolist()
        if str(value or "").strip()
    ]
    return ", ".join(names[:limit])


def parse_sklik_tags_from_gtm(gtm_tags: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "tag_id",
        "tag_name",
        "tag_type",
        "has_sem",
        "has_sklik_custom_template",
        "has_sklik_conversion_template",
        "has_old_conversion_script",
        "has_old_retargeting_script",
        "has_valid_sklik_tag",
        "has_consent",
        "trigger_name",
        "custom_html",
    ]
    if gtm_tags is None or gtm_tags.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for _, row in gtm_tags.iterrows():
        text = _row_text(row)
        lowered = text.lower()
        tag_type = _tag_type(row)
        tag_type_lower = tag_type.lower()

        has_known_sklik_template = tag_type_lower in SKLIK_CUSTOM_TEMPLATE_IDS
        has_sklik_marker = any(marker in lowered for marker in SKLIK_MARKERS)

        if not has_sklik_marker and not has_known_sklik_template:
            continue

        has_sem = (
            "sul.js" in lowered
            or "seznam event measurement" in lowered
            or "seznam_event_measurement" in lowered
        )

        has_old_retargeting_script = (
            "c.seznam.cz/js/rc.js" in lowered
            or (
                "retargeting" in lowered
                and any(host in lowered for host in SEZNAM_SCRIPT_HOSTS)
            )
        )

        has_old_conversion_script = (
            any(host in lowered for host in SEZNAM_SCRIPT_HOSTS)
            and not has_old_retargeting_script
        )

        has_sklik_custom_template = has_known_sklik_template or (
            tag_type_lower.startswith("cvt_")
            and ("sklik" in lowered or "seznam" in lowered)
        )

        has_conversion_signal = any(
            marker in lowered
            for marker in (
                "conversion",
                "konverz",
                "purchase",
                "transaction",
                "transak",
                "objedn",
                "lead",
            )
        )

        has_sklik_conversion_template = bool(has_sklik_custom_template and has_conversion_signal)

        has_valid_sklik_tag = bool(
            has_sem
            or has_sklik_conversion_template
            or has_old_conversion_script
        )

        rows.append(
            {
                "tag_id": row.get("tag_id", ""),
                "tag_name": _tag_name(row),
                "tag_type": tag_type,
                "has_sem": has_sem,
                "has_sklik_custom_template": has_sklik_custom_template,
                "has_sklik_conversion_template": has_sklik_conversion_template,
                "has_old_conversion_script": has_old_conversion_script,
                "has_old_retargeting_script": has_old_retargeting_script,
                "has_valid_sklik_tag": has_valid_sklik_tag,
                "has_consent": _has_consent_signal(text),
                "trigger_name": row.get("trigger_name") or row.get("firing_trigger_ids") or "",
                "custom_html": text,
            }
        )

    return pd.DataFrame(rows, columns=columns)


def build_gtm_crosscheck(
    *,
    context_key: str,
    expected_domains: list[str],
    gtm_tags: pd.DataFrame | None,
) -> dict[str, Any]:
    parsed = parse_sklik_tags_from_gtm(gtm_tags if gtm_tags is not None else pd.DataFrame())
    warnings: list[str] = []
    errors: list[str] = []
    infos: list[str] = []

    if parsed.empty:
        warnings.append("GTM export nenašel žádné Sklik/Seznam tagy.")
        return {
            "context_key": context_key,
            "expected_domains": list(expected_domains),
            "matched": False,
            "warnings": warnings,
            "errors": errors,
            "infos": infos,
            "found_tags": parsed.to_dict(orient="records"),
            "raw_tag_count": int(len(parsed.index)),
            "has_valid_sklik_tag": False,
            "has_sem_tag": False,
            "has_sklik_conversion_template": False,
            "has_legacy_conversion_script": False,
            "has_legacy_retargeting_script": False,
        }

    has_valid_sklik_tag = bool(parsed["has_valid_sklik_tag"].any())
    has_sem_tag = bool(parsed["has_sem"].any())
    has_sklik_conversion_template = bool(parsed["has_sklik_conversion_template"].any())
    has_legacy_conversion_script = bool(parsed["has_old_conversion_script"].any())
    has_legacy_retargeting_script = bool(parsed["has_old_retargeting_script"].any())

    if has_sklik_conversion_template:
        template_tags = parsed[parsed["has_sklik_conversion_template"]]
        names = _tag_names(template_tags)
        infos.append(
            "GTM obsahuje Sklik custom template conversion tag"
            + (f": {names}" if names else ".")
        )

    if has_sem_tag:
        sem_tags = parsed[parsed["has_sem"]]
        names = _tag_names(sem_tags)
        infos.append(
            "GTM obsahuje nový Sklik/Seznam SEM tag"
            + (f": {names}" if names else ".")
        )

    if not has_valid_sklik_tag:
        warnings.append(
            "GTM obsahuje Sklik/Seznam tagy, ale nebyl detekovaný validní Sklik conversion/SEM tag."
        )

    missing_consent = parsed[
        parsed["has_valid_sklik_tag"] & ~parsed["has_consent"]
    ]
    if not missing_consent.empty:
        names = _tag_names(missing_consent)
        warnings.append(
            "Některé validní Sklik/Seznam GTM tagy nemají detekované consent nastavení"
            + (f": {names}" if names else ".")
        )

    legacy_conversion = parsed[parsed["has_old_conversion_script"]]
    if not legacy_conversion.empty:
        names = _tag_names(legacy_conversion)
        warnings.append(
            "GTM obsahuje legacy Seznam konverzní skripty ke kontrole / náhradě"
            + (f": {names}" if names else ".")
        )

    legacy_retargeting = parsed[parsed["has_old_retargeting_script"]]
    if not legacy_retargeting.empty:
        names = _tag_names(legacy_retargeting)
        warnings.append(
            "GTM obsahuje legacy Sklik retargeting HTML tagy ke kontrole / náhradě"
            + (f": {names}" if names else ".")
        )

    # "matched" answers the narrow question: did we find any Sklik/Seznam GTM tag?
    # It must stay true even when we also report a legacy-tag warning, otherwise
    # audit_rules can incorrectly produce "GTM chybí Sklik tagy".
    matched = bool(
        has_valid_sklik_tag
        or has_legacy_retargeting_script
        or has_legacy_conversion_script
    )

    return {
        "context_key": context_key,
        "expected_domains": list(expected_domains),
        "matched": matched,
        "warnings": warnings,
        "errors": errors,
        "infos": infos,
        "found_tags": parsed.to_dict(orient="records"),
        "raw_tag_count": int(len(parsed.index)),
        "has_valid_sklik_tag": has_valid_sklik_tag,
        "has_sem_tag": has_sem_tag,
        "has_sklik_conversion_template": has_sklik_conversion_template,
        "has_legacy_conversion_script": has_legacy_conversion_script,
        "has_legacy_retargeting_script": has_legacy_retargeting_script,
    }