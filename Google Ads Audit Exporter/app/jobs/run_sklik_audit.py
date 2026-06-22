from __future__ import annotations

import argparse
from pathlib import Path

from app.integrations.sklik.reporting import resolve_date_range
from app.integrations.sklik.sync import export_all_enabled_sklik_contexts, export_sklik_context
from app.web.services.sklik_runtime import load_sklik_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sklik / Seznam audit export.")
    parser.add_argument("--context", default="", help="Single context key to export.")
    parser.add_argument("--preset", default="last_90_days", help="Date preset, e.g. last_30_days.")
    parser.add_argument("--date-from", default="", help="Custom ISO start date.")
    parser.add_argument("--date-to", default="", help="Custom ISO end date.")
    parser.add_argument("--granularity", default="daily", help="daily|weekly|monthly|total")
    parser.add_argument("--include-raw", action="store_true", help="Include sanitized raw payloads.")
    parser.add_argument("--include-empty-statistics", action="store_true", help="Allow empty stats rows in reports.")
    parser.add_argument("--disable-fenix", action="store_true", help="Disable Fenix export.")
    parser.add_argument("--disable-gtm-crosscheck", action="store_true", help="Disable GTM cross-check.")
    parser.add_argument("--disable-web-scan", action="store_true", help="Disable landing page scan.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    runtime = load_sklik_runtime(project_root)
    date_range = resolve_date_range(
        preset=args.preset,
        date_from=args.date_from,
        date_to=args.date_to,
        default_days=runtime.default_date_range_days,
    )

    if args.context:
        output = export_sklik_context(
            project_root=project_root,
            context_key=args.context,
            date_from=date_range.start.isoformat(),
            date_to=date_range.end.isoformat(),
            granularity=args.granularity,
            include_raw=args.include_raw,
            include_empty_statistics=args.include_empty_statistics,
            enable_fenix=not args.disable_fenix,
            enable_gtm_crosscheck=not args.disable_gtm_crosscheck,
            enable_web_scan=not args.disable_web_scan,
        )
        print(output)
        return

    outputs = export_all_enabled_sklik_contexts(
        project_root=project_root,
        date_from=date_range.start.isoformat(),
        date_to=date_range.end.isoformat(),
        granularity=args.granularity,
        include_raw=args.include_raw,
        include_empty_statistics=args.include_empty_statistics,
        enable_fenix=not args.disable_fenix,
        enable_gtm_crosscheck=not args.disable_gtm_crosscheck,
        enable_web_scan=not args.disable_web_scan,
    )
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()

