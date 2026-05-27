from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from app.config.settings import load_settings
from app.export.workflow import run_export


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Google Ads audit exporter.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file.")
    parser.add_argument("--customer-id", help="Override customer_id from config.")
    parser.add_argument(
        "--preset",
        choices=("LAST_30_DAYS", "LAST_90_DAYS", "LAST_365_DAYS"),
        help="Override date_range preset from config.",
    )
    parser.add_argument("--date-from", help="Override date_from (YYYY-MM-DD).")
    parser.add_argument("--date-to", help="Override date_to (YYYY-MM-DD).")
    return parser

def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    settings = load_settings(
        config_path=project_root / args.config,
        customer_id_override=args.customer_id,
        preset_override=args.preset,
        date_from_override=args.date_from,
        date_to_override=args.date_to,
    )
    return run_export(
        settings=settings,
        project_root=project_root,
        config_path=project_root / args.config,
    )


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
