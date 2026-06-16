from __future__ import annotations

from typing import Any

import pandas as pd

from app.config.settings import FlagsConfig


FLAG_COLUMNS = [
    "entity_type",
    "entity_id",
    "entity_name",
    "parent_campaign",
    "flag_type",
    "severity",
    "metric_1",
    "metric_2",
    "note",
]


def _safe_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _campaign_name(row: pd.Series) -> str:
    return str(row.get("campaign_name") or "")


def _append_flag(target: list[dict[str, Any]], **kwargs: Any) -> None:
    row = {column: kwargs.get(column, "") for column in FLAG_COLUMNS}
    target.append(row)


def _flag_spend_without_conversions(
    target: list[dict[str, Any]],
    frame: pd.DataFrame,
    entity_type: str,
    entity_id_col: str,
    entity_name_col: str,
    flag_type: str,
    flags_config: FlagsConfig,
) -> None:
    for _, row in frame.iterrows():
        cost_micros = _safe_number(row.get("cost_micros"))
        conversions = _safe_number(row.get("conversions"))
        clicks = _safe_number(row.get("clicks"))
        if cost_micros >= flags_config.min_spend_micros and conversions <= 0 and clicks >= flags_config.min_clicks:
            _append_flag(
                target,
                entity_type=entity_type,
                entity_id=row.get(entity_id_col, ""),
                entity_name=row.get(entity_name_col, ""),
                parent_campaign=_campaign_name(row),
                flag_type=flag_type,
                severity="high",
                metric_1=cost_micros,
                metric_2=conversions,
                note="Spend above threshold with zero conversions.",
            )


def _flag_low_ctr(target: list[dict[str, Any]], campaigns: pd.DataFrame, flags_config: FlagsConfig) -> None:
    for _, row in campaigns.iterrows():
        ctr = _safe_number(row.get("ctr"))
        impressions = _safe_number(row.get("impressions"))
        if impressions > 0 and ctr < flags_config.low_ctr_threshold:
            _append_flag(
                target,
                entity_type="campaign",
                entity_id=row.get("campaign_id", ""),
                entity_name=row.get("campaign_name", ""),
                parent_campaign=row.get("campaign_name", ""),
                flag_type="low_ctr",
                severity="medium",
                metric_1=ctr,
                metric_2=impressions,
                note="CTR below configured threshold.",
            )


def _flag_budget_and_rank_limits(target: list[dict[str, Any]], campaigns: pd.DataFrame) -> None:
    for _, row in campaigns.iterrows():
        budget_lost = _safe_number(row.get("search_budget_lost_impression_share"))
        rank_lost = _safe_number(row.get("search_rank_lost_impression_share"))
        if budget_lost >= 0.10:
            _append_flag(
                target,
                entity_type="campaign",
                entity_id=row.get("campaign_id", ""),
                entity_name=row.get("campaign_name", ""),
                parent_campaign=row.get("campaign_name", ""),
                flag_type="budget_limited",
                severity="medium",
                metric_1=budget_lost,
                metric_2=row.get("search_impression_share", ""),
                note="Search budget lost impression share is elevated.",
            )
        if rank_lost >= 0.10:
            _append_flag(
                target,
                entity_type="campaign",
                entity_id=row.get("campaign_id", ""),
                entity_name=row.get("campaign_name", ""),
                parent_campaign=row.get("campaign_name", ""),
                flag_type="rank_limited",
                severity="medium",
                metric_1=rank_lost,
                metric_2=row.get("search_impression_share", ""),
                note="Search rank lost impression share is elevated.",
            )


def _flag_target_metrics(target: list[dict[str, Any]], campaigns: pd.DataFrame, flags_config: FlagsConfig) -> None:
    for _, row in campaigns.iterrows():
        cost_micros = _safe_number(row.get("cost_micros"))
        conversions = _safe_number(row.get("conversions"))
        conversions_value = _safe_number(row.get("conversions_value"))

        if flags_config.target_cpa_micros and conversions > 0:
            cpa = cost_micros / conversions
            if cpa > flags_config.target_cpa_micros:
                _append_flag(
                    target,
                    entity_type="campaign",
                    entity_id=row.get("campaign_id", ""),
                    entity_name=row.get("campaign_name", ""),
                    parent_campaign=row.get("campaign_name", ""),
                    flag_type="high_cpa",
                    severity="high",
                    metric_1=cpa,
                    metric_2=flags_config.target_cpa_micros,
                    note="Observed CPA is above configured target.",
                )

        if flags_config.target_roas and cost_micros > 0:
            spend = cost_micros / 1_000_000
            roas = conversions_value / spend if spend > 0 else 0
            if roas < flags_config.target_roas:
                _append_flag(
                    target,
                    entity_type="campaign",
                    entity_id=row.get("campaign_id", ""),
                    entity_name=row.get("campaign_name", ""),
                    parent_campaign=row.get("campaign_name", ""),
                    flag_type="high_spend_low_roas",
                    severity="high",
                    metric_1=roas,
                    metric_2=flags_config.target_roas,
                    note="Observed ROAS is below configured target.",
                )


def build_basic_flags(
    campaigns: pd.DataFrame,
    keywords: pd.DataFrame,
    search_terms: pd.DataFrame,
    landing_pages: pd.DataFrame,
    devices: pd.DataFrame,
    locations: pd.DataFrame,
    flags_config: FlagsConfig,
) -> pd.DataFrame:
    flags: list[dict[str, Any]] = []

    if not campaigns.empty:
        _flag_spend_without_conversions(
            flags,
            campaigns,
            entity_type="campaign",
            entity_id_col="campaign_id",
            entity_name_col="campaign_name",
            flag_type="high_spend_no_conversions",
            flags_config=flags_config,
        )
        _flag_low_ctr(flags, campaigns, flags_config)
        _flag_budget_and_rank_limits(flags, campaigns)
        _flag_target_metrics(flags, campaigns, flags_config)

    if not keywords.empty:
        _flag_spend_without_conversions(
            flags,
            keywords,
            entity_type="keyword",
            entity_id_col="criterion_id",
            entity_name_col="keyword_text",
            flag_type="keyword_spend_no_conversion",
            flags_config=flags_config,
        )

    if not search_terms.empty:
        _flag_spend_without_conversions(
            flags,
            search_terms,
            entity_type="search_term",
            entity_id_col="search_term",
            entity_name_col="search_term",
            flag_type="search_term_spend_no_conversion",
            flags_config=flags_config,
        )

    if not landing_pages.empty:
        _flag_spend_without_conversions(
            flags,
            landing_pages,
            entity_type="landing_page",
            entity_id_col="expanded_final_url",
            entity_name_col="expanded_final_url",
            flag_type="landing_page_spend_no_conversion",
            flags_config=flags_config,
        )

    if not devices.empty:
        _flag_spend_without_conversions(
            flags,
            devices,
            entity_type="device",
            entity_id_col="device",
            entity_name_col="device",
            flag_type="device_segment_bad_performance",
            flags_config=flags_config,
        )

    if not locations.empty:
        _flag_spend_without_conversions(
            flags,
            locations,
            entity_type="location",
            entity_id_col="country_criterion_id",
            entity_name_col="geo_target_name",
            flag_type="location_bad_performance",
            flags_config=flags_config,
        )

    if not flags:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    return pd.DataFrame(flags, columns=FLAG_COLUMNS)
