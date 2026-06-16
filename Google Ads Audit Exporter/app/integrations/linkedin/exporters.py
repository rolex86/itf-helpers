from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.export.csv_exporter import export_csv
from app.export.metadata_exporter import write_json
from app.integrations.linkedin.models import LinkedInAuditFinding, LinkedInExportManifest


CSV_DATASETS = {
    "lead_form_responses",
    "insights_account_daily",
    "insights_campaign_daily",
    "insights_creative_daily",
    "insights_campaign_all",
    "insights_creative_all",
    "professional_demographics_campaign",
    "professional_demographics_creative",
    "utm_audit",
}


def _serialize_frame(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    if dataframe.empty:
        return []
    return json.loads(dataframe.to_json(orient="records"))


def export_linkedin_bundle(
    *,
    export_root: Path,
    datasets: dict[str, pd.DataFrame],
    raw_payloads: dict[str, Any],
    manifest: LinkedInExportManifest,
    findings: list[LinkedInAuditFinding],
    report_markdown: str,
) -> Path:
    export_root.mkdir(parents=True, exist_ok=True)
    raw_dir = export_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for key, dataframe in datasets.items():
        if key == "lead_form_responses":
            csv_target = export_root / "lead_form_responses.csv"
            json_target = export_root / "lead_form_responses.json"
            export_csv(dataframe, csv_target)
            write_json(json_target, _serialize_frame(dataframe))
            manifest.files.append(str(csv_target.relative_to(export_root)).replace("\\", "/"))
            manifest.files.append(str(json_target.relative_to(export_root)).replace("\\", "/"))
            continue
        target = export_root / (f"{key}.csv" if key in CSV_DATASETS else f"{key}.json")
        if key in CSV_DATASETS:
            export_csv(dataframe, target)
        else:
            write_json(target, _serialize_frame(dataframe))
        manifest.files.append(str(target.relative_to(export_root)).replace("\\", "/"))

    for key, payload in raw_payloads.items():
        target = raw_dir / f"{key}.json"
        write_json(target, payload)
        manifest.files.append(str(target.relative_to(export_root)).replace("\\", "/"))

    write_json(export_root / "manifest.json", manifest.to_dict())
    write_json(export_root / "audit_findings.json", [finding.to_dict() for finding in findings])
    (export_root / "audit_report.md").write_text(report_markdown, encoding="utf-8")
    manifest.files.extend(["manifest.json", "audit_findings.json", "audit_report.md"])
    write_json(export_root / "manifest.json", manifest.to_dict())
    return export_root
