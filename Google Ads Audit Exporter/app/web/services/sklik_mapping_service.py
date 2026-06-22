from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.accounts.context_config import load_account_contexts
from app.integrations.sklik.connections import load_sklik_connections
from app.integrations.sklik.models import SklikAccountContextMapping
from app.integrations.sklik.sync import load_sklik_mapping
from app.integrations.sklik.validators import validate_mapping
from app.web.services.sklik_discovery_service import load_all_sklik_discovery_snapshots


def mapping_path(project_root: Path) -> Path:
    return project_root / "app_state" / "sklik_mapping.json"


def _string(value: Any) -> str:
    return str(value or "").strip()


def parse_textarea_lines(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).replace("\\n", "\n").replace(";", "\n").replace(",", "\n").splitlines()

    normalized: list[str] = []
    for item in raw:
        text = str(item or "").replace("\\n", "\n")
        for line in text.splitlines():
            clean = line.strip()
            if clean and clean not in normalized:
                normalized.append(clean)
    return normalized


def _split_list(value: Any) -> list[str]:
    return parse_textarea_lines(value)


def _mapping_from_payload(payload: dict[str, Any], context_key: str) -> SklikAccountContextMapping:
    return SklikAccountContextMapping(
        context_key=context_key,
        enabled=bool(payload.get("enabled", False)),
        connection_key=_string(payload.get("connection_key")),
        drak_user_ids=_split_list(payload.get("drak_user_ids")),
        fenix_user_ids=_split_list(payload.get("fenix_user_ids")),
        fenix_premise_ids=_split_list(payload.get("fenix_premise_ids")),
        expected_domains=_split_list(payload.get("expected_domains")),
        expected_utm_source=_split_list(payload.get("expected_utm_source")) or ["sklik", "seznam"],
        expected_utm_medium=_split_list(payload.get("expected_utm_medium")) or ["cpc", "ppc"],
        expected_sem=bool(payload.get("expected_sem", True)),
        expected_sklik_conversions=_split_list(payload.get("expected_sklik_conversions")),
        expected_retargeting_lists=_split_list(payload.get("expected_retargeting_lists")),
        enable_reporting=bool(payload.get("enable_reporting", True)),
        enable_fenix=bool(payload.get("enable_fenix", True)),
        enable_gtm_crosscheck=bool(payload.get("enable_gtm_crosscheck", True)),
        enable_web_scan=bool(payload.get("enable_web_scan", True)),
        notes=_string(payload.get("notes")),
    )


def save_sklik_mapping(project_root: Path, mappings: dict[str, SklikAccountContextMapping]) -> None:
    path = mapping_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contexts": {key: value.to_dict() for key, value in mappings.items()}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sklik_mapping_state(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    mappings = load_sklik_mapping(project_root)
    snapshots = load_all_sklik_discovery_snapshots(project_root)
    return {
        "contexts": contexts,
        "contexts_payload": [
            {
                "key": context.key,
                "label": context.label,
                "source_domains": list(context.effective_source_domains),
                "sklik": (mappings.get(context.key) or SklikAccountContextMapping(context_key=context.key)).to_dict(),
            }
            for context in contexts
        ],
        "snapshots": snapshots,
    }


def save_sklik_mapping_state(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("contexts", []) or []
    if not isinstance(items, list):
        raise ValueError("Sklik mapping payload musí obsahovat pole contexts.")

    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    connections = load_sklik_connections(project_root)
    mappings: dict[str, SklikAccountContextMapping] = {}

    for row in items:
        if not isinstance(row, dict):
            continue
        context_key = _string(row.get("key"))
        sklik_payload = row.get("sklik", {}) if isinstance(row.get("sklik"), dict) else {}
        if not context_key:
            continue
        mapping = _mapping_from_payload(sklik_payload, context_key)
        validation = validate_mapping(
            mapping,
            known_context_keys={item.key for item in contexts},
            known_connection_keys={item.key for item in connections},
        )
        if not validation.ok:
            raise ValueError(" | ".join(validation.errors))
        mappings[context_key] = mapping

    save_sklik_mapping(project_root, mappings)
    return load_sklik_mapping_state(project_root)
