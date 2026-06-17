from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.conversions import fetch_conversions_for_accounts
from app.integrations.linkedin.lead_sync import fetch_lead_forms
from app.integrations.linkedin.models import LinkedInDiscoverySnapshot
from app.integrations.linkedin.normalizers import normalize_entity_identifiers, urn_to_id
from app.integrations.linkedin.organizations import fetch_organizations
from app.integrations.linkedin.restli import (
    campaign_urn,
    owner_param_for_organization,
    owner_param_for_sponsored_account,
    sponsored_account_urn,
)


CAMPAIGN_GROUP_STATUSES = (
    "ACTIVE",
    "ARCHIVED",
    "CANCELED",
    "DRAFT",
    "PAUSED",
    "PENDING_DELETION",
    "REMOVED",
)

CAMPAIGN_STATUSES = (
    "ACTIVE",
    "PAUSED",
    "ARCHIVED",
    "COMPLETED",
    "CANCELED",
    "DRAFT",
    "PENDING_DELETION",
    "REMOVED",
)


def _status_search_params(statuses: tuple[str, ...]) -> dict[str, str]:
    return {
        "q": "search",
        "search": f"(status:(values:List({','.join(statuses)})))",
        "sortOrder": "DESCENDING",
    }


def _search_without_status_params() -> dict[str, str]:
    return {
        "q": "search",
        "sortOrder": "DESCENDING",
    }


def _safe_collect(
    snapshot: LinkedInDiscoverySnapshot,
    *,
    key: str,
    action: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    try:
        rows = action()
        snapshot.raw_snapshots[key] = rows
        return rows
    except Exception as exc:
        snapshot.warnings.append(f"{key} discovery warning: {exc}")
        snapshot.raw_snapshots[key] = []
        return []


def _string(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []

    for value in values or []:
        text = _string(value)
        if text and text not in deduped:
            deduped.append(text)

    return deduped


def _restli_list(values: list[str] | tuple[str, ...]) -> str:
    return "List(" + ",".join(_dedupe([_string(value) for value in values or []])) + ")"


def _elements_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("elements", "data", "values"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    return []


def _safe_get_elements(
    *,
    client: LinkedInRestClient,
    path: str,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    payload = client.get(path, params=params or {}, extra_headers=extra_headers)
    return _elements_from_payload(payload)


def _row_id(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _string(row.get(key))
        if value:
            return urn_to_id(value)
    return ""


def _merge_unique_rows(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    id_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows = list(existing)
    seen = {
        _row_id(row, *id_keys)
        for row in rows
        if _row_id(row, *id_keys)
    }

    for row in incoming:
        row_key = _row_id(row, *id_keys)
        if row_key and row_key in seen:
            continue
        if row_key:
            seen.add(row_key)
        rows.append(row)

    return rows


def _extract_account_ids(ad_accounts: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []

    for row in ad_accounts:
        account_id = _string(row.get("account_id") or row.get("id") or row.get("account"))
        if account_id:
            ids.append(urn_to_id(account_id))

    return _dedupe(ids)


def _extract_campaign_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []

    for row in rows:
        campaign_id = _string(
            row.get("campaign_id")
            or row.get("campaign")
            or row.get("campaign_urn")
            or row.get("sponsoredCampaign")
            or row.get("sponsoredCampaignUrn")
            or row.get("id")
        )
        if campaign_id:
            ids.append(urn_to_id(campaign_id))

    return _dedupe(ids)


def _extract_campaign_group_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []

    for row in rows:
        group_id = _string(
            row.get("campaign_group_id")
            or row.get("campaignGroup_id")
            or row.get("campaignGroup")
            or row.get("campaign_group_urn")
            or row.get("campaignGroupUrn")
            or row.get("id")
        )
        if group_id:
            ids.append(urn_to_id(group_id))

    return _dedupe(ids)


def _normalize_account_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []

    for row in rows:
        normalized = normalize_entity_identifiers(row)
        account_id = _string(normalized.get("account_id") or normalized.get("id") or normalized.get("account"))
        if account_id:
            normalized["account_id"] = urn_to_id(account_id)
            normalized["account_urn"] = sponsored_account_urn(account_id)

        account_name = _string(
            normalized.get("name")
            or normalized.get("localizedName")
            or normalized.get("accountName")
            or normalized.get("reference")
        )
        if account_name:
            normalized["account_name"] = account_name

        normalized_rows.append(normalized)

    return normalized_rows


def _fetch_campaign_group_rows(
    *,
    client: LinkedInRestClient,
    snapshot: LinkedInDiscoverySnapshot,
    account_id: str,
) -> list[dict[str, Any]]:
    first_pass = _safe_collect(
        snapshot,
        key=f"campaign_groups_{account_id}",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                f"adAccounts/{account_id}/adCampaignGroups",
                params=_status_search_params(CAMPAIGN_GROUP_STATUSES),
                page_size=100,
            )
        ],
    )

    if first_pass:
        return first_pass

    fallback = _safe_collect(
        snapshot,
        key=f"campaign_groups_{account_id}_fallback_all",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                f"adAccounts/{account_id}/adCampaignGroups",
                params=_search_without_status_params(),
                page_size=100,
            )
        ],
    )

    return _merge_unique_rows(
        first_pass,
        fallback,
        id_keys=("campaign_group_id", "campaignGroup_id", "campaignGroup", "id"),
    )


def _fetch_campaign_rows(
    *,
    client: LinkedInRestClient,
    snapshot: LinkedInDiscoverySnapshot,
    account_id: str,
) -> list[dict[str, Any]]:
    first_pass = _safe_collect(
        snapshot,
        key=f"campaigns_{account_id}",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                f"adAccounts/{account_id}/adCampaigns",
                params=_status_search_params(CAMPAIGN_STATUSES),
                page_size=100,
            )
        ],
    )

    fallback = _safe_collect(
        snapshot,
        key=f"campaigns_{account_id}_fallback_all",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                f"adAccounts/{account_id}/adCampaigns",
                params=_search_without_status_params(),
                page_size=100,
            )
        ],
    )

    return _merge_unique_rows(
        first_pass,
        fallback,
        id_keys=("campaign_id", "campaign", "campaign_urn", "id"),
    )


def _fetch_creative_rows(
    *,
    client: LinkedInRestClient,
    snapshot: LinkedInDiscoverySnapshot,
    account_id: str,
) -> list[dict[str, Any]]:
    return _safe_collect(
        snapshot,
        key=f"creatives_{account_id}",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                f"adAccounts/{account_id}/creatives",
                params={"q": "criteria"},
                page_size=100,
                extra_headers={"X-RestLi-Method": "FINDER"},
            )
        ],
    )


def _fetch_campaign_by_id(
    *,
    client: LinkedInRestClient,
    account_id: str,
    campaign_id: str,
) -> dict[str, Any] | None:
    clean_campaign_id = urn_to_id(campaign_id)
    if not clean_campaign_id:
        return None

    candidate_paths = (
        f"adAccounts/{account_id}/adCampaigns/{clean_campaign_id}",
        f"adCampaigns/{clean_campaign_id}",
        f"adCampaigns/{campaign_urn(clean_campaign_id)}",
    )

    for path in candidate_paths:
        try:
            payload = client.get(path)
        except Exception:
            continue

        if isinstance(payload, dict) and payload:
            normalized = normalize_entity_identifiers(payload)
            normalized.setdefault("account_id", account_id)
            normalized.setdefault("account_urn", sponsored_account_urn(account_id))
            normalized.setdefault("campaign_id", clean_campaign_id)
            normalized.setdefault("campaign_urn", campaign_urn(clean_campaign_id))
            normalized["_discovery_source"] = "direct_campaign_lookup"
            return normalized

    return None


def _fetch_missing_campaigns_from_creatives(
    *,
    client: LinkedInRestClient,
    snapshot: LinkedInDiscoverySnapshot,
    account_id: str,
    campaign_rows: list[dict[str, Any]],
    creative_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    known_campaign_ids = set(_extract_campaign_ids(campaign_rows))
    creative_campaign_ids = set(_extract_campaign_ids(creative_rows))
    missing_campaign_ids = sorted(creative_campaign_ids - known_campaign_ids)

    if not missing_campaign_ids:
        snapshot.raw_snapshots[f"campaigns_{account_id}_direct_from_creatives"] = []
        return campaign_rows

    direct_rows: list[dict[str, Any]] = []

    for campaign_id in missing_campaign_ids:
        direct_row = _fetch_campaign_by_id(
            client=client,
            account_id=account_id,
            campaign_id=campaign_id,
        )
        if direct_row:
            direct_rows.append(direct_row)

    snapshot.raw_snapshots[f"campaigns_{account_id}_direct_from_creatives"] = direct_rows

    if missing_campaign_ids and not direct_rows:
        snapshot.warnings.append(
            f"campaigns_{account_id} discovery warning: creatives reference campaigns "
            f"{', '.join(missing_campaign_ids)}, but direct campaign lookup returned no rows."
        )

    return _merge_unique_rows(
        campaign_rows,
        direct_rows,
        id_keys=("campaign_id", "campaign", "campaign_urn", "id"),
    )


def _enrich_creatives_with_account(
    *,
    account_id: str,
    creative_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched_rows: list[dict[str, Any]] = []

    for row in creative_rows:
        enriched = dict(row)
        enriched.setdefault("account_id", account_id)
        enriched.setdefault("account_urn", sponsored_account_urn(account_id))

        campaign_id = _string(enriched.get("campaign_id") or enriched.get("campaign") or enriched.get("campaign_urn"))
        if campaign_id:
            enriched["campaign_id"] = urn_to_id(campaign_id)
            enriched["campaign_urn"] = campaign_urn(campaign_id)

        enriched_rows.append(enriched)

    return enriched_rows


def run_linkedin_discovery(
    *,
    connection_key: str,
    client: LinkedInRestClient,
) -> LinkedInDiscoverySnapshot:
    snapshot = LinkedInDiscoverySnapshot(connection_key=connection_key)

    ad_accounts = _safe_collect(
        snapshot,
        key="ad_accounts",
        action=lambda: _normalize_account_rows(
            [
                row
                for row in client.paginate_cursor(
                    "adAccounts",
                    params={"q": "search"},
                    page_size=100,
                )
            ]
        ),
    )
    snapshot.ad_accounts.extend(ad_accounts)

    ad_account_users = _safe_collect(
        snapshot,
        key="ad_account_users_authenticated_user",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in _safe_get_elements(
                client=client,
                path="adAccountUsers",
                params={"q": "authenticatedUser"},
            )
        ],
    )
    snapshot.ad_account_users.extend(ad_account_users)

    account_ids = _extract_account_ids(ad_accounts)

    ad_account_roles: list[dict[str, Any]] = []
    campaign_groups: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    creatives: list[dict[str, Any]] = []
    creative_content: list[dict[str, Any]] = []
    campaign_ids_by_account: dict[str, list[str]] = {}

    for account_id in account_ids:
        account_urn = sponsored_account_urn(account_id)

        account_role_rows = _safe_collect(
            snapshot,
            key=f"ad_account_users_roles_{account_id}",
            action=lambda account_urn=account_urn: [
                normalize_entity_identifiers(row)
                for row in _safe_get_elements(
                    client=client,
                    path="adAccountUsers",
                    params={
                        "q": "accounts",
                        "accounts": _restli_list([account_urn]),
                    },
                )
            ],
        )
        ad_account_roles.extend(account_role_rows)

        campaign_group_rows = _fetch_campaign_group_rows(
            client=client,
            snapshot=snapshot,
            account_id=account_id,
        )
        campaign_groups.extend(campaign_group_rows)

        campaign_rows = _fetch_campaign_rows(
            client=client,
            snapshot=snapshot,
            account_id=account_id,
        )

        creative_rows = _enrich_creatives_with_account(
            account_id=account_id,
            creative_rows=_fetch_creative_rows(
                client=client,
                snapshot=snapshot,
                account_id=account_id,
            ),
        )

        campaign_rows = _fetch_missing_campaigns_from_creatives(
            client=client,
            snapshot=snapshot,
            account_id=account_id,
            campaign_rows=campaign_rows,
            creative_rows=creative_rows,
        )

        campaigns.extend(campaign_rows)
        creatives.extend(creative_rows)
        creative_content.extend(creative_rows)

        campaign_ids_by_account[account_id] = _dedupe(
            _extract_campaign_ids(campaign_rows) + _extract_campaign_ids(creative_rows)
        )

    snapshot.ad_account_roles.extend(ad_account_roles)
    snapshot.campaign_groups.extend(campaign_groups)
    snapshot.campaigns.extend(campaigns)
    snapshot.creatives.extend(creatives)
    snapshot.creative_content.extend(creative_content)

    organizations = fetch_organizations(client)
    snapshot.organizations.extend([normalize_entity_identifiers(row) for row in organizations])
    snapshot.raw_snapshots["organizations"] = organizations
    snapshot.organization_roles.extend(snapshot.organizations)

    (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        conversion_warnings,
    ) = fetch_conversions_for_accounts(
        client,
        account_ids=account_ids,
        campaign_ids_by_account=campaign_ids_by_account,
    )
    snapshot.conversions.extend(conversions)
    snapshot.campaign_conversions.extend(campaign_conversions)
    snapshot.insight_tags.extend(insight_tags)
    snapshot.insight_tag_domains.extend(insight_tag_domains)
    snapshot.raw_snapshots["conversions"] = conversions
    snapshot.raw_snapshots["campaign_conversions"] = campaign_conversions
    snapshot.raw_snapshots["insight_tags"] = insight_tags
    snapshot.raw_snapshots["insight_tag_domains"] = insight_tag_domains
    snapshot.raw_snapshots["insight_tags_permission"] = insight_tags_permission
    snapshot.warnings.extend(conversion_warnings)

    owner_urns = [owner_param_for_sponsored_account(account_id) for account_id in account_ids]
    owner_urns.extend(
        owner_param_for_organization(str(row.get("organization_id") or row.get("id") or ""))
        for row in snapshot.organizations
        if str(row.get("organization_id") or row.get("id") or "")
    )

    lead_forms, lead_form_questions, lead_warnings = fetch_lead_forms(
        client,
        owner_urns=owner_urns,
    )
    snapshot.lead_forms.extend(lead_forms)
    snapshot.lead_form_questions.extend(lead_form_questions)
    snapshot.raw_snapshots["lead_forms"] = lead_forms
    snapshot.raw_snapshots["lead_form_questions"] = lead_form_questions
    snapshot.warnings.extend(lead_warnings)

    snapshot.status = "success" if not snapshot.warnings else "partial"
    return snapshot