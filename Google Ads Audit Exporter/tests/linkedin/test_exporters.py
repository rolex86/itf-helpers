from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.integrations.linkedin.exporters import export_linkedin_bundle
from app.integrations.linkedin.models import LinkedInAuditFinding, LinkedInExportManifest


def manifest() -> LinkedInExportManifest:
    return LinkedInExportManifest(
        platform="linkedin",
        context_key="ctx",
        connection_key="linkedin-main",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        status="success",
        api_version="202606",
        ad_account_ids=["123456"],
        organization_ids=["987"],
        counts={
            "campaigns": 1,
        },
    )


def finding() -> LinkedInAuditFinding:
    return LinkedInAuditFinding(
        severity="warning",
        category="tracking",
        code="LINKEDIN_TEST_FINDING",
        title="Test finding",
        detail="Test detail",
        recommendation="Test recommendation",
        evidence={
            "campaign_id": "200",
        },
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_linkedin_bundle_writes_expected_files(tmp_path: Path) -> None:
    export_root = tmp_path / "linkedin_export"
    export_manifest = manifest()

    datasets = {
        "campaigns": pd.DataFrame(
            [
                {
                    "campaign_id": "200",
                    "campaign_name": "Lead campaign",
                }
            ]
        ),
        "insights_account_all": pd.DataFrame(
            [
                {
                    "account_id": "123456",
                    "impressions": 1000,
                    "clicks": 50,
                    "costInLocalCurrency": 1000,
                }
            ]
        ),
        "professional_demographics_account": pd.DataFrame(
            [
                {
                    "account_id": "123456",
                    "requested_pivot": "MEMBER_SENIORITY",
                    "impressions": 100,
                }
            ]
        ),
        "utm_audit": pd.DataFrame(
            [
                {
                    "landing_page_url": "https://example.cz/",
                    "issue_code": "ok",
                }
            ]
        ),
    }
    raw_payloads = {
        "ad_accounts_raw": {
            "elements": [
                {
                    "id": "urn:li:sponsoredAccount:123456",
                }
            ]
        }
    }

    result = export_linkedin_bundle(
        export_root=export_root,
        datasets=datasets,
        raw_payloads=raw_payloads,
        manifest=export_manifest,
        findings=[finding()],
        report_markdown="# LinkedIn audit\n\nOK",
    )

    assert result == export_root

    assert (export_root / "campaigns.json").exists()
    assert (export_root / "insights_account_all.csv").exists()
    assert (export_root / "professional_demographics_account.csv").exists()
    assert (export_root / "utm_audit.csv").exists()
    assert (export_root / "raw" / "ad_accounts_raw.json").exists()
    assert (export_root / "audit_findings.json").exists()
    assert (export_root / "audit_report.md").exists()
    assert (export_root / "manifest.json").exists()

    assert read_json(export_root / "campaigns.json") == [
        {
            "campaign_id": "200",
            "campaign_name": "Lead campaign",
        }
    ]

    insights = pd.read_csv(export_root / "insights_account_all.csv")
    assert insights.iloc[0]["account_id"] == 123456
    assert insights.iloc[0]["impressions"] == 1000

    demographics = pd.read_csv(export_root / "professional_demographics_account.csv")
    assert demographics.iloc[0]["requested_pivot"] == "MEMBER_SENIORITY"

    raw_payload = read_json(export_root / "raw" / "ad_accounts_raw.json")
    assert raw_payload["elements"][0]["id"] == "urn:li:sponsoredAccount:123456"

    findings = read_json(export_root / "audit_findings.json")
    assert findings[0]["code"] == "LINKEDIN_TEST_FINDING"

    assert (export_root / "audit_report.md").read_text(encoding="utf-8") == "# LinkedIn audit\n\nOK"


def test_export_linkedin_bundle_exports_lead_form_responses_as_csv_and_json(tmp_path: Path) -> None:
    export_root = tmp_path / "linkedin_export"
    export_manifest = manifest()

    export_linkedin_bundle(
        export_root=export_root,
        datasets={
            "lead_form_responses": pd.DataFrame(
                [
                    {
                        "lead_id": "lead-1",
                        "email": "test@example.cz",
                        "answer_1_value": "Jan Novák",
                    }
                ]
            )
        },
        raw_payloads={},
        manifest=export_manifest,
        findings=[],
        report_markdown="report",
    )

    csv_target = export_root / "lead_form_responses.csv"
    json_target = export_root / "lead_form_responses.json"

    assert csv_target.exists()
    assert json_target.exists()

    csv_frame = pd.read_csv(csv_target)
    assert csv_frame.iloc[0]["lead_id"] == "lead-1"
    assert csv_frame.iloc[0]["email"] == "test@example.cz"

    json_payload = read_json(json_target)
    assert json_payload == [
        {
            "lead_id": "lead-1",
            "email": "test@example.cz",
            "answer_1_value": "Jan Novák",
        }
    ]

    manifest_payload = read_json(export_root / "manifest.json")
    assert "lead_form_responses.csv" in manifest_payload["files"]
    assert "lead_form_responses.json" in manifest_payload["files"]
    assert any(warning["category"] == "pii" for warning in manifest_payload["warnings"])


def test_export_linkedin_bundle_adds_pii_warning_for_raw_lead_response_payloads(tmp_path: Path) -> None:
    export_root = tmp_path / "linkedin_export"
    export_manifest = manifest()

    export_linkedin_bundle(
        export_root=export_root,
        datasets={},
        raw_payloads={
            "lead_form_responses_raw": {
                "elements": [
                    {
                        "email": "test@example.cz",
                    }
                ]
            }
        },
        manifest=export_manifest,
        findings=[],
        report_markdown="report",
    )

    manifest_payload = read_json(export_root / "manifest.json")

    assert "raw/lead_form_responses_raw.json" in manifest_payload["files"]
    assert any(
        warning["category"] == "pii" and warning["file"] == "raw/lead_form_responses_raw.json"
        for warning in manifest_payload["warnings"]
    )


def test_export_linkedin_bundle_does_not_duplicate_manifest_files(tmp_path: Path) -> None:
    export_root = tmp_path / "linkedin_export"
    export_manifest = manifest()
    export_manifest.files = ["campaigns.json"]

    export_linkedin_bundle(
        export_root=export_root,
        datasets={
            "campaigns": pd.DataFrame(
                [
                    {
                        "campaign_id": "200",
                    }
                ]
            )
        },
        raw_payloads={},
        manifest=export_manifest,
        findings=[],
        report_markdown="report",
    )

    manifest_payload = read_json(export_root / "manifest.json")

    assert manifest_payload["files"].count("campaigns.json") == 1
    assert manifest_payload["files"].count("manifest.json") == 1
    assert manifest_payload["files"].count("audit_findings.json") == 1
    assert manifest_payload["files"].count("audit_report.md") == 1


def test_export_linkedin_bundle_serializes_empty_dataframe_as_empty_json_list(tmp_path: Path) -> None:
    export_root = tmp_path / "linkedin_export"

    export_linkedin_bundle(
        export_root=export_root,
        datasets={
            "campaigns": pd.DataFrame(),
        },
        raw_payloads={},
        manifest=manifest(),
        findings=[],
        report_markdown="report",
    )

    assert read_json(export_root / "campaigns.json") == []


def test_export_linkedin_bundle_writes_manifest_after_files_are_collected(tmp_path: Path) -> None:
    export_root = tmp_path / "linkedin_export"

    export_linkedin_bundle(
        export_root=export_root,
        datasets={
            "insights_campaign_all": pd.DataFrame(
                [
                    {
                        "campaign_id": "200",
                        "clicks": 10,
                    }
                ]
            )
        },
        raw_payloads={
            "campaigns_raw": [
                {
                    "id": "urn:li:sponsoredCampaign:200",
                }
            ]
        },
        manifest=manifest(),
        findings=[finding()],
        report_markdown="report",
    )

    manifest_payload = read_json(export_root / "manifest.json")

    assert manifest_payload["platform"] == "linkedin"
    assert manifest_payload["context_key"] == "ctx"
    assert manifest_payload["connection_key"] == "linkedin-main"
    assert manifest_payload["api_version"] == "202606"

    assert "insights_campaign_all.csv" in manifest_payload["files"]
    assert "raw/campaigns_raw.json" in manifest_payload["files"]
    assert "audit_findings.json" in manifest_payload["files"]
    assert "audit_report.md" in manifest_payload["files"]
    assert "manifest.json" in manifest_payload["files"]