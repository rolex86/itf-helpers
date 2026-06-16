from __future__ import annotations

from pathlib import Path
from typing import Any

from app.web.services.linkedin_audit_service import run_linkedin_export_for_all_enabled_contexts, run_linkedin_export_for_context


def run_selected(project_root: Path, context_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_linkedin_export_for_context(project_root, context_key, payload)


def run_all(project_root: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_linkedin_export_for_all_enabled_contexts(project_root, payload)

