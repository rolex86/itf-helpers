from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("google_ads_audit_exporter")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
