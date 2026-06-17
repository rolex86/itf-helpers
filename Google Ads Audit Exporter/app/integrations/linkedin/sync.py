from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.gtm.export import build_gtm_exports
from app.integrations.linkedin.audit_rules import build_audit_findings
from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.conversions import fetch_conversions_for_accounts
from app.integrations.linkedin.exporters import export_linkedin_bundle
from app.integrations.linkedin.gtm_crosscheck import build_gtm_crosscheck
from app.integrations.linkedin.lead_sync import fetch_lead_forms, fetch_lead_responses
from app.integrations.linkedin.models import (
    LinkedInAccountContextMapping,
    LinkedInAuditFinding,
    LinkedInConnection,
    LinkedInExportManifest,
    LinkedInRuntimeConfig,
)
from app.integrations.linkedin.normalizers import (
    normalize_entity_identifiers,
    records_to_frame,
    sanitize_pii_for_report,
    urn_to_id,
)
from app.integrations.linkedin.reporting import LinkedInDateRange, build_reporting_exports
from app.integrations.linkedin.restli import (
    campaign_urn,
    owner_param_for_organization,
    owner_param_for_sponsored_account,
    sponsored_account_urn,
)
from app.integrations.linkedin.web_scan import build_utm_audit_rows, scan_landing_pages


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


@dataclass(slots=True)
class LinkedInSyncResult:
    context_key: str
    export_dir: str = ""
    manifest: LinkedInExportManifest | None = None
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    raw_payloads: dict[str, Any] = field(default_factory=dict)
    findings: list[LinkedInAuditFinding] = field(default_factory=list)
    info_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


def _string(value: Any) -> str:
    return str(value or "").strip()


def _add_info(result: LinkedInSyncResult, message: str) -> None:
    text = _string(message)
    if text:
        result.info_notes.append(text)


def _add_warning(result: LinkedInSyncResult, message: str) -> None:
    text = _string(message)
    if text:
        result.warnings.append(text)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []

    for value in values or []:
        text = _string(value)
        if text and text not in deduped:
            deduped.append(text)

    return deduped


def _restli_list(values: list[str] | tuple[str, ...]) -> str:
    return "List(" + ",".join(_dedupe([_string(value) for value in values or []])) + ")"


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


def _safe_collect(
    *,
    result: LinkedInSyncResult,
    raw_key: str,
    action: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    try:
        rows = action()
        result.raw_payloads[raw_key] = rows
        return rows
    except Exception as exc:
        _add_warning(result, f"{raw_key} selhalo: {exc}")
        result.raw_payloads[raw_key] = []
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


def _extract_landing_url(record: dict[str, Any]) -> str:
    direct_keys = ("landingPageUrl", "landingPage", "clickUri", "url")

    for key in direct_keys:
        candidate = _string(record.get(key))
        if candidate.startswith(("http://", "https://")):
            return candidate

    nested_keys = ("variables", "content", "object", "reference")

    for nested_key in nested_keys:
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            nested_url = _extract_landing_url(nested)
            if nested_url:
                return nested_url

    return ""


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


def _enrich_creatives(
    *,
    account_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []

    for row in rows:
        normalized = normalize_entity_identifiers(row)

        normalized.setdefault("account_id", account_id)
        normalized.setdefault("account_urn", sponsored_account_urn(account_id))

        campaign_id = _string(
            normalized.get("campaign_id")
            or normalized.get("campaign")
            or normalized.get("campaign_urn")
            or normalized.get("sponsoredCampaign")
            or normalized.get("sponsoredCampaignUrn")
        )
        if campaign_id:
            normalized["campaign_id"] = urn_to_id(campaign_id)
            normalized["campaign_urn"] = campaign_urn(campaign_id)

        landing_page_url = _extract_landing_url(row)
        normalized["landing_page_url"] = landing_page_url
        normalized["final_domain"] = urlsplit(landing_page_url).netloc.lower() if landing_page_url else ""

        enriched.append(normalized)

    return enriched


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
    result: LinkedInSyncResult,
    account_id: str,
) -> list[dict[str, Any]]:
    first_pass = _safe_collect(
        result=result,
        raw_key=f"campaign_groups_raw_{account_id}",
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
        result=result,
        raw_key=f"campaign_groups_raw_{account_id}_fallback_all",
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
    result: LinkedInSyncResult,
    account_id: str,
) -> list[dict[str, Any]]:
    first_pass = _safe_collect(
        result=result,
        raw_key=f"campaigns_raw_{account_id}",
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
        result=result,
        raw_key=f"campaigns_raw_{account_id}_fallback_all",
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
    result: LinkedInSyncResult,
    account_id: str,
) -> list[dict[str, Any]]:
    rows = _safe_collect(
        result=result,
        raw_key=f"creatives_raw_{account_id}",
        action=lambda: list(
            client.paginate_cursor(
                f"adAccounts/{account_id}/creatives",
                params={"q": "criteria"},
                page_size=100,
                extra_headers={"X-RestLi-Method": "FINDER"},
            )
        ),
    )

    return _enrich_creatives(account_id=account_id, rows=rows)


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
            normalized["_sync_source"] = "direct_campaign_lookup"
            return normalized

    return None


def _fetch_missing_campaigns_from_creatives(
    *,
    client: LinkedInRestClient,
    result: LinkedInSyncResult,
    account_id: str,
    campaign_rows: list[dict[str, Any]],
    creative_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    known_campaign_ids = set(_extract_campaign_ids(campaign_rows))
    creative_campaign_ids = set(_extract_campaign_ids(creative_rows))
    missing_campaign_ids = sorted(creative_campaign_ids - known_campaign_ids)

    if not missing_campaign_ids:
        result.raw_payloads[f"campaigns_raw_{account_id}_direct_from_creatives"] = []
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

    result.raw_payloads[f"campaigns_raw_{account_id}_direct_from_creatives"] = direct_rows

    if missing_campaign_ids and not direct_rows:
        result.warnings.append(
            f"campaigns_raw_{account_id} selhalo: creatives odkazují na kampaně "
            f"{', '.join(missing_campaign_ids)}, ale direct lookup nic nevrátil."
        )

    return _merge_unique_rows(
        campaign_rows,
        direct_rows,
        id_keys=("campaign_id", "campaign", "campaign_urn", "id"),
    )


def _empty_reporting_datasets() -> dict[str, pd.DataFrame]:
    keys = (
        "insights_account_daily",
        "insights_campaign_daily",
        "insights_creative_daily",
        "insights_account_all",
        "insights_campaign_all",
        "insights_creative_all",
        "professional_demographics_account",
        "professional_demographics_campaign",
        "professional_demographics_creative",
    )
    return {key: records_to_frame([]) for key in keys}


def run_linkedin_context_sync(
    *,
    project_root: Path,
    connection: LinkedInConnection,
    mapping: LinkedInAccountContextMapping,
    runtime_config: LinkedInRuntimeConfig,
    access_token: str,
    date_range: LinkedInDateRange,
    include_raw: bool = True,
    include_reporting: bool = True,
    include_professional_demographics: bool = True,
    include_lead_sync: bool = True,
    include_web_scan: bool = True,
    include_gtm_crosscheck: bool = True,
    limited_to_test_leads: bool = True,
    gtm_env_config: GoogleAdsEnvConfig | None = None,
) -> LinkedInSyncResult:
    include_raw = bool(include_raw and runtime_config.export_raw)
    include_reporting = bool(include_reporting)
    include_professional_demographics = bool(include_professional_demographics)
    include_lead_sync = bool(include_lead_sync and runtime_config.enable_lead_sync and mapping.lead_sync_enabled)
    include_web_scan = bool(include_web_scan and runtime_config.enable_web_scan and mapping.web_scan_enabled)
    include_gtm_crosscheck = bool(include_gtm_crosscheck)

    client = LinkedInRestClient(connection=connection, runtime_config=runtime_config, access_token=access_token)
    result = LinkedInSyncResult(context_key=mapping.context_key)

    manifest = LinkedInExportManifest(
        platform="linkedin",
        context_key=mapping.context_key,
        connection_key=mapping.connection_key,
        started_at=datetime.now(timezone.utc).isoformat(),
        api_version=connection.linkedin_api_version or runtime_config.api_version,
        scopes_seen=list(connection.granted_scopes),
        ad_account_ids=list(mapping.ad_account_ids),
        organization_ids=list(mapping.organization_ids),
        date_range={"start": date_range.start.isoformat(), "end": date_range.end.isoformat()},
    )
    result.manifest = manifest

    ad_accounts = _safe_collect(
        result=result,
        raw_key="ad_accounts_raw",
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

    ad_account_lookup = {
        _string(row.get("account_id") or row.get("id")): row
        for row in ad_accounts
        if _string(row.get("account_id") or row.get("id"))
    }
    filtered_ad_accounts = [
        ad_account_lookup[account_id]
        for account_id in mapping.ad_account_ids
        if account_id in ad_account_lookup
    ] or ad_accounts

    ad_account_users = _safe_collect(
        result=result,
        raw_key="ad_account_users_raw",
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in _safe_get_elements(
                client=client,
                path="adAccountUsers",
                params={"q": "authenticatedUser"},
            )
        ],
    )

    account_roles: list[dict[str, Any]] = []
    campaign_groups: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    creatives: list[dict[str, Any]] = []
    campaign_ids_by_account: dict[str, list[str]] = {}

    for account_id in mapping.ad_account_ids:
        account_urn = sponsored_account_urn(account_id)

        account_role_rows = _safe_collect(
            result=result,
            raw_key=f"ad_account_roles_raw_{account_id}",
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
        account_roles.extend(account_role_rows)

        campaign_group_rows = _fetch_campaign_group_rows(
            client=client,
            result=result,
            account_id=account_id,
        )
        campaign_groups.extend(campaign_group_rows)

        campaign_rows = _fetch_campaign_rows(
            client=client,
            result=result,
            account_id=account_id,
        )

        creative_rows = _fetch_creative_rows(
            client=client,
            result=result,
            account_id=account_id,
        )

        campaign_rows = _fetch_missing_campaigns_from_creatives(
            client=client,
            result=result,
            account_id=account_id,
            campaign_rows=campaign_rows,
            creative_rows=creative_rows,
        )

        campaigns.extend(campaign_rows)
        creatives.extend(creative_rows)

        campaign_ids_by_account[account_id] = _dedupe(
            _extract_campaign_ids(campaign_rows) + _extract_campaign_ids(creative_rows)
        )

    result.raw_payloads["ad_account_roles_raw"] = list(account_roles)
    result.raw_payloads["campaign_groups_raw"] = list(campaign_groups)
    result.raw_payloads["campaigns_raw"] = list(campaigns)
    result.raw_payloads["creatives_raw"] = list(creatives)
    result.raw_payloads["creative_content_raw"] = list(creatives)

    organizations = _safe_collect(
        result=result,
        raw_key="organizations_raw",
        action=lambda: [
            normalize_entity_identifiers(client.get(f"organizations/{org_id}"))
            for org_id in mapping.organization_ids
        ],
    )

    (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        conversion_warnings,
    ) = fetch_conversions_for_accounts(
        client,
        account_ids=mapping.ad_account_ids,
        campaign_ids_by_account=campaign_ids_by_account,
    )
    result.warnings.extend(conversion_warnings)
    result.raw_payloads["conversions_raw"] = list(conversions)
    result.raw_payloads["campaign_conversions_raw"] = list(campaign_conversions)
    result.raw_payloads["insight_tags_raw"] = list(insight_tags)
    result.raw_payloads["insight_tag_domains_raw"] = list(insight_tag_domains)
    result.raw_payloads["insight_tags_permission_raw"] = list(insight_tags_permission)

    lead_forms: list[dict[str, Any]] = []
    lead_form_questions: list[dict[str, Any]] = []
    lead_form_responses: list[dict[str, Any]] = []
    lead_notifications: list[dict[str, Any]] = []

    if include_lead_sync:
        owner_urns = [owner_param_for_sponsored_account(account_id) for account_id in mapping.ad_account_ids]
        owner_urns.extend(owner_param_for_organization(org_id) for org_id in mapping.organization_ids)

        lead_forms, lead_form_questions, lead_warnings = fetch_lead_forms(
            client,
            owner_urns=owner_urns,
        )
        result.warnings.extend(lead_warnings)
        result.raw_payloads["lead_forms_raw"] = sanitize_pii_for_report({"rows": lead_forms}).get("rows", [])

        lead_form_responses, response_warnings = fetch_lead_responses(
            client,
            forms=lead_forms,
            limited_to_test_leads=limited_to_test_leads,
        )
        result.warnings.extend(response_warnings)
        result.raw_payloads["lead_form_responses_raw"] = [
            sanitize_pii_for_report(row)
            for row in lead_form_responses
        ]

        lead_notifications = _safe_collect(
            result=result,
            raw_key="lead_notifications_raw",
            action=lambda: list(
                client.paginate_start_count(
                    "leadNotifications",
                    params={"q": "search"},
                    count=100,
                )
            ),
        )
    else:
        _add_info(result, "Lead Sync byl vypnutý runtime flagem, UI volbou nebo mappingem.")
        result.raw_payloads["lead_forms_raw"] = []
        result.raw_payloads["lead_form_responses_raw"] = []
        result.raw_payloads["lead_notifications_raw"] = []

    reporting_datasets: dict[str, pd.DataFrame] = _empty_reporting_datasets()

    if include_reporting:
        reporting_datasets, reporting_raw, reporting_warnings = build_reporting_exports(
            client,
            account_ids=mapping.ad_account_ids,
            date_range=date_range,
            include_professional_demographics=include_professional_demographics,
        )
        result.raw_payloads.update(reporting_raw)
        result.raw_payloads["ad_analytics_raw"] = {
            key: value
            for key, value in reporting_raw.items()
            if key.startswith("insights_") or key.startswith("professional_demographics_")
        }
        result.warnings.extend(reporting_warnings)
    else:
        _add_info(result, "Reporting byl vypnutý UI volbou.")
        result.raw_payloads["ad_analytics_raw"] = {}

    gtm_crosscheck: dict[str, Any] = {
        "context_key": mapping.context_key,
        "expected_domains": list(mapping.expected_domains),
        "expected_conversion_ids": list(mapping.expected_conversion_ids),
        "expected_insight_tag_ids": list(mapping.expected_insight_tag_ids),
        "found_insight_tags": [],
        "found_conversion_tags": [],
        "found_partner_ids": [],
        "matched": False,
        "warnings": [],
        "errors": [],
    }

    if include_gtm_crosscheck and gtm_env_config is not None:
        gtm_result = build_gtm_exports(env_config=gtm_env_config, reports_enabled={"gtm_tags": True})
        gtm_tags = gtm_result.datasets.get("gtm_tags", pd.DataFrame())
        gtm_crosscheck = build_gtm_crosscheck(
            context_key=mapping.context_key,
            expected_domains=mapping.expected_domains,
            expected_conversion_ids=mapping.expected_conversion_ids,
            expected_insight_tag_ids=mapping.expected_insight_tag_ids,
            gtm_tags=gtm_tags,
        )
    elif not include_gtm_crosscheck:
        _add_info(result, "GTM cross-check byl vypnutý UI volbou.")

    landing_rows: list[dict[str, Any]] = []

    if include_web_scan:
        landing_urls = [
            _string(row.get("landing_page_url"))
            for row in creatives
            if _string(row.get("landing_page_url"))
        ]
        landing_rows, web_warnings = scan_landing_pages(
            landing_urls,
            timeout_seconds=runtime_config.request_timeout_seconds,
        )
        result.warnings.extend(web_warnings)
    else:
        _add_info(result, "Web scan byl vypnutý runtime flagem, UI volbou nebo mappingem.")


    creative_landing_seed_rows = [
        {
            "landing_page_url": row.get("landing_page_url", ""),
            "final_domain": row.get("final_domain", ""),
            "account_id": row.get("account_id", ""),
            "campaign_id": row.get("campaign_id", ""),
            "campaign_name": row.get("campaign_name", ""),
            "creative_id": row.get("creative_id", ""),
            "creative_name": row.get("name", ""),
        }
        for row in creatives
        if _string(row.get("landing_page_url"))
    ]

    utm_source_rows = landing_rows if landing_rows else creative_landing_seed_rows
    utm_source_rows = [
        row
        for row in utm_source_rows
        if _string(row.get("source_url") or row.get("landing_page_url") or row.get("final_url"))
    ]

    utm_audit_rows = build_utm_audit_rows(
        utm_source_rows,
        expected_source=mapping.expected_utm_source,
        expected_medium=mapping.expected_utm_medium,
        expected_domains=list(mapping.expected_domains),
    )

    datasets = {
        "connection_summary": records_to_frame([connection.to_dict()]),
        "mapping_used": records_to_frame([mapping.to_dict()]),
        "ad_accounts": records_to_frame(filtered_ad_accounts),
        "ad_account_users": records_to_frame(ad_account_users),
        "ad_account_roles": records_to_frame(account_roles),
        "organizations": records_to_frame(organizations),
        "campaign_groups": records_to_frame(campaign_groups),
        "campaigns": records_to_frame(campaigns),
        "creatives": records_to_frame(creatives),
        "creative_content": records_to_frame(creatives),
        "conversions": records_to_frame(conversions),
        "campaign_conversions": records_to_frame(campaign_conversions),
        "insight_tags": records_to_frame(insight_tags),
        "insight_tag_domains": records_to_frame(insight_tag_domains),
        "insight_tags_permission": records_to_frame(insight_tags_permission),
        "lead_forms": records_to_frame(lead_forms),
        "lead_form_questions": records_to_frame(lead_form_questions),
        "lead_form_responses": records_to_frame(lead_form_responses),
        "lead_notifications": records_to_frame(lead_notifications),
        "gtm_linkedin_crosscheck": records_to_frame([gtm_crosscheck]),
        "web_insight_tag_scan": records_to_frame(landing_rows),
        "landing_page_scan": records_to_frame(landing_rows),
        "utm_audit": records_to_frame(utm_audit_rows),
    }
    datasets.update(reporting_datasets)

    for key, value in _empty_reporting_datasets().items():
        datasets.setdefault(key, value)

    findings = build_audit_findings(
        connection=connection,
        mapping=mapping,
        lead_sync_active=include_lead_sync,
        datasets=datasets,
        gtm_crosscheck=gtm_crosscheck,
        web_scan_rows=landing_rows,
        export_warnings=result.warnings,
        export_info_notes=result.info_notes,
    )
    result.findings = findings
    result.datasets = datasets

    counts = {
        key: int(len(dataframe.index))
        for key, dataframe in datasets.items()
        if isinstance(dataframe, pd.DataFrame)
    }
    manifest.counts = counts
    manifest.infos = [{"message": note} for note in result.info_notes]
    manifest.warnings = [{"message": warning} for warning in result.warnings]
    manifest.errors = [{"message": error} for error in result.errors]
    manifest.finished_at = datetime.now(timezone.utc).isoformat()
    manifest.status = "failed" if result.errors else ("partial" if result.warnings else "success")

    report_lines = [
        f"# LinkedIn audit report - {mapping.context_key}",
        "",
        f"- Connection key: `{mapping.connection_key}`",
        f"- API version: `{manifest.api_version}`",
        f"- Date range: `{date_range.start.isoformat()}` -> `{date_range.end.isoformat()}`",
        f"- Status: `{manifest.status}`",
        "",
        "## Findings",
        "",
    ]

    if findings:
        for finding in findings:
            report_lines.append(f"- [{finding.severity}] {finding.code}: {finding.title}")
            report_lines.append(f"  {finding.detail}")
    else:
        report_lines.append("- Bez nálezů.")

    export_root = project_root / "exports" / "linkedin" / mapping.context_key / _timestamp()
    raw_payloads = result.raw_payloads if include_raw else {}

    export_linkedin_bundle(
        export_root=export_root,
        datasets=datasets,
        raw_payloads=raw_payloads,
        manifest=manifest,
        findings=findings,
        report_markdown="\n".join(report_lines),
    )

    result.export_dir = str(export_root)
    return result
