from __future__ import annotations

from typing import Any

import pandas as pd

from app.integrations.linkedin.audit_rules import build_audit_findings
from app.integrations.linkedin.models import LinkedInAccountContextMapping, LinkedInConnection


def connection(**overrides: Any) -> LinkedInConnection:
    payload: dict[str, Any] = {
        "key": "main",
        "label": "Main",
        "status": "active",
        "granted_scopes": ["r_ads", "r_ads_reporting", "r_marketing_leadgen_automation"],
        "token_expires_at": "2999-01-01T00:00:00+00:00",
        "refresh_token_expires_at": "2999-01-01T00:00:00+00:00",
        "last_error": "",
    }
    payload.update(overrides)
    return LinkedInConnection(**payload)


def mapping(**overrides: Any) -> LinkedInAccountContextMapping:
    payload: dict[str, Any] = {
        "context_key": "ctx",
        "enabled": True,
        "expected_conversion_type": "lead",
        "expected_domains": ["example.cz"],
        "lead_sync_enabled": True,
    }
    payload.update(overrides)
    return LinkedInAccountContextMapping(**payload)


def empty_datasets() -> dict[str, pd.DataFrame]:
    return {
        "ad_accounts": pd.DataFrame(),
        "campaign_groups": pd.DataFrame(),
        "campaigns": pd.DataFrame(),
        "creatives": pd.DataFrame(),
        "lead_forms": pd.DataFrame(),
        "insight_tags": pd.DataFrame(),
        "insight_tag_domains": pd.DataFrame(),
        "campaign_conversions": pd.DataFrame(),
        "insights_campaign_all": pd.DataFrame(),
        "utm_audit": pd.DataFrame(),
    }


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def finding_by_code(findings, code: str):
    return [finding for finding in findings if finding.code == code]


def test_audit_rules_detect_missing_scope() -> None:
    findings = build_audit_findings(
        connection=connection(granted_scopes=["r_ads"]),
        mapping=mapping(),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_SCOPE_MISSING" in codes(findings)
    assert any(finding.evidence.get("scope") == "r_ads_reporting" for finding in findings)


def test_audit_rules_warns_when_scopes_are_unknown() -> None:
    findings = build_audit_findings(
        connection=connection(granted_scopes=[]),
        mapping=mapping(),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_SCOPES_UNKNOWN" in codes(findings)


def test_audit_rules_warns_when_lead_sync_scope_is_missing() -> None:
    findings = build_audit_findings(
        connection=connection(granted_scopes=["r_ads", "r_ads_reporting"]),
        mapping=mapping(lead_sync_enabled=True),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_LEAD_SYNC_SCOPE_MISSING" in codes(findings)


def test_audit_rules_detects_inactive_connection_and_expiring_tokens() -> None:
    findings = build_audit_findings(
        connection=connection(
            status="needs_reauth",
            last_error="Token expired",
            token_expires_at="2000-01-01T00:00:00+00:00",
            refresh_token_expires_at="2000-01-01T00:00:00+00:00",
        ),
        mapping=mapping(),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_CONNECTION_NOT_ACTIVE" in found_codes
    assert "LINKEDIN_ACCESS_TOKEN_EXPIRES_SOON" in found_codes
    assert "LINKEDIN_REFRESH_TOKEN_EXPIRES_SOON" in found_codes

    inactive = finding_by_code(findings, "LINKEDIN_CONNECTION_NOT_ACTIVE")[0]
    assert inactive.severity == "critical"
    assert inactive.evidence["status"] == "needs_reauth"
    assert inactive.evidence["last_error"] == "Token expired"


def test_audit_rules_detects_no_ad_accounts_and_no_campaigns() -> None:
    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(enabled=True),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_NO_AD_ACCOUNTS_FOUND" in found_codes
    assert "LINKEDIN_NO_CAMPAIGNS_FOUND" in found_codes


def test_audit_rules_detects_campaigns_without_creatives() -> None:
    datasets = empty_datasets()
    datasets["ad_accounts"] = pd.DataFrame([{"account_id": "123456"}])
    datasets["campaigns"] = pd.DataFrame(
        [
            {
                "campaign_id": "200",
                "campaign_name": "Lead campaign",
                "status": "ACTIVE",
            }
        ]
    )
    datasets["creatives"] = pd.DataFrame()

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=datasets,
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_ACTIVE_CAMPAIGNS_WITHOUT_CREATIVES" in codes(findings)


def test_audit_rules_detects_active_campaign_group_without_campaigns() -> None:
    datasets = empty_datasets()
    datasets["ad_accounts"] = pd.DataFrame([{"account_id": "123456"}])
    datasets["campaign_groups"] = pd.DataFrame(
        [
            {
                "campaign_group_id": "100",
                "name": "Active group",
                "status": "ACTIVE",
            }
        ]
    )
    datasets["campaigns"] = pd.DataFrame(
        [
            {
                "campaign_id": "200",
                "campaign_group_id": "999",
                "campaign_name": "Other campaign",
            }
        ]
    )
    datasets["creatives"] = pd.DataFrame([{"creative_id": "300"}])

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=datasets,
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_ACTIVE_GROUP_WITHOUT_CAMPAIGNS" in codes(findings)

    finding = finding_by_code(findings, "LINKEDIN_ACTIVE_GROUP_WITHOUT_CAMPAIGNS")[0]
    assert finding.entity_type == "campaign_group"
    assert finding.entity_id == "100"


def test_audit_rules_detects_missing_tracking_metadata_for_lead_context() -> None:
    datasets = empty_datasets()
    datasets["ad_accounts"] = pd.DataFrame([{"account_id": "123456"}])
    datasets["campaigns"] = pd.DataFrame([{"campaign_id": "200"}])
    datasets["creatives"] = pd.DataFrame([{"creative_id": "300"}])

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(expected_conversion_type="lead", lead_sync_enabled=True, expected_domains=["example.cz"]),
        datasets=datasets,
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_LEAD_FORMS_MISSING" in found_codes
    assert "LINKEDIN_INSIGHT_TAGS_MISSING" in found_codes
    assert "LINKEDIN_CAMPAIGN_CONVERSIONS_MISSING" in found_codes


def test_audit_rules_detects_insight_tag_domain_mismatch() -> None:
    datasets = empty_datasets()
    datasets["ad_accounts"] = pd.DataFrame([{"account_id": "123456"}])
    datasets["campaigns"] = pd.DataFrame([{"campaign_id": "200"}])
    datasets["creatives"] = pd.DataFrame([{"creative_id": "300"}])
    datasets["lead_forms"] = pd.DataFrame([{"lead_form_id": "111"}])
    datasets["insight_tags"] = pd.DataFrame([{"insight_tag_id": "555"}])
    datasets["campaign_conversions"] = pd.DataFrame([{"campaign_id": "200", "conversion_id": "999"}])
    datasets["insight_tag_domains"] = pd.DataFrame([{"domain": "other.cz"}])

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(expected_domains=["example.cz"]),
        datasets=datasets,
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_INSIGHT_TAG_DOMAIN_MISMATCH" in codes(findings)

    finding = finding_by_code(findings, "LINKEDIN_INSIGHT_TAG_DOMAIN_MISMATCH")[0]
    assert finding.evidence["expected_domains"] == ["example.cz"]
    assert finding.evidence["exported_domains"] == ["other.cz"]


def test_audit_rules_detects_gtm_crosscheck_warnings_and_missing_match() -> None:
    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(expected_domains=["example.cz"]),
        datasets=empty_datasets(),
        gtm_crosscheck={
            "matched": False,
            "warnings": ["Missing LinkedIn conversion tag"],
            "found_insight_tags": [],
            "found_conversion_tags": [],
        },
        web_scan_rows=[],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_GTM_CROSSCHECK_WARNING" in found_codes
    assert "LINKEDIN_GTM_NOT_MATCHED" in found_codes

    warning = finding_by_code(findings, "LINKEDIN_GTM_CROSSCHECK_WARNING")[0]
    assert warning.detail == "Missing LinkedIn conversion tag"


def test_audit_rules_detects_bad_landing_pages_and_missing_insight_tag() -> None:
    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[
            {
                "source_url": "https://example.cz/broken/",
                "status_code": 404,
                "has_insight_tag": False,
            }
        ],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_LANDING_NON_200" in found_codes
    assert "LINKEDIN_INSIGHT_TAG_NOT_FOUND_ON_PAGE" in found_codes

    status_finding = finding_by_code(findings, "LINKEDIN_LANDING_NON_200")[0]
    assert status_finding.severity == "error"
    assert status_finding.entity_type == "landing_page"
    assert status_finding.entity_id == "https://example.cz/broken/"


def test_audit_rules_detects_performance_issues() -> None:
    datasets = empty_datasets()
    datasets["ad_accounts"] = pd.DataFrame([{"account_id": "123456"}])
    datasets["campaigns"] = pd.DataFrame([{"campaign_id": "200", "campaign_name": "Lead campaign"}])
    datasets["creatives"] = pd.DataFrame([{"creative_id": "300"}])
    datasets["lead_forms"] = pd.DataFrame([{"lead_form_id": "111"}])
    datasets["insight_tags"] = pd.DataFrame([{"insight_tag_id": "555"}])
    datasets["campaign_conversions"] = pd.DataFrame([{"campaign_id": "200", "conversion_id": "999"}])
    datasets["insight_tag_domains"] = pd.DataFrame([{"domain": "example.cz"}])
    datasets["insights_campaign_all"] = pd.DataFrame(
        [
            {
                "campaign_id": "200",
                "campaign_name": "Lead campaign",
                "costInLocalCurrency": 1000,
                "clicks": 50,
                "impressions": 2000,
                "ctr": 0.001,
                "averageCpc": 200,
                "externalWebsiteConversions": 0,
                "oneClickLeads": 0,
            },
            {
                "campaign_id": "201",
                "campaign_name": "Expensive lead campaign",
                "costInLocalCurrency": 10000,
                "clicks": 100,
                "impressions": 5000,
                "ctr": 0.02,
                "averageCpc": 100,
                "externalWebsiteConversions": 2,
                "oneClickLeads": 0,
            },
        ]
    )

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=datasets,
        gtm_crosscheck={"matched": True, "warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_SPEND_NO_CONVERSIONS" in found_codes
    assert "LINKEDIN_CLICKS_NO_CONVERSIONS" in found_codes
    assert "LINKEDIN_LOW_CTR" in found_codes
    assert "LINKEDIN_HIGH_CPC" in found_codes
    assert "LINKEDIN_HIGH_CPL" in found_codes

    high_cpl = finding_by_code(findings, "LINKEDIN_HIGH_CPL")[0]
    assert high_cpl.entity_id == "201"
    assert high_cpl.evidence["cpl"] == 5000.0


def test_audit_rules_uses_combined_lead_and_conversion_columns() -> None:
    datasets = empty_datasets()
    datasets["ad_accounts"] = pd.DataFrame([{"account_id": "123456"}])
    datasets["campaigns"] = pd.DataFrame([{"campaign_id": "200"}])
    datasets["creatives"] = pd.DataFrame([{"creative_id": "300"}])
    datasets["lead_forms"] = pd.DataFrame([{"lead_form_id": "111"}])
    datasets["insight_tags"] = pd.DataFrame([{"insight_tag_id": "555"}])
    datasets["campaign_conversions"] = pd.DataFrame([{"campaign_id": "200", "conversion_id": "999"}])
    datasets["insight_tag_domains"] = pd.DataFrame([{"domain": "example.cz"}])
    datasets["insights_campaign_all"] = pd.DataFrame(
        [
            {
                "campaign_id": "200",
                "costInLocalCurrency": 1000,
                "clicks": 50,
                "impressions": 2000,
                "ctr": 0.01,
                "averageCpc": 20,
                "externalWebsiteConversions": 1,
                "externalWebsitePostClickConversions": 1,
                "externalWebsitePostViewConversions": 1,
                "oneClickLeads": 1,
            }
        ]
    )

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=datasets,
        gtm_crosscheck={"matched": True, "warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    found_codes = codes(findings)

    assert "LINKEDIN_SPEND_NO_CONVERSIONS" not in found_codes
    assert "LINKEDIN_CLICKS_NO_CONVERSIONS" not in found_codes


def test_audit_rules_adds_export_warnings() -> None:
    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=empty_datasets(),
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=["adAnalytics endpoint failed"],
    )

    assert "LINKEDIN_EXPORT_WARNING" in codes(findings)

    warning = finding_by_code(findings, "LINKEDIN_EXPORT_WARNING")[0]
    assert warning.severity == "info"
    assert warning.detail == "adAnalytics endpoint failed"


def test_audit_rules_turns_utm_audit_rows_into_findings() -> None:
    datasets = empty_datasets()
    datasets["utm_audit"] = pd.DataFrame(
        [
            {
                "landing_page_url": "https://example.cz/page/",
                "issue_code": "missing_utm_source",
                "severity": "warning",
                "recommendation": "Doplň utm_source=linkedin.",
            },
            {
                "landing_page_url": "https://example.cz/ok/",
                "issue_code": "ok",
                "severity": "info",
                "recommendation": "OK",
            },
        ]
    )

    findings = build_audit_findings(
        connection=connection(),
        mapping=mapping(),
        datasets=datasets,
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )

    assert "LINKEDIN_UTM_MISSING_UTM_SOURCE" in codes(findings)
    assert "LINKEDIN_UTM_OK" not in codes(findings)

    utm_finding = finding_by_code(findings, "LINKEDIN_UTM_MISSING_UTM_SOURCE")[0]
    assert utm_finding.entity_type == "landing_page"
    assert utm_finding.entity_id == "https://example.cz/page/"
    assert utm_finding.recommendation == "Doplň utm_source=linkedin."