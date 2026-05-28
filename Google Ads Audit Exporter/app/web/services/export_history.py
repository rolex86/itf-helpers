from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ExportHistoryItem:
    directory_name: str
    export_path: str
    xlsx_path: str
    relative_xlsx_path: str
    created_at: str
    customer_id: str
    date_label: str
    error_count: int
    query_count: int


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_export_history(exports_root: Path) -> list[ExportHistoryItem]:
    if not exports_root.exists():
        return []

    items: list[ExportHistoryItem] = []
    directories = [item for item in exports_root.rglob("*") if item.is_dir() and (item / "metadata").exists()]
    for directory in sorted(directories, key=lambda item: str(item).lower(), reverse=True):
        metadata_dir = directory / "metadata"
        config = _read_json(metadata_dir / "export_config.json") or {}
        errors = _read_json(metadata_dir / "errors.json") or []
        queries = _read_json(metadata_dir / "query_log.json") or []

        if not isinstance(config, dict):
            config = {}
        if not isinstance(errors, list):
            errors = []
        if not isinstance(queries, list):
            queries = []

        xlsx_candidates = list(directory.glob("*.xlsx"))
        xlsx_path = str(xlsx_candidates[0]) if xlsx_candidates else ""
        relative_xlsx_path = str(xlsx_candidates[0].relative_to(exports_root.parent)).replace("\\", "/") if xlsx_candidates else ""
        date_range = config.get("date_range", {}) if isinstance(config.get("date_range", {}), dict) else {}
        relative_dir = str(directory.relative_to(exports_root)).replace("\\", "/")

        items.append(
            ExportHistoryItem(
                directory_name=relative_dir,
                export_path=str(directory),
                xlsx_path=xlsx_path,
                relative_xlsx_path=relative_xlsx_path,
                created_at=directory.name.split("_")[0] if "_" in directory.name else directory.name,
                customer_id=str(config.get("customer_id", "")),
                date_label=str(date_range.get("label", "")),
                error_count=len(errors),
                query_count=len(queries),
            )
        )
    return items
