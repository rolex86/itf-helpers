from __future__ import annotations

from typing import Any

import pandas as pd


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(records) if records else pd.DataFrame()


def flatten_insights_actions(record: dict[str, Any]) -> dict[str, Any]:
    actions = record.get("actions", []) or []
    action_values = record.get("action_values", []) or []
    flattened = dict(record)
    for row in actions:
        if not isinstance(row, dict):
            continue
        action_type = str(row.get("action_type") or "").strip()
        if action_type:
            flattened[f"action__{action_type}"] = row.get("value")
    for row in action_values:
        if not isinstance(row, dict):
            continue
        action_type = str(row.get("action_type") or "").strip()
        if action_type:
            flattened[f"action_value__{action_type}"] = row.get("value")
    return flattened
