from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SearchConsoleCache:
    base_dir: Path
    ttl_hours: int = 12

    def load(self, namespace: str, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        cache_path = self._cache_path(namespace=namespace, payload=payload)
        if not cache_path.exists():
            return None
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        created_at = raw.get("created_at")
        if not created_at:
            return None
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - created > timedelta(hours=self.ttl_hours):
            return None
        rows = raw.get("rows", [])
        return rows if isinstance(rows, list) else None

    def save(self, namespace: str, payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        cache_path = self._cache_path(namespace=namespace, payload=payload)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _cache_path(self, namespace: str, payload: dict[str, Any]) -> Path:
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
        return self.base_dir / namespace / f"{digest}.json"
