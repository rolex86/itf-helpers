from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers
from app.integrations.linkedin.restli import campaign_urn, restli_list, sponsored_account_urn


CAMPAIGN_CONVERSIONS_BATCH_SIZE = 50


def _dedupe(values: list[str]) -> list[str]:
    normalized: list[str] = []

    for value in values or []:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)

    return normalized


def _chunks(values: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [values]
    return [values[index : index + size] for index in range(0, len(values), size)]


def _normalize_rows(rows: list[dict[str, Any]], *, account_id: str = "") -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []

    for row in rows:
        normalized = normalize_entity_identifiers(row)
        if account_id and not normalized.get("account_id"):
            normalized["account_id"] = account_id
        normalized_rows.append(normalized)

    return normalized_rows


def fetch_conversions_for_accounts(
    client: LinkedInRestClient,
    *,
    account_ids: list[str],
    campaign_ids_by_account: dict[str, list[str]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    conversions: list[dict[str, Any]] = []
    campaign_conversions: list[dict[str, Any]] = []
    insight_tags: list[dict[str, Any]] = []
    insight_tag_domains: list[dict[str, Any]] = []
    insight_tags_permission: list[dict[str, Any]] = []
    warnings: list[str] = []

    campaign_ids_by_account = campaign_ids_by_account or {}

    for account_id in _dedupe(account_ids):
        account_urn = sponsored_account_urn(account_id)

        try:
            conversion_rows = list(
                client.paginate_cursor(
                    "conversions",
                    params={
                        "q": "account",
                        "account": account_urn,
                    },
                    page_size=100,
                )
            )
            conversions.extend(_normalize_rows(conversion_rows, account_id=account_id))
        except Exception as exc:
            warnings.append(f"Conversions pro account {account_id} nebylo možné načíst: {exc}")

        campaign_ids = _dedupe(campaign_ids_by_account.get(account_id, []))
        if campaign_ids:
            for campaign_id_batch in _chunks(campaign_ids, CAMPAIGN_CONVERSIONS_BATCH_SIZE):
                try:
                    campaign_urns = [campaign_urn(campaign_id) for campaign_id in campaign_id_batch]
                    association_rows = list(
                        client.paginate_cursor(
                            "campaignConversions",
                            params={
                                "q": "campaigns",
                                "campaigns": restli_list(campaign_urns),
                            },
                            page_size=100,
                        )
                    )
                    campaign_conversions.extend(_normalize_rows(association_rows, account_id=account_id))
                except Exception as exc:
                    warnings.append(
                        f"Campaign conversion associations pro account {account_id} nebylo možné načíst: {exc}"
                    )
        else:
            warnings.append(
                f"Campaign conversion associations pro account {account_id} nebyly načteny, protože nejsou dostupná campaign IDs."
            )

        try:
            tag_rows = list(
                client.paginate_cursor(
                    "insightTags",
                    params={
                        "q": "account",
                        "account": account_urn,
                    },
                    page_size=100,
                )
            )
            insight_tags.extend(_normalize_rows(tag_rows, account_id=account_id))
        except Exception as exc:
            warnings.append(f"Insight tags pro account {account_id} nebylo možné načíst: {exc}")

        try:
            domain_rows = list(
                client.paginate_cursor(
                    "insightTagDomains",
                    params={
                        "q": "account",
                        "account": account_urn,
                    },
                    page_size=100,
                )
            )
            insight_tag_domains.extend(_normalize_rows(domain_rows, account_id=account_id))
        except Exception as exc:
            warnings.append(f"Insight tag domains pro account {account_id} nebylo možné načíst: {exc}")

        try:
            permission_rows = list(
                client.paginate_cursor(
                    "insightTagsPermission",
                    params={
                        "q": "account",
                        "account": account_urn,
                    },
                    page_size=100,
                )
            )
            insight_tags_permission.extend(_normalize_rows(permission_rows, account_id=account_id))
        except Exception as exc:
            warnings.append(f"Insight tags permission pro account {account_id} nebylo možné načíst: {exc}")

    return (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        warnings,
    )