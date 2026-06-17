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
    "insights_account_all",
    "insights_campaign_all",
    "insights_creative_all",
    "professional_demographics_account",
    "professional_demographics_campaign",
    "professional_demographics_creative",
    "utm_audit",
}


PII_DATASETS = {
    "lead_form_responses",
    "lead_form_responses_raw",
}


def _serialize_frame(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    if dataframe.empty:
        return []

    return json.loads(dataframe.to_json(orient="records"))


def _relative_path(export_root: Path, target: Path) -> str:
    return str(target.relative_to(export_root)).replace("\\", "/")


def _add_file(manifest: LinkedInExportManifest, export_root: Path, target: Path) -> None:
    relative = _relative_path(export_root, target)
    if relative not in manifest.files:
        manifest.files.append(relative)


def _is_pii_payload_key(key: str) -> bool:
    return key in PII_DATASETS or "lead_form_responses" in key


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

    for key, dataframe in datasets.items():
        if key == "lead_form_responses":
            csv_target = export_root / "lead_form_responses.csv"
            json_target = export_root / "lead_form_responses.json"

            export_csv(dataframe, csv_target)
            write_json(json_target, _serialize_frame(dataframe))

            _add_file(manifest, export_root, csv_target)
            _add_file(manifest, export_root, json_target)
            continue

        target = export_root / (f"{key}.csv" if key in CSV_DATASETS else f"{key}.json")

        if key in CSV_DATASETS:
            export_csv(dataframe, target)
        else:
            write_json(target, _serialize_frame(dataframe))

        _add_file(manifest, export_root, target)

    if raw_payloads:
        raw_dir = export_root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        for key, payload in raw_payloads.items():
            target = raw_dir / f"{key}.json"
            write_json(target, payload)
            _add_file(manifest, export_root, target)

            if _is_pii_payload_key(key):
                manifest.infos.append(
                    {
                        "message": f"Soubor {_relative_path(export_root, target)} může obsahovat PII. Nesdílet mimo oprávněné osoby.",
                        "category": "pii",
                        "file": _relative_path(export_root, target),
                    }
                )

    if "lead_form_responses.csv" in manifest.files:
        manifest.infos.append(
            {
                "message": "Soubor lead_form_responses.csv může obsahovat PII. Nesdílet mimo oprávněné osoby.",
                "category": "pii",
                "file": "lead_form_responses.csv",
            }
        )

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
