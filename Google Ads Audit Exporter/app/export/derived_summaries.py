from __future__ import annotations

from typing import Any

import pandas as pd

from app.config.settings import FlagsConfig


def _to_numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    normalized = frame.copy()
    for column in columns:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0)
    return normalized


def _aggregate_summary(
    frame: pd.DataFrame,
    group_columns: list[str],
    flags_config: FlagsConfig,
    rename_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    numeric_columns = [
        "impressions",
        "clicks",
        "cost_micros",
        "conversions",
        "conversions_value",
    ]
    normalized = _to_numeric(frame, numeric_columns)
    normalized["row_count"] = 1

    grouped = (
        normalized.groupby(group_columns, dropna=False, as_index=False)
        .agg(
            {
                "impressions": "sum",
                "clicks": "sum",
                "cost_micros": "sum",
                "conversions": "sum",
                "conversions_value": "sum",
                "row_count": "sum",
            }
        )
        .sort_values(by="cost_micros", ascending=False)
    )

    grouped["average_cpc"] = grouped.apply(
        lambda row: row["cost_micros"] / row["clicks"] if row["clicks"] else 0,
        axis=1,
    )
    grouped["conversion_rate"] = grouped.apply(
        lambda row: row["conversions"] / row["clicks"] if row["clicks"] else 0,
        axis=1,
    )
    grouped["cost_per_conversion"] = grouped.apply(
        lambda row: row["cost_micros"] / row["conversions"] if row["conversions"] else 0,
        axis=1,
    )
    grouped["value_per_conversion"] = grouped.apply(
        lambda row: row["conversions_value"] / row["conversions"] if row["conversions"] else 0,
        axis=1,
    )

    filtered = grouped[
        (grouped["cost_micros"] >= flags_config.min_spend_micros)
        | (grouped["clicks"] >= flags_config.min_clicks)
    ].copy()

    if rename_map:
        filtered = filtered.rename(columns=rename_map)
    return filtered.reset_index(drop=True)


def build_landing_pages_summary(
    landing_pages: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    return _aggregate_summary(
        frame=landing_pages,
        group_columns=["expanded_final_url"],
        flags_config=flags_config,
    )


def build_locations_summary(
    locations: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    summary = _aggregate_summary(
        frame=locations,
        group_columns=["country_criterion_id", "location_type", "geo_target_name"],
        flags_config=flags_config,
    )
    if summary.empty:
        return summary

    if "geo_target_name" in summary.columns:
        summary["geo_target_name"] = summary["geo_target_name"].replace("", "(neznamy nazev lokality)")
    return summary
