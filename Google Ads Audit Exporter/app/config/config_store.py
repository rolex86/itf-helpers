from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.config.settings import DEFAULT_REPORTS


DEFAULT_CONFIG_PAYLOAD = {
    "customer_id": "",
    "date_range": {
        "preset": "LAST_90_DAYS",
        "date_from": None,
        "date_to": None,
    },
    "output": {
        "base_dir": "exports",
        "xlsx_filename": "audit_export.xlsx",
        "include_raw_csv": True,
        "include_metadata": True,
    },
    "reports": dict(DEFAULT_REPORTS),
    "flags": {
        "min_spend_micros": 100_000_000,
        "min_clicks": 50,
        "target_cpa_micros": None,
        "target_roas": None,
        "low_ctr_threshold": 0.01,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config_payload(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return _deep_merge(DEFAULT_CONFIG_PAYLOAD, {})

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Top-level config.yaml structure must be a mapping.")
    return _deep_merge(DEFAULT_CONFIG_PAYLOAD, raw)


def save_config_payload(config_path: Path, payload: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(DEFAULT_CONFIG_PAYLOAD, payload)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False, allow_unicode=False)
