from __future__ import annotations

import pandas as pd


def _first_non_empty(*values: object) -> object:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _postprocess_assets(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        if "asset_content" not in dataframe.columns:
            dataframe["asset_content"] = pd.Series(dtype="object")
        return dataframe

    dataframe = dataframe.copy()
    dataframe["asset_content"] = dataframe.apply(
        lambda row: _first_non_empty(
            row.get("text_asset_text"),
            row.get("sitelink_text"),
            row.get("callout_text"),
        ),
        axis=1,
    )
    return dataframe


def _resource_name_id(resource_name: object) -> str:
    if resource_name in (None, ""):
        return ""
    parts = str(resource_name).rstrip("/").split("/")
    return parts[-1] if parts else ""


def _normalize_text_column(dataframe: pd.DataFrame, column: str) -> pd.Series:
    if column not in dataframe.columns:
        return pd.Series("", index=dataframe.index, dtype="string")
    return dataframe[column].fillna("").astype("string")


def _postprocess_recommendations(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        if "optimization_score_uplift" not in dataframe.columns:
            dataframe["optimization_score_uplift"] = pd.Series(dtype="float64")
        return dataframe

    dataframe = dataframe.copy()

    if "campaign_resource_name" in dataframe.columns:
        campaign_ids = _normalize_text_column(dataframe, "campaign_id")
        campaign_resource_names = _normalize_text_column(dataframe, "campaign_resource_name")
        missing_campaign_id = campaign_ids.str.strip().eq("")
        derived_campaign_ids = campaign_resource_names.map(_resource_name_id).astype("string")

        dataframe["campaign_id"] = campaign_ids
        dataframe.loc[missing_campaign_id, "campaign_id"] = derived_campaign_ids.loc[missing_campaign_id]

    if "optimization_score_uplift" not in dataframe.columns:
        dataframe["optimization_score_uplift"] = pd.Series(0.0, index=dataframe.index, dtype="float64")
    else:
        dataframe["optimization_score_uplift"] = pd.to_numeric(
            dataframe["optimization_score_uplift"],
            errors="coerce",
        ).fillna(0.0)

    return dataframe


def postprocess_report_dataframe(report_key: str, dataframe: pd.DataFrame) -> pd.DataFrame:
    if report_key == "assets":
        return _postprocess_assets(dataframe)
    if report_key == "google_ads_recommendations":
        return _postprocess_recommendations(dataframe)
    return dataframe
