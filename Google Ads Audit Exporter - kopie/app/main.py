from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from app.accounts.context_config import load_account_contexts
from app.accounts.context_runner import run_context_export, run_multi_context_export
from app.config.env_settings import load_env_config
from app.config.settings import load_settings
from app.export.workflow import run_export
from app.utils.dates import resolve_date_range


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
    parser.add_argument("--context-key", help="Run export for one context from config.accounts.yaml.")
    parser.add_argument("--all-contexts", action="store_true", help="Run export for all enabled contexts from config.accounts.yaml.")
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
    env_config = load_env_config(project_root / ".env")

    if args.all_contexts:
        contexts = load_account_contexts(project_root / "config.accounts.yaml")
        result = run_multi_context_export(
            project_root=project_root,
            settings=settings,
            config_path=project_root / args.config,
            base_env_config=env_config,
            contexts=contexts,
        )
        return 0 if not result.context_errors else 1

    if args.context_key:
        contexts = load_account_contexts(project_root / "config.accounts.yaml")
        selected = next((context for context in contexts if context.key == args.context_key), None)
        if selected is None:
            raise ValueError(f"Context '{args.context_key}' not found in config.accounts.yaml.")
        run_date_prefix = resolve_date_range(settings.date_range).export_date.isoformat()
        result = run_context_export(
            project_root=project_root,
            settings=settings,
            config_path=project_root / args.config,
            base_env_config=env_config,
            context=selected,
            export_base_name_override=f"{run_date_prefix}_{selected.key}_{selected.google_ads_customer_id}",
            export_mode="selected_context",
        )
        return result.exit_code

    return run_export(
        settings=settings,
        project_root=project_root,
        config_path=project_root / args.config,
    )


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
