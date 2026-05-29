from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.accounts.context_config import (
    AccountContext,
    context_from_mapping,
    context_to_mapping,
    load_account_contexts,
    save_account_contexts,
)
from app.accounts.context_runner import run_context_export, run_multi_context_export, test_account_context
from app.config.env_settings import load_env_config
from app.config.settings import load_settings
from app.web.services.discovery_service import load_discovery_tables


LOGGER = logging.getLogger("google_ads_audit_exporter")
_TEST_JOBS: dict[str, dict[str, Any]] = {}
_TEST_JOBS_LOCK = threading.Lock()


def accounts_config_path(project_root: Path) -> Path:
    return project_root / "config.accounts.yaml"


def context_test_results_path(project_root: Path) -> Path:
    return project_root / "exports" / "_mapping" / "context_test_results.json"


def _load_test_results(project_root: Path) -> dict[str, Any]:
    path = context_test_results_path(project_root)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    results = payload.get("results", {})
    return results if isinstance(results, dict) else {}


def _save_test_results(project_root: Path, results: dict[str, Any]) -> None:
    path = context_test_results_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"results": results}, handle, ensure_ascii=False, indent=2)


def _context_test_state(context: AccountContext, stored_result: dict[str, Any] | None) -> dict[str, Any]:
    if not context.enabled:
        return {
            "status": "disabled",
            "label": "Disabled",
            "class_name": "status-disabled",
            "tested_at": stored_result.get("tested_at", "") if stored_result else "",
            "summary": "Kontext je vypnut\u00fd a ne\u00fa\u010dastn\u00ed se multi-exportu.",
        }
    if not stored_result:
        return {
            "status": "unknown",
            "label": "Not tested",
            "class_name": "status-unknown",
            "tested_at": "",
            "summary": "Kontext zat\u00edm nebyl otestovan\u00fd.",
        }
    ok = bool(stored_result.get("ok"))
    return {
        "status": "ok" if ok else "problem",
        "label": "OK" if ok else "Problem",
        "class_name": "status-ok" if ok else "status-problem",
        "tested_at": str(stored_result.get("tested_at", "") or ""),
        "summary": str(stored_result.get("summary", "") or ""),
    }


def _serialize_test_result(result: dict[str, Any]) -> dict[str, Any]:
    services = result.get("services", {})
    serialized_services = {}
    if isinstance(services, dict):
        for key, value in services.items():
            if not isinstance(value, dict):
                continue
            serialized_services[str(key)] = {
                "status": str(value.get("status", "") or ""),
                "details": str(value.get("details", "") or ""),
            }
    return {
        "ok": bool(result.get("ok")),
        "context_key": str(result.get("context_key", "") or ""),
        "context_label": str(result.get("context_label", "") or ""),
        "tested_at": str(result.get("tested_at", "") or ""),
        "summary": str(result.get("summary", "") or ""),
        "services": serialized_services,
    }


def _job_snapshot(job_id: str) -> dict[str, Any] | None:
    with _TEST_JOBS_LOCK:
        job = _TEST_JOBS.get(job_id)
        return dict(job) if job else None


def _store_job(job_id: str, payload: dict[str, Any]) -> None:
    with _TEST_JOBS_LOCK:
        _TEST_JOBS[job_id] = payload


def _update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    with _TEST_JOBS_LOCK:
        job = _TEST_JOBS.get(job_id)
        if job is None:
            return None
        job.update(fields)
        return dict(job)


def _service_job_entry(status: str, details: str) -> dict[str, str]:
    return {"status": status, "details": details}


def load_mapping_state(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(accounts_config_path(project_root))
    stored_results = _load_test_results(project_root)
    contexts_payload: list[dict[str, Any]] = []
    for context in contexts:
        payload = context_to_mapping(context)
        stored_result = stored_results.get(context.key) if context.key else None
        payload["test_result"] = _serialize_test_result(stored_result) if isinstance(stored_result, dict) else None
        payload["test_state"] = _context_test_state(
            context,
            stored_result if isinstance(stored_result, dict) else None,
        )
        contexts_payload.append(payload)
    return {
        "contexts": contexts,
        "contexts_payload": contexts_payload,
        "discovery_tables": load_discovery_tables(project_root),
    }


def save_mapping(project_root: Path, contexts: list[AccountContext]) -> None:
    save_account_contexts(accounts_config_path(project_root), contexts)


def parse_contexts_payload(payload: dict[str, Any]) -> list[AccountContext]:
    raw_contexts = payload.get("account_contexts", []) or []
    contexts: list[AccountContext] = []
    seen_keys: set[str] = set()
    for item in raw_contexts:
        if not isinstance(item, dict):
            continue
        context = context_from_mapping(item)
        if not context.key:
            continue
        if context.key in seen_keys:
            raise ValueError(f"Key '{context.key}' je v mappingu v\u00edckr\u00e1t.")
        seen_keys.add(context.key)
        contexts.append(context)
    return contexts


def _execute_context_test(
    project_root: Path,
    context: AccountContext,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    if not context.key:
        raise ValueError("Kontext mus\u00ed m\u00edt vypln\u011bn\u00fd key.")
    env_config = load_env_config(project_root / ".env")

    def _progress(service_key: str, phase: str, details: str) -> None:
        if not job_id:
            return
        snapshot = _job_snapshot(job_id)
        if snapshot is None:
            return
        services = dict(snapshot.get("services", {}))
        if phase == "running":
            services[service_key] = _service_job_entry("running", details)
            _update_job(
                job_id,
                services=services,
                state="running",
                current_service=service_key,
                current_message=details,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            services[service_key] = _service_job_entry("finished", details)
            _update_job(
                job_id,
                services=services,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

    result = test_account_context(
        context=context,
        base_env_config=env_config,
        progress_callback=_progress if job_id else None,
    )
    service_payload = {
        key: {
            "status": value.status,
            "details": value.details,
        }
        for key, value in result.services.items()
    }
    response = {
        "ok": result.ok,
        "context_key": result.context_key,
        "context_label": result.context_label,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "summary": (
            "V\u0161echny kontroly pro\u0161ly."
            if result.ok
            else "Alespo\u0148 jedna kontrola skon\u010dila probl\u00e9mem."
        ),
        "services": service_payload,
    }
    stored_results = _load_test_results(project_root)
    stored_results[context.key] = response
    _save_test_results(project_root, stored_results)
    response["test_state"] = _context_test_state(context, response)
    if job_id:
        _update_job(
            job_id,
            services={key: dict(value) for key, value in service_payload.items()},
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    return response


def test_context_from_payload(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return _execute_context_test(project_root, context_from_mapping(payload))


def start_context_test_job(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    context = context_from_mapping(payload)
    if not context.key:
        raise ValueError("Kontext mus\u00ed m\u00edt vypln\u011bn\u00fd key.")

    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    _store_job(
        job_id,
        {
            "job_id": job_id,
            "state": "queued",
            "context_key": context.key,
            "context_label": context.label,
            "services": {},
            "current_service": "",
            "current_message": "Test kontextu je ve front\u011b.",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": "",
        },
    )

    def _runner() -> None:
        LOGGER.info("Context test job queued job_id=%s context_key=%s", job_id, context.key)
        _update_job(
            job_id,
            state="running",
            current_message="Test kontextu byl spu\u0161t\u011bn na serveru.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            result = _execute_context_test(project_root, context, job_id=job_id)
            _update_job(
                job_id,
                state="finished",
                current_service="",
                current_message="Test kontextu byl dokon\u010den.",
                updated_at=datetime.now(timezone.utc).isoformat(),
                result=result,
            )
            LOGGER.info(
                "Context test job finished job_id=%s context_key=%s ok=%s",
                job_id,
                context.key,
                result.get("ok"),
            )
        except Exception as exc:
            LOGGER.exception("Context test job failed job_id=%s context_key=%s", job_id, context.key)
            _update_job(
                job_id,
                state="error",
                current_service="",
                current_message="Test kontextu selhal.",
                updated_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )

    thread = threading.Thread(target=_runner, daemon=True, name=f"context-test-{context.key}")
    thread.start()
    return {
        "ok": True,
        "job_id": job_id,
        "context_key": context.key,
        "context_label": context.label,
        "state": "queued",
    }


def get_context_test_job_status(job_id: str) -> dict[str, Any]:
    snapshot = _job_snapshot(job_id)
    if snapshot is None:
        raise ValueError("Testovac\u00ed job nebyl nalezen.")
    return snapshot


def run_selected_context_export(
    project_root: Path,
    *,
    context_key: str,
) -> dict[str, Any]:
    contexts = load_account_contexts(accounts_config_path(project_root))
    selected = next((context for context in contexts if context.key == context_key), None)
    if selected is None:
        raise ValueError(f"Kontext '{context_key}' nebyl nalezen v config.accounts.yaml.")

    env_config = load_env_config(project_root / ".env")
    settings = load_settings(project_root / "config.yaml")
    result = run_context_export(
        project_root=project_root,
        settings=settings,
        config_path=project_root / "config.yaml",
        base_env_config=env_config,
        context=selected,
        export_base_name_override=f"{result_date_prefix(settings, project_root)}_{selected.key}_{selected.google_ads_customer_id}",
        export_mode="selected_context",
    )
    return {
        "ok": result.exit_code == 0,
        "context_key": selected.key,
        "context_label": selected.label,
        "export_path": str(result.export_paths.base_dir),
        "error_count": len(result.errors),
        "xlsx_path": str(result.export_paths.xlsx_path),
    }


def run_all_context_exports(project_root: Path) -> dict[str, Any]:
    contexts = load_account_contexts(accounts_config_path(project_root))
    env_config = load_env_config(project_root / ".env")
    settings = load_settings(project_root / "config.yaml")
    result = run_multi_context_export(
        project_root=project_root,
        settings=settings,
        config_path=project_root / "config.yaml",
        base_env_config=env_config,
        contexts=contexts,
    )
    return {
        "ok": True,
        "mode": result.mode,
        "cross_account_dir": result.cross_account_dir,
        "context_results": [
            {
                "context_key": bundle.context.key,
                "context_label": bundle.context.label,
                "export_path": str(bundle.result.export_paths.base_dir),
                "error_count": len(bundle.result.errors),
            }
            for bundle in result.context_results
        ],
        "context_errors": result.context_errors,
    }


def result_date_prefix(settings, project_root: Path) -> str:
    from app.utils.dates import resolve_date_range

    return resolve_date_range(settings.date_range).export_date.isoformat()
