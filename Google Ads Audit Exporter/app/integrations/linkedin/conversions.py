from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers
from app.integrations.linkedin.restli import sponsored_account_urn


def fetch_conversions_for_accounts(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    conversions: list[dict[str, Any]] = []
    campaign_conversions: list[dict[str, Any]] = []
    insight_tags: list[dict[str, Any]] = []
    insight_tag_domains: list[dict[str, Any]] = []
    warnings: list[str] = []

    for account_id in account_ids:
        account_urn = sponsored_account_urn(account_id)
        try:
            conversion_rows = list(
                client.paginate(
                    "conversions",
                    params={"q": "account", "account": account_urn, "count": 100},
                    count=100,
                )
            )
            conversions.extend([normalize_entity_identifiers(row) for row in conversion_rows])
        except Exception as exc:
            warnings.append(f"Conversions pro account {account_id} nebylo možné načíst: {exc}")

        try:
            association_rows = list(
                client.paginate(
                    "campaignConversions",
                    params={"q": "account", "account": account_urn, "count": 100},
                    count=100,
                )
            )
            campaign_conversions.extend([normalize_entity_identifiers(row) for row in association_rows])
        except Exception as exc:
            warnings.append(f"Campaign conversion associations pro account {account_id} nebylo možné načíst: {exc}")

        try:
            tag_rows = list(
                client.paginate(
                    "insightTags",
                    params={"q": "account", "account": account_urn, "count": 100},
                    count=100,
                )
            )
            normalized_tags = [normalize_entity_identifiers(row) for row in tag_rows]
            insight_tags.extend(normalized_tags)
            for row in normalized_tags:
                domains = row.get("domains", []) or row.get("domainNames", []) or []
                for domain in domains:
                    insight_tag_domains.append(
                        {
                            "account_id": account_id,
                            "insight_tag_id": row.get("id") or row.get("insight_tag_id") or "",
                            "domain": str(domain or ""),
                        }
                    )
        except Exception as exc:
            warnings.append(f"Insight tags pro account {account_id} nebylo možné načíst: {exc}")

    return conversions, campaign_conversions, insight_tags, insight_tag_domains, warnings

