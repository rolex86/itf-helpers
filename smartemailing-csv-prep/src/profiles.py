from __future__ import annotations

import copy
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PROFILE_ID = "plussystem"
DEFAULT_PROFILE_NAME = "PlusSystem"
PROFILE_SETTINGS_FILENAME = "profile_settings.local.yaml"
PROFILE_ALLOWLIST_FILENAME = "program_custom_fields_allowlist.local"
PROFILE_FAVORITES_FILENAME = "se_list_favorites.local"


@dataclass(frozen=True)
class ProfileItem:
    id: str
    name: str


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


def slugify_profile_id(name: str) -> str:
    raw = _normalize_text(name).casefold()
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = raw.strip("_")
    return raw or "profil"


def ensure_unique_profile_id(base_id: str, used_ids: set[str]) -> str:
    candidate = slugify_profile_id(base_id)
    if candidate not in used_ids:
        return candidate
    counter = 2
    while f"{candidate}_{counter}" in used_ids:
        counter += 1
    return f"{candidate}_{counter}"


def profile_dir(root: Path, profile_id: str) -> Path:
    return root / str(profile_id).strip()


def profile_settings_path(root: Path, profile_id: str) -> Path:
    return profile_dir(root, profile_id) / PROFILE_SETTINGS_FILENAME


def profile_allowlist_path(root: Path, profile_id: str) -> Path:
    return profile_dir(root, profile_id) / PROFILE_ALLOWLIST_FILENAME


def profile_favorites_path(root: Path, profile_id: str) -> Path:
    return profile_dir(root, profile_id) / PROFILE_FAVORITES_FILENAME


def _default_index() -> dict[str, Any]:
    now = utcnow_iso()
    return {
        "version": 1,
        "active_profile_id": DEFAULT_PROFILE_ID,
        "profiles": [
            {
                "id": DEFAULT_PROFILE_ID,
                "name": DEFAULT_PROFILE_NAME,
                "created_at": now,
                "updated_at": now,
            }
        ],
    }


def _sanitize_index(data: Any) -> dict[str, Any]:
    now = utcnow_iso()
    payload = data if isinstance(data, dict) else {}
    profiles_raw = payload.get("profiles", [])
    profiles: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for item in profiles_raw if isinstance(profiles_raw, list) else []:
        if not isinstance(item, dict):
            continue
        raw_name = _normalize_text(item.get("name", ""))
        raw_id = _normalize_text(item.get("id", ""))
        if not raw_name and not raw_id:
            continue
        profile_id = ensure_unique_profile_id(raw_id or raw_name, used_ids)
        used_ids.add(profile_id)
        profile_name = raw_name or raw_id or profile_id
        profiles.append(
            {
                "id": profile_id,
                "name": profile_name,
                "created_at": str(item.get("created_at", "")).strip() or now,
                "updated_at": str(item.get("updated_at", "")).strip() or now,
            }
        )

    if not profiles:
        return _default_index()

    active_profile_id = _normalize_text(payload.get("active_profile_id", ""))
    active_ids = {x["id"] for x in profiles}
    if active_profile_id not in active_ids:
        active_profile_id = profiles[0]["id"]

    return {
        "version": 1,
        "active_profile_id": active_profile_id,
        "profiles": profiles,
    }


def load_profiles_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_index()
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
    except Exception:
        return _default_index()
    return _sanitize_index(payload)


def save_profiles_index(path: Path, index_payload: dict[str, Any]) -> None:
    payload = _sanitize_index(index_payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def list_profiles(index_payload: dict[str, Any]) -> list[ProfileItem]:
    rows = _sanitize_index(index_payload).get("profiles", [])
    out: list[ProfileItem] = []
    for row in rows:
        out.append(ProfileItem(id=str(row.get("id", "")).strip(), name=str(row.get("name", "")).strip()))
    return out


def get_active_profile_id(index_payload: dict[str, Any]) -> str:
    sanitized = _sanitize_index(index_payload)
    return str(sanitized.get("active_profile_id", DEFAULT_PROFILE_ID)).strip() or DEFAULT_PROFILE_ID


def set_active_profile(index_payload: dict[str, Any], profile_id: str) -> dict[str, Any]:
    payload = _sanitize_index(index_payload)
    target = str(profile_id).strip()
    valid_ids = {x["id"] for x in payload.get("profiles", [])}
    if target in valid_ids:
        payload["active_profile_id"] = target
    return payload


def create_profile(index_payload: dict[str, Any], name: str) -> tuple[dict[str, Any], str]:
    payload = _sanitize_index(index_payload)
    profile_name = _normalize_text(name) or "Nový profil"
    used_ids = {x["id"] for x in payload.get("profiles", [])}
    profile_id = ensure_unique_profile_id(profile_name, used_ids)
    now = utcnow_iso()
    profiles = list(payload.get("profiles", []))
    profiles.append(
        {
            "id": profile_id,
            "name": profile_name,
            "created_at": now,
            "updated_at": now,
        }
    )
    payload["profiles"] = profiles
    payload["active_profile_id"] = profile_id
    return payload, profile_id


def duplicate_profile(index_payload: dict[str, Any], source_profile_id: str, new_name: str = "") -> tuple[dict[str, Any], str]:
    payload = _sanitize_index(index_payload)
    source_id = str(source_profile_id).strip()
    source = next((x for x in payload.get("profiles", []) if str(x.get("id", "")).strip() == source_id), None)
    if source is None:
        return create_profile(payload, new_name or "Kopie profilu")

    source_name = _normalize_text(source.get("name", ""))
    profile_name = _normalize_text(new_name) or f"{source_name} kopie"
    used_ids = {x["id"] for x in payload.get("profiles", [])}
    profile_id = ensure_unique_profile_id(profile_name, used_ids)
    now = utcnow_iso()
    profiles = list(payload.get("profiles", []))
    profiles.append(
        {
            "id": profile_id,
            "name": profile_name,
            "created_at": now,
            "updated_at": now,
        }
    )
    payload["profiles"] = profiles
    payload["active_profile_id"] = profile_id
    return payload, profile_id


def rename_profile(index_payload: dict[str, Any], profile_id: str, new_name: str) -> dict[str, Any]:
    payload = _sanitize_index(index_payload)
    target_id = str(profile_id).strip()
    target_name = _normalize_text(new_name)
    if not target_name:
        return payload
    now = utcnow_iso()
    for row in payload.get("profiles", []):
        if str(row.get("id", "")).strip() == target_id:
            row["name"] = target_name
            row["updated_at"] = now
            break
    return payload


def delete_profile(index_payload: dict[str, Any], profile_id: str) -> tuple[dict[str, Any], str]:
    payload = _sanitize_index(index_payload)
    target_id = str(profile_id).strip()
    rows = list(payload.get("profiles", []))
    if len(rows) <= 1:
        return payload, get_active_profile_id(payload)

    remaining = [x for x in rows if str(x.get("id", "")).strip() != target_id]
    if not remaining:
        return payload, get_active_profile_id(payload)

    payload["profiles"] = remaining
    active_id = get_active_profile_id(payload)
    if active_id == target_id:
        active_id = str(remaining[0].get("id", "")).strip()
        payload["active_profile_id"] = active_id
    return payload, active_id


def delete_profile_files(root: Path, profile_id: str) -> None:
    target_dir = profile_dir(root, profile_id)
    if target_dir.exists():
        shutil.rmtree(target_dir)


def duplicate_profile_files(root: Path, source_profile_id: str, target_profile_id: str) -> None:
    src = profile_dir(root, source_profile_id)
    dst = profile_dir(root, target_profile_id)
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for filename in [PROFILE_SETTINGS_FILENAME, PROFILE_ALLOWLIST_FILENAME, PROFILE_FAVORITES_FILENAME]:
        src_file = src / filename
        dst_file = dst / filename
        if src_file.exists():
            shutil.copy2(src_file, dst_file)


def _default_profile_payload(profile_id: str = "", profile_name: str = "") -> dict[str, Any]:
    return {
        "version": 1,
        "profile_id": str(profile_id).strip(),
        "profile_name": str(profile_name).strip(),
        "saved_at": "",
        "settings": {},
        "presets": [],
    }


def load_profile_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_profile_payload()
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
    except Exception:
        return _default_profile_payload()
    if not isinstance(payload, dict):
        return _default_profile_payload()

    out = _default_profile_payload()
    out["version"] = 1
    out["profile_id"] = str(payload.get("profile_id", "")).strip()
    out["profile_name"] = str(payload.get("profile_name", "")).strip()
    out["saved_at"] = str(payload.get("saved_at", "")).strip()
    settings = payload.get("settings", {})
    out["settings"] = settings if isinstance(settings, dict) else {}
    presets = payload.get("presets", [])
    out["presets"] = presets if isinstance(presets, list) else []
    return out


def save_profile_payload(path: Path, payload: dict[str, Any]) -> None:
    merged = _default_profile_payload()
    source = payload if isinstance(payload, dict) else {}
    merged["profile_id"] = str(source.get("profile_id", "")).strip()
    merged["profile_name"] = str(source.get("profile_name", "")).strip()
    merged["settings"] = source.get("settings", {}) if isinstance(source.get("settings", {}), dict) else {}
    merged["presets"] = source.get("presets", []) if isinstance(source.get("presets", []), list) else []
    merged["saved_at"] = utcnow_iso()

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)


def load_profile_settings(path: Path) -> dict[str, Any]:
    payload = load_profile_payload(path)
    settings = payload.get("settings", {})
    return copy.deepcopy(settings if isinstance(settings, dict) else {})


def save_profile_settings(
    path: Path,
    settings: dict[str, Any],
    profile_id: str = "",
    profile_name: str = "",
    presets: list[dict[str, Any]] | None = None,
) -> None:
    payload = load_profile_payload(path)
    payload["profile_id"] = str(profile_id).strip() or str(payload.get("profile_id", "")).strip()
    payload["profile_name"] = str(profile_name).strip() or str(payload.get("profile_name", "")).strip()
    payload["settings"] = copy.deepcopy(settings if isinstance(settings, dict) else {})
    if presets is not None:
        payload["presets"] = copy.deepcopy(presets if isinstance(presets, list) else [])
    save_profile_payload(path, payload)


def load_profile_presets(path: Path) -> list[dict[str, Any]]:
    payload = load_profile_payload(path)
    presets = payload.get("presets", [])
    return copy.deepcopy(presets if isinstance(presets, list) else [])


def save_profile_presets(path: Path, presets: list[dict[str, Any]], profile_id: str = "", profile_name: str = "") -> None:
    payload = load_profile_payload(path)
    payload["profile_id"] = str(profile_id).strip() or str(payload.get("profile_id", "")).strip()
    payload["profile_name"] = str(profile_name).strip() or str(payload.get("profile_name", "")).strip()
    payload["presets"] = copy.deepcopy(presets if isinstance(presets, list) else [])
    save_profile_payload(path, payload)
