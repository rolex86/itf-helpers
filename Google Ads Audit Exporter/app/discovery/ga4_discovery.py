from __future__ import annotations

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.ga4.client import Ga4ApiClient


DISCOVERY_COLUMNS = [
    "account_id",
    "account_display_name",
    "property_id",
    "property_display_name",
    "property_type",
    "resource_name",
]


def discover_ga4_properties(env_config: GoogleAdsEnvConfig) -> pd.DataFrame:
    client = Ga4ApiClient.from_env_config(env_config)
    properties = client.list_accessible_properties()
    rows = [
        {
            "account_id": str(item.get("account_resource", "")).replace("accounts/", ""),
            "account_display_name": item.get("account_name", ""),
            "property_id": item.get("property_id", ""),
            "property_display_name": item.get("property_name", ""),
            "property_type": item.get("property_type", ""),
            "resource_name": item.get("property_resource", ""),
        }
        for item in properties
    ]
    return pd.DataFrame(rows, columns=DISCOVERY_COLUMNS)
