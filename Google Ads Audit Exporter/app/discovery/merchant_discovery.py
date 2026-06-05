from __future__ import annotations

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.merchant.client import MerchantApiClient


DISCOVERY_COLUMNS = [
    "merchant_account_id",
    "account_name",
    "account_type",
    "parent_account_id",
    "website_url",
    "is_advanced_account",
]


def discover_merchant_accounts(env_config: GoogleAdsEnvConfig) -> pd.DataFrame:
    client = MerchantApiClient.from_env_config(env_config)
    rows = [
        {
            "merchant_account_id": str(account.get("account_id") or ""),
            "account_name": account.get("account_name", ""),
            "account_type": account.get("account_type", ""),
            "parent_account_id": account.get("parent_account_id", ""),
            "website_url": account.get("website_url", "") or account.get("homepage", ""),
            "is_advanced_account": bool(account.get("is_advanced_account")),
        }
        for account in client.discover_accounts_hierarchy()
    ]

    return pd.DataFrame(rows, columns=DISCOVERY_COLUMNS)
