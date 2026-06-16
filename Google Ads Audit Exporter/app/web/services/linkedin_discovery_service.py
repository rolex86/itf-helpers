from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import json

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.connections import load_linkedin_connections
from app.integrations.linkedin.discovery import run_linkedin_discovery
from app.integrations.linkedin.models import LinkedInDiscoverySnapshot
from app.integrations.linkedin.token_store import load_token_payload
from app.web.services.linkedin_runtime import load_linkedin_runtime_config


def discovery_dir(project_root: Path) -> Path:
    return project_root / "app_state" / "linkedin_discovery"


def _snapshot_path(project_root: Path, connection_key: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    return discovery_dir(project_root) / f"{connection_key}_{timestamp}.json"


def _load_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_all_linkedin_discovery_snapshots(project_root: Path) -> list[dict[str, Any]]:
    root = discovery_dir(project_root)
    if not root.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        payload = _load_snapshot(path)
        if payload is not None:
            payload["_path"] = str(path)
            snapshots.append(payload)
    return snapshots


def run_linkedin_discovery_for_connection(project_root: Path, connection_key: str) -> dict[str, Any]:
    connection = next((item for item in load_linkedin_connections(project_root) if item.key == connection_key), None)
    if connection is None:
        raise ValueError(f"LinkedIn connection '{connection_key}' nebyla nalezena.")
    runtime_config = load_linkedin_runtime_config(project_root)
    token_payload = load_token_payload(project_root, connection.key)
    access_token = token_payload.get("access_token") or token_payload.get("manual_token")
    if not access_token:
        raise ValueError("Pro discovery chybí LinkedIn access token.")
    client = LinkedInRestClient(connection=connection, runtime_config=runtime_config, access_token=access_token)
    snapshot = run_linkedin_discovery(connection_key=connection.key, client=client)
    path = _snapshot_path(project_root, connection.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    payload = snapshot.to_dict()
    payload["_path"] = str(path)
    return payload

