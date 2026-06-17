from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers
from app.integrations.linkedin.restli import campaign_urn, restli_list, sponsored_account_urn


def _safe_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("elements", "data", "values"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    return []


def _get_rows(
    client: LinkedInRestClient,
    path: str,
    *,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    payload = client.get(path, params=params)
    return _safe_rows_from_payload(payload)


def _normalize_rows(rows: list[dict[str, Any]], *, account_id: str = "") -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []

    for row in rows:
        normalized = normalize_entity_identifiers(row)

        if account_id:
            normalized.setdefault("account_id", account_id)
            normalized.setdefault("account_urn", sponsored_account_urn(account_id))

        normalized_rows.append(normalized)

    return normalized_rows


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []

    for value in values or []:
        text = str(value or "").strip()
        if text and text not in deduped:
            deduped.append(text)

    return deduped


def _chunk(values: list[str], size: int) -> list[list[str]]:
    cleaned = _dedupe(values)
    return [cleaned[index:index + size] for index in range(0, len(cleaned), size)]


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

    for account_id in account_ids:
        clean_account_id = str(account_id or "").replace("act_", "").strip()
        if not clean_account_id:
            continue

        account_urn = sponsored_account_urn(clean_account_id)

        try:
            conversion_rows = _get_rows(
                client,
                "conversions",
                params={
                    "q": "account",
                    "account": account_urn,
                },
            )
            conversions.extend(_normalize_rows(conversion_rows, account_id=clean_account_id))
        except Exception as exc:
            warnings.append(f"Conversions pro account {clean_account_id} nebylo možné načíst: {exc}")

        campaign_ids = _dedupe(campaign_ids_by_account.get(clean_account_id, []))
        if campaign_ids:
            for campaign_id_batch in _chunk(campaign_ids, 50):
                campaign_urns = [campaign_urn(campaign_id) for campaign_id in campaign_id_batch]

                try:
                    association_rows = _get_rows(
                        client,
                        "campaignConversions",
                        params={
                            "q": "campaigns",
                            "campaigns": restli_list(campaign_urns),
                        },
                    )
                    campaign_conversions.extend(
                        _normalize_rows(association_rows, account_id=clean_account_id)
                    )
                except Exception as exc:
                    warnings.append(
                        f"Campaign conversion associations pro account {clean_account_id} "
                        f"nebylo možné načíst pro kampaně {', '.join(campaign_id_batch)}: {exc}"
                    )

        try:
            tag_rows = _get_rows(
                client,
                "insightTags",
                params={
                    "q": "account",
                    "account": account_urn,
                },
            )
            insight_tags.extend(_normalize_rows(tag_rows, account_id=clean_account_id))
        except Exception as exc:
            warnings.append(f"Insight tags pro account {clean_account_id} nebylo možné načíst: {exc}")

        try:
            domain_rows = _get_rows(
                client,
                "insightTagDomains",
                params={
                    "q": "account",
                    "account": account_urn,
                },
            )
            insight_tag_domains.extend(_normalize_rows(domain_rows, account_id=clean_account_id))
        except Exception as exc:
            warnings.append(f"Insight tag domains pro account {clean_account_id} nebylo možné načíst: {exc}")

        try:
            permission_rows = _get_rows(
                client,
                "insightTagsPermission",
                params={
                    "q": "account",
                    "account": account_urn,
                },
            )
            insight_tags_permission.extend(_normalize_rows(permission_rows, account_id=clean_account_id))
        except Exception as exc:
            warnings.append(f"Insight tags permission pro account {clean_account_id} nebylo možné načíst: {exc}")

    return (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        warnings,
    )