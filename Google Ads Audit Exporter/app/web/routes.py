from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from app.web.forms import parse_dashboard_form
from app.web.services.dashboard_service import (
    load_dashboard_state,
    run_export_from_dashboard,
    run_multi_mode_export_from_dashboard,
    save_dashboard_configuration,
)
from app.web.services.discovery_service import load_discovery_tables, run_discovery
from app.web.services.folder_picker import pick_directory
from app.web.services.ga4_dashboard_service import ga4_list_properties, ga4_test_connection
from app.web.services.gtm_dashboard_service import gtm_list_accounts, gtm_test_connection
from app.web.services.gsc_dashboard_service import gsc_list_properties, gsc_test_connection
from app.web.services.mapping_service import (
    get_context_test_job_status,
    load_mapping_state,
    parse_contexts_payload,
    run_all_context_exports,
    run_selected_context_export,
    save_mapping,
    start_context_test_job,
    test_context_from_payload,
)
from app.web.services.merchant_dashboard_service import merchant_list_accounts, merchant_test_connection


web_bp = Blueprint("web", __name__)


def _project_root() -> Path:
    return Path(current_app.config["PROJECT_ROOT"])


def _selected_preset(config_payload: dict) -> str:
    date_range = config_payload.get("date_range", {})
    if date_range.get("preset") == "CUSTOM":
        return "CUSTOM"
    if date_range.get("date_from") and date_range.get("date_to"):
        return "CUSTOM"
    return date_range.get("preset", "LAST_90_DAYS")


@web_bp.get("/")
def dashboard():
    state = load_dashboard_state(_project_root())
    return render_template(
        "dashboard.html",
        state=state,
        run_result=None,
        selected_preset=_selected_preset(state.config_payload),
        selected_export_mode="single_account",
        selected_context_key="",
    )


@web_bp.post("/save")
def save_configuration():
    payload = parse_dashboard_form(request.form)
    try:
        save_dashboard_configuration(_project_root(), payload)
        flash("Konfigurace byla uložena do .env a config.yaml.", "success")
    except Exception as exc:
        flash(f"Uložení konfigurace selhalo: {exc}", "error")
    return redirect(url_for("web.dashboard"))


@web_bp.post("/run-export")
def run_export():
    payload = parse_dashboard_form(request.form)
    state = load_dashboard_state(_project_root())
    selected_preset = payload["ui_state"]["selected_preset"]
    export_mode = payload["ui_state"].get("export_mode", "single_account")
    selected_context_key = payload["ui_state"].get("selected_context_key", "")

    try:
        if export_mode == "single_account":
            result = run_export_from_dashboard(_project_root(), payload)
            if result.exit_code == 0:
                flash("Export byl dokončen.", "success")
            else:
                flash("Export skončil s chybou autentizace.", "error")
            run_result = result
        else:
            multi_result = run_multi_mode_export_from_dashboard(_project_root(), payload)
            flash("Multi-context export byl dokončen.", "success")
            run_result = multi_result
        state = load_dashboard_state(_project_root())
        return render_template(
            "dashboard.html",
            state=state,
            run_result=run_result,
            selected_preset=selected_preset or _selected_preset(state.config_payload),
            selected_export_mode=export_mode,
            selected_context_key=selected_context_key,
        )
    except Exception as exc:
        flash(f"Export selhal: {exc}", "error")
        return render_template(
            "dashboard.html",
            state=state,
            run_result=None,
            selected_preset=selected_preset or _selected_preset(state.config_payload),
            selected_export_mode=export_mode,
            selected_context_key=selected_context_key,
        )


@web_bp.get("/discovery")
def discovery_page():
    return render_template(
        "discovery.html",
        state=load_dashboard_state(_project_root()),
        discovery_tables=load_discovery_tables(_project_root()),
        discovery_result=None,
    )


@web_bp.post("/discovery/run")
def run_discovery_page():
    try:
        result = run_discovery(_project_root())
        flash("Průzkum byl dokončen a CSV byla uložena do exports/_discovery.", "success")
        return render_template(
            "discovery.html",
            state=load_dashboard_state(_project_root()),
            discovery_tables=result.get("tables", {}),
            discovery_result=result,
        )
    except Exception as exc:
        flash(f"Průzkum selhal: {exc}", "error")
        return render_template(
            "discovery.html",
            state=load_dashboard_state(_project_root()),
            discovery_tables=load_discovery_tables(_project_root()),
            discovery_result=None,
        )


@web_bp.get("/mapping")
def mapping_page():
    mapping_state = load_mapping_state(_project_root())
    return render_template(
        "mapping.html",
        state=load_dashboard_state(_project_root()),
        mapping_state=mapping_state,
    )


@web_bp.post("/mapping/save")
def save_mapping_page():
    payload = request.get_json(silent=True) or {}
    try:
        contexts = parse_contexts_payload(payload)
        save_mapping(_project_root(), contexts)
        mapping_state = load_mapping_state(_project_root())
        return jsonify(
            {
                "ok": True,
                "message": "Mapování bylo uloženo do config.accounts.yaml.",
                "contexts": mapping_state["contexts_payload"],
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@web_bp.post("/mapping/test-context")
def test_mapping_context():
    payload = request.get_json(silent=True) or {}
    context_key = str(payload.get("key") or "").strip()
    context_label = str(payload.get("label") or "").strip()
    try:
        current_app.logger.info(
            "Starting mapping context test context_key=%s context_label=%s",
            context_key,
            context_label,
        )
        result = test_context_from_payload(_project_root(), payload)
        current_app.logger.info(
            "Finished mapping context test context_key=%s ok=%s",
            result.get("context_key", context_key),
            result.get("ok"),
        )
        return jsonify(result), 200
    except Exception as exc:
        current_app.logger.exception(
            "Mapping context test failed context_key=%s context_label=%s",
            context_key,
            context_label,
        )
        return jsonify({"ok": False, "message": str(exc)}), 500


@web_bp.post("/mapping/test-context/start")
def start_mapping_context_test():
    payload = request.get_json(silent=True) or {}
    try:
        result = start_context_test_job(_project_root(), payload)
        return jsonify(result), 202
    except Exception as exc:
        current_app.logger.exception("Mapping context test job start failed")
        return jsonify({"ok": False, "message": str(exc)}), 400


@web_bp.get("/mapping/test-context-status/<job_id>")
def mapping_context_test_status(job_id: str):
    try:
        return jsonify(get_context_test_job_status(job_id)), 200
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 404


@web_bp.post("/run-context-export")
def run_context_export_route():
    payload = request.get_json(silent=True) or {}
    context_key = str(payload.get("context_key") or "").strip()
    try:
        result = run_selected_context_export(_project_root(), context_key=context_key)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@web_bp.post("/run-multi-export")
def run_multi_export_route():
    try:
        result = run_all_context_exports(_project_root())
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@web_bp.get("/downloads/<path:relative_path>")
def download_file(relative_path: str):
    project_root = _project_root()
    target = (project_root / relative_path).resolve()
    exports_root = (project_root / "exports").resolve()
    if exports_root not in target.parents and target != exports_root:
        abort(404)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_file(target, as_attachment=True)


@web_bp.post("/api/pick-folder")
def pick_folder():
    payload = request.get_json(silent=True) or {}
    initial_dir = payload.get("initial_dir")
    try:
        selected = pick_directory(initial_dir=initial_dir)
        return jsonify({"ok": True, "path": selected or ""})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@web_bp.post("/api/merchant/test-connection")
def merchant_test():
    payload = request.get_json(silent=True) or {}
    try:
        result = merchant_test_connection(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "instructions": []}), 500


@web_bp.post("/api/merchant/list-accounts")
def merchant_accounts():
    payload = request.get_json(silent=True) or {}
    try:
        result = merchant_list_accounts(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "accounts": []}), 500


@web_bp.post("/api/ga4/test-connection")
def ga4_test():
    payload = request.get_json(silent=True) or {}
    try:
        result = ga4_test_connection(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "instructions": []}), 500


@web_bp.post("/api/ga4/list-properties")
def ga4_properties():
    payload = request.get_json(silent=True) or {}
    try:
        result = ga4_list_properties(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "properties": []}), 500


@web_bp.post("/api/gsc/test-connection")
def gsc_test():
    payload = request.get_json(silent=True) or {}
    try:
        result = gsc_test_connection(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "instructions": []}), 500


@web_bp.post("/api/gsc/list-properties")
def gsc_properties():
    payload = request.get_json(silent=True) or {}
    try:
        result = gsc_list_properties(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "properties": []}), 500


@web_bp.post("/api/gtm/test-connection")
def gtm_test():
    payload = request.get_json(silent=True) or {}
    try:
        result = gtm_test_connection(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "instructions": []}), 500


@web_bp.post("/api/gtm/list-accounts")
def gtm_accounts():
    payload = request.get_json(silent=True) or {}
    try:
        result = gtm_list_accounts(payload)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "accounts": []}), 500
