from __future__ import annotations

import argparse

from app.web.app_factory import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Google Ads Audit Exporter web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on.")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
