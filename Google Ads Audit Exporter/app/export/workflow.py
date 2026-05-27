from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.audit.basic_flags import build_basic_flags
from app.auth.google_ads_client import build_google_ads_client
from app.config.settings import AppSettings
from app.export.csv_exporter import export_csv
from app.export.metadata_exporter import write_json
from app.export.xlsx_exporter import export_workbook
from app.google_ads.fetcher import GoogleAdsFetcher
from app.google_ads.report_definitions import REPORT_ORDER, empty_report_frame, get_report_definition
from app.utils.dates import ResolvedDateRange, resolve_date_range
from app.utils.logging import configure_logging
from app.utils.paths import ExportPaths, prepare_export_paths


@dataclass(slots=True)
class ExportRunState:
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    query_log: list[dict[str, Any]] = field(default_factory=list)
    report_rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    account_info: dict[str, Any] = field(default_factory=dict)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_summary_rows(
    customer_id: str,
    resolved_range: ResolvedDateRange,
    export_paths: ExportPaths,
    report_rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "section": "export",
            "name": "customer_id",
            "value": customer_id,
            "details": "",
            "status": "ok",
            "rows": "",
        },
        {
            "section": "export",
            "name": "date_from",
            "value": resolved_range.date_from.isoformat(),
            "details": "",
            "status": "ok",
            "rows": "",
        },
        {
            "section": "export",
            "name": "date_to",
            "value": resolved_range.date_to.isoformat(),
            "details": "",
            "status": "ok",
            "rows": "",
        },
        {
            "section": "export",
            "name": "date_label",
            "value": resolved_range.label,
            "details": "",
            "status": "ok",
            "rows": "",
        },
        {
            "section": "export",
            "name": "output_dir",
            "value": str(export_paths.base_dir),
            "details": "",
            "status": "ok",
            "rows": "",
        },
    ]

    for warning in resolved_range.warnings:
        rows.append(
            {
                "section": "warning",
                "name": "date_range",
                "value": warning,
                "details": "",
                "status": "warning",
                "rows": "",
            }
        )

    rows.extend(report_rows)

    for error in errors:
        rows.append(
            {
                "section": "error",
                "name": error.get("report", "general"),
                "value": error.get("message", ""),
                "details": error.get("details", ""),
                "status": "error",
                "rows": "",
            }
        )

    return rows


def _persist_metadata(export_paths: ExportPaths, state: ExportRunState, enabled: bool) -> None:
    if not enabled:
        return
    write_json(export_paths.metadata_dir / "account_info.json", state.account_info)
    write_json(export_paths.metadata_dir / "query_log.json", state.query_log)
    write_json(export_paths.metadata_dir / "errors.json", state.errors)


def _record_auth_failure(state: ExportRunState, message: str) -> None:
    state.errors.append(
        {
            "report": "authentication",
            "message": message,
            "details": "",
            "timestamp": _timestamp(),
        }
    )


def _record_report_success(
    state: ExportRunState,
    report_key: str,
    sheet_name: str,
    rows: int,
    notes: list[str],
) -> None:
    state.report_rows.append(
        {
            "section": "report",
            "name": report_key,
            "value": sheet_name,
            "details": " | ".join(notes),
            "status": "ok",
            "rows": rows,
        }
    )


def _record_report_failure(
    state: ExportRunState,
    report_key: str,
    sheet_name: str,
    priority: bool,
    message: str,
) -> None:
    state.errors.append(
        {
            "report": report_key,
            "message": message,
            "details": "Priority report failure" if priority else "",
            "timestamp": _timestamp(),
        }
    )
    state.report_rows.append(
        {
            "section": "report",
            "name": report_key,
            "value": sheet_name,
            "details": "Priority report failure" if priority else message,
            "status": "error",
            "rows": 0,
        }
    )


def run_export(settings: AppSettings, project_root: Path, config_path: Path) -> int:
    resolved_range = resolve_date_range(settings.date_range)
    export_paths = prepare_export_paths(
        project_root=project_root,
        base_dir_name=settings.output.base_dir,
        customer_id=settings.customer_id,
        run_date=resolved_range.export_date,
        xlsx_filename=settings.output.xlsx_filename,
    )
    logger = configure_logging(export_paths.log_path)
    state = ExportRunState(account_info={"customer_id": settings.customer_id})

    logger.info("Starting export for customer_id=%s", settings.customer_id)
    logger.info(
        "Resolved date range %s -> %s (%s)",
        resolved_range.date_from.isoformat(),
        resolved_range.date_to.isoformat(),
        resolved_range.label,
    )
    for warning in resolved_range.warnings:
        logger.warning(warning)

    write_json(
        export_paths.metadata_dir / "export_config.json",
        settings.to_metadata(resolved_range=resolved_range, config_path=config_path),
    )

    try:
        client = build_google_ads_client()
    except Exception as exc:  # pragma: no cover - depends on credentials/runtime
        message = f"Authentication failed: {exc}"
        logger.exception(message)
        _record_auth_failure(state, message)
        _persist_metadata(export_paths, state, settings.output.include_metadata)
        return 1

    fetcher = GoogleAdsFetcher(client=client, project_root=project_root, logger=logger)

    for report_key in REPORT_ORDER:
        if not settings.reports.get(report_key, False):
            logger.info("Skipping disabled report=%s", report_key)
            continue

        report = get_report_definition(report_key)
        logger.info("Starting report=%s", report.key)

        try:
            result = fetcher.fetch_report(
                report=report,
                customer_id=settings.customer_id,
                resolved_range=resolved_range,
            )
            state.datasets[report.key] = result.dataframe
            state.query_log.extend(result.query_attempts)

            if settings.output.include_raw_csv:
                export_csv(result.dataframe, export_paths.raw_dir / f"{report.key}.csv")

            if report.key == "account" and not result.dataframe.empty:
                state.account_info = result.dataframe.iloc[0].to_dict()

            notes = list(result.notes)
            if result.dropped_optional_fields:
                notes.append("Dropped optional fields: " + ", ".join(result.dropped_optional_fields))

            _record_report_success(
                state=state,
                report_key=report.key,
                sheet_name=report.sheet_name,
                rows=int(len(result.dataframe)),
                notes=notes,
            )
            logger.info("Finished report=%s rows=%s", report.key, len(result.dataframe))
        except Exception as exc:  # pragma: no cover - API dependent
            logger.exception("Report failed report=%s", report.key)
            state.datasets[report.key] = empty_report_frame(report)
            _record_report_failure(
                state=state,
                report_key=report.key,
                sheet_name=report.sheet_name,
                priority=report.priority,
                message=str(exc),
            )

    flags_df = build_basic_flags(
        campaigns=state.datasets.get("campaigns", empty_report_frame(get_report_definition("campaigns"))),
        keywords=state.datasets.get("keywords", empty_report_frame(get_report_definition("keywords"))),
        search_terms=state.datasets.get(
            "search_terms",
            empty_report_frame(get_report_definition("search_terms")),
        ),
        landing_pages=state.datasets.get(
            "landing_pages",
            empty_report_frame(get_report_definition("landing_pages")),
        ),
        devices=state.datasets.get("devices", empty_report_frame(get_report_definition("devices"))),
        locations=state.datasets.get("locations", empty_report_frame(get_report_definition("locations"))),
        flags_config=settings.flags,
    )

    summary_rows = _build_summary_rows(
        customer_id=settings.customer_id,
        resolved_range=resolved_range,
        export_paths=export_paths,
        report_rows=state.report_rows,
        errors=state.errors,
    )

    try:
        export_workbook(
            xlsx_path=export_paths.xlsx_path,
            summary_rows=summary_rows,
            datasets=state.datasets,
            flags_df=flags_df,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.exception("Workbook export failed")
        state.errors.append(
            {
                "report": "xlsx_export",
                "message": f"Workbook export failed: {exc}",
                "details": "",
                "timestamp": _timestamp(),
            }
        )

    _persist_metadata(export_paths, state, settings.output.include_metadata)
    logger.info("Finished export path=%s", export_paths.base_dir)
    return 0
