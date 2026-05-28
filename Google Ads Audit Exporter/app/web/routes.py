from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from app.web.forms import parse_dashboard_form
from app.web.services.dashboard_service import load_dashboard_state, run_export_from_dashboard, save_dashboard_configuration
from app.web.services.folder_picker import pick_directory
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
    )


@web_bp.post("/save")
def save_configuration():
    payload = parse_dashboard_form(request.form)
    try:
        save_dashboard_configuration(_project_root(), payload)
        flash("Konfigurace byla ulozena do .env a config.yaml.", "success")
    except Exception as exc:
        flash(f"Ulozeni konfigurace selhalo: {exc}", "error")
    return redirect(url_for("web.dashboard"))


@web_bp.post("/run-export")
def run_export():
    payload = parse_dashboard_form(request.form)
    state = load_dashboard_state(_project_root())
    selected_preset = payload["ui_state"]["selected_preset"]

    try:
        result = run_export_from_dashboard(_project_root(), payload)
        if result.exit_code == 0:
            flash("Export byl dokonceny.", "success")
        else:
            flash("Export skoncil s chybou autentizace.", "error")
        state = load_dashboard_state(_project_root())
        return render_template(
            "dashboard.html",
            state=state,
            run_result=result,
            selected_preset=selected_preset or _selected_preset(state.config_payload),
        )
    except Exception as exc:
        flash(f"Export selhal: {exc}", "error")
        return render_template(
            "dashboard.html",
            state=state,
            run_result=None,
            selected_preset=selected_preset or _selected_preset(state.config_payload),
        )


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
