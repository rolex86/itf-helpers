from __future__ import annotations

from pathlib import Path

from app.web.services.meta_audit_service import run_meta_export_for_context


def run(project_root: Path, context_key: str) -> dict:
    return run_meta_export_for_context(project_root, context_key)
