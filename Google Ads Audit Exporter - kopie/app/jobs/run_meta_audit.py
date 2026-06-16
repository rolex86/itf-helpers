from __future__ import annotations

from pathlib import Path

from app.web.services.meta_audit_service import run_meta_export_for_all_enabled_contexts


def run(project_root: Path) -> dict:
    return run_meta_export_for_all_enabled_contexts(project_root)
