from __future__ import annotations

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.merchant.client import MerchantApiClient, MerchantApiError


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
    accessible_accounts = client.list_accessible_accounts()
    rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for account in accessible_accounts:
        account_id = str(account.get("account_id") or "")
        subaccounts: list[dict[str, object]] = []
        is_advanced = False
        try:
            subaccounts = client.list_subaccounts(account_id)
            is_advanced = True
        except MerchantApiError:
            subaccounts = []

        if account_id and account_id not in seen_ids:
            seen_ids.add(account_id)
            rows.append(
                {
                    "merchant_account_id": account_id,
                    "account_name": account.get("account_name", ""),
                    "account_type": "advanced" if is_advanced else "standalone",
                    "parent_account_id": "",
                    "website_url": account.get("website_url", "") or account.get("homepage", ""),
                    "is_advanced_account": is_advanced,
                }
            )

        for subaccount in subaccounts:
            subaccount_id = str(subaccount.get("account_id") or "")
            if not subaccount_id or subaccount_id in seen_ids:
                continue
            seen_ids.add(subaccount_id)
            rows.append(
                {
                    "merchant_account_id": subaccount_id,
                    "account_name": subaccount.get("account_name", ""),
                    "account_type": "subaccount",
                    "parent_account_id": account_id,
                    "website_url": subaccount.get("website_url", "") or subaccount.get("homepage", ""),
                    "is_advanced_account": False,
                }
            )

    return pd.DataFrame(rows, columns=DISCOVERY_COLUMNS)
