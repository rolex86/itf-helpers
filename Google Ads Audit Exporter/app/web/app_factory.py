from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.web.routes import web_bp


def create_app(project_root: Path | None = None) -> Flask:
    root = project_root or Path(__file__).resolve().parents[2]
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )
    app.config["SECRET_KEY"] = "local-google-ads-audit-exporter"
    app.config["PROJECT_ROOT"] = root
    app.register_blueprint(web_bp)
    return app
