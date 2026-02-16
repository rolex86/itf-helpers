from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_JOB_HISTORY_PATH = Path("config/job_history.jsonl")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_job_history(record: dict[str, Any], path: Path = DEFAULT_JOB_HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    if not payload.get("timestamp"):
        payload["timestamp"] = utcnow_iso()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_job_history(path: Path = DEFAULT_JOB_HISTORY_PATH, limit: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)

    rows.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return rows[: max(0, int(limit))]


def summarize_job_alerts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"total": 0, "failed": 0, "failure_rate": 0.0, "recent_failures": 0}

    total = len(rows)
    failed = sum(1 for x in rows if str(x.get("status", "")).lower() in {"failed", "error"})
    recent = rows[:10]
    recent_failures = sum(1 for x in recent if str(x.get("status", "")).lower() in {"failed", "error"})
    rate = failed / total if total else 0.0

    return {
        "total": total,
        "failed": failed,
        "failure_rate": rate,
        "recent_failures": recent_failures,
    }
