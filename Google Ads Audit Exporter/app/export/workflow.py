from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.audit.basic_flags import build_basic_flags
from app.auth.google_ads_client import build_google_ads_client
from app.config.env_settings import load_env_config
from app.config.settings import AppSettings
from app.export.csv_exporter import export_csv
from app.export.derived_summaries import build_landing_pages_summary, build_locations_summary
from app.export.metadata_exporter import write_json
from app.export.xlsx_exporter import export_workbook
from app.ga4.export import build_ga4_exports
from app.google_ads.diagnostics import build_supplemental_reports
from app.google_ads.fetcher import GoogleAdsFetcher
from app.google_ads.postprocess import postprocess_report_dataframe
from app.google_ads.report_definitions import REPORT_ORDER, empty_report_frame, get_report_definition
from app.merchant.export import build_merchant_exports
from app.search_console.export import build_search_console_exports
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


@dataclass(slots=True)
class ExportExecutionResult:
    exit_code: int
    export_paths: ExportPaths
    resolved_range: ResolvedDateRange
    errors: list[dict[str, Any]]
    report_rows: list[dict[str, Any]]
    account_info: dict[str, Any]
    fallback_report_count: int


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_summary_rows(
    customer_id: str,
    settings: AppSettings,
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
        {
            "section": "policy",
            "name": "free_only",
            "value": "true" if settings.cost_policy.free_only else "false",
            "details": "Free-only rezim aktivni, pouze read-only API a lokalni uloziste.",
            "status": "ok" if settings.cost_policy.free_only else "warning",
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
    status: str,
    dropped_fields: list[str],
) -> None:
    state.report_rows.append(
        {
            "section": "report",
            "name": report_key,
            "value": sheet_name,
            "details": " | ".join(notes),
            "status": status,
            "rows": rows,
            "dropped_fields": dropped_fields,
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


def _persist_dataset_as_csv(
    export_paths: ExportPaths,
    dataset: pd.DataFrame,
    report_key: str,
    enabled: bool,
) -> None:
    if enabled:
        export_csv(dataset, export_paths.raw_dir / f"{report_key}.csv")


def execute_export(settings: AppSettings, project_root: Path, config_path: Path) -> ExportExecutionResult:
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
    env_config = load_env_config(project_root / ".env")
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
        return ExportExecutionResult(
            exit_code=1,
            export_paths=export_paths,
            resolved_range=resolved_range,
            errors=list(state.errors),
            report_rows=list(state.report_rows),
            account_info=dict(state.account_info),
            fallback_report_count=0,
        )

    fetcher = GoogleAdsFetcher(client=client, project_root=project_root, logger=logger)

    for report_key in REPORT_ORDER:
        if not settings.reports.get(report_key, False):
            logger.info("Skipping disabled report=%s", report_key)
            continue

        report = get_report_definition(report_key)
        if not report.supports_fetch:
            logger.info("Skipping synthetic report during GAQL fetch phase report=%s", report.key)
            continue
        logger.info("Starting report=%s", report.key)

        try:
            result = fetcher.fetch_report(
                report=report,
                customer_id=settings.customer_id,
                resolved_range=resolved_range,
            )
            processed_dataframe = postprocess_report_dataframe(
                report_key=report.key,
                dataframe=result.dataframe,
            )
            state.datasets[report.key] = processed_dataframe
            state.query_log.extend(result.query_attempts)

            _persist_dataset_as_csv(
                export_paths=export_paths,
                dataset=processed_dataframe,
                report_key=report.key,
                enabled=settings.output.include_raw_csv,
            )

            if report.key == "account" and not processed_dataframe.empty:
                state.account_info = processed_dataframe.iloc[0].to_dict()

            notes = list(result.notes)
            if result.dropped_optional_fields:
                notes.append("Dropped optional fields: " + ", ".join(result.dropped_optional_fields))

            _record_report_success(
                state=state,
                report_key=report.key,
                sheet_name=report.sheet_name,
                rows=int(len(processed_dataframe)),
                notes=notes,
                status="warning" if result.dropped_optional_fields else "ok",
                dropped_fields=list(result.dropped_optional_fields),
            )
            logger.info("Finished report=%s rows=%s", report.key, len(processed_dataframe))
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

    supplemental = build_supplemental_reports(
        fetcher=fetcher,
        customer_id=settings.customer_id,
        resolved_range=resolved_range,
        datasets=state.datasets,
        enabled_reports=settings.reports,
        flags_config=settings.flags,
    )
    state.query_log.extend(supplemental.query_attempts)
    state.errors.extend(supplemental.errors)

    for report_key, dataset in supplemental.datasets.items():
        state.datasets[report_key] = dataset
        _persist_dataset_as_csv(
            export_paths=export_paths,
            dataset=dataset,
            report_key=report_key,
            enabled=settings.output.include_raw_csv,
        )
        notes = list(supplemental.report_notes.get(report_key, []))
        existing_row = next((row for row in state.report_rows if row.get("name") == report_key), None)
        if existing_row is not None:
            existing_details = existing_row.get("details", "")
            extra_details = " | ".join(note for note in notes if note)
            if extra_details:
                existing_row["details"] = (
                    f"{existing_details} | {extra_details}" if existing_details else extra_details
                )
            existing_row["rows"] = int(len(dataset))
            if report_key in supplemental.report_warning_keys and existing_row.get("status") != "error":
                existing_row["status"] = "warning"
            continue

        report = get_report_definition(report_key)
        _record_report_success(
            state=state,
            report_key=report_key,
            sheet_name=report.sheet_name,
            rows=int(len(dataset)),
            notes=notes,
            status="warning" if report_key in supplemental.report_warning_keys else "ok",
            dropped_fields=[],
        )

    merchant_result = build_merchant_exports(
        env_config=env_config,
        datasets=state.datasets,
        reports_enabled=settings.reports,
        flags_config=settings.flags,
    )
    state.errors.extend(merchant_result.errors)

    for report_key, dataset in merchant_result.datasets.items():
        state.datasets[report_key] = dataset
        _persist_dataset_as_csv(
            export_paths=export_paths,
            dataset=dataset,
            report_key=report_key,
            enabled=settings.output.include_raw_csv,
        )
        existing_row = next((row for row in state.report_rows if row.get("name") == report_key), None)
        notes = list(merchant_result.report_notes.get(report_key, []))
        if existing_row is not None:
            existing_details = existing_row.get("details", "")
            extra_details = " | ".join(note for note in notes if note)
            if extra_details:
                existing_row["details"] = (
                    f"{existing_details} | {extra_details}" if existing_details else extra_details
                )
            existing_row["rows"] = int(len(dataset))
            if report_key in merchant_result.report_warning_keys and existing_row.get("status") != "error":
                existing_row["status"] = "warning"
            continue

        report = get_report_definition(report_key)
        _record_report_success(
            state=state,
            report_key=report_key,
            sheet_name=report.sheet_name,
            rows=int(len(dataset)),
            notes=notes,
            status="warning" if report_key in merchant_result.report_warning_keys else "ok",
            dropped_fields=[],
        )

    ga4_result = build_ga4_exports(
        env_config=env_config,
        datasets=state.datasets,
        reports_enabled=settings.reports,
        resolved_range=resolved_range,
        flags_config=settings.flags,
    )
    state.errors.extend(ga4_result.errors)

    for report_key, dataset in ga4_result.datasets.items():
        state.datasets[report_key] = dataset
        _persist_dataset_as_csv(
            export_paths=export_paths,
            dataset=dataset,
            report_key=report_key,
            enabled=settings.output.include_raw_csv,
        )
        existing_row = next((row for row in state.report_rows if row.get("name") == report_key), None)
        notes = list(ga4_result.report_notes.get(report_key, []))
        if existing_row is not None:
            existing_details = existing_row.get("details", "")
            extra_details = " | ".join(note for note in notes if note)
            if extra_details:
                existing_row["details"] = (
                    f"{existing_details} | {extra_details}" if existing_details else extra_details
                )
            existing_row["rows"] = int(len(dataset))
            if report_key in ga4_result.report_warning_keys and existing_row.get("status") != "error":
                existing_row["status"] = "warning"
            continue

        report = get_report_definition(report_key)
        _record_report_success(
            state=state,
            report_key=report_key,
            sheet_name=report.sheet_name,
            rows=int(len(dataset)),
            notes=notes,
            status="warning" if report_key in ga4_result.report_warning_keys else "ok",
            dropped_fields=[],
        )

    gsc_result = build_search_console_exports(
        env_config=env_config,
        datasets=state.datasets,
        reports_enabled=settings.reports,
        resolved_range=resolved_range,
        flags_config=settings.flags,
        cache_dir=project_root / "exports" / "_cache" / "search_console",
    )
    state.errors.extend(gsc_result.errors)

    for report_key, dataset in gsc_result.datasets.items():
        state.datasets[report_key] = dataset
        _persist_dataset_as_csv(
            export_paths=export_paths,
            dataset=dataset,
            report_key=report_key,
            enabled=settings.output.include_raw_csv,
        )
        existing_row = next((row for row in state.report_rows if row.get("name") == report_key), None)
        notes = list(gsc_result.report_notes.get(report_key, []))
        if existing_row is not None:
            existing_details = existing_row.get("details", "")
            extra_details = " | ".join(note for note in notes if note)
            if extra_details:
                existing_row["details"] = (
                    f"{existing_details} | {extra_details}" if existing_details else extra_details
                )
            existing_row["rows"] = int(len(dataset))
            if report_key in gsc_result.report_warning_keys and existing_row.get("status") != "error":
                existing_row["status"] = "warning"
            continue

        report = get_report_definition(report_key)
        _record_report_success(
            state=state,
            report_key=report_key,
            sheet_name=report.sheet_name,
            rows=int(len(dataset)),
            notes=notes,
            status="warning" if report_key in gsc_result.report_warning_keys else "ok",
            dropped_fields=[],
        )

    summary_rows = _build_summary_rows(
        customer_id=settings.customer_id,
        settings=settings,
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
            derived_sheets=[
                (
                    "Landing pages summary",
                    build_landing_pages_summary(
                        landing_pages=state.datasets.get(
                            "landing_pages",
                            empty_report_frame(get_report_definition("landing_pages")),
                        ),
                        flags_config=settings.flags,
                    ),
                ),
                (
                    "Locations summary",
                    build_locations_summary(
                        locations=state.datasets.get(
                            "locations",
                            empty_report_frame(get_report_definition("locations")),
                        ),
                        flags_config=settings.flags,
                    ),
                ),
            ],
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
    fallback_report_count = sum(1 for row in state.report_rows if row.get("status") == "warning")
    return ExportExecutionResult(
        exit_code=0,
        export_paths=export_paths,
        resolved_range=resolved_range,
        errors=list(state.errors),
        report_rows=list(state.report_rows),
        account_info=dict(state.account_info),
        fallback_report_count=fallback_report_count,
    )


def run_export(settings: AppSettings, project_root: Path, config_path: Path) -> int:
    return execute_export(
        settings=settings,
        project_root=project_root,
        config_path=config_path,
    ).exit_code
