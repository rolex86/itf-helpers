from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.export.csv_exporter import export_csv
from app.export.metadata_exporter import write_json
from app.integrations.meta.models import MetaAuditFinding


def _json_safe(value: Any) -> Any:
    if value is None:
        return None

    if value is pd.NA:
        return None

    try:
        if pd.isna(value) and not isinstance(value, (list, tuple, dict, set)):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(key): _json_safe(nested) for key, nested in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(nested) for nested in value]

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass

    return str(value)


def _dataframe_to_records(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    if dataframe.empty:
        return []

    records = dataframe.to_dict(orient="records")
    return [_json_safe(record) for record in records]


def _write_dataset(export_root: Path, key: str, dataframe: pd.DataFrame) -> None:
    if key.endswith("_daily"):
        export_csv(dataframe, export_root / f"{key}.csv")
        return

    write_json(
        export_root / f"{key}.json",
        _dataframe_to_records(dataframe),
    )


def export_meta_bundle(
    *,
    export_root: Path,
    datasets: dict[str, pd.DataFrame],
    raw_payloads: dict[str, Any],
    findings: list[MetaAuditFinding],
    report_markdown: str,
) -> Path:
    export_root.mkdir(parents=True, exist_ok=True)

    for key, dataframe in datasets.items():
        _write_dataset(export_root, key, dataframe)

    write_json(export_root / "meta_raw_snapshots.json", _json_safe(raw_payloads))
    write_json(export_root / "audit_findings.json", [finding.to_dict() for finding in findings])
    (export_root / "audit_report.md").write_text(report_markdown, encoding="utf-8")

    return export_root