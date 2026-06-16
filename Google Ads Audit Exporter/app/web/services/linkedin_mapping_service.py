from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.accounts.context_config import load_account_contexts
from app.integrations.linkedin.models import LinkedInAccountContextMapping
from app.integrations.linkedin.validators import normalize_domains, validate_mapping
from app.web.services.linkedin_discovery_service import load_all_linkedin_discovery_snapshots


def mapping_path(project_root: Path) -> Path:
    return project_root / "app_state" / "linkedin_mapping.json"


def _string(value: Any) -> str:
    return str(value or "").strip()


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[\n,;]+", _string(value))
    normalized: list[str] = []
    for item in raw:
        text = _string(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _mapping_from_payload(payload: dict[str, Any], context_key: str) -> LinkedInAccountContextMapping:
    mapping = LinkedInAccountContextMapping(
        context_key=context_key,
        enabled=bool(payload.get("enabled", False)),
        connection_key=_string(payload.get("connection_key")),
        ad_account_ids=_split_list(payload.get("ad_account_ids")),
        organization_ids=_split_list(payload.get("organization_ids")),
        expected_domains=normalize_domains(_split_list(payload.get("expected_domains"))),
        expected_insight_tag_ids=_split_list(payload.get("expected_insight_tag_ids")),
        expected_conversion_ids=_split_list(payload.get("expected_conversion_ids")),
        expected_lead_form_ids=_split_list(payload.get("expected_lead_form_ids")),
        expected_utm_source=_string(payload.get("expected_utm_source")) or "linkedin",
        expected_utm_medium=_string(payload.get("expected_utm_medium")) or "paid_social",
        expected_conversion_type=_string(payload.get("expected_conversion_type")) or "lead",
        lead_sync_enabled=bool(payload.get("lead_sync_enabled", True)),
        web_scan_enabled=bool(payload.get("web_scan_enabled", True)),
        notes=_string(payload.get("notes")),
    )
    errors = validate_mapping(mapping)
    if errors:
        raise ValueError(" | ".join(errors))
    return mapping


def load_linkedin_mapping(project_root: Path) -> dict[str, LinkedInAccountContextMapping]:
    path = mapping_path(project_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("contexts", {}) if isinstance(payload, dict) else {}
    if not isinstance(rows, dict):
        return {}
    mappings: dict[str, LinkedInAccountContextMapping] = {}
    for context_key, row in rows.items():
        if isinstance(row, dict):
            mappings[context_key] = _mapping_from_payload(row, str(context_key))
    return mappings


def save_linkedin_mapping(project_root: Path, mappings: dict[str, LinkedInAccountContextMapping]) -> None:
    path = mapping_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contexts": {key: value.to_dict() for key, value in mappings.items()}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_linkedin_mapping_state(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    mappings = load_linkedin_mapping(project_root)
    snapshots = load_all_linkedin_discovery_snapshots(project_root)
    return {
        "contexts": contexts,
        "contexts_payload": [
            {
                "key": context.key,
                "label": context.label,
                "source_domains": list(context.effective_source_domains),
                "linkedin": (mappings.get(context.key) or LinkedInAccountContextMapping(context_key=context.key)).to_dict(),
            }
            for context in contexts
        ],
        "snapshots": snapshots,
    }


def save_linkedin_mapping_state(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("contexts", []) or []
    if not isinstance(items, list):
        raise ValueError("LinkedIn mapping payload musí obsahovat pole contexts.")
    mappings: dict[str, LinkedInAccountContextMapping] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        context_key = _string(row.get("key"))
        linkedin_payload = row.get("linkedin", {}) if isinstance(row.get("linkedin"), dict) else {}
        if not context_key:
            continue
        mappings[context_key] = _mapping_from_payload(linkedin_payload, context_key)
    save_linkedin_mapping(project_root, mappings)
    return load_linkedin_mapping_state(project_root)

