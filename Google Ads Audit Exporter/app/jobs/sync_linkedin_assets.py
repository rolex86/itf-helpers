from __future__ import annotations

from pathlib import Path

from app.web.services.linkedin_discovery_service import run_linkedin_discovery_for_connection


def run(project_root: Path, connection_key: str) -> dict:
    return run_linkedin_discovery_for_connection(project_root, connection_key)

