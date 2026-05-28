from __future__ import annotations

from pathlib import Path

from app.config.env_settings import GoogleAdsEnvConfig
from app.discovery.export import run_discovery_step, write_discovery_dataset
from app.discovery.ga4_discovery import DISCOVERY_COLUMNS as GA4_COLUMNS
from app.discovery.ga4_discovery import discover_ga4_properties
from app.discovery.google_ads_discovery import DISCOVERY_COLUMNS as GOOGLE_ADS_COLUMNS
from app.discovery.google_ads_discovery import discover_google_ads_customers
from app.discovery.gsc_discovery import DISCOVERY_COLUMNS as GSC_COLUMNS
from app.discovery.gsc_discovery import discover_gsc_sites
from app.discovery.gtm_discovery import DISCOVERY_COLUMNS as GTM_COLUMNS
from app.discovery.gtm_discovery import discover_gtm_accounts_and_containers
from app.discovery.merchant_discovery import DISCOVERY_COLUMNS as MERCHANT_COLUMNS
from app.discovery.merchant_discovery import discover_merchant_accounts
from app.discovery.models import DiscoveryResult


def run_all_discovery(project_root: Path, env_config: GoogleAdsEnvConfig) -> DiscoveryResult:
    result = DiscoveryResult()

    google_ads = run_discovery_step(
        result,
        key="google_ads_customers",
        columns=GOOGLE_ADS_COLUMNS,
        runner=lambda: discover_google_ads_customers(env_config),
    )
    write_discovery_dataset(result, project_root=project_root, key="google_ads_customers", dataframe=google_ads)

    ga4 = run_discovery_step(
        result,
        key="ga4_properties",
        columns=GA4_COLUMNS,
        runner=lambda: discover_ga4_properties(env_config),
    )
    write_discovery_dataset(result, project_root=project_root, key="ga4_properties", dataframe=ga4)

    gsc = run_discovery_step(
        result,
        key="gsc_sites",
        columns=GSC_COLUMNS,
        runner=lambda: discover_gsc_sites(env_config),
    )
    write_discovery_dataset(result, project_root=project_root, key="gsc_sites", dataframe=gsc)

    gtm = run_discovery_step(
        result,
        key="gtm_containers",
        columns=GTM_COLUMNS,
        runner=lambda: discover_gtm_accounts_and_containers(env_config),
    )
    write_discovery_dataset(result, project_root=project_root, key="gtm_containers", dataframe=gtm)

    merchant = run_discovery_step(
        result,
        key="merchant_accounts",
        columns=MERCHANT_COLUMNS,
        runner=lambda: discover_merchant_accounts(env_config),
    )
    write_discovery_dataset(result, project_root=project_root, key="merchant_accounts", dataframe=merchant)

    return result
