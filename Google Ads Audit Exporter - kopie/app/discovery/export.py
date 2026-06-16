from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from app.discovery.models import DiscoveryResult
from app.export.csv_exporter import export_csv


def discovery_output_dir(project_root: Path) -> Path:
    path = project_root / "exports" / "_discovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def empty_discovery_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_discovery_dataset(
    result: DiscoveryResult,
    *,
    project_root: Path,
    key: str,
    dataframe: pd.DataFrame,
) -> None:
    target = discovery_output_dir(project_root) / f"{key}.csv"
    export_csv(dataframe, target)
    result.datasets[key] = dataframe
    result.csv_paths[key] = target


def run_discovery_step(
    result: DiscoveryResult,
    *,
    key: str,
    columns: list[str],
    runner: Callable[[], pd.DataFrame],
) -> pd.DataFrame:
    try:
        frame = runner()
    except Exception as exc:
        frame = empty_discovery_frame(columns)
        result.errors.append(
            {
                "discovery_key": key,
                "message": str(exc),
                "timestamp": timestamp_iso(),
            }
        )
    return frame
