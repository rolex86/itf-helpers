from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.accounts.context_config import (
    MetaContextConfig,
    load_account_contexts,
    load_merchant_parent_account_id,
    save_account_contexts,
)
from app.web.services.meta_discovery_service import load_all_meta_discovery_snapshots


EXPECTED_EVENT_ALIASES = {
    "": "",
    "purchase": "purchase",
    "purchases": "purchase",
    "nakup": "purchase",
    "nákup": "purchase",
    "lead": "lead",
    "leads": "lead",
    "form": "lead",
    "formular": "lead",
    "formulář": "lead",
    "poptavka": "lead",
    "poptávka": "lead",
    "contact": "lead",
    "kontakt": "lead",
}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, set):
        return list(value)

    text = _string(value)
    if not text:
        return []

    return [item for item in re.split(r"[\n,;]+", text) if _string(item)]


def _normalize_meta_id(value: Any, *, keep_act_prefix: bool = False) -> str:
    text = _string(value)
    if not text:
        return ""

    # Allow users to paste full ids with accidental spaces/hyphens.
    text = text.replace(" ", "").replace("-", "")

    if keep_act_prefix and text.startswith("act_"):
        suffix = re.sub(r"\D", "", text[4:])
        return f"act_{suffix}" if suffix else ""

    digits = re.sub(r"\D", "", text)
    return digits


def _normalize_id_list(value: Any, *, keep_act_prefix: bool = False) -> list[str]:
    normalized: list[str] = []

    for item in _as_list(value):
        meta_id = _normalize_meta_id(item, keep_act_prefix=keep_act_prefix)
        if meta_id and meta_id not in normalized:
            normalized.append(meta_id)

    return normalized


def _normalize_connection_key(value: Any) -> str:
    return _string(value)


def _normalize_expected_event(value: Any) -> str:
    text = _string(value).lower()
    return EXPECTED_EVENT_ALIASES.get(text, text)


def _context_payload(context) -> dict[str, Any]:
    return {
        "key": context.key,
        "label": context.label,
        "source_domains": list(context.effective_source_domains),
        "meta": {
            "enabled": context.meta.enabled,
            "connection_key": context.meta.connection_key,
            "business_id": context.meta.business_id,
            "ad_account_ids": list(context.meta.ad_account_ids),
            "pixel_ids": list(context.meta.pixel_ids),
            "catalog_ids": list(context.meta.catalog_ids),
            "product_set_ids": list(context.meta.product_set_ids),
            "expected_conversion_event": context.meta.expected_conversion_event,
        },
    }


def _mapping_from_payload(meta: dict[str, Any]) -> MetaContextConfig:
    return MetaContextConfig(
        enabled=bool(meta.get("enabled", False)),
        connection_key=_normalize_connection_key(meta.get("connection_key")),
        business_id=_normalize_meta_id(meta.get("business_id")),
        ad_account_ids=_normalize_id_list(meta.get("ad_account_ids"), keep_act_prefix=True),
        pixel_ids=_normalize_id_list(meta.get("pixel_ids")),
        catalog_ids=_normalize_id_list(meta.get("catalog_ids")),
        product_set_ids=_normalize_id_list(meta.get("product_set_ids")),
        expected_conversion_event=_normalize_expected_event(meta.get("expected_conversion_event")),
    )


def load_meta_mapping_state(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(project_root / "config.accounts.yaml")
    snapshots = load_all_meta_discovery_snapshots(project_root)

    return {
        "contexts": contexts,
        "contexts_payload": [_context_payload(context) for context in contexts],
        "snapshots": snapshots,
        "merchant_parent_account_id": load_merchant_parent_account_id(project_root / "config.accounts.yaml"),
    }


def save_meta_mapping(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    contexts_path = project_root / "config.accounts.yaml"
    contexts = load_account_contexts(contexts_path)

    items = payload.get("contexts", []) or []
    if not isinstance(items, list):
        raise ValueError("Meta mapping payload musi obsahovat pole contexts.")

    by_key = {
        _string(item.get("key")): item
        for item in items
        if isinstance(item, dict) and _string(item.get("key"))
    }

    for context in contexts:
        # Important: update only contexts explicitly included in payload.
        # Otherwise a partial UI payload could wipe existing Meta mapping for omitted contexts.
        if context.key not in by_key:
            continue

        raw = by_key.get(context.key, {})
        meta = raw.get("meta", {}) if isinstance(raw, dict) else {}

        if not isinstance(meta, dict):
            continue

        context.meta = _mapping_from_payload(meta)

    save_account_contexts(
        contexts_path,
        contexts,
        merchant_parent_account_id=load_merchant_parent_account_id(contexts_path),
    )

    return load_meta_mapping_state(project_root)