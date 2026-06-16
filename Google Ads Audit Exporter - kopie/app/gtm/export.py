from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.google_ads.report_definitions import get_report_definition
from app.gtm.client import GtmApiClient, GtmApiError


GTM_REPORT_KEYS = [
    "gtm_tags",
    "gtm_triggers",
    "gtm_variables",
    "gtm_versions",
    "measurement_diagnostics",
]


@dataclass(slots=True)
class GtmExportResult:
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    report_notes: dict[str, list[str]] = field(default_factory=dict)
    report_warning_keys: set[str] = field(default_factory=set)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_report(report_key: str) -> pd.DataFrame:
    report = get_report_definition(report_key)
    return pd.DataFrame(columns=report.aliases)


def _json_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _safe_int(value: object) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _lower_text(value: object) -> str:
    return str(value or "").strip().lower()


def _parameter_payload(parameter: dict[str, Any]) -> Any:
    param_type = str(parameter.get("type") or "").lower()

    if param_type == "list":
        return [_parameter_payload(item) for item in parameter.get("list", []) or []]

    if param_type == "map":
        mapped: dict[str, Any] = {}
        for item in parameter.get("map", []) or []:
            key = str(item.get("key") or "")
            mapped[key] = _parameter_payload(item)
        return mapped

    for field_name in ("value", "template", "tagReference", "type", "triggerReference"):
        if parameter.get(field_name) not in (None, ""):
            return parameter.get(field_name)
    return ""


def _parameters_to_dict(parameters: list[dict[str, Any]] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, parameter in enumerate(parameters or []):
        key = str(parameter.get("key") or f"unnamed_{index}")
        result[key] = _parameter_payload(parameter)
    return result


def _flatten_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        collected: list[str] = []
        for nested in value.values():
            collected.extend(_flatten_values(nested))
        return collected
    if isinstance(value, list):
        collected = []
        for nested in value:
            collected.extend(_flatten_values(nested))
        return collected
    text = str(value or "").strip()
    return [text] if text else []


def _extract_nested_value(data: dict[str, Any], *keys: str) -> str:
    search_keys = {key.lower() for key in keys}

    def _walk(value: Any) -> str:
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if nested_key.lower() in search_keys:
                    flattened = _flatten_values(nested_value)
                    if flattened:
                        return flattened[0]
                found = _walk(nested_value)
                if found:
                    return found
        elif isinstance(value, list):
            for nested_value in value:
                found = _walk(nested_value)
                if found:
                    return found
        return ""

    return _walk(data)


def _workspace_choice(workspaces: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not workspaces:
        return None
    for workspace in workspaces:
        if str(workspace.get("name") or "").strip().lower() == "default workspace":
            return workspace
    return workspaces[0]


def _event_name(trigger: dict[str, Any], parameter_map: dict[str, Any]) -> str:
    direct_event = str(trigger.get("eventName") or "").strip()
    if direct_event:
        return direct_event
    return _extract_nested_value(parameter_map, "eventName", "event_name", "arg0")


def _tag_rows(tags: list[dict[str, Any]], workspace: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for tag in tags:
        parameter_map = _parameters_to_dict(tag.get("parameter"))
        rows.append(
            {
                "workspace_id": workspace.get("workspace_id", ""),
                "workspace_name": workspace.get("name", ""),
                "tag_id": tag.get("tagId", ""),
                "name": tag.get("name", ""),
                "type": tag.get("type", ""),
                "live_only": _as_bool(tag.get("liveOnly")),
                "firing_trigger_ids": " | ".join(tag.get("firingTriggerId", []) or []),
                "blocking_trigger_ids": " | ".join(tag.get("blockingTriggerId", []) or []),
                "consent_settings": _json_text(tag.get("consentSettings")),
                "schedule_start_ms": tag.get("scheduleStartMs", ""),
                "schedule_end_ms": tag.get("scheduleEndMs", ""),
                "notes": tag.get("notes", ""),
                "parameter_json": _json_text(parameter_map),
            }
        )
    return pd.DataFrame(rows, columns=get_report_definition("gtm_tags").aliases)


def _trigger_rows(triggers: list[dict[str, Any]], workspace: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trigger in triggers:
        parameter_map = _parameters_to_dict(trigger.get("parameter"))
        rows.append(
            {
                "workspace_id": workspace.get("workspace_id", ""),
                "workspace_name": workspace.get("name", ""),
                "trigger_id": trigger.get("triggerId", ""),
                "name": trigger.get("name", ""),
                "type": trigger.get("type", ""),
                "event_name": _event_name(trigger, parameter_map),
                "filter_json": _json_text(trigger.get("filter")),
                "parameter_json": _json_text(parameter_map),
            }
        )
    return pd.DataFrame(rows, columns=get_report_definition("gtm_triggers").aliases)


def _variable_rows(variables: list[dict[str, Any]], workspace: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variable in variables:
        parameter_map = _parameters_to_dict(variable.get("parameter"))
        rows.append(
            {
                "workspace_id": workspace.get("workspace_id", ""),
                "workspace_name": workspace.get("name", ""),
                "variable_id": variable.get("variableId", ""),
                "name": variable.get("name", ""),
                "type": variable.get("type", ""),
                "notes": variable.get("notes", ""),
                "parameter_json": _json_text(parameter_map),
            }
        )
    return pd.DataFrame(rows, columns=get_report_definition("gtm_variables").aliases)


def _version_rows(version_headers: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for version in version_headers:
        rows.append(
            {
                "container_version_id": version.get("containerVersionId", ""),
                "name": version.get("name", ""),
                "deleted": _as_bool(version.get("deleted")),
                "num_tags": _safe_int(version.get("numTags")),
                "num_triggers": _safe_int(version.get("numTriggers")),
                "num_variables": _safe_int(version.get("numVariables")),
                "notes": "",
            }
        )
    return pd.DataFrame(rows, columns=get_report_definition("gtm_versions").aliases)


def _has_ga4_config_tag(tags: list[dict[str, Any]]) -> bool:
    for tag in tags:
        tag_type = _lower_text(tag.get("type"))
        tag_name = _lower_text(tag.get("name"))
        parameter_map = _parameters_to_dict(tag.get("parameter"))
        measurement_id = _extract_nested_value(parameter_map, "measurementId", "tagId", "streamId")
        if measurement_id:
            return True
        if ("ga4" in tag_name and "config" in tag_name) or tag_type in {"googtag", "gaawc"}:
            return True
    return False


def _google_ads_conversion_pairs(tags: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for tag in tags:
        parameter_map = _parameters_to_dict(tag.get("parameter"))
        conversion_id = _extract_nested_value(
            parameter_map,
            "conversionId",
            "googleConversionId",
            "send_to",
        )
        conversion_label = _extract_nested_value(
            parameter_map,
            "conversionLabel",
            "googleConversionLabel",
        )
        if conversion_id or conversion_label:
            pairs.append((str(tag.get("tagId") or ""), conversion_id, conversion_label))
    return pairs


def _has_remarketing_tag(tags: list[dict[str, Any]]) -> bool:
    for tag in tags:
        tag_name = _lower_text(tag.get("name"))
        tag_type = _lower_text(tag.get("type"))
        parameter_map = _parameters_to_dict(tag.get("parameter"))
        if "remarketing" in tag_name or "remarketing" in tag_type:
            return True
        if _extract_nested_value(parameter_map, "remarketingLists", "googleConversionId"):
            return True
    return False


def _has_consent_mode(tags: list[dict[str, Any]], variables: list[dict[str, Any]]) -> bool:
    for tag in tags:
        if tag.get("consentSettings"):
            return True
        if "consent" in _lower_text(tag.get("name")) or "consent" in _lower_text(tag.get("type")):
            return True
    for variable in variables:
        if "consent" in _lower_text(variable.get("name")) or "consent" in _lower_text(variable.get("type")):
            return True
    return False


def _event_presence(tags: list[dict[str, Any]], triggers: list[dict[str, Any]], target_event: str) -> bool:
    normalized_target = target_event.strip().lower()
    for trigger in triggers:
        parameter_map = _parameters_to_dict(trigger.get("parameter"))
        if _event_name(trigger, parameter_map).strip().lower() == normalized_target:
            return True
        if normalized_target in _lower_text(trigger.get("name")):
            return True
    for tag in tags:
        parameter_map = _parameters_to_dict(tag.get("parameter"))
        if _extract_nested_value(parameter_map, "eventName", "event_name").strip().lower() == normalized_target:
            return True
        if normalized_target in _lower_text(tag.get("name")):
            return True
    return False


def _duplicate_conversion_details(tags: list[dict[str, Any]]) -> tuple[int, str]:
    pairs = _google_ads_conversion_pairs(tags)
    grouped: dict[tuple[str, str], list[str]] = {}
    for tag_id, conversion_id, conversion_label in pairs:
        if not conversion_id and not conversion_label:
            continue
        grouped.setdefault((conversion_id, conversion_label), []).append(tag_id)

    duplicates: list[str] = []
    for (conversion_id, conversion_label), tag_ids in grouped.items():
        if len(tag_ids) < 2:
            continue
        duplicates.append(
            f"{conversion_id or '-'} / {conversion_label or '-'} -> {', '.join(tag_ids)}"
        )
    return len(duplicates), " | ".join(duplicates)


def _paused_or_unused_details(tags: list[dict[str, Any]]) -> tuple[int, str]:
    flagged: list[str] = []
    for tag in tags:
        name = str(tag.get("name") or tag.get("tagId") or "tag")
        if _as_bool(tag.get("paused")):
            flagged.append(f"{name} (paused)")
            continue
        firing_ids = tag.get("firingTriggerId", []) or []
        if not firing_ids:
            flagged.append(f"{name} (no firing trigger)")
    return len(flagged), " | ".join(flagged[:10])


def _version_age_note(version_headers: list[dict[str, Any]]) -> tuple[str, str]:
    if not version_headers:
        return (
            "warning",
            "V GTM nebyla nalezena zadna ulozena verze kontejneru.",
        )
    return (
        "info",
        "GTM version headers nevraci datum publikace, takze stari publikovane verze nelze spolehlive zhodnotit automaticky.",
    )


def _diagnostic_rows(
    *,
    tags: list[dict[str, Any]],
    triggers: list[dict[str, Any]],
    variables: list[dict[str, Any]],
    version_headers: list[dict[str, Any]],
) -> pd.DataFrame:
    duplicate_count, duplicate_details = _duplicate_conversion_details(tags)
    paused_or_unused_count, paused_or_unused_details = _paused_or_unused_details(tags)
    version_status, version_details = _version_age_note(version_headers)
    conversion_pairs = _google_ads_conversion_pairs(tags)

    rows = [
        {
            "diagnostic_key": "ga4_config_tag_exists",
            "label": "GA4 konfiguracni tag",
            "value": "true" if _has_ga4_config_tag(tags) else "false",
            "status": "ok" if _has_ga4_config_tag(tags) else "warning",
            "details": "Kontrola hleda GTM tag s GA4 config signaly nebo measurement ID.",
        },
        {
            "diagnostic_key": "google_ads_conversion_tag_exists",
            "label": "Google Ads konverzni tag",
            "value": "true" if bool(conversion_pairs) else "false",
            "status": "ok" if conversion_pairs else "warning",
            "details": "Kontrola hleda conversion ID a conversion label v GTM tag parametrech.",
        },
        {
            "diagnostic_key": "remarketing_tag_exists",
            "label": "Remarketing tag",
            "value": "true" if _has_remarketing_tag(tags) else "false",
            "status": "ok" if _has_remarketing_tag(tags) else "warning",
            "details": "Kontrola je best-effort podle typu, nazvu a parametru tagu.",
        },
        {
            "diagnostic_key": "consent_mode_present",
            "label": "Consent Mode",
            "value": "true" if _has_consent_mode(tags, variables) else "false",
            "status": "ok" if _has_consent_mode(tags, variables) else "warning",
            "details": "Kontrola hleda consent settings nebo tagy a promenne souvisejici s consentem.",
        },
        {
            "diagnostic_key": "purchase_event_exists",
            "label": "Purchase event",
            "value": "true" if _event_presence(tags, triggers, "purchase") else "false",
            "status": "ok" if _event_presence(tags, triggers, "purchase") else "warning",
            "details": "Kontrola hleda purchase event v triggerech a tag parametrech.",
        },
        {
            "diagnostic_key": "lead_event_exists",
            "label": "Lead event",
            "value": "true" if _event_presence(tags, triggers, "lead") else "false",
            "status": "ok" if _event_presence(tags, triggers, "lead") else "warning",
            "details": "Kontrola hleda lead event v triggerech a tag parametrech.",
        },
        {
            "diagnostic_key": "duplicate_conversion_tags",
            "label": "Duplicitni konverzni tagy",
            "value": str(duplicate_count),
            "status": "warning" if duplicate_count else "ok",
            "details": duplicate_details or "Nebyla nalezena zadna zjevna duplicita conversion ID + label.",
        },
        {
            "diagnostic_key": "paused_or_unused_tags",
            "label": "Pozastavene nebo nepouzite tagy",
            "value": str(paused_or_unused_count),
            "status": "warning" if paused_or_unused_count else "ok",
            "details": paused_or_unused_details or "Nebyl nalezen zadny paused tag ani tag bez triggeru.",
        },
        {
            "diagnostic_key": "old_published_version",
            "label": "Stari publikovane verze",
            "value": "manual_review",
            "status": version_status,
            "details": version_details,
        },
    ]
    return pd.DataFrame(rows, columns=get_report_definition("measurement_diagnostics").aliases)


def build_gtm_exports(
    *,
    env_config: GoogleAdsEnvConfig,
    reports_enabled: dict[str, bool],
) -> GtmExportResult:
    result = GtmExportResult()
    enabled_report_keys = [key for key in GTM_REPORT_KEYS if reports_enabled.get(key, False)]
    if not enabled_report_keys:
        return result

    if not env_config.gtm_enabled:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["GTM modul je vypnutý v .env."]
            result.report_warning_keys.add(key)
        return result

    if not env_config.gtm_account_id or not env_config.gtm_container_id:
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["Chybi GTM_ACCOUNT_ID nebo GTM_CONTAINER_ID v .env."]
            result.report_warning_keys.add(key)
        return result

    client = GtmApiClient.from_env_config(env_config)

    try:
        workspaces = client.list_workspaces()
        selected_workspace = _workspace_choice(workspaces)
        if selected_workspace is None:
            raise GtmApiError("V GTM containeru nebyl nalezen zadny workspace.", status_code=404)

        workspace_path = str(selected_workspace.get("path") or "").strip()
        tags = client.list_tags(workspace_path) if workspace_path else []
        triggers = client.list_triggers(workspace_path) if workspace_path else []
        variables = client.list_variables(workspace_path) if workspace_path else []
        version_payload = client.list_versions()
        version_headers = version_payload.get("containerVersionHeader", [])
    except GtmApiError as exc:
        result.errors.append(
            {
                "report": "gtm_api",
                "message": exc.message,
                "details": exc.details,
                "timestamp": _timestamp(),
            }
        )
        for key in enabled_report_keys:
            result.datasets[key] = _empty_report(key)
            result.report_notes[key] = ["GTM API dotaz selhal, ale zbytek exportu pokracoval dal."]
            result.report_warning_keys.add(key)
        return result

    gtm_tags = _tag_rows(tags, selected_workspace)
    gtm_triggers = _trigger_rows(triggers, selected_workspace)
    gtm_variables = _variable_rows(variables, selected_workspace)
    gtm_versions = _version_rows(version_headers)
    diagnostics = _diagnostic_rows(
        tags=tags,
        triggers=triggers,
        variables=variables,
        version_headers=version_headers,
    )

    result.report_notes["gtm_tags"] = [
        f"Tagy jsou nacitane read-only z workspace '{selected_workspace.get('name', '') or 'workspace'}'."
    ]
    result.report_notes["gtm_triggers"] = [
        "Triggery jsou vypsane read-only z aktualne vybraneho GTM workspace."
    ]
    result.report_notes["gtm_variables"] = [
        "Promenne jsou vypsane read-only z aktualne vybraneho GTM workspace."
    ]
    result.report_notes["gtm_versions"] = [
        "Verze vychazeji z GTM containerVersionHeader endpointu bez jakehokoli publish nebo update kroku."
    ]
    result.report_notes["measurement_diagnostics"] = [
        "Diagnostika mereni je heuristicka a nema zadny write-back do GTM."
    ]

    if reports_enabled.get("gtm_tags", False):
        result.datasets["gtm_tags"] = gtm_tags
    if reports_enabled.get("gtm_triggers", False):
        result.datasets["gtm_triggers"] = gtm_triggers
    if reports_enabled.get("gtm_variables", False):
        result.datasets["gtm_variables"] = gtm_variables
    if reports_enabled.get("gtm_versions", False):
        result.datasets["gtm_versions"] = gtm_versions
    if reports_enabled.get("measurement_diagnostics", False):
        result.datasets["measurement_diagnostics"] = diagnostics
        if any(status == "warning" for status in diagnostics.get("status", pd.Series(dtype=str)).tolist()):
            result.report_warning_keys.add("measurement_diagnostics")

    return result
