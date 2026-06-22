from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.export.csv_exporter import export_csv
from app.export.metadata_exporter import write_json
from app.integrations.sklik.models import SklikAuditFinding, SklikExportManifest
from app.integrations.sklik.normalizers import dataframe_to_records


CSV_DATASETS = {
    "account_stats_daily",
    "account_stats_total",
    "account_stats_by_conversion_daily",
    "campaigns_daily",
    "campaigns_total",
    "campaign_settings_audit",
    "groups_daily",
    "groups_total",
    "ads_daily",
    "ads_total",
    "ad_landing_pages",
    "ad_creatives_audit",
    "banners_daily",
    "banners_total",
    "banner_assets",
    "keywords_daily",
    "keywords_total",
    "negative_keywords_daily",
    "campaign_negative_keywords_daily",
    "queries_daily",
    "queries_total",
    "search_terms_audit",
    "shared_budget_campaigns",
    "sitelinks_daily",
    "sitelinks_total",
    "retargeting_daily",
    "retargeting_total",
    "fenix_campaign_stats_daily",
    "fenix_campaign_stats_total",
    "utm_audit",
}

PII_DATASETS = {
    "fenix_gdpr_withdrawals",
}


def _serialize(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return dataframe_to_records(value)
    return value


def _relative_path(export_root: Path, target: Path) -> str:
    return str(target.relative_to(export_root)).replace("\\", "/")


def _add_file(manifest: SklikExportManifest, export_root: Path, target: Path) -> None:
    relative = _relative_path(export_root, target)
    if relative not in manifest.files:
        manifest.files.append(relative)


def export_sklik_bundle(
    *,
    export_root: Path,
    datasets: dict[str, Any],
    raw_payloads: dict[str, Any],
    manifest: SklikExportManifest,
    findings: list[SklikAuditFinding],
    report_markdown: str,
) -> Path:
    export_root.mkdir(parents=True, exist_ok=True)

    for key, value in datasets.items():
        target = export_root / (f"{key}.csv" if key in CSV_DATASETS else f"{key}.json")

        if key in CSV_DATASETS:
            dataframe = value if isinstance(value, pd.DataFrame) else pd.DataFrame(_serialize(value))
            export_csv(dataframe, target)
        else:
            write_json(target, _serialize(value))

        _add_file(manifest, export_root, target)

        if key in PII_DATASETS:
            relative = _relative_path(export_root, target)
            if relative not in manifest.pii_files:
                manifest.pii_files.append(relative)

    if raw_payloads:
        raw_dir = export_root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        for key, payload in raw_payloads.items():
            target = raw_dir / f"{key}.json"
            write_json(target, payload)
            _add_file(manifest, export_root, target)

    findings_target = export_root / "audit_findings.json"
    report_target = export_root / "audit_report.md"
    manifest_target = export_root / "manifest.json"

    write_json(findings_target, [finding.to_dict() for finding in findings])
    report_target.write_text(report_markdown, encoding="utf-8")
    _add_file(manifest, export_root, findings_target)
    _add_file(manifest, export_root, report_target)

    write_json(manifest_target, manifest.to_dict())
    _add_file(manifest, export_root, manifest_target)
    write_json(manifest_target, manifest.to_dict())

    return export_root
