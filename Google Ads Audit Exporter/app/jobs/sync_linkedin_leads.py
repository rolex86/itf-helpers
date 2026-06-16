from __future__ import annotations

from pathlib import Path
from typing import Any

from app.web.services.linkedin_audit_service import run_linkedin_export_for_context


def run(project_root: Path, context_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    lead_payload = dict(payload or {})
    lead_payload["include_lead_sync"] = True
    return run_linkedin_export_for_context(project_root, context_key, lead_payload)
