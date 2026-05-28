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
    if not resource_name:
        return ""
    parts = str(resource_name).rstrip("/").split("/")
    return parts[-1] if parts else ""


def _postprocess_recommendations(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        if "optimization_score_uplift" not in dataframe.columns:
            dataframe["optimization_score_uplift"] = pd.Series(dtype="float64")
        return dataframe

    dataframe = dataframe.copy()
    if "campaign_resource_name" in dataframe.columns and "campaign_id" in dataframe.columns:
        missing_campaign_id = dataframe["campaign_id"].isin([None, ""])
        dataframe.loc[missing_campaign_id, "campaign_id"] = dataframe.loc[
            missing_campaign_id, "campaign_resource_name"
        ].apply(_resource_name_id)

    if "optimization_score_uplift" not in dataframe.columns:
        dataframe["optimization_score_uplift"] = None
    return dataframe


def postprocess_report_dataframe(report_key: str, dataframe: pd.DataFrame) -> pd.DataFrame:
    if report_key == "assets":
        return _postprocess_assets(dataframe)
    if report_key == "google_ads_recommendations":
        return _postprocess_recommendations(dataframe)
    return dataframe
