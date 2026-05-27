from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(slots=True)
class ExportPaths:
    base_dir: Path
    raw_dir: Path
    metadata_dir: Path
    xlsx_path: Path
    log_path: Path


def prepare_export_paths(
    project_root: Path,
    base_dir_name: str,
    customer_id: str,
    run_date: date,
    xlsx_filename: str,
) -> ExportPaths:
    exports_root = project_root / base_dir_name
    exports_root.mkdir(parents=True, exist_ok=True)

    base_name = f"{run_date.isoformat()}_{customer_id}"
    base_dir = exports_root / base_name
    suffix = 1
    while base_dir.exists():
        suffix += 1
        base_dir = exports_root / f"{base_name}_{suffix:02d}"

    raw_dir = base_dir / "raw"
    metadata_dir = base_dir / "metadata"
    raw_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    return ExportPaths(
        base_dir=base_dir,
        raw_dir=raw_dir,
        metadata_dir=metadata_dir,
        xlsx_path=base_dir / xlsx_filename,
        log_path=metadata_dir / "export.log",
    )
