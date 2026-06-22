from __future__ import annotations

from pathlib import Path

from app.integrations.sklik.auth import load_sklik_runtime_config as _load_sklik_runtime_config


def load_sklik_runtime(project_root: Path):
    return _load_sklik_runtime_config(project_root)


def load_sklik_runtime_config(project_root: Path):
    return _load_sklik_runtime_config(project_root)
