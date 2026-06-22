from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.integrations.sklik.auth import get_secret, load_sklik_runtime_config
from app.integrations.sklik.client_drak import SklikDrakClient
from app.integrations.sklik.client_fenix import SklikFenixClient
from app.integrations.sklik.connections import load_sklik_connections
from app.integrations.sklik.discovery import run_sklik_discovery


def discovery_dir(project_root: Path) -> Path:
    return project_root / "app_state" / "sklik_discovery"


def discovery_snapshot_path(project_root: Path, connection_key: str, *, prefix: str = "") -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    name = f"{prefix}{connection_key}_{timestamp}.json"
    return discovery_dir(project_root) / name


def load_all_sklik_discovery_snapshots(project_root: Path) -> list[dict[str, Any]]:
    root = discovery_dir(project_root)
    if not root.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            snapshots.append(payload)
    return snapshots


def run_sklik_discovery_for_connection(project_root: Path, connection_key: str) -> dict[str, Any]:
    connection = next((item for item in load_sklik_connections(project_root) if item.key == connection_key), None)
    if connection is None:
        raise ValueError(f"Sklik connection '{connection_key}' nebyla nalezena.")

    runtime_config = load_sklik_runtime_config(project_root)
    drak_token = get_secret(project_root, connection.drak_token_env_key)
    fenix_refresh_token = get_secret(project_root, connection.fenix_refresh_token_env_key)

    drak_client = None
    if connection.drak_enabled and drak_token:
        drak_client = SklikDrakClient(
            token=drak_token,
            base_url=runtime_config.drak_base_url,
            timeout=runtime_config.request_timeout_seconds,
            max_retries=runtime_config.max_retries,
            user_agent=runtime_config.user_agent,
        )

    fenix_client = None
    if connection.fenix_enabled and fenix_refresh_token:
        fenix_client = SklikFenixClient(
            refresh_token=fenix_refresh_token,
            base_url=runtime_config.fenix_base_url,
            timeout=runtime_config.request_timeout_seconds,
            max_retries=runtime_config.max_retries,
            user_agent=runtime_config.user_agent,
        )

    snapshot = run_sklik_discovery(connection=connection, drak_client=drak_client, fenix_client=fenix_client)
    prefix = "fenix_" if connection.fenix_enabled and not connection.drak_enabled else ""
    target = discovery_snapshot_path(project_root, connection.key, prefix=prefix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot.to_dict()

