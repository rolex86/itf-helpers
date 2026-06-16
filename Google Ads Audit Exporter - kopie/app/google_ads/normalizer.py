from __future__ import annotations

import json
from collections.abc import Iterable
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

    try:
        if hasattr(value, "_pb") and hasattr(value._pb, "DESCRIPTOR"):
            data = MessageToDict(value._pb, preserving_proto_field_name=True)
        elif hasattr(value, "DESCRIPTOR"):
            data = MessageToDict(value, preserving_proto_field_name=True)
        else:
            return str(value)
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return str(value)


def _is_repeated_container(value: Any) -> bool:
    if isinstance(value, (str, bytes, bytearray, dict)):
        return False
    if isinstance(value, (datetime, date, Decimal, Enum)):
        return False
    if isinstance(value, Iterable) and not hasattr(value, "DESCRIPTOR"):
        return True
    return False


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

    if hasattr(value, "paths"):
        return " | ".join(str(item) for item in getattr(value, "paths"))

    if _is_repeated_container(value):
        items = list(value)
        if not items:
            return ""

        if all(hasattr(item, "text") for item in items):
            return " | ".join(
                str(getattr(item, "text", ""))
                for item in items
                if getattr(item, "text", "")
            )

        return " | ".join(str(serialize_value(item)) for item in items)

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
