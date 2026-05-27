from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

try:  # pragma: no cover - optional at static verification time
    from google.protobuf.json_format import MessageToDict
except Exception:  # pragma: no cover - fallback when protobuf is unavailable
    MessageToDict = None  # type: ignore[assignment]


def _proto_message_to_dict(value: Any) -> Any:
    if MessageToDict is None:
        return str(value)
    if hasattr(value, "_pb"):
        return MessageToDict(value._pb, preserving_proto_field_name=True)
    if hasattr(value, "DESCRIPTOR"):
        return MessageToDict(value, preserving_proto_field_name=True)
    return str(value)


def serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        if all(hasattr(item, "text") for item in value):
            return " | ".join(str(getattr(item, "text", "")) for item in value)
        return " | ".join(str(serialize_value(item)) for item in value)
    if hasattr(value, "paths"):
        return " | ".join(str(item) for item in getattr(value, "paths"))
    if hasattr(value, "_pb") or hasattr(value, "DESCRIPTOR"):
        return _proto_message_to_dict(value)
    if hasattr(value, "name") and not isinstance(value, str):
        return str(getattr(value, "name"))
    return str(value)


def extract_path_value(row: Any, path: str) -> Any:
    current = row
    for part in path.split("."):
        if current is None:
            return None
        attr_name = f"{part}_" if part == "type" else part
        if not hasattr(current, attr_name):
            return None
        current = getattr(current, attr_name)
    return serialize_value(current)
