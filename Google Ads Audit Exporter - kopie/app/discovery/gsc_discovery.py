from __future__ import annotations

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.search_console.client import SearchConsoleApiClient


DISCOVERY_COLUMNS = [
    "site_url",
    "permission_level",
]


def discover_gsc_sites(env_config: GoogleAdsEnvConfig) -> pd.DataFrame:
    client = SearchConsoleApiClient.from_env_config(env_config)
    sites = client.list_sites()
    return pd.DataFrame(sites, columns=DISCOVERY_COLUMNS)
