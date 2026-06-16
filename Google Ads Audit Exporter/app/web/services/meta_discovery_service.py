from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.integrations.meta.connections import load_meta_connections
from app.integrations.meta.discovery import run_meta_discovery
from app.integrations.meta.models import MetaDiscoverySnapshot


def discovery_root(project_root: Path) -> Path:
    return project_root / "exports" / "_meta_discovery"


def discovery_snapshot_path(project_root: Path, connection_key: str) -> Path:
    return discovery_root(project_root) / f"{connection_key}.json"


def _snapshot_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "businesses": len(payload.get("businesses", []) or []),
        "ad_accounts": len(payload.get("ad_accounts", []) or []),
        "pixels": len(payload.get("pixels", []) or []),
        "catalogs": len(payload.get("catalogs", []) or []),
        "product_sets": len(payload.get("product_sets", []) or []),
        "product_feeds": len(payload.get("product_feeds", []) or []),
        "custom_conversions": len(payload.get("custom_conversions", []) or []),
        "warnings": len(payload.get("warnings", []) or []),
    }


def _snapshot_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload)
    summary.pop("raw_snapshots", None)
    summary["counts"] = _snapshot_counts(payload)
    return summary


def save_meta_discovery_snapshot(project_root: Path, snapshot: MetaDiscoverySnapshot) -> None:
    path = discovery_snapshot_path(project_root, snapshot.connection_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_meta_discovery_snapshot(project_root: Path, connection_key: str, *, include_raw: bool = False) -> dict[str, Any] | None:
    path = discovery_snapshot_path(project_root, connection_key)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return payload if include_raw else _snapshot_summary(payload)


def load_all_meta_discovery_snapshots(project_root: Path, *, include_raw: bool = False) -> dict[str, Any]:
    root = discovery_root(project_root)
    if not root.exists():
        return {}

    snapshots: dict[str, Any] = {}

    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        snapshots[path.stem] = payload if include_raw else _snapshot_summary(payload)

    return snapshots


def run_meta_discovery_for_connection(project_root: Path, connection_key: str) -> dict[str, Any]:
    connection = next(
        (item for item in load_meta_connections(project_root) if item.key == connection_key),
        None,
    )

    if connection is None:
        raise ValueError(f"Meta connection '{connection_key}' nebyla nalezena.")

    snapshot = run_meta_discovery(connection)
    save_meta_discovery_snapshot(project_root, snapshot)

    return _snapshot_summary(snapshot.to_dict())