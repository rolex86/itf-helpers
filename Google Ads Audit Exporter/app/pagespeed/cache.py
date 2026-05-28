from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PageSpeedCache:
    base_dir: Path
    ttl_days: int

    def load(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        cache_path = self._cache_path(payload)
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
        if datetime.now(timezone.utc) - created > timedelta(days=self.ttl_days):
            return None
        result = raw.get("result")
        return result if isinstance(result, dict) else None

    def save(self, payload: dict[str, Any], result: dict[str, Any]) -> None:
        cache_path = self._cache_path(payload)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _cache_path(self, payload: dict[str, Any]) -> Path:
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
        return self.base_dir / f"{digest}.json"
