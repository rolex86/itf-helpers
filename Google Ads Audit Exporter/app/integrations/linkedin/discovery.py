from __future__ import annotations

from typing import Any, Callable

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.conversions import fetch_conversions_for_accounts
from app.integrations.linkedin.lead_sync import fetch_lead_forms
from app.integrations.linkedin.models import LinkedInDiscoverySnapshot
from app.integrations.linkedin.normalizers import normalize_entity_identifiers
from app.integrations.linkedin.organizations import fetch_organizations
from app.integrations.linkedin.restli import owner_param_for_organization, owner_param_for_sponsored_account, sponsored_account_urn


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


def run_linkedin_discovery(
    *,
    connection_key: str,
    client: LinkedInRestClient,
) -> LinkedInDiscoverySnapshot:
    snapshot = LinkedInDiscoverySnapshot(connection_key=connection_key)

    ad_accounts = _safe_collect(
        snapshot,
        key="ad_accounts",
        action=lambda: [normalize_entity_identifiers(row) for row in client.paginate("adAccounts", params={"q": "search", "count": 100})],
    )
    snapshot.ad_accounts.extend(ad_accounts)

    ad_account_users = _safe_collect(
        snapshot,
        key="ad_account_users",
        action=lambda: [normalize_entity_identifiers(row) for row in client.paginate("adAccountUsers", params={"q": "search", "count": 100})],
    )
    snapshot.ad_account_users.extend(ad_account_users)
    snapshot.ad_account_roles.extend(ad_account_users)

    account_ids = [str(row.get("account_id") or row.get("id") or "") for row in ad_accounts if str(row.get("account_id") or row.get("id") or "")]

    campaign_groups: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    creatives: list[dict[str, Any]] = []
    creative_content: list[dict[str, Any]] = []
    for account_id in account_ids:
        account_urn = sponsored_account_urn(account_id)
        campaign_group_rows = _safe_collect(
            snapshot,
            key=f"campaign_groups_{account_id}",
            action=lambda account_urn=account_urn: [
                normalize_entity_identifiers(row)
                for row in client.paginate("adCampaignGroups", params={"q": "search", "search.account.values[0]": account_urn, "count": 100})
            ],
        )
        campaign_groups.extend(campaign_group_rows)

        campaign_rows = _safe_collect(
            snapshot,
            key=f"campaigns_{account_id}",
            action=lambda account_urn=account_urn: [
                normalize_entity_identifiers(row)
                for row in client.paginate("adCampaigns", params={"q": "search", "search.account.values[0]": account_urn, "count": 100})
            ],
        )
        campaigns.extend(campaign_rows)

        creative_rows = _safe_collect(
            snapshot,
            key=f"creatives_{account_id}",
            action=lambda account_urn=account_urn: [
                normalize_entity_identifiers(row)
                for row in client.paginate("adCreatives", params={"q": "search", "search.account.values[0]": account_urn, "count": 100})
            ],
        )
        creatives.extend(creative_rows)
        creative_content.extend(creative_rows)

    snapshot.campaign_groups.extend(campaign_groups)
    snapshot.campaigns.extend(campaigns)
    snapshot.creatives.extend(creatives)
    snapshot.creative_content.extend(creative_content)

    organizations = fetch_organizations(client)
    snapshot.organizations.extend([normalize_entity_identifiers(row) for row in organizations])
    snapshot.raw_snapshots["organizations"] = organizations
    snapshot.organization_roles.extend(snapshot.organizations)

    conversions, campaign_conversions, insight_tags, insight_tag_domains, conversion_warnings = fetch_conversions_for_accounts(
        client,
        account_ids=account_ids,
    )
    snapshot.conversions.extend(conversions)
    snapshot.campaign_conversions.extend(campaign_conversions)
    snapshot.insight_tags.extend(insight_tags)
    snapshot.insight_tag_domains.extend(insight_tag_domains)
    snapshot.raw_snapshots["conversions"] = conversions
    snapshot.raw_snapshots["campaign_conversions"] = campaign_conversions
    snapshot.raw_snapshots["insight_tags"] = insight_tags
    snapshot.raw_snapshots["insight_tag_domains"] = insight_tag_domains
    snapshot.warnings.extend(conversion_warnings)

    owner_urns = [owner_param_for_sponsored_account(account_id) for account_id in account_ids]
    owner_urns.extend(
        owner_param_for_organization(str(row.get("organization_id") or row.get("id") or ""))
        for row in snapshot.organizations
        if str(row.get("organization_id") or row.get("id") or "")
    )
    lead_forms, lead_form_questions, lead_warnings = fetch_lead_forms(client, owner_urns=owner_urns)
    snapshot.lead_forms.extend(lead_forms)
    snapshot.lead_form_questions.extend(lead_form_questions)
    snapshot.raw_snapshots["lead_forms"] = lead_forms
    snapshot.raw_snapshots["lead_form_questions"] = lead_form_questions
    snapshot.warnings.extend(lead_warnings)

    snapshot.status = "success" if not snapshot.warnings else "partial"
    return snapshot

