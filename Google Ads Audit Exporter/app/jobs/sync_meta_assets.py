from __future__ import annotations

from pathlib import Path

from app.web.services.meta_discovery_service import run_meta_discovery_for_connection


def run(project_root: Path, connection_key: str) -> dict:
    return run_meta_discovery_for_connection(project_root, connection_key)
