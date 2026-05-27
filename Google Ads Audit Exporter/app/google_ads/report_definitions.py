from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass(frozen=True, slots=True)
class FieldSpec:
    path: str
    alias: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    key: str
    sheet_name: str
    query_file: str
    fields: tuple[FieldSpec, ...]
    priority: bool = False

    @property
    def aliases(self) -> list[str]:
        return [field.alias for field in self.fields]

    @property
    def required_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(field for field in self.fields if not field.optional)

    @property
    def optional_aliases(self) -> list[str]:
        return [field.alias for field in self.fields if field.optional]

    def query_path(self, project_root: Path) -> Path:
        return project_root / "app" / "google_ads" / "queries" / self.query_file


def _common_perf_fields() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("metrics.impressions", "impressions"),
        FieldSpec("metrics.clicks", "clicks"),
        FieldSpec("metrics.cost_micros", "cost_micros"),
        FieldSpec("metrics.average_cpc", "average_cpc"),
        FieldSpec("metrics.ctr", "ctr"),
        FieldSpec("metrics.conversions", "conversions"),
        FieldSpec("metrics.cost_per_conversion", "cost_per_conversion"),
        FieldSpec("metrics.conversions_from_interactions_rate", "conversion_rate"),
        FieldSpec("metrics.conversions_value", "conversions_value"),
        FieldSpec("metrics.value_per_conversion", "value_per_conversion"),
    )


REPORTS: dict[str, ReportDefinition] = {
    "account": ReportDefinition(
        key="account",
        sheet_name="Account",
        query_file="account.sql",
        fields=(
            FieldSpec("customer.id", "customer_id"),
            FieldSpec("customer.descriptive_name", "descriptive_name"),
            FieldSpec("customer.currency_code", "currency_code"),
            FieldSpec("customer.time_zone", "time_zone"),
            FieldSpec("customer.tracking_url_template", "tracking_url_template", optional=True),
            FieldSpec("customer.final_url_suffix", "final_url_suffix", optional=True),
            FieldSpec("customer.auto_tagging_enabled", "auto_tagging_enabled", optional=True),
        ),
    ),
    "campaigns": ReportDefinition(
        key="campaigns",
        sheet_name="Campaigns",
        query_file="campaigns.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("campaign.status", "campaign_status"),
            FieldSpec("campaign.advertising_channel_type", "advertising_channel_type"),
            FieldSpec("campaign.advertising_channel_sub_type", "advertising_channel_sub_type", optional=True),
            FieldSpec("campaign.bidding_strategy_type", "bidding_strategy_type"),
            FieldSpec("campaign_budget.name", "campaign_budget", optional=True),
            FieldSpec("campaign_budget.amount_micros", "budget_amount_micros", optional=True),
            FieldSpec("campaign.optimization_score", "optimization_score", optional=True),
            *_common_perf_fields(),
            FieldSpec("metrics.all_conversions", "all_conversions"),
            FieldSpec("metrics.all_conversions_value", "all_conversions_value"),
            FieldSpec("metrics.search_impression_share", "search_impression_share", optional=True),
            FieldSpec(
                "metrics.search_budget_lost_impression_share",
                "search_budget_lost_impression_share",
                optional=True,
            ),
            FieldSpec(
                "metrics.search_rank_lost_impression_share",
                "search_rank_lost_impression_share",
                optional=True,
            ),
        ),
    ),
    "ad_groups": ReportDefinition(
        key="ad_groups",
        sheet_name="Ad groups",
        query_file="ad_groups.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("ad_group.id", "ad_group_id"),
            FieldSpec("ad_group.name", "ad_group_name"),
            FieldSpec("ad_group.status", "ad_group_status"),
            FieldSpec("ad_group.type", "ad_group_type", optional=True),
            *_common_perf_fields(),
        ),
    ),
    "keywords": ReportDefinition(
        key="keywords",
        sheet_name="Keywords",
        query_file="keywords.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("ad_group.id", "ad_group_id"),
            FieldSpec("ad_group.name", "ad_group_name"),
            FieldSpec("ad_group_criterion.criterion_id", "criterion_id"),
            FieldSpec("ad_group_criterion.keyword.text", "keyword_text"),
            FieldSpec("ad_group_criterion.keyword.match_type", "keyword_match_type"),
            FieldSpec("ad_group_criterion.status", "keyword_status"),
            FieldSpec("ad_group_criterion.quality_info.quality_score", "quality_score", optional=True),
            FieldSpec(
                "ad_group_criterion.quality_info.creative_quality_score",
                "creative_quality_score",
                optional=True,
            ),
            FieldSpec(
                "ad_group_criterion.quality_info.post_click_quality_score",
                "post_click_quality_score",
                optional=True,
            ),
            FieldSpec(
                "ad_group_criterion.quality_info.search_predicted_ctr",
                "search_predicted_ctr",
                optional=True,
            ),
            *_common_perf_fields(),
        ),
    ),
    "search_terms": ReportDefinition(
        key="search_terms",
        sheet_name="Search terms",
        query_file="search_terms.sql",
        priority=True,
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("ad_group.id", "ad_group_id"),
            FieldSpec("ad_group.name", "ad_group_name"),
            FieldSpec("search_term_view.search_term", "search_term"),
            FieldSpec("segments.keyword.info.text", "keyword_text", optional=True),
            FieldSpec("segments.keyword.info.match_type", "keyword_match_type", optional=True),
            *_common_perf_fields(),
        ),
    ),
    "ads": ReportDefinition(
        key="ads",
        sheet_name="Ads",
        query_file="ads.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("ad_group.id", "ad_group_id"),
            FieldSpec("ad_group.name", "ad_group_name"),
            FieldSpec("ad_group_ad.ad.id", "ad_id"),
            FieldSpec("ad_group_ad.status", "ad_group_ad_status"),
            FieldSpec("ad_group_ad.ad.type", "ad_type"),
            FieldSpec("ad_group_ad.ad_strength", "ad_strength", optional=True),
            FieldSpec("ad_group_ad.ad.final_urls", "final_urls", optional=True),
            FieldSpec(
                "ad_group_ad.ad.responsive_search_ad.headlines",
                "responsive_search_ad_headlines",
                optional=True,
            ),
            FieldSpec(
                "ad_group_ad.ad.responsive_search_ad.descriptions",
                "responsive_search_ad_descriptions",
                optional=True,
            ),
            *_common_perf_fields(),
        ),
    ),
    "assets": ReportDefinition(
        key="assets",
        sheet_name="Assets",
        query_file="assets.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id", optional=True),
            FieldSpec("campaign.name", "campaign_name", optional=True),
            FieldSpec("asset.id", "asset_id"),
            FieldSpec("asset.name", "asset_name", optional=True),
            FieldSpec("asset.type", "asset_type"),
            FieldSpec("ad_group_ad_asset_view.field_type", "field_type", optional=True),
            FieldSpec("asset.source", "source", optional=True),
            FieldSpec("ad_group_ad_asset_view.performance_label", "performance_label", optional=True),
            FieldSpec("asset.text_asset.text", "text_asset_text", optional=True),
            FieldSpec("asset.sitelink_asset.link_text", "sitelink_text", optional=True),
            FieldSpec("asset.callout_asset.callout_text", "callout_text", optional=True),
            FieldSpec("metrics.impressions", "impressions"),
            FieldSpec("metrics.clicks", "clicks"),
            FieldSpec("metrics.cost_micros", "cost_micros"),
            FieldSpec("metrics.ctr", "ctr"),
            FieldSpec("metrics.conversions", "conversions"),
            FieldSpec("metrics.conversions_value", "conversions_value"),
        ),
    ),
    "devices": ReportDefinition(
        key="devices",
        sheet_name="Devices",
        query_file="devices.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("segments.device", "device"),
            *_common_perf_fields(),
        ),
    ),
    "locations": ReportDefinition(
        key="locations",
        sheet_name="Locations",
        query_file="locations.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("geographic_view.country_criterion_id", "country_criterion_id"),
            FieldSpec("geographic_view.location_type", "location_type"),
            FieldSpec("segments.geo_target_most_specific_location", "geo_target_name", optional=True),
            *_common_perf_fields(),
        ),
    ),
    "landing_pages": ReportDefinition(
        key="landing_pages",
        sheet_name="Landing pages",
        query_file="landing_pages.sql",
        fields=(
            FieldSpec("expanded_landing_page_view.expanded_final_url", "expanded_final_url"),
            FieldSpec("campaign.id", "campaign_id", optional=True),
            FieldSpec("campaign.name", "campaign_name", optional=True),
            FieldSpec("ad_group.id", "ad_group_id", optional=True),
            FieldSpec("ad_group.name", "ad_group_name", optional=True),
            *_common_perf_fields(),
        ),
    ),
    "conversion_actions": ReportDefinition(
        key="conversion_actions",
        sheet_name="Conversions",
        query_file="conversion_actions.sql",
        fields=(
            FieldSpec("conversion_action.id", "conversion_action_id"),
            FieldSpec("conversion_action.name", "conversion_action_name"),
            FieldSpec("conversion_action.status", "status"),
            FieldSpec("conversion_action.type", "type"),
            FieldSpec("conversion_action.category", "category"),
            FieldSpec("conversion_action.primary_for_goal", "primary_for_goal"),
            FieldSpec("conversion_action.include_in_conversions_metric", "include_in_conversions_metric"),
            FieldSpec(
                "conversion_action.value_settings.default_value",
                "default_value",
                optional=True,
            ),
            FieldSpec(
                "conversion_action.value_settings.default_currency_code",
                "default_currency_code",
                optional=True,
            ),
            FieldSpec(
                "conversion_action.value_settings.always_use_default_value",
                "always_use_default_value",
                optional=True,
            ),
            FieldSpec("conversion_action.counting_type", "counting_type", optional=True),
            FieldSpec(
                "conversion_action.attribution_model_settings.attribution_model",
                "attribution_model",
                optional=True,
            ),
            FieldSpec(
                "conversion_action.attribution_model_settings.data_driven_model_status",
                "data_driven_model_status",
                optional=True,
            ),
        ),
    ),
    "pmax_campaigns": ReportDefinition(
        key="pmax_campaigns",
        sheet_name="PMax campaigns",
        query_file="pmax_campaigns.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("campaign.status", "campaign_status"),
            FieldSpec("campaign.advertising_channel_type", "advertising_channel_type"),
            FieldSpec("campaign.advertising_channel_sub_type", "advertising_channel_sub_type", optional=True),
            FieldSpec("campaign.bidding_strategy_type", "bidding_strategy_type"),
            *_common_perf_fields(),
        ),
    ),
    "pmax_asset_groups": ReportDefinition(
        key="pmax_asset_groups",
        sheet_name="PMax asset groups",
        query_file="pmax_asset_groups.sql",
        fields=(
            FieldSpec("campaign.id", "campaign_id"),
            FieldSpec("campaign.name", "campaign_name"),
            FieldSpec("asset_group.id", "asset_group_id"),
            FieldSpec("asset_group.name", "asset_group_name"),
            FieldSpec("asset_group.status", "asset_group_status"),
            FieldSpec("metrics.impressions", "impressions"),
            FieldSpec("metrics.clicks", "clicks"),
            FieldSpec("metrics.cost_micros", "cost_micros"),
            FieldSpec("metrics.ctr", "ctr"),
            FieldSpec("metrics.conversions", "conversions"),
            FieldSpec("metrics.cost_per_conversion", "cost_per_conversion"),
            FieldSpec("metrics.conversions_value", "conversions_value"),
            FieldSpec("metrics.value_per_conversion", "value_per_conversion"),
        ),
    ),
    "change_history": ReportDefinition(
        key="change_history",
        sheet_name="Change history",
        query_file="change_history.sql",
        fields=(
            FieldSpec("change_event.change_date_time", "change_date_time"),
            FieldSpec("change_event.user_email", "user_email", optional=True),
            FieldSpec("change_event.resource_change_operation", "resource_type"),
            FieldSpec("change_event.resource_name", "resource_name"),
            FieldSpec("change_event.client_type", "client_type"),
            FieldSpec("change_event.change_resource_type", "change_resource_type"),
            FieldSpec("change_event.changed_fields", "changed_fields", optional=True),
            FieldSpec("change_event.old_resource", "old_resource", optional=True),
            FieldSpec("change_event.new_resource", "new_resource", optional=True),
        ),
    ),
}


REPORTS["campaigns_monthly"] = ReportDefinition(
    key="campaigns_monthly",
    sheet_name="Campaigns monthly",
    query_file="campaigns_monthly.sql",
    fields=(FieldSpec("segments.month", "month"), *REPORTS["campaigns"].fields),
)

REPORT_ORDER = [
    "account",
    "campaigns",
    "campaigns_monthly",
    "ad_groups",
    "keywords",
    "search_terms",
    "ads",
    "assets",
    "devices",
    "locations",
    "landing_pages",
    "conversion_actions",
    "pmax_campaigns",
    "pmax_asset_groups",
    "change_history",
]


def get_report_definition(report_key: str) -> ReportDefinition:
    return REPORTS[report_key]


def empty_report_frame(report: ReportDefinition) -> pd.DataFrame:
    return pd.DataFrame(columns=report.aliases)


def report_aliases(report_keys: Iterable[str]) -> dict[str, list[str]]:
    return {key: REPORTS[key].aliases for key in report_keys}
