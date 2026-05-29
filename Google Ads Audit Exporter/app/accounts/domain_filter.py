from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pandas as pd


ADS_DATASET_KEYS_BY_CAMPAIGN = (
    "campaigns",
    "campaigns_monthly",
    "ad_groups",
    "keywords",
    "search_terms",
    "ads",
    "assets",
    "devices",
    "locations",
    "shopping_products",
    "shopping_products_summary",
    "pmax_campaigns",
    "pmax_asset_groups",
    "google_ads_recommendations",
)

LANDING_PAGE_URL_COLUMNS = (
    "expanded_final_url",
    "landing_page_url",
    "final_url",
    "url",
)


@dataclass(slots=True)
class DomainFilterResult:
    source_domains: list[str]
    landing_pages_before: int
    landing_pages_after: int
    matched_campaign_ids: set[str]
    dataset_stats: dict[str, dict[str, int]]
    warnings: list[str]
    status: str

    @property
    def matched_campaign_count(self) -> int:
        return len(self.matched_campaign_ids)


def normalize_domain(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("sc-domain:"):
        text = text.replace("sc-domain:", "", 1).strip()
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    host = (parsed.netloc or parsed.path or "").split("@")[-1]
    host = host.split(":", 1)[0].strip().lower().rstrip(".")
    return host


def normalize_source_domains(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = values.replace(",", "\n").splitlines()
    elif isinstance(values, (list, tuple, set)):
        raw_items = [str(item or "") for item in values]
    else:
        raw_items = [str(values or "")]

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        candidate = normalize_domain(raw_item)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def extract_domains_from_gsc_site_url(site_url: object) -> list[str]:
    site = str(site_url or "").strip()
    if not site:
        return []
    primary = normalize_domain(site)
    if not primary:
        return []
    suggestions = [primary]
    if primary.startswith("www."):
        suggestions.append(primary.replace("www.", "", 1))
    else:
        suggestions.append(f"www.{primary}")
    return normalize_source_domains(suggestions)


def extract_domain_from_url(url: object) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    host = (parsed.netloc or "").split("@")[-1]
    host = host.split(":", 1)[0].strip().lower().rstrip(".")
    return host


def url_matches_source_domains(url: object, source_domains: list[str]) -> bool:
    normalized_domains = normalize_source_domains(source_domains)
    if not normalized_domains:
        return True
    host = extract_domain_from_url(url)
    if not host:
        return False
    return host in set(normalized_domains)


def source_domains_display(source_domains: list[str]) -> str:
    return ", ".join(normalize_source_domains(source_domains))


def filter_dataframe_by_source_domains(
    dataframe: pd.DataFrame,
    url_columns: list[str],
    source_domains: list[str],
) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe.copy()
    normalized_domains = normalize_source_domains(source_domains)
    if not normalized_domains:
        return dataframe.copy()

    existing_columns = [column for column in url_columns if column in dataframe.columns]
    if not existing_columns:
        return dataframe.iloc[0:0].copy()

    domain_set = set(normalized_domains)

    def _row_matches(row: pd.Series) -> bool:
        for column in existing_columns:
            host = extract_domain_from_url(row.get(column))
            if host and host in domain_set:
                return True
        return False

    mask = dataframe.apply(_row_matches, axis=1)
    return dataframe.loc[mask].copy()


def derive_campaign_ids_from_landing_pages(landing_pages: pd.DataFrame) -> set[str]:
    if landing_pages.empty or "campaign_id" not in landing_pages.columns:
        return set()
    ids = (
        landing_pages["campaign_id"]
        .dropna()
        .astype(str)
        .map(str.strip)
    )
    return {value for value in ids if value}


def _filter_dataframe_by_campaign_ids(
    dataframe: pd.DataFrame,
    campaign_ids: set[str],
) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe.copy()
    if "campaign_id" not in dataframe.columns:
        return dataframe.copy()
    if not campaign_ids:
        return dataframe.iloc[0:0].copy()
    mask = dataframe["campaign_id"].astype(str).map(str.strip).isin(campaign_ids)
    return dataframe.loc[mask].copy()


def filter_ads_datasets_by_campaign_ids(
    datasets: dict[str, pd.DataFrame],
    campaign_ids: set[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, int]]]:
    filtered: dict[str, pd.DataFrame] = {}
    stats: dict[str, dict[str, int]] = {}
    for key, dataframe in datasets.items():
        before = int(len(dataframe))
        if key in ADS_DATASET_KEYS_BY_CAMPAIGN:
            updated = _filter_dataframe_by_campaign_ids(dataframe, campaign_ids)
        else:
            updated = dataframe.copy()
        filtered[key] = updated
        stats[key] = {"before": before, "after": int(len(updated))}
    return filtered, stats


def apply_context_domain_filters(
    datasets: dict[str, pd.DataFrame],
    source_domains: list[str],
) -> DomainFilterResult:
    normalized_domains = normalize_source_domains(source_domains)
    warnings: list[str] = []
    landing_pages = datasets.get("landing_pages", pd.DataFrame())
    landing_pages_before = int(len(landing_pages))

    if not normalized_domains:
        warnings.append(
            "Kontext nema nastavene source_domains. Google Ads / PageSpeed data nemusi byt domenove oddelena."
        )
        return DomainFilterResult(
            source_domains=[],
            landing_pages_before=landing_pages_before,
            landing_pages_after=landing_pages_before,
            matched_campaign_ids=derive_campaign_ids_from_landing_pages(landing_pages),
            dataset_stats={key: {"before": int(len(df)), "after": int(len(df))} for key, df in datasets.items()},
            warnings=warnings,
            status="missing_source_domains",
        )

    filtered_landing_pages = filter_dataframe_by_source_domains(
        landing_pages,
        url_columns=list(LANDING_PAGE_URL_COLUMNS),
        source_domains=normalized_domains,
    )
    landing_pages_after = int(len(filtered_landing_pages))
    campaign_ids = derive_campaign_ids_from_landing_pages(filtered_landing_pages)

    filtered_datasets = dict(datasets)
    filtered_datasets["landing_pages"] = filtered_landing_pages
    filtered_datasets, stats = filter_ads_datasets_by_campaign_ids(filtered_datasets, campaign_ids)
    filtered_datasets["landing_pages"] = filtered_landing_pages
    stats["landing_pages"] = {"before": landing_pages_before, "after": landing_pages_after}

    if landing_pages_after == 0:
        warnings.append("Kontext nema zadne Ads landing pages odpovidajici source_domains.")
        status = "no_matches"
    else:
        status = "filtered"

    datasets.clear()
    datasets.update(filtered_datasets)

    return DomainFilterResult(
        source_domains=normalized_domains,
        landing_pages_before=landing_pages_before,
        landing_pages_after=landing_pages_after,
        matched_campaign_ids=campaign_ids,
        dataset_stats=stats,
        warnings=warnings,
        status=status,
    )
