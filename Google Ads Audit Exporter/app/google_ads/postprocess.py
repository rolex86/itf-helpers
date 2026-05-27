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


def postprocess_report_dataframe(report_key: str, dataframe: pd.DataFrame) -> pd.DataFrame:
    if report_key == "assets":
        return _postprocess_assets(dataframe)
    return dataframe
