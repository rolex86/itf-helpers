from __future__ import annotations

from collections.abc import Callable

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.conversions import fetch_conversions_for_accounts
from app.integrations.linkedin.lead_sync import fetch_lead_forms
from app.integrations.linkedin.models import LinkedInDiscoverySnapshot
from app.integrations.linkedin.normalizers import normalize_entity_identifiers
from app.integrations.linkedin.organizations import fetch_organizations
from app.integrations.linkedin.restli import (
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


def _safe_collect(
    snapshot: LinkedInDiscoverySnapshot,
    *,
    key: str,
    action: Callable[[], list[dict[str, object]]],
) -> list[dict[str, object]]:
    try:
        rows = action()
        snapshot.raw_snapshots[key] = rows
        return rows
    except Exception as exc:
        snapshot.warnings.append(f"{key} discovery warning: {exc}")
        snapshot.raw_snapshots[key] = []
        return []


def run_linkedin_discovery(
    *,
    connection_key: str,
    client: LinkedInRestClient,
) -> LinkedInDiscoverySnapshot:
    snapshot = LinkedInDiscoverySnapshot(connection_key=connection_key)

    ad_accounts = _safe_collect(
        snapshot,
        key="ad_accounts",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                "adAccounts",
                params={"q": "search"},
                page_size=100,
            )
        ],
    )
    snapshot.ad_accounts.extend(ad_accounts)

    ad_account_users = _safe_collect(
        snapshot,
        key="ad_account_users_authenticated_user",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                "adAccountUsers",
                params={"q": "authenticatedUser"},
                page_size=100,
            )
        ],
    )
    snapshot.ad_account_users.extend(ad_account_users)

    account_ids = [
        str(row.get("account_id") or row.get("id") or "")
        for row in ad_accounts
        if str(row.get("account_id") or row.get("id") or "")
    ]

    ad_account_roles: list[dict[str, object]] = []
    campaign_groups: list[dict[str, object]] = []
    campaigns: list[dict[str, object]] = []
    creatives: list[dict[str, object]] = []
    creative_content: list[dict[str, object]] = []
    campaign_ids_by_account: dict[str, list[str]] = {}

    for account_id in account_ids:
        account_urn = sponsored_account_urn(account_id)

        account_role_rows = _safe_collect(
            snapshot,
            key=f"ad_account_users_roles_{account_id}",
            action=lambda account_urn=account_urn: [
                normalize_entity_identifiers(row)
                for row in client.paginate_cursor(
                    "adAccountUsers",
                    params={
                        "q": "accounts",
                        "accounts": account_urn,
                    },
                    page_size=100,
                )
            ],
        )
        ad_account_roles.extend(account_role_rows)

        campaign_group_rows = _safe_collect(
            snapshot,
            key=f"campaign_groups_{account_id}",
            action=lambda account_id=account_id: [
                normalize_entity_identifiers(row)
                for row in client.paginate_cursor(
                    f"adAccounts/{account_id}/adCampaignGroups",
                    params=_status_search_params(CAMPAIGN_GROUP_STATUSES),
                    page_size=100,
                )
            ],
        )
        campaign_groups.extend(campaign_group_rows)

        campaign_rows = _safe_collect(
            snapshot,
            key=f"campaigns_{account_id}",
            action=lambda account_id=account_id: [
                normalize_entity_identifiers(row)
                for row in client.paginate_cursor(
                    f"adAccounts/{account_id}/adCampaigns",
                    params=_status_search_params(CAMPAIGN_STATUSES),
                    page_size=100,
                )
            ],
        )
        campaigns.extend(campaign_rows)

        campaign_ids_by_account[account_id] = [
            str(row.get("campaign_id") or row.get("id") or "")
            for row in campaign_rows
            if str(row.get("campaign_id") or row.get("id") or "")
        ]

        creative_rows = _safe_collect(
            snapshot,
            key=f"creatives_{account_id}",
            action=lambda account_id=account_id: [
                normalize_entity_identifiers(row)
                for row in client.paginate_cursor(
                    f"adAccounts/{account_id}/creatives",
                    params={"q": "criteria"},
                    page_size=100,
                    extra_headers={"X-RestLi-Method": "FINDER"},
                )
            ],
        )
        creatives.extend(creative_rows)
        creative_content.extend(creative_rows)

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