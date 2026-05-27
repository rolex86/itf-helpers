from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.google_ads.normalizer import extract_path_value
from app.google_ads.report_definitions import FieldSpec, ReportDefinition, empty_report_frame
from app.utils.dates import ResolvedDateRange


@dataclass(slots=True)
class FetchResult:
    dataframe: pd.DataFrame
    query_attempts: list[dict[str, Any]]
    dropped_optional_fields: list[str]
    notes: list[str]


class GoogleAdsFetcher:
    def __init__(self, client: Any, project_root: Path, logger: logging.Logger) -> None:
        self.client = client
        self.project_root = project_root
        self.logger = logger
        self.google_ads_service = client.get_service("GoogleAdsService")

    def fetch_report(
        self,
        report: ReportDefinition,
        customer_id: str,
        resolved_range: ResolvedDateRange,
    ) -> FetchResult:
        query_template = report.query_path(self.project_root).read_text(encoding="utf-8")
        query_attempts: list[dict[str, Any]] = []
        last_exception: Exception | None = None
        field_variant = report.fields
        attempted_variants: set[tuple[str, ...]] = set()

        while True:
            signature = tuple(field.path for field in field_variant)
            if signature in attempted_variants:
                break
            attempted_variants.add(signature)

            query = self._render_query(
                query_template=query_template,
                fields=field_variant,
                resolved_range=resolved_range,
            )
            started = time.perf_counter()
            try:
                rows = self._run_query(customer_id=customer_id, query=query)
                dataframe = self._rows_to_frame(rows=rows, selected_fields=field_variant, report=report)
                query_attempts.append(
                    {
                        "report": report.key,
                        "status": "ok",
                        "rows": int(len(dataframe)),
                        "selected_fields": [field.path for field in field_variant],
                        "query": query,
                        "duration_seconds": round(time.perf_counter() - started, 3),
                    }
                )
                dropped = [
                    field.alias for field in report.fields if field.alias not in dataframe.columns or field not in field_variant
                ]
                notes = []
                if report.key == "change_history":
                    notes.append("Google Ads API restricts change_event queries to the last 30 days.")
                return FetchResult(
                    dataframe=dataframe,
                    query_attempts=query_attempts,
                    dropped_optional_fields=[alias for alias in dropped if alias in report.optional_aliases],
                    notes=notes,
                )
            except Exception as exc:  # pragma: no cover - API dependent
                last_exception = exc
                query_attempts.append(
                    {
                        "report": report.key,
                        "status": "error",
                        "rows": 0,
                        "selected_fields": [field.path for field in field_variant],
                        "query": query,
                        "error": self._format_exception(exc),
                        "duration_seconds": round(time.perf_counter() - started, 3),
                    }
                )
                self.logger.warning(
                    "Query attempt failed report=%s fields=%s error=%s",
                    report.key,
                    ",".join(field.alias for field in field_variant),
                    self._format_exception(exc),
                )

                next_variant = self._build_next_field_variant(
                    exc=exc,
                    selected_fields=field_variant,
                )
                if next_variant is None:
                    break
                field_variant = next_variant

        if last_exception is None:
            raise RuntimeError(f"Unknown failure while fetching report {report.key}")
        raise RuntimeError(self._format_exception(last_exception))

    def _render_query(
        self,
        query_template: str,
        fields: tuple[FieldSpec, ...],
        resolved_range: ResolvedDateRange,
    ) -> str:
        select_fields = ",\n".join(f"  {field.path}" for field in fields)
        rendered = query_template.format(
            select_fields=select_fields,
            date_from=resolved_range.date_from.isoformat(),
            date_to=resolved_range.date_to.isoformat(),
            change_date_from=resolved_range.change_history_from.isoformat(),
            change_date_to=resolved_range.change_history_to.isoformat(),
        )
        return rendered.strip()

    def _run_query(self, customer_id: str, query: str) -> list[Any]:
        stream = self.google_ads_service.search_stream(customer_id=customer_id, query=query)
        rows: list[Any] = []
        for batch in stream:
            rows.extend(batch.results)
        return rows

    def _rows_to_frame(
        self,
        rows: list[Any],
        selected_fields: tuple[FieldSpec, ...],
        report: ReportDefinition,
    ) -> pd.DataFrame:
        if not rows:
            return empty_report_frame(report)

        records = []
        for row in rows:
            records.append(
                {
                    field.alias: extract_path_value(row, field.path)
                    for field in selected_fields
                }
            )

        frame = pd.DataFrame.from_records(records)
        for field in report.fields:
            if field.alias not in frame.columns:
                frame[field.alias] = None
        return frame.loc[:, report.aliases]

    def _format_exception(self, exc: Exception) -> str:
        errors = getattr(exc, "failure", None)
        if not errors:
            return str(exc)

        error_messages: list[str] = []
        for error in getattr(errors, "errors", []):
            message = getattr(error, "message", None) or str(error)
            if getattr(error, "location", None):
                field_paths = []
                for element in getattr(error.location, "field_path_elements", []):
                    field_name = getattr(element, "field_name", None)
                    if field_name:
                        field_paths.append(field_name)
                if field_paths:
                    message = f"{message} ({'.'.join(field_paths)})"
            error_messages.append(message)
        return " | ".join(error_messages) if error_messages else str(exc)

    def _build_next_field_variant(
        self,
        exc: Exception,
        selected_fields: tuple[FieldSpec, ...],
    ) -> tuple[FieldSpec, ...] | None:
        optional_fields = [field for field in selected_fields if field.optional]
        if not optional_fields:
            return None

        invalid_paths = self._extract_invalid_field_paths(exc=exc, selected_fields=selected_fields)
        if invalid_paths:
            filtered_fields = tuple(
                field for field in selected_fields if field.path not in invalid_paths
            )
            if filtered_fields != selected_fields:
                return filtered_fields

        fallback_field = optional_fields[-1]
        return tuple(field for field in selected_fields if field.path != fallback_field.path)

    def _extract_invalid_field_paths(
        self,
        exc: Exception,
        selected_fields: tuple[FieldSpec, ...],
    ) -> set[str]:
        searchable_text = self._format_exception(exc)
        raw_text = str(exc)
        haystacks = [searchable_text, raw_text]
        matched_paths: set[str] = set()

        for field in selected_fields:
            if not field.optional:
                continue
            if any(field.path in haystack for haystack in haystacks):
                matched_paths.add(field.path)

        if matched_paths:
            return matched_paths

        failure = getattr(exc, "failure", None)
        if not failure:
            return set()

        token_candidates: set[str] = set()
        for error in getattr(failure, "errors", []):
            location = getattr(error, "location", None)
            if not location:
                continue
            names = [
                getattr(element, "field_name", "")
                for element in getattr(location, "field_path_elements", [])
                if getattr(element, "field_name", "")
            ]
            token_candidates.update(names)

        if not token_candidates:
            return set()

        for field in selected_fields:
            if not field.optional:
                continue
            field_tokens = set(re.split(r"[._]", field.path))
            if token_candidates & field_tokens:
                matched_paths.add(field.path)
        return matched_paths
