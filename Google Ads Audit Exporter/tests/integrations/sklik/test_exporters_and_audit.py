from __future__ import annotations

import json

import pandas as pd

from app.integrations.sklik.audit_rules import build_audit_findings
from app.integrations.sklik.exporters import export_sklik_bundle
from app.integrations.sklik.models import SklikAccountContextMapping, SklikConnection, SklikExportManifest


def _mapping(**overrides):
    payload = {
        "context_key": "shopid_cz",
        "enabled": True,
        "connection_key": "itfuture",
        "drak_user_ids": ["123456"],
        "fenix_premise_ids": ["987"],
        "expected_domains": ["shopid.cz"],
        "expected_utm_source": ["sklik"],
        "expected_utm_medium": ["cpc"],
        "enable_fenix": True,
        "enable_web_scan": True,
    }
    payload.update(overrides)
    return SklikAccountContextMapping(**payload)


def _connection() -> SklikConnection:
    return SklikConnection(
        key="itfuture",
        label="ITFuture",
        drak_enabled=True,
        fenix_enabled=True,
        drak_token_env_key="SKLIK_DRAK_TOKEN__ITFUTURE",
        fenix_refresh_token_env_key="SKLIK_FENIX_REFRESH_TOKEN__ITFUTURE",
    )


def test_export_manifest_contains_all_files(tmp_path) -> None:
    manifest = SklikExportManifest(
        platform="sklik",
        context_key="shopid_cz",
        export_started_at="2026-06-18T00:00:00+00:00",
        connection_key="itfuture",
    )
    export_root = export_sklik_bundle(
        export_root=tmp_path / "exports",
        datasets={
            "connection_summary": {"ok": True},
            "mapping_used": {"context_key": "shopid_cz"},
            "campaigns": [{"campaign_id": 1}],
            "queries_daily": pd.DataFrame([{"date": "2026-06-01", "clicks": 1}]),
        },
        raw_payloads={"client_get": {"session": "***"}},
        manifest=manifest,
        findings=[],
        report_markdown="# report",
    )

    payload = json.loads((export_root / "manifest.json").read_text(encoding="utf-8"))
    assert "connection_summary.json" in payload["files"]
    assert "campaigns.json" in payload["files"]
    assert "queries_daily.csv" in payload["files"]
    assert "audit_findings.json" in payload["files"]
    assert "manifest.json" in payload["files"]


def test_audit_rules_autotagging_and_campaign_toggle_findings() -> None:
    findings = build_audit_findings(
        connection=_connection(),
        mapping=_mapping(),
        datasets={
            "utm_settings_audit": [
                {
                    "user_id": "123456",
                    "enabled": False,
                    "utm_source_value": "facebook",
                    "utm_medium_value": "social",
                    "utm_campaign_enabled": False,
                    "expected_utm_source": ["sklik"],
                    "expected_utm_medium": ["cpc"],
                }
            ],
            "autotagging_default": [{"user_id": "123456", "enabled": False}],
            "utm_audit": pd.DataFrame(),
            "fenix_campaigns": [{"id": 1}],
        },
        gtm_crosscheck={},
        web_scan_rows=[],
        export_warnings=[],
        export_info_notes=[],
    )
    codes = {item.code for item in findings}
    assert "SKLIK_AUTOTAGGING_DISABLED" in codes
    assert "SKLIK_UTM_CAMPAIGN_DISABLED" in codes
    assert "SKLIK_UTM_SOURCE_NONSTANDARD" in codes
    assert "SKLIK_UTM_MEDIUM_NONSTANDARD" in codes
    assert "SKLIK_AUTOTAGGING_DEFAULT_DISABLED" in codes


def test_audit_rules_wrong_domain_and_missing_campaign() -> None:
    findings = build_audit_findings(
        connection=_connection(),
        mapping=_mapping(),
        datasets={
            "utm_settings_audit": [],
            "autotagging_default": [],
            "utm_audit": pd.DataFrame(
                [
                    {
                        "issue_code": "domain_mismatch",
                        "final_domain": "other.example",
                        "ad_id": "55",
                    },
                    {
                        "issue_code": "missing_utm_campaign",
                        "final_domain": "shopid.cz",
                        "ad_id": "56",
                    },
                ]
            ),
            "fenix_campaigns": [{"id": 1}],
        },
        gtm_crosscheck={},
        web_scan_rows=[],
        export_warnings=[],
        export_info_notes=[],
    )
    codes = {item.code for item in findings}
    assert "SKLIK_AD_FINAL_URL_WRONG_DOMAIN" in codes
    assert "SKLIK_UTM_CAMPAIGN_MISSING" in codes


def test_audit_rules_warns_about_manual_fenix_premise_mapping() -> None:
    findings = build_audit_findings(
        connection=_connection(),
        mapping=_mapping(),
        datasets={
            "utm_settings_audit": [],
            "autotagging_default": [],
            "utm_audit": pd.DataFrame(),
            "fenix_campaigns": [{"id": 1}],
        },
        gtm_crosscheck={},
        web_scan_rows=[],
        export_warnings=["FENIX_PREMISES_AUTODISCOVERY_NOT_CONFIRMED_BY_PUBLIC_DOCS"],
        export_info_notes=[],
    )

    assert any(item.code == "SKLIK_FENIX_PREMISE_AUTODISCOVERY_UNCONFIRMED" for item in findings)
