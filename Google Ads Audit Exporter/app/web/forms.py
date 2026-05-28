from __future__ import annotations

from typing import Any

from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import DEFAULT_REPORTS


def _to_optional_int(raw: str) -> int | None:
    value = raw.strip()
    return int(value) if value else None


def _to_optional_float(raw: str) -> float | None:
    value = raw.strip()
    return float(value) if value else None


def parse_dashboard_form(form: Any) -> dict[str, Any]:
    preset = (form.get("preset") or "LAST_90_DAYS").strip()
    date_from = (form.get("date_from") or "").strip() or None
    date_to = (form.get("date_to") or "").strip() or None
    if preset != "CUSTOM":
        date_from = None
        date_to = None

    reports = {
        report_key: form.get(f"report_{report_key}") == "on"
        for report_key in DEFAULT_REPORTS
    }

    env_config = GoogleAdsEnvConfig(
        developer_token=(form.get("developer_token") or "").strip(),
        client_id=(form.get("client_id") or "").strip(),
        client_secret=(form.get("client_secret") or "").strip(),
        refresh_token=(form.get("refresh_token") or "").strip(),
        login_customer_id=(form.get("login_customer_id") or "").replace("-", "").strip(),
    )

    payload = {
        "env_config": env_config,
        "config_payload": {
            "customer_id": (form.get("customer_id") or "").replace("-", "").strip(),
            "date_range": {
                "preset": preset,
                "date_from": date_from,
                "date_to": date_to,
            },
            "output": {
                "base_dir": (form.get("base_dir") or "exports").strip() or "exports",
                "xlsx_filename": (form.get("xlsx_filename") or "audit_export.xlsx").strip()
                or "audit_export.xlsx",
                "include_raw_csv": form.get("include_raw_csv") == "on",
                "include_metadata": form.get("include_metadata") == "on",
            },
            "reports": reports,
            "flags": {
                "min_spend_micros": int((form.get("min_spend_micros") or "100000000").strip()),
                "min_clicks": int((form.get("min_clicks") or "50").strip()),
                "target_cpa_micros": _to_optional_int(form.get("target_cpa_micros") or ""),
                "target_roas": _to_optional_float(form.get("target_roas") or ""),
                "low_ctr_threshold": float((form.get("low_ctr_threshold") or "0.01").strip()),
            },
            "cost_policy": {
                "free_only": form.get("free_only") == "on",
                "forbid_paid_cloud_resources": form.get("forbid_paid_cloud_resources") == "on",
                "allow_local_storage_only": form.get("allow_local_storage_only") == "on",
            },
        },
        "ui_state": {
            "selected_preset": preset,
        },
    }
    return payload
