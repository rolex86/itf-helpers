from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config.env_settings import load_env_config
from app.discovery.export import discovery_output_dir
from app.discovery.runner import run_all_discovery


DISCOVERY_FILES = {
    "google_ads_customers": "google_ads_customers.csv",
    "ga4_properties": "ga4_properties.csv",
    "gsc_sites": "gsc_sites.csv",
    "gtm_containers": "gtm_containers.csv",
    "merchant_accounts": "merchant_accounts.csv",
}


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def load_discovery_tables(project_root: Path) -> dict[str, list[dict[str, object]]]:
    root = discovery_output_dir(project_root)
    tables: dict[str, list[dict[str, object]]] = {}
    for key, filename in DISCOVERY_FILES.items():
        frame = _load_csv(root / filename)
        tables[key] = frame.fillna("").to_dict(orient="records") if not frame.empty else []
    return tables


def run_discovery(project_root: Path) -> dict[str, object]:
    env_config = load_env_config(project_root / ".env")
    result = run_all_discovery(project_root=project_root, env_config=env_config)
    return {
        "tables": {
            key: dataframe.fillna("").to_dict(orient="records")
            for key, dataframe in result.datasets.items()
        },
        "csv_paths": {key: str(path) for key, path in result.csv_paths.items()},
        "errors": result.errors,
    }
