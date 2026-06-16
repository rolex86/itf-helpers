from __future__ import annotations

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
from app.integrations.linkedin.normalizers import normalize_entity_identifiers, records_to_frame, sanitize_pii_for_report
from app.integrations.linkedin.reporting import LinkedInDateRange, build_reporting_exports
from app.integrations.linkedin.restli import (
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
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


def _status_search_params(statuses: tuple[str, ...]) -> dict[str, str]:
    return {
        "q": "search",
        "search": f"(status:(values:List({','.join(statuses)})))",
        "sortOrder": "DESCENDING",
    }


def _extract_landing_url(record: dict[str, Any]) -> str:
    direct_keys = ("landingPageUrl", "landingPage", "clickUri", "url")
    for key in direct_keys:
        candidate = str(record.get(key) or "").strip()
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


def _enrich_creatives(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []

    for row in rows:
        normalized = normalize_entity_identifiers(row)
        landing_page_url = _extract_landing_url(row)
        normalized["landing_page_url"] = landing_page_url
        normalized["final_domain"] = urlsplit(landing_page_url).netloc.lower() if landing_page_url else ""
        enriched.append(normalized)

    return enriched


def _safe_collect(
    *,
    result: LinkedInSyncResult,
    raw_key: str,
    action,
) -> list[dict[str, Any]]:
    try:
        rows = action()
        result.raw_payloads[raw_key] = rows
        return rows
    except Exception as exc:
        result.warnings.append(f"{raw_key} selhalo: {exc}")
        result.raw_payloads[raw_key] = []
        return []


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
    include_lead_sync = bool(include_lead_sync and runtime_config.enable_lead_sync and mapping.lead_sync_enabled)
    include_web_scan = bool(include_web_scan and runtime_config.enable_web_scan and mapping.web_scan_enabled)

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
        action=lambda: [
            normalize_entity_identifiers(row)
            for row in client.paginate_cursor(
                "adAccounts",
                params={"q": "search"},
                page_size=100,
            )
        ],
    )

    ad_account_lookup = {
        str(row.get("account_id") or row.get("id") or ""): row
        for row in ad_accounts
        if str(row.get("account_id") or row.get("id") or "")
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
            for row in client.paginate_cursor(
                "adAccountUsers",
                params={"q": "authenticatedUser"},
                page_size=100,
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
        account_roles.extend(account_role_rows)

        campaign_group_rows = _safe_collect(
            result=result,
            raw_key=f"campaign_groups_raw_{account_id}",
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
            result=result,
            raw_key=f"campaigns_raw_{account_id}",
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
            result=result,
            raw_key=f"creatives_raw_{account_id}",
            action=lambda account_id=account_id: _enrich_creatives(
                list(
                    client.paginate_cursor(
                        f"adAccounts/{account_id}/creatives",
                        params={"q": "criteria"},
                        page_size=100,
                        extra_headers={"X-RestLi-Method": "FINDER"},
                    )
                )
            ),
        )
        creatives.extend(creative_rows)

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
        result.warnings.append("Lead Sync byl vypnutý runtime flagem nebo mappingem.")

    reporting_datasets: dict[str, pd.DataFrame] = {}

    if include_reporting:
        reporting_datasets, reporting_raw, reporting_warnings = build_reporting_exports(
            client,
            account_ids=mapping.ad_account_ids,
            date_range=date_range,
        )
        result.raw_payloads.update(reporting_raw)
        result.raw_payloads["ad_analytics_raw"] = {
            key: value
            for key, value in reporting_raw.items()
            if key.startswith("insights_") or key.startswith("professional_demographics_")
        }
        result.warnings.extend(reporting_warnings)

        if not include_professional_demographics:
            reporting_datasets["professional_demographics_account"] = records_to_frame([])
            reporting_datasets["professional_demographics_campaign"] = records_to_frame([])
            reporting_datasets["professional_demographics_creative"] = records_to_frame([])

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

    landing_rows: list[dict[str, Any]] = []

    if include_web_scan:
        landing_urls = [
            str(row.get("landing_page_url") or "")
            for row in creatives
            if str(row.get("landing_page_url") or "")
        ]
        landing_rows, web_warnings = scan_landing_pages(
            landing_urls,
            timeout_seconds=runtime_config.request_timeout_seconds,
        )
        result.warnings.extend(web_warnings)
    else:
        result.warnings.append("Web scan byl vypnutý runtime flagem nebo mappingem.")

    utm_audit_rows = build_utm_audit_rows(
        landing_rows
        if landing_rows
        else [
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
        ],
        expected_source=mapping.expected_utm_source,
        expected_medium=mapping.expected_utm_medium,
        expected_domains=list(mapping.expected_domains),
    )

    datasets = {
        "connection_summary": records_to_frame([connection.to_dict()]),
        "mapping_used": records_to_frame([mapping.to_dict()]),
        "ad_accounts": records_to_frame(filtered_ad_accounts),
        "ad_account_users": records_to_frame(ad_account_users),
        "organizations": records_to_frame(organizations),
        "campaign_groups": records_to_frame(campaign_groups),
        "campaigns": records_to_frame(campaigns),
        "creatives": records_to_frame(creatives),
        "creative_content": records_to_frame(creatives),
        "conversions": records_to_frame(conversions),
        "campaign_conversions": records_to_frame(campaign_conversions),
        "insight_tags": records_to_frame(insight_tags),
        "insight_tag_domains": records_to_frame(insight_tag_domains),
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

    for key in (
        "insights_account_daily",
        "insights_campaign_daily",
        "insights_creative_daily",
        "insights_account_all",
        "insights_campaign_all",
        "insights_creative_all",
        "professional_demographics_account",
        "professional_demographics_campaign",
        "professional_demographics_creative",
    ):
        datasets.setdefault(key, records_to_frame([]))

    findings = build_audit_findings(
        connection=connection,
        mapping=mapping,
        datasets=datasets,
        gtm_crosscheck=gtm_crosscheck,
        web_scan_rows=landing_rows,
        export_warnings=result.warnings,
    )
    result.findings = findings
    result.datasets = datasets

    counts = {
        key: int(len(dataframe.index))
        for key, dataframe in datasets.items()
        if isinstance(dataframe, pd.DataFrame)
    }
    manifest.counts = counts
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