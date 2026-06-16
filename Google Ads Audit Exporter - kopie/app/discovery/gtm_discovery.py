from __future__ import annotations

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.gtm.client import GtmApiClient


DISCOVERY_COLUMNS = [
    "account_id",
    "account_name",
    "container_id",
    "public_id",
    "container_name",
    "domain_name",
    "usage_context",
    "path",
]


def discover_gtm_accounts_and_containers(env_config: GoogleAdsEnvConfig) -> pd.DataFrame:
    client = GtmApiClient.from_env_config(env_config)
    accounts = client.list_accounts()
    rows: list[dict[str, object]] = []
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        account_name = str(account.get("name") or "")
        containers = client.list_containers(account_id)
        if not containers:
            rows.append(
                {
                    "account_id": account_id,
                    "account_name": account_name,
                    "container_id": "",
                    "public_id": "",
                    "container_name": "",
                    "domain_name": "",
                    "usage_context": "",
                    "path": "",
                }
            )
            continue
        for container in containers:
            rows.append(
                {
                    "account_id": account_id,
                    "account_name": account_name,
                    "container_id": container.get("container_id", ""),
                    "public_id": container.get("public_id", ""),
                    "container_name": container.get("name", ""),
                    "domain_name": container.get("domain_name", ""),
                    "usage_context": container.get("usage_context", ""),
                    "path": container.get("path", ""),
                }
            )
    return pd.DataFrame(rows, columns=DISCOVERY_COLUMNS)
