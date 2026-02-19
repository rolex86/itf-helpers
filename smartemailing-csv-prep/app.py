from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from src.export_smartemailing import (
    build_import_df,
    dataframe_to_csv_bytes,
    deduplicate_import_df,
    drop_empty_columns,
    split_by_bucket,
)
from src.io_utils import clean_columns, read_csv_best_effort
from src.normalize import detect_source, normalize_df
from src.reporting import build_report, find_duplicates_from_stats
from src.schema import Schema, schema_from_export_df
from src.jobs import append_job_history, clear_job_history, load_job_history, summarize_job_alerts
from src.profiles import (
    create_profile,
    delete_profile,
    delete_profile_files,
    duplicate_profile,
    duplicate_profile_files,
    get_active_profile_id,
    list_profiles,
    load_profile_payload,
    load_profile_presets,
    load_profiles_index,
    profile_allowlist_path,
    profile_dir,
    profile_favorites_path,
    profile_settings_path,
    rename_profile,
    save_profile_presets,
    save_profile_settings,
    save_profiles_index,
    slugify_profile_id,
    set_active_profile,
)
from src.smartemailing_api import (
    DEFAULT_MANAGED_EMPTY_CUSTOM_FIELD_NAME_PATTERN,
    DEFAULT_BASE_URL,
    SmartEmailingApiClient,
    SmartEmailingApiError,
    SmartEmailingCredentials,
    build_api_contacts_from_import_df,
    combine_schema_columns,
)
from src.transforms import (
    apply_country_bucket,
    apply_name_split,
    split_emails,
    validate_emails_without_split,
)


st.set_page_config(page_title="SmartEmailing CSV Prep", layout="wide")
st.title("SmartEmailing CSV Prep – generátor importů (API/CSV)")
st.markdown(
    """
    <style>
    div[data-testid="stFileUploaderDropzoneInstructions"] {
        position: relative;
    }
    div[data-testid="stFileUploaderDropzoneInstructions"] > div:first-child,
    div[data-testid="stFileUploaderDropzoneInstructions"] p,
    div[data-testid="stFileUploaderDropzoneInstructions"] span {
        font-size: 0 !important;
        line-height: 0 !important;
    }
    div[data-testid="stFileUploaderDropzoneInstructions"]::before {
        content: "Přetáhněte soubor sem";
        font-size: 0.95rem;
        font-weight: 600;
        line-height: 1.4;
        display: block;
        margin-bottom: 0.25rem;
    }
    div[data-testid="stFileUploaderDropzone"] button,
    div[data-testid="stFileUploaderDropzone"] button * {
        font-size: 0 !important;
    }
    div[data-testid="stFileUploaderDropzone"] button::after {
        content: "Procházet soubory";
        font-size: 0.875rem;
        line-height: 1.4;
        display: inline-block;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


CFG_PATH = Path("config/mappings.yaml")
with CFG_PATH.open("r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

SCHEMA_CACHE_PATH = Path("config/schema_cache.yaml")
API_SCHEMA_CACHE_PATH = Path("config/schema_cache_api.yaml")
API_CREDENTIALS_PATH = Path("config/se_api_credentials.local")
PROFILES_ROOT_PATH = Path("config/profiles")
PROFILES_INDEX_PATH = PROFILES_ROOT_PATH / "index.yaml"
LEGACY_API_LIST_FAVORITES_PATH = Path("config/se_list_favorites.local")
LEGACY_PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH = Path("config/program_custom_fields_allowlist.local")
COUNTRY_BUCKET_KEYS = ("CZ_SK", "DE_AT_CH", "EN")


def schema_hash(columns: list[str]) -> str:
    payload = "\n".join(columns).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_cached_schema(path: Path) -> tuple[Schema | None, dict[str, Any]]:
    meta: dict[str, Any] = {}
    if not path.exists():
        return None, meta
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        columns = [str(c).strip() for c in data.get("columns", []) if str(c).strip()]
        if not columns:
            return None, meta
        meta = {k: v for k, v in data.items() if k != "columns"}
        if "schema_hash" not in meta:
            meta["schema_hash"] = schema_hash(columns)
        if "version" not in meta:
            meta["version"] = 1
        return Schema(columns=columns, columns_set=set(columns)), meta
    except Exception:
        return None, meta


def save_cached_schema(
    path: Path,
    schema: Schema,
    source_file: str,
    source_kind: str = "csv_upload",
    extra_meta: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "source_kind": source_kind,
        "schema_hash": schema_hash(schema.columns),
        "columns": schema.columns,
    }
    if extra_meta:
        payload.update(extra_meta)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def load_saved_api_credentials(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"username": "", "api_key": "", "base_url": DEFAULT_BASE_URL}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {
            "username": str(data.get("username", "")).strip(),
            "api_key": str(data.get("api_key", "")).strip(),
            "base_url": str(data.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL,
        }
    except Exception:
        return {"username": "", "api_key": "", "base_url": DEFAULT_BASE_URL}


def save_api_credentials(path: Path, username: str, api_key: str, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "username": str(username).strip(),
        "api_key": str(api_key).strip(),
        "base_url": str(base_url).strip() or DEFAULT_BASE_URL,
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def clear_api_credentials(path: Path) -> None:
    if path.exists():
        path.unlink()


def load_api_list_favorites(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw_ids = data.get("favorite_list_ids", [])
        if not isinstance(raw_ids, list):
            return set()
        return {str(x).strip() for x in raw_ids if str(x).strip()}
    except Exception:
        return set()


def save_api_list_favorites(path: Path, favorite_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def _sort_key(raw_id: str) -> tuple[int, int, str]:
        value = str(raw_id).strip()
        try:
            return (0, -int(value), value.casefold())
        except Exception:
            return (1, 0, value.casefold())

    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "favorite_list_ids": sorted(
            {str(x).strip() for x in favorite_ids if str(x).strip()},
            key=_sort_key,
        ),
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def normalize_api_favorite_list_ids(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(x).strip() for x in values if str(x).strip()}


def normalize_api_bucket_favorite_ids_map(values: Any) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {bucket: [] for bucket in COUNTRY_BUCKET_KEYS}
    if not isinstance(values, dict):
        return normalized
    for bucket in COUNTRY_BUCKET_KEYS:
        raw_values = values.get(bucket, [])
        if not isinstance(raw_values, list):
            raw_values = []
        normalized[bucket] = sorted(
            {str(x).strip() for x in raw_values if str(x).strip()}
        )
    return normalized


def load_api_favorite_list_ids_from_preset(preset: dict[str, Any] | None) -> set[str]:
    if not isinstance(preset, dict):
        return set()
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return set()
    return normalize_api_favorite_list_ids(values.get("api_list_favorite_ids", []))


def preset_has_api_favorite_list_ids_definition(preset: dict[str, Any] | None) -> bool:
    if not isinstance(preset, dict):
        return False
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return False
    return "api_list_favorite_ids" in values


def save_api_favorite_list_ids_to_preset(preset: dict[str, Any], favorite_ids: set[str]) -> None:
    values = preset.get("values", {})
    if not isinstance(values, dict):
        values = {}
    values["api_list_favorite_ids"] = sorted(
        {str(x).strip() for x in favorite_ids if str(x).strip()}
    )
    preset["values"] = values
    preset["updated_at"] = datetime.now(timezone.utc).isoformat()


def load_api_bucket_favorite_ids_from_preset(preset: dict[str, Any] | None) -> dict[str, list[str]]:
    if not isinstance(preset, dict):
        return {bucket: [] for bucket in COUNTRY_BUCKET_KEYS}
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return {bucket: [] for bucket in COUNTRY_BUCKET_KEYS}
    return normalize_api_bucket_favorite_ids_map(values.get("api_bucket_favorite_list_ids_by_bucket", {}))


def preset_has_api_bucket_favorite_ids_definition(preset: dict[str, Any] | None) -> bool:
    if not isinstance(preset, dict):
        return False
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return False
    return "api_bucket_favorite_list_ids_by_bucket" in values


def save_api_bucket_favorite_ids_to_preset(
    preset: dict[str, Any],
    bucket_favorite_ids: dict[str, list[str]],
) -> None:
    values = preset.get("values", {})
    if not isinstance(values, dict):
        values = {}
    values["api_bucket_favorite_list_ids_by_bucket"] = normalize_api_bucket_favorite_ids_map(
        bucket_favorite_ids
    )
    preset["values"] = values
    preset["updated_at"] = datetime.now(timezone.utc).isoformat()


def load_program_custom_fields_allowlist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw_ids = data.get("custom_field_ids", [])
        if not isinstance(raw_ids, list):
            return set()
        return {str(x).strip() for x in raw_ids if str(x).strip()}
    except Exception:
        return set()


def save_program_custom_fields_allowlist(path: Path, custom_field_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "custom_field_ids": sorted({str(x).strip() for x in custom_field_ids if str(x).strip()}),
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def normalize_allowlist_ids(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(x).strip() for x in values if str(x).strip()}


def load_allowlist_ids_from_preset(preset: dict[str, Any] | None) -> set[str]:
    if not isinstance(preset, dict):
        return set()
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return set()
    return normalize_allowlist_ids(values.get("program_custom_fields_allowlist_ids", []))


def preset_has_allowlist_definition(preset: dict[str, Any] | None) -> bool:
    if not isinstance(preset, dict):
        return False
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return False
    return "program_custom_fields_allowlist_ids" in values


def save_allowlist_ids_to_preset(preset: dict[str, Any], allowlist_ids: set[str]) -> None:
    values = preset.get("values", {})
    if not isinstance(values, dict):
        values = {}
    values["program_custom_fields_allowlist_ids"] = sorted(
        {str(x).strip() for x in allowlist_ids if str(x).strip()}
    )
    preset["values"] = values
    preset["updated_at"] = datetime.now(timezone.utc).isoformat()


def build_system_schema_columns(cfg: dict[str, Any]) -> list[str]:
    se_cfg = cfg.get("smartemailing", {})
    field_map = se_cfg.get("field_map", {})
    base = [str(col).strip() for col in field_map.keys() if str(col).strip()]

    programs_cfg = se_cfg.get("programs", {})
    combined_field = str(programs_cfg.get("combined_field_name", "")).strip()
    if programs_cfg.get("also_fill_combined_field") and combined_field:
        base.append(combined_field)

    return base


def build_ignore_missing_custom_for_columns(
    field_map_cfg: dict[str, Any],
    api_system_field_map_cfg: dict[str, Any],
    configured_ignore_missing_custom: list[str],
) -> list[str]:
    mapped_system_keys = {
        str(x).strip().casefold()
        for x in api_system_field_map_cfg.keys()
        if str(x).strip()
    }
    default_ignore_missing_custom = [
        str(col).strip()
        for col in field_map_cfg.keys()
        if str(col).strip() and str(col).strip().casefold() not in mapped_system_keys
    ]
    return list(dict.fromkeys(default_ignore_missing_custom + configured_ignore_missing_custom))


def fetch_schema_from_api(
    username: str,
    api_key: str,
    base_url: str,
    read_only: bool = False,
) -> tuple[Schema, dict[str, Any]]:
    creds = SmartEmailingCredentials(
        username=str(username).strip(),
        api_key=str(api_key).strip(),
        base_url=str(base_url).strip() or DEFAULT_BASE_URL,
    )
    if not creds.username or not creds.api_key:
        raise ValueError("Vyplň SmartEmailing uživatelské jméno i API klíč.")

    client = SmartEmailingApiClient(creds, read_only=bool(read_only))
    ping = client.ping()
    api_cfg = CFG.get("smartemailing", {}).get("api", {})
    custom_fields_endpoint_candidates = [
        str(x).strip()
        for x in api_cfg.get("custom_fields_endpoint_candidates", [])
        if str(x).strip()
    ]
    custom_fields_search_endpoint_candidates = [
        str(x).strip()
        for x in api_cfg.get(
            "custom_fields_search_endpoint_candidates",
            [],
        )
        if str(x).strip()
    ]
    custom_fields = client.fetch_custom_field_names(
        endpoint_candidates=custom_fields_endpoint_candidates,
        search_endpoint_candidates=custom_fields_search_endpoint_candidates,
    )
    min_custom_fields = 1
    try:
        min_custom_fields = int(api_cfg.get("required_min_custom_fields", 1))
    except Exception:
        min_custom_fields = 1
    if min_custom_fields > 0 and len(custom_fields) < min_custom_fields:
        raise SmartEmailingApiError(
            f"API vrátilo jen {len(custom_fields)} vlastních polí, minimum je {min_custom_fields}."
        )
    columns = combine_schema_columns(build_system_schema_columns(CFG), custom_fields)
    if not columns:
        raise SmartEmailingApiError("SmartEmailing API nevrátilo žádné použitelné názvy polí.")

    schema = Schema(columns=columns, columns_set=set(columns))
    meta = {
        "source_kind": "smartemailing_api",
        "api_base_url": client.base_url,
        "ping_status": str(ping.get("status", "")) if isinstance(ping, dict) else "",
        "custom_field_count": len(custom_fields),
    }
    return schema, meta


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def api_issues_to_report_df(issues: list[dict[str, Any]]) -> pd.DataFrame:
    if not issues:
        return pd.DataFrame(
            columns=["type", "row_index", "detail", "email_raw", "company", "source_file", "source_row_index"]
        )

    return pd.DataFrame(
        {
            "type": "api_payload_issue",
            "row_index": [str(x.get("row_index", "")) for x in issues],
            "detail": [f"{x.get('issue', '')}: {x.get('detail', '')}".strip(": ") for x in issues],
            "email_raw": "",
            "company": "",
            "source_file": "",
            "source_row_index": "",
        }
    )


def compute_import_confirmation_fingerprint(
    execution_mode: str,
    list_id: str,
    tag: str,
    canary_size: int,
    batch_size: int,
    max_contacts_limit: int,
    contacts: list[dict[str, Any]],
    bucket_routing: dict[str, str] | None = None,
) -> str:
    hasher = hashlib.sha256()
    meta = {
        "execution_mode": str(execution_mode).strip(),
        "list_id": str(list_id).strip(),
        "tag": str(tag).strip(),
        "canary_size": int(canary_size),
        "batch_size": int(batch_size),
        "max_contacts_limit": int(max_contacts_limit),
        "contacts_total": len(contacts),
        "bucket_routing": {
            str(k).strip(): str(v).strip()
            for k, v in (bucket_routing or {}).items()
            if str(k).strip() and str(v).strip()
        },
    }
    hasher.update(json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    hasher.update(b"\n")
    for contact in contacts:
        try:
            serialized = json.dumps(contact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            serialized = str(contact)
        hasher.update(serialized.encode("utf-8", errors="ignore"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def build_import_confirmation_summary(
    execution_mode: str,
    list_id: str,
    tag: str,
    canary_size: int,
    batch_size: int,
    max_contacts_limit: int,
    contacts: list[dict[str, Any]],
    issues_count: int,
    diff_existing_contacts: int | None = None,
    diff_new_contacts: int | None = None,
    diff_updated_contacts: int | None = None,
    diff_unchanged_contacts: int | None = None,
    diff_removed_program_custom_fields: int | None = None,
    clear_removed_program_custom_fields_enabled: bool = False,
    bucket_routing: dict[str, str] | None = None,
    bucket_contacts: dict[str, int] | None = None,
) -> dict[str, Any]:
    emails_preview: list[str] = []
    payload_system_fields: set[str] = set()
    unique_custom_field_ids: set[str] = set()
    contacts_with_custom_fields = 0
    contacts_with_list_assignment = 0
    contacts_with_tags = 0
    custom_field_values_total = 0

    for contact in contacts:
        email = str(contact.get("emailaddress", "")).strip()
        if email and len(emails_preview) < 20:
            emails_preview.append(email)

        for key, value in contact.items():
            key_name = str(key).strip()
            if not key_name or key_name in {"customfields", "contactlists", "tags"} or key_name.startswith("__"):
                continue
            if str(value).strip():
                payload_system_fields.add(key_name)

        custom_values = contact.get("customfields", [])
        if isinstance(custom_values, list) and custom_values:
            contacts_with_custom_fields += 1
            custom_field_values_total += len(custom_values)
            for item in custom_values:
                if not isinstance(item, dict):
                    continue
                field_id = str(item.get("id", "")).strip()
                if field_id:
                    unique_custom_field_ids.add(field_id)

        contact_lists = contact.get("contactlists", [])
        if isinstance(contact_lists, list) and contact_lists:
            contacts_with_list_assignment += 1

        tags = contact.get("tags", [])
        if isinstance(tags, list) and tags:
            contacts_with_tags += 1

    mode_label = {
        "api_safe_import": "API bezpečný import",
        "api_full_import": "API plný import",
    }.get(execution_mode, str(execution_mode))

    diff_available = all(
        value is not None
        for value in [diff_existing_contacts, diff_new_contacts, diff_updated_contacts, diff_unchanged_contacts]
    )

    return {
        "mode_label": mode_label,
        "contacts_total": len(contacts),
        "issues_count": int(issues_count),
        "staging_list_id": str(list_id).strip(),
        "bucket_routing": {
            str(k).strip(): str(v).strip()
            for k, v in (bucket_routing or {}).items()
            if str(k).strip() and str(v).strip()
        },
        "bucket_contacts": {
            str(k).strip(): int(v)
            for k, v in (bucket_contacts or {}).items()
            if str(k).strip()
        },
        "staging_tag": str(tag).strip(),
        "canary_size": int(canary_size),
        "batch_size": int(batch_size),
        "max_contacts_limit": int(max_contacts_limit),
        "contacts_with_custom_fields": contacts_with_custom_fields,
        "contacts_with_list_assignment": contacts_with_list_assignment,
        "contacts_with_tags": contacts_with_tags,
        "custom_field_values_total": custom_field_values_total,
        "custom_fields_unique_ids": len(unique_custom_field_ids),
        "payload_system_fields": sorted(payload_system_fields),
        "emails_preview": emails_preview,
        "diff_available": bool(diff_available),
        "diff_existing_contacts": int(diff_existing_contacts or 0),
        "diff_new_contacts": int(diff_new_contacts or 0),
        "diff_updated_contacts": int(diff_updated_contacts or 0),
        "diff_unchanged_contacts": int(diff_unchanged_contacts or 0),
        "diff_removed_program_custom_fields": int(diff_removed_program_custom_fields or 0),
        "clear_removed_program_custom_fields_enabled": bool(clear_removed_program_custom_fields_enabled),
        "new_vs_update_note": (
            "Rozdělení nové/aktualizace je spočítané diff porovnáním se staging seznamem."
            if diff_available
            else "Rozdělení na nové vs. aktualizované kontakty API před importem spolehlivě nevrací."
        ),
    }


def normalize_email_key(email: Any) -> str:
    return str(email).strip().casefold()


def normalize_scalar_for_diff(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


CASE_INSENSITIVE_SYSTEM_FIELDS_FOR_DIFF = {
    "name",
    "surname",
    "titlesbefore",
    "titlesafter",
}


def normalize_system_field_for_diff(field_name: Any, value: Any) -> str:
    normalized = normalize_scalar_for_diff(value)
    key = str(field_name).strip().casefold()
    if key in CASE_INSENSITIVE_SYSTEM_FIELDS_FOR_DIFF:
        return normalized.casefold()
    return normalized


def normalize_array_for_diff(value: Any, separators: list[str]) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, list):
        cleaned = [normalize_scalar_for_diff(x) for x in value]
        return tuple(sorted({x for x in cleaned if x}))
    value_str = normalize_scalar_for_diff(value)
    if not value_str:
        return tuple()
    if value_str.startswith("[") and value_str.endswith("]"):
        try:
            parsed = json.loads(value_str)
            if isinstance(parsed, list):
                cleaned = [normalize_scalar_for_diff(x) for x in parsed]
                return tuple(sorted({x for x in cleaned if x}))
        except Exception:
            pass
    split_parts = [value_str]
    for sep in separators:
        if sep and sep in value_str:
            split_parts = [part.strip() for part in value_str.split(sep)]
            break
    cleaned = [normalize_scalar_for_diff(x) for x in split_parts]
    return tuple(sorted({x for x in cleaned if x}))


def flatten_custom_value_for_diff(value: Any, separators: list[str], split_scalar_values: bool) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(flatten_custom_value_for_diff(item, separators, split_scalar_values=True))
        return out

    if isinstance(value, dict):
        for key in ["value", "values", "customfield_value", "custom_field_value"]:
            if key in value:
                return flatten_custom_value_for_diff(value.get(key), separators, split_scalar_values=True)

        for key in ["data", "item", "attributes"]:
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                nested_flat = flatten_custom_value_for_diff(nested, separators, split_scalar_values=True)
                if nested_flat:
                    return nested_flat

        for key in ["name", "label", "title", "code"]:
            raw = normalize_scalar_for_diff(value.get(key, ""))
            if raw:
                return [raw]

        out: list[str] = []
        for nested in value.values():
            out.extend(flatten_custom_value_for_diff(nested, separators, split_scalar_values=True))
        if out:
            return out
        return []

    value_str = normalize_scalar_for_diff(value)
    if not value_str:
        return []

    if value_str.startswith("[") and value_str.endswith("]"):
        try:
            parsed = json.loads(value_str)
            if isinstance(parsed, list):
                return flatten_custom_value_for_diff(parsed, separators, split_scalar_values=True)
        except Exception:
            pass

    if split_scalar_values:
        split_parts = [value_str]
        for sep in separators:
            if sep and sep in value_str:
                split_parts = [part.strip() for part in value_str.split(sep)]
                break
        return [x for x in [normalize_scalar_for_diff(part) for part in split_parts] if x]

    return [value_str]


def normalize_custom_value_for_diff(value: Any, separators: list[str], split_scalar_values: bool) -> tuple[str, ...]:
    flattened = flatten_custom_value_for_diff(value, separators, split_scalar_values=split_scalar_values)
    return tuple(sorted({x for x in flattened if x}))


def to_streamlit_safe_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != "object":
            continue
        out[col] = out[col].map(
            lambda value: json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list, tuple, set))
            else value
        )
    return out


def init_run_log_state() -> None:
    if "run_log_entries" not in st.session_state:
        st.session_state["run_log_entries"] = []


def clear_run_log_state() -> None:
    st.session_state["run_log_entries"] = []


def append_run_log_entry(level: str, message: str) -> None:
    init_run_log_state()
    entries = st.session_state.get("run_log_entries", [])
    if not isinstance(entries, list):
        entries = []
    entries.append(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": str(level).strip().upper(),
            "message": str(message),
        }
    )
    st.session_state["run_log_entries"] = entries[-300:]


def card_container():
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()


def split_contact_for_diff(contact: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    system_fields: dict[str, Any] = {}
    custom_fields: dict[str, Any] = {}
    tags: list[str] = []

    for key, value in contact.items():
        key_name = str(key).strip()
        if not key_name or key_name in {"contactlists"}:
            continue
        if key_name.startswith("__"):
            continue
        if key_name == "customfields":
            if isinstance(value, list):
                for custom_field in value:
                    if not isinstance(custom_field, dict):
                        continue
                    field_id = str(custom_field.get("id", "")).strip()
                    if not field_id:
                        continue
                    custom_fields[field_id] = custom_field.get("value")
            continue
        if key_name == "tags":
            if isinstance(value, list):
                tags = [normalize_scalar_for_diff(x) for x in value if normalize_scalar_for_diff(x)]
            elif normalize_scalar_for_diff(value):
                tags = [normalize_scalar_for_diff(value)]
            continue
        if key_name == "emailaddress":
            continue
        if normalize_scalar_for_diff(value):
            system_fields[key_name] = value

    return system_fields, custom_fields, sorted(set(tags))


def diff_api_contacts(
    import_contacts: list[dict[str, Any]],
    existing_contacts: list[dict[str, Any]],
    array_value_split_separators: list[str],
    clearable_custom_field_ids: set[str] | None = None,
) -> dict[str, Any]:
    existing_by_email: dict[str, dict[str, Any]] = {}
    existing_split_by_email: dict[str, tuple[dict[str, Any], dict[str, Any], list[str]]] = {}
    for existing_contact in existing_contacts:
        email_key = normalize_email_key(existing_contact.get("emailaddress", ""))
        if not email_key:
            continue
        if email_key not in existing_by_email:
            existing_by_email[email_key] = existing_contact
            existing_split_by_email[email_key] = split_contact_for_diff(existing_contact)

    prepared_import_rows: list[
        tuple[
            dict[str, Any],
            str,
            str,
            dict[str, Any],
            dict[str, Any],
            list[str],
        ]
    ] = []
    import_has_custom_fields = False
    for import_contact in import_contacts:
        email = normalize_scalar_for_diff(import_contact.get("emailaddress", ""))
        email_key = normalize_email_key(email)
        if not email_key:
            continue
        import_system, import_custom, import_tags = split_contact_for_diff(import_contact)
        if import_custom:
            import_has_custom_fields = True
        prepared_import_rows.append(
            (import_contact, email, email_key, import_system, import_custom, import_tags)
        )

    existing_contacts_with_custom_fields = 0
    for _, existing_custom_fields, _ in existing_split_by_email.values():
        if existing_custom_fields:
            existing_contacts_with_custom_fields += 1
    existing_contacts_with_customfields_key = sum(
        1 for existing_contact in existing_by_email.values() if "customfields" in existing_contact
    )
    custom_fields_compare_enabled = (
        len(existing_by_email) == 0
        or not import_has_custom_fields
        or existing_contacts_with_custom_fields > 0
        or existing_contacts_with_customfields_key > 0
    )
    new_contacts: list[dict[str, Any]] = []
    updated_contacts: list[dict[str, Any]] = []
    unchanged_contacts: list[dict[str, Any]] = []
    contacts_to_send: list[dict[str, Any]] = []
    updated_details: list[dict[str, Any]] = []
    unchanged_emails: list[str] = []
    matched_existing_contacts = 0
    removed_clearable_custom_fields_by_email: dict[str, list[str]] = {}
    removed_nonclearable_custom_fields_by_email: dict[str, list[str]] = {}
    clear_operations: list[dict[str, str]] = []
    clearable_ids = {str(x).strip() for x in (clearable_custom_field_ids or set()) if str(x).strip()}

    for contact, email, email_key, import_system, import_custom, import_tags in prepared_import_rows:
        existing_contact = existing_by_email.get(email_key)
        if existing_contact is None:
            new_contacts.append(contact)
            contacts_to_send.append(contact)
            continue
        matched_existing_contacts += 1

        existing_system, existing_custom, existing_tags = existing_split_by_email.get(
            email_key,
            ({}, {}, []),
        )

        changed_fields: list[str] = []
        field_diffs_by_field: dict[str, dict[str, Any]] = {}
        for field_name, import_value in import_system.items():
            existing_value = existing_system.get(field_name, "")
            import_norm = normalize_system_field_for_diff(field_name, import_value)
            existing_norm = normalize_system_field_for_diff(field_name, existing_value)
            if import_norm != existing_norm:
                changed_fields.append(field_name)
                field_diffs_by_field[field_name] = {
                    "field": field_name,
                    "before": existing_value,
                    "after": import_value,
                }

        if import_tags:
            existing_tags_set = {normalize_scalar_for_diff(x) for x in existing_tags}
            import_tags_set = {normalize_scalar_for_diff(x) for x in import_tags}
            if not import_tags_set.issubset(existing_tags_set):
                changed_fields.append("tags")
                field_diffs_by_field["tags"] = {
                    "field": "tags",
                    "before": list(existing_tags),
                    "after": list(import_tags),
                }

        contact_managed_custom_field_ids = {
            str(field_id).strip()
            for field_id in contact.get("__managed_custom_field_ids", [])
            if str(field_id).strip()
        }
        contact_clearable_ids = set(clearable_ids) | set(contact_managed_custom_field_ids)
        fields_to_compare_set = {
            str(field_id).strip() for field_id in import_custom.keys() if str(field_id).strip()
        }
        if contact_clearable_ids:
            fields_to_compare_set.update(
                {
                    str(field_id).strip()
                    for field_id in existing_custom.keys()
                    if str(field_id).strip() and str(field_id).strip() in contact_clearable_ids
                }
            )
        requires_custom_compare_for_clear = bool(contact_clearable_ids.intersection(set(existing_custom.keys())))
        has_custom_fields_to_compare = bool(fields_to_compare_set)
        should_compare_custom = (
            custom_fields_compare_enabled
            and has_custom_fields_to_compare
            and (not changed_fields or requires_custom_compare_for_clear)
        )

        if should_compare_custom:
            fields_to_compare = sorted(fields_to_compare_set)
            removed_clearable_fields: list[str] = []
            removed_nonclearable_fields: list[str] = []
            for field_id in fields_to_compare:
                import_value = import_custom.get(field_id, "")
                existing_value = existing_custom.get(field_id, "")
                split_scalar_values = isinstance(import_value, (list, dict)) or isinstance(existing_value, (list, dict))
                import_norm = normalize_custom_value_for_diff(
                    import_value,
                    separators=array_value_split_separators,
                    split_scalar_values=split_scalar_values,
                )
                existing_norm = normalize_custom_value_for_diff(
                    existing_value,
                    separators=array_value_split_separators,
                    split_scalar_values=split_scalar_values,
                )
                if import_norm != existing_norm:
                    field_name = f"customfield:{field_id}"
                    changed_fields.append(field_name)
                    field_diffs_by_field[field_name] = {
                        "field": field_name,
                        "before": existing_value,
                        "after": import_value,
                    }
                    if existing_norm and not import_norm:
                        if field_id in contact_clearable_ids:
                            removed_clearable_fields.append(field_id)
                        else:
                            removed_nonclearable_fields.append(field_id)
            if removed_clearable_fields:
                removed_clearable_custom_fields_by_email[email_key] = sorted(set(removed_clearable_fields))
                existing_contact_id = str(existing_contact.get("id", "")).strip()
                for field_id in sorted(set(removed_clearable_fields)):
                    clear_operations.append(
                        {
                            "email": email,
                            "email_key": email_key,
                            "contact_id": existing_contact_id,
                            "field_id": field_id,
                        }
                    )
            if removed_nonclearable_fields:
                removed_nonclearable_custom_fields_by_email[email_key] = sorted(set(removed_nonclearable_fields))

        if changed_fields:
            changed_fields_unique = sorted(set(changed_fields))
            updated_contacts.append(contact)
            contacts_to_send.append(contact)
            updated_details.append(
                {
                    "email": email,
                    "changed_fields": changed_fields_unique,
                    "field_diffs": [
                        field_diffs_by_field[field_name]
                        for field_name in changed_fields_unique
                        if field_name in field_diffs_by_field
                    ],
                }
            )
        else:
            unchanged_contacts.append(contact)
            unchanged_emails.append(email)

    return {
        "existing_total": len(existing_by_email),
        "new_contacts": new_contacts,
        "updated_contacts": updated_contacts,
        "unchanged_contacts": unchanged_contacts,
        "contacts_to_send": contacts_to_send,
        "updated_details": updated_details,
        "unchanged_emails": unchanged_emails,
        "custom_fields_compare_enabled": custom_fields_compare_enabled,
        "existing_contacts_with_custom_fields": existing_contacts_with_custom_fields,
        "existing_contacts_with_customfields_key": existing_contacts_with_customfields_key,
        "matched_existing_contacts": matched_existing_contacts,
        "removed_clearable_custom_fields_by_email": removed_clearable_custom_fields_by_email,
        "removed_clearable_custom_fields_total": int(
            sum(len(x) for x in removed_clearable_custom_fields_by_email.values())
        ),
        "removed_nonclearable_custom_fields_by_email": removed_nonclearable_custom_fields_by_email,
        "removed_nonclearable_custom_fields_total": int(
            sum(len(x) for x in removed_nonclearable_custom_fields_by_email.values())
        ),
        "clear_operations": clear_operations,
    }


def build_diff_preview_rows(api_diff_summary: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_emails: set[str] = set()

    def _append_row(status: str, email: str, changed_fields: list[str] | None = None) -> None:
        email_clean = normalize_scalar_for_diff(email)
        email_key = normalize_email_key(email_clean)
        if not email_key or email_key in seen_emails:
            return
        seen_emails.add(email_key)
        changed = [str(x).strip() for x in (changed_fields or []) if str(x).strip()]
        rows.append(
            {
                "status": status,
                "email": email_clean,
                "changed_fields_count": len(changed),
                "changed_fields": ", ".join(changed),
            }
        )

    for contact in api_diff_summary.get("new_contacts", []):
        if isinstance(contact, dict):
            _append_row("new", contact.get("emailaddress", ""))

    for detail in api_diff_summary.get("updated_details", []):
        if isinstance(detail, dict):
            _append_row("updated", detail.get("email", ""), detail.get("changed_fields", []))

    for email in api_diff_summary.get("unchanged_emails", []):
        _append_row("unchanged", email)

    status_rank = {"new": 0, "updated": 1, "unchanged": 2}
    rows.sort(key=lambda row: (status_rank.get(str(row.get("status", "")), 9), str(row.get("email", "")).casefold()))
    return rows[: max(0, int(limit))]


def stringify_diff_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        items = [stringify_diff_value(x) for x in value]
        items = [x for x in items if x]
        return ", ".join(items)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    return str(value).strip()


def build_diff_preview_detail_map(api_diff_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for detail in api_diff_summary.get("updated_details", []):
        if not isinstance(detail, dict):
            continue
        email = normalize_scalar_for_diff(detail.get("email", ""))
        email_key = normalize_email_key(email)
        if not email_key:
            continue
        field_diffs = detail.get("field_diffs", [])
        field_rows: list[dict[str, str]] = []
        if isinstance(field_diffs, list):
            for item in field_diffs:
                if not isinstance(item, dict):
                    continue
                field_name = str(item.get("field", "")).strip()
                if not field_name:
                    continue
                field_rows.append(
                    {
                        "field": field_name,
                        "before": stringify_diff_value(item.get("before", "")),
                        "after": stringify_diff_value(item.get("after", "")),
                    }
                )
        if not field_rows:
            for field_name in detail.get("changed_fields", []):
                field_name_clean = str(field_name).strip()
                if not field_name_clean:
                    continue
                field_rows.append(
                    {
                        "field": field_name_clean,
                        "before": "(nezjištěno)",
                        "after": "(nezjištěno)",
                    }
                )
        out[email_key] = {
            "email": email,
            "field_diffs": field_rows,
        }
    return out


profiles_index = load_profiles_index(PROFILES_INDEX_PATH)
profile_items = list_profiles(profiles_index)
profile_id_to_name = {item.id: item.name for item in profile_items}
active_profile_id = get_active_profile_id(profiles_index)
active_profile_name = profile_id_to_name.get(active_profile_id, active_profile_id)

ACTIVE_PROFILE_DIR = profile_dir(PROFILES_ROOT_PATH, active_profile_id)
PROFILE_SETTINGS_PATH = profile_settings_path(PROFILES_ROOT_PATH, active_profile_id)
API_LIST_FAVORITES_PATH = profile_favorites_path(PROFILES_ROOT_PATH, active_profile_id)
PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH = profile_allowlist_path(PROFILES_ROOT_PATH, active_profile_id)
active_profile_payload = load_profile_payload(PROFILE_SETTINGS_PATH)
profile_settings_saved = (
    active_profile_payload.get("settings", {})
    if isinstance(active_profile_payload.get("settings", {}), dict)
    else {}
)
profile_ui_saved = profile_settings_saved.get("ui", {}) if isinstance(profile_settings_saved.get("ui", {}), dict) else {}
profile_api_saved = profile_settings_saved.get("api", {}) if isinstance(profile_settings_saved.get("api", {}), dict) else {}
profile_safety_saved = (
    profile_settings_saved.get("safety", {})
    if isinstance(profile_settings_saved.get("safety", {}), dict)
    else {}
)

if active_profile_id == "plussystem":
    if not API_LIST_FAVORITES_PATH.exists() and LEGACY_API_LIST_FAVORITES_PATH.exists():
        API_LIST_FAVORITES_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_API_LIST_FAVORITES_PATH, API_LIST_FAVORITES_PATH)
    if (
        not PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH.exists()
        and LEGACY_PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH.exists()
    ):
        PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH, PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH)

if st.session_state.get("_runtime_active_profile_id", "") != active_profile_id:
    for key in [
        "do_split_emails",
        "do_split_names",
        "do_bucket_country",
        "output_encoding",
        "dedup_label",
        "use_cached_schema",
        "use_api_schema",
        "refresh_api_schema_on_generate",
        "use_api_cache_on_error",
        "history_filter_active_profile",
        "execution_mode_label",
        "api_canary_size_main",
        "api_batch_size_main",
        "safe_import_limit_main",
        "full_import_limit_main",
        "list_status_main",
        "strict_custom_fields_main",
        "use_profile_system_field_map_main",
        "profile_system_field_map_yaml_main",
        "use_profile_exclude_columns_main",
        "profile_exclude_columns_text_main",
        "staging_tag_input",
        "diff_preflight_enabled_main",
        "diff_send_only_changes_main",
        "diff_fallback_send_all_on_error_main",
        "skip_blacklisted_contacts_main",
        "clear_removed_program_custom_fields_main",
        "diff_page_limit_main",
        "diff_max_pages_main",
        "diff_target_email_batch_size_main",
        "api_read_parallel_workers_main",
        "auto_create_unknown_program_fields_main",
        "auto_add_created_program_fields_to_allowlist_main",
        "profile_lock_critical_options_main",
        "api_list_favorite_ids",
        "program_custom_fields_allowlist_ids",
        "program_custom_fields_catalog",
        "program_custom_fields_catalog_meta",
        "profile_selected_preset_id",
        "profile_selected_preset_id_pending",
        "profile_new_preset_name",
        "staging_list_manual",
        "staging_list_select",
        "api_bucket_select_cz_sk_main",
        "api_bucket_select_de_at_ch_main",
        "api_bucket_select_en_main",
        "api_bucket_favorite_list_ids_by_bucket",
        "api_contact_lists_cache",
        "api_contact_lists_cache_meta",
        "diff_preview_detail_email_select",
        "diff_preview_detail_email_select_fallback",
        "diff_preview_editor",
    ]:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if key.startswith("program_custom_fields_allowlist_checkbox_"):
            st.session_state.pop(key, None)
        if key.startswith("program_custom_fields_allowlist_filter_"):
            st.session_state.pop(key, None)
    st.session_state["_runtime_active_profile_id"] = active_profile_id

pending_profile_preset_values = st.session_state.pop("pending_profile_preset_values", None)
if isinstance(pending_profile_preset_values, dict):
    for key, value in pending_profile_preset_values.items():
        key_str = str(key)
        if key_str == "program_custom_fields_allowlist_ids":
            normalized_ids = sorted(
                {str(x).strip() for x in (value if isinstance(value, list) else []) if str(x).strip()}
            )
            st.session_state[key_str] = normalized_ids
            st.session_state["program_custom_fields_allowlist_checkbox_seed"] = (
                int(st.session_state.get("program_custom_fields_allowlist_checkbox_seed", 0)) + 1
            )
        elif key_str == "api_list_favorite_ids":
            st.session_state[key_str] = sorted(
                normalize_api_favorite_list_ids(value if isinstance(value, list) else [])
            )
        elif key_str == "api_bucket_favorite_list_ids_by_bucket":
            st.session_state[key_str] = normalize_api_bucket_favorite_ids_map(value)
        else:
            st.session_state[key_str] = value
    st.session_state["pending_profile_preset_applied_notice"] = True

profile_presets_sidebar = [
    x for x in load_profile_presets(PROFILE_SETTINGS_PATH) if isinstance(x, dict) and str(x.get("id", "")).strip()
]
profile_preset_name_by_id_sidebar = {
    str(x.get("id", "")).strip(): str(x.get("name", "")).strip() or str(x.get("id", "")).strip()
    for x in profile_presets_sidebar
}
preset_options_sidebar = ["(žádný)"] + [str(x.get("id", "")).strip() for x in profile_presets_sidebar]
pending_selected_preset_for_sidebar = str(
    st.session_state.get("profile_selected_preset_id_pending", "")
).strip()
selected_preset_for_sidebar = str(
    st.session_state.get(
        "profile_selected_preset_id",
        str(profile_ui_saved.get("selected_preset_id", "(žádný)")).strip() or "(žádný)",
    )
).strip() or "(žádný)"
if pending_selected_preset_for_sidebar and pending_selected_preset_for_sidebar in preset_options_sidebar:
    selected_preset_for_sidebar = pending_selected_preset_for_sidebar
if selected_preset_for_sidebar not in preset_options_sidebar:
    selected_preset_for_sidebar = "(žádný)"
if selected_preset_for_sidebar == "(žádný)":
    st.sidebar.markdown("**Aktivní preset:** `(žádný)`")
else:
    st.sidebar.markdown(
        "**Aktivní preset:** "
        f"`{profile_preset_name_by_id_sidebar.get(selected_preset_for_sidebar, selected_preset_for_sidebar)}`"
    )
st.sidebar.markdown(
    f"**Aktivní profil:** `{active_profile_name}`"
)

profile_options_sidebar = [item.id for item in profile_items]
if "quick_active_profile_selectbox" not in st.session_state or str(
    st.session_state.get("quick_active_profile_selectbox", "")
).strip() not in profile_options_sidebar:
    st.session_state["quick_active_profile_selectbox"] = active_profile_id
if "quick_profile_selected_preset_id" not in st.session_state or str(
    st.session_state.get("quick_profile_selected_preset_id", "")
).strip() not in preset_options_sidebar:
    st.session_state["quick_profile_selected_preset_id"] = selected_preset_for_sidebar

st.sidebar.caption("Rychlá změna bez rozkliknutí konfigurace")
quick_profile_selected = st.sidebar.selectbox(
    "Profil",
    options=profile_options_sidebar,
    index=(
        profile_options_sidebar.index(st.session_state.get("quick_active_profile_selectbox", active_profile_id))
        if profile_options_sidebar and st.session_state.get("quick_active_profile_selectbox", active_profile_id) in profile_options_sidebar
        else 0
    ),
    format_func=lambda profile_id: profile_id_to_name.get(profile_id, profile_id),
    key="quick_active_profile_selectbox",
)
if quick_profile_selected != active_profile_id:
    profiles_index = set_active_profile(profiles_index, quick_profile_selected)
    save_profiles_index(PROFILES_INDEX_PATH, profiles_index)
    st.rerun()

quick_preset_selected = st.sidebar.selectbox(
    "Preset",
    options=preset_options_sidebar,
    format_func=lambda preset_id: (
        "(žádný)"
        if preset_id == "(žádný)"
        else profile_preset_name_by_id_sidebar.get(preset_id, preset_id)
    ),
    key="quick_profile_selected_preset_id",
)
if quick_preset_selected != selected_preset_for_sidebar:
    st.session_state["profile_selected_preset_id_pending"] = quick_preset_selected
    st.rerun()

if st.sidebar.button(
    "Aplikovat preset",
    key="quick_apply_profile_preset_btn",
    disabled=quick_preset_selected == "(žádný)",
    use_container_width=True,
):
    quick_selected_preset = next(
        (
            item
            for item in profile_presets_sidebar
            if str(item.get("id", "")).strip() == str(quick_preset_selected).strip()
        ),
        None,
    )
    if quick_selected_preset is None:
        st.sidebar.error("Vybraný preset nebyl nalezen.")
    else:
        preset_values = quick_selected_preset.get("values", {})
        if isinstance(preset_values, dict):
            st.session_state["pending_profile_preset_values"] = {
                str(key): value for key, value in preset_values.items()
            }
            st.session_state["profile_selected_preset_id_pending"] = str(quick_preset_selected).strip()
            st.rerun()
        else:
            st.sidebar.error("Preset nemá validní `values`.")

with st.sidebar.expander("Profil importu", expanded=False):
    profile_options = [item.id for item in profile_items]
    if active_profile_id not in profile_options and profile_options:
        active_profile_id = profile_options[0]
    active_index = profile_options.index(active_profile_id) if active_profile_id in profile_options else 0

    selected_profile_id = st.selectbox(
        "Aktivní profil",
        options=profile_options,
        index=active_index if profile_options else 0,
        format_func=lambda profile_id: f"{profile_id_to_name.get(profile_id, profile_id)} ({profile_id})",
        key="active_profile_selectbox",
    )
    if selected_profile_id != active_profile_id:
        profiles_index = set_active_profile(profiles_index, selected_profile_id)
        save_profiles_index(PROFILES_INDEX_PATH, profiles_index)
        st.rerun()

    create_name = st.text_input("Nový profil", value="", key="create_profile_name")
    if st.button("Vytvořit profil", key="create_profile_btn"):
        profiles_index, created_profile_id = create_profile(profiles_index, create_name)
        save_profiles_index(PROFILES_INDEX_PATH, profiles_index)
        (PROFILES_ROOT_PATH / created_profile_id).mkdir(parents=True, exist_ok=True)
        st.success(f"Profil vytvořen: {created_profile_id}")
        st.rerun()

    duplicate_name = st.text_input("Název kopie profilu", value=f"{active_profile_name} kopie", key="duplicate_profile_name")
    if st.button("Duplikovat aktivní profil", key="duplicate_profile_btn"):
        source_profile_id = get_active_profile_id(profiles_index)
        profiles_index, duplicated_profile_id = duplicate_profile(
            profiles_index,
            source_profile_id=source_profile_id,
            new_name=duplicate_name,
        )
        duplicate_profile_files(PROFILES_ROOT_PATH, source_profile_id, duplicated_profile_id)
        save_profiles_index(PROFILES_INDEX_PATH, profiles_index)
        st.success(f"Profil duplikován: {duplicated_profile_id}")
        st.rerun()

    rename_name = st.text_input("Přejmenovat aktivní profil", value=active_profile_name, key="rename_profile_name")
    if st.button("Přejmenovat profil", key="rename_profile_btn"):
        target_profile_id = get_active_profile_id(profiles_index)
        profiles_index = rename_profile(profiles_index, target_profile_id, rename_name)
        save_profiles_index(PROFILES_INDEX_PATH, profiles_index)
        st.success("Profil přejmenován.")
        st.rerun()

    can_delete_profile = len(profile_items) > 1
    delete_confirm = st.checkbox(
        "Potvrzuji smazání aktivního profilu",
        value=False,
        key="delete_profile_confirm",
        disabled=not can_delete_profile,
    )
    if st.button("Smazat aktivní profil", key="delete_profile_btn", disabled=not (can_delete_profile and delete_confirm)):
        target_profile_id = get_active_profile_id(profiles_index)
        profiles_index, _ = delete_profile(profiles_index, target_profile_id)
        save_profiles_index(PROFILES_INDEX_PATH, profiles_index)
        delete_profile_files(PROFILES_ROOT_PATH, target_profile_id)
        st.success(f"Profil smazán: {target_profile_id}")
        st.rerun()

    st.checkbox(
        "Uzamknout kritické volby profilu",
        value=bool(profile_safety_saved.get("lock_critical_options", False)),
        key="profile_lock_critical_options_main",
        help="Když je zapnuto, kritické volby importu se jen zobrazí a nejdou měnit.",
    )

    profile_presets = [
        x for x in load_profile_presets(PROFILE_SETTINGS_PATH) if isinstance(x, dict) and str(x.get("id", "")).strip()
    ]
    preset_options = ["(žádný)"] + [str(x.get("id", "")).strip() for x in profile_presets]
    preset_id_to_name = {
        str(x.get("id", "")).strip(): str(x.get("name", "")).strip() or str(x.get("id", "")).strip()
        for x in profile_presets
    }
    pending_selected_preset_id = str(st.session_state.pop("profile_selected_preset_id_pending", "")).strip()
    if pending_selected_preset_id:
        st.session_state["profile_selected_preset_id"] = (
            pending_selected_preset_id if pending_selected_preset_id in preset_options else "(žádný)"
        )
    saved_selected_preset_id = str(profile_ui_saved.get("selected_preset_id", "(žádný)")).strip() or "(žádný)"
    if st.session_state.get("profile_selected_preset_id") not in preset_options:
        st.session_state["profile_selected_preset_id"] = (
            saved_selected_preset_id if saved_selected_preset_id in preset_options else "(žádný)"
        )
    selected_preset_id = st.selectbox(
        "Preset profilu",
        options=preset_options,
        key="profile_selected_preset_id",
        format_func=lambda preset_id: (
            "(žádný)"
            if preset_id == "(žádný)"
            else f"{preset_id_to_name.get(preset_id, preset_id)} ({preset_id})"
        ),
    )
    selected_preset = next((x for x in profile_presets if str(x.get("id", "")).strip() == selected_preset_id), None)
    tracked_keys = [
        "do_split_emails",
        "do_split_names",
        "do_bucket_country",
        "output_encoding",
        "dedup_label",
        "use_cached_schema",
        "use_api_schema",
        "refresh_api_schema_on_generate",
        "use_api_cache_on_error",
        "execution_mode_label",
        "strict_custom_fields_main",
        "list_status_main",
        "staging_list_manual",
        "api_bucket_select_cz_sk_main",
        "api_bucket_select_de_at_ch_main",
        "api_bucket_select_en_main",
        "staging_tag_input",
        "api_canary_size_main",
        "api_batch_size_main",
        "safe_import_limit_main",
        "full_import_limit_main",
        "diff_preflight_enabled_main",
        "diff_send_only_changes_main",
        "diff_fallback_send_all_on_error_main",
        "skip_blacklisted_contacts_main",
        "clear_removed_program_custom_fields_main",
        "clear_allowed_name_prefixes_main",
        "diff_page_limit_main",
        "diff_max_pages_main",
        "diff_target_email_batch_size_main",
        "api_read_parallel_workers_main",
        "auto_create_unknown_program_fields_main",
        "auto_add_created_program_fields_to_allowlist_main",
        "profile_lock_critical_options_main",
        "use_profile_system_field_map_main",
        "profile_system_field_map_yaml_main",
        "use_profile_exclude_columns_main",
        "profile_exclude_columns_text_main",
        "api_list_favorite_ids",
        "api_bucket_favorite_list_ids_by_bucket",
        "program_custom_fields_allowlist_ids",
    ]

    def collect_current_preset_values() -> dict[str, Any]:
        profile_system_map_saved = (
            profile_api_saved.get("system_field_map", {})
            if isinstance(profile_api_saved.get("system_field_map", {}), dict)
            else {}
        )
        profile_system_map_clean = {
            str(k).strip(): str(v).strip()
            for k, v in profile_system_map_saved.items()
            if str(k).strip() and str(v).strip()
        }
        if not profile_system_map_clean:
            profile_system_map_cfg_fallback = CFG.get("smartemailing", {}).get("api", {}).get("system_field_map", {})
            if isinstance(profile_system_map_cfg_fallback, dict):
                profile_system_map_clean = {
                    str(k).strip(): str(v).strip()
                    for k, v in profile_system_map_cfg_fallback.items()
                    if str(k).strip() and str(v).strip()
                }
        profile_system_map_yaml_fallback = yaml.safe_dump(
            profile_system_map_clean,
            allow_unicode=True,
            sort_keys=False,
        )

        profile_exclude_columns_saved = [
            str(col).strip()
            for col in profile_api_saved.get("exclude_columns_from_api_import", [])
            if str(col).strip()
        ]
        profile_exclude_columns_fallback = "\n".join(profile_exclude_columns_saved)

        fallback_values: dict[str, Any] = {
            "use_profile_system_field_map_main": bool(profile_api_saved.get("use_profile_system_field_map", False)),
            "profile_system_field_map_yaml_main": profile_system_map_yaml_fallback,
            "use_profile_exclude_columns_main": bool(profile_api_saved.get("use_profile_exclude_columns", False)),
            "profile_exclude_columns_text_main": profile_exclude_columns_fallback,
            "staging_list_manual": str(profile_api_saved.get("staging_list_value", "")).strip(),
        }

        values: dict[str, Any] = {}
        for tracked_key in tracked_keys:
            if tracked_key in st.session_state:
                values[tracked_key] = st.session_state.get(tracked_key)
            elif tracked_key in fallback_values:
                values[tracked_key] = fallback_values.get(tracked_key)
        return values

    if st.session_state.pop("pending_profile_preset_applied_notice", False):
        st.success("Preset aplikován.")
    preset_action_col_apply, preset_action_col_update = st.columns(2)
    with preset_action_col_apply:
        if st.button("Aplikovat preset", key="apply_profile_preset_btn", disabled=selected_preset is None):
            preset_values = selected_preset.get("values", {}) if isinstance(selected_preset, dict) else {}
            if isinstance(preset_values, dict):
                st.session_state["pending_profile_preset_values"] = {
                    str(key): value for key, value in preset_values.items()
                }
            st.rerun()
    with preset_action_col_update:
        if st.button(
            "Aktualizovat vybraný preset",
            key="update_profile_preset_btn",
            disabled=selected_preset is None,
            help="Přepíše vybraný preset aktuálním stavem formuláře.",
        ):
            selected_idx = next(
                (
                    idx
                    for idx, item in enumerate(profile_presets)
                    if str(item.get("id", "")).strip() == str(selected_preset_id).strip()
                ),
                None,
            )
            if selected_idx is None:
                st.error("Není vybraný preset k aktualizaci.")
            else:
                profile_presets[selected_idx]["updated_at"] = datetime.now(timezone.utc).isoformat()
                profile_presets[selected_idx]["values"] = collect_current_preset_values()
                save_profile_presets(
                    PROFILE_SETTINGS_PATH,
                    profile_presets,
                    profile_id=active_profile_id,
                    profile_name=active_profile_name,
                )
                st.session_state["profile_selected_preset_id_pending"] = str(
                    profile_presets[selected_idx].get("id", "")
                ).strip()
                st.success("Vybraný preset aktualizován.")
                st.rerun()

    new_preset_name = st.text_input("Uložit aktuální jako preset", value="", key="profile_new_preset_name")
    if st.button("Uložit preset", key="save_profile_preset_btn"):
        preset_name_clean = str(new_preset_name).strip()
        if not preset_name_clean:
            st.error("Vyplň název presetu.")
        else:
            preset_values = collect_current_preset_values()
            existing_idx = next(
                (
                    idx
                    for idx, item in enumerate(profile_presets)
                    if str(item.get("name", "")).strip().casefold() == preset_name_clean.casefold()
                ),
                None,
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            if existing_idx is None:
                used_ids = {
                    str(item.get("id", "")).strip()
                    for item in profile_presets
                    if str(item.get("id", "")).strip()
                }
                base_id = slugify_profile_id(preset_name_clean)
                preset_id = base_id
                suffix = 2
                while preset_id in used_ids:
                    preset_id = f"{base_id}_{suffix}"
                    suffix += 1
                profile_presets.append(
                    {
                        "id": preset_id,
                        "name": preset_name_clean,
                        "created_at": now_iso,
                        "updated_at": now_iso,
                        "values": preset_values,
                    }
                )
                st.session_state["profile_selected_preset_id_pending"] = preset_id
            else:
                profile_presets[existing_idx]["updated_at"] = now_iso
                profile_presets[existing_idx]["values"] = preset_values
                st.session_state["profile_selected_preset_id_pending"] = str(
                    profile_presets[existing_idx].get("id", "")
                ).strip()
            save_profile_presets(
                PROFILE_SETTINGS_PATH,
                profile_presets,
                profile_id=active_profile_id,
                profile_name=active_profile_name,
            )
            st.success("Preset uložen.")
            st.rerun()

    if st.button("Smazat vybraný preset", key="delete_profile_preset_btn", disabled=selected_preset is None):
        profile_presets = [
            item
            for item in profile_presets
            if str(item.get("id", "")).strip() != selected_preset_id
        ]
        save_profile_presets(
            PROFILE_SETTINGS_PATH,
            profile_presets,
            profile_id=active_profile_id,
            profile_name=active_profile_name,
        )
        st.session_state["profile_selected_preset_id_pending"] = "(žádný)"
        st.success("Preset smazán.")
        st.rerun()

    if st.button("Uložit nastavení profilu", key="manual_profile_settings_save_btn"):
        st.session_state["manual_profile_save_requested"] = True

    export_profile_payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "id": active_profile_id,
            "name": active_profile_name,
        },
        "payload": load_profile_payload(PROFILE_SETTINGS_PATH),
        "allowlist_custom_field_ids": sorted(
            {
                str(x).strip()
                for x in st.session_state.get("program_custom_fields_allowlist_ids", [])
                if str(x).strip()
            }
        ),
        "favorite_list_ids": sorted(
            {
                str(x).strip()
                for x in st.session_state.get("api_list_favorite_ids", [])
                if str(x).strip()
            }
        ),
    }
    export_profile_yaml = yaml.safe_dump(export_profile_payload, allow_unicode=True, sort_keys=False)
    st.download_button(
        "Exportovat profil (YAML)",
        data=export_profile_yaml.encode("utf-8"),
        file_name=f"profile_{active_profile_id}.yaml",
        mime="text/yaml",
        key="export_profile_yaml_btn",
    )

    import_profile_file = st.file_uploader(
        "Import profilu z YAML (do aktivního profilu)",
        type=["yaml", "yml"],
        key="import_profile_yaml_file",
    )
    if st.button("Importovat profil YAML", key="import_profile_yaml_btn", disabled=import_profile_file is None):
        if import_profile_file is None:
            st.error("Vyber nejdřív YAML soubor pro import.")
        else:
            try:
                imported_data = yaml.safe_load(import_profile_file.getvalue().decode("utf-8")) or {}
                if not isinstance(imported_data, dict):
                    raise ValueError("Importovaný YAML musí být objekt (mapa).")
                imported_payload = imported_data.get("payload", imported_data)
                if not isinstance(imported_payload, dict):
                    raise ValueError("Klíč `payload` musí být objekt.")
                imported_settings = imported_payload.get("settings", {})
                imported_presets = imported_payload.get("presets", [])
                if not isinstance(imported_settings, dict):
                    imported_settings = {}
                if not isinstance(imported_presets, list):
                    imported_presets = []

                save_profile_settings(
                    PROFILE_SETTINGS_PATH,
                    settings=imported_settings,
                    profile_id=active_profile_id,
                    profile_name=active_profile_name,
                    presets=imported_presets,
                )

                imported_allowlist = imported_data.get("allowlist_custom_field_ids", [])
                if isinstance(imported_allowlist, list):
                    save_program_custom_fields_allowlist(
                        PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH,
                        {str(x).strip() for x in imported_allowlist if str(x).strip()},
                    )

                imported_favorites = imported_data.get("favorite_list_ids", [])
                if isinstance(imported_favorites, list):
                    save_api_list_favorites(
                        API_LIST_FAVORITES_PATH,
                        {str(x).strip() for x in imported_favorites if str(x).strip()},
                    )

                st.success("Profil byl naimportován do aktivního profilu. Obnovuji UI.")
                st.session_state["_runtime_active_profile_id"] = ""
                st.rerun()
            except Exception as exc:
                st.error(f"Nepodařilo se importovat profil YAML: {exc}")

    st.caption(f"Aktivní profil: `{active_profile_name}` (`{active_profile_id}`)")

profile_lock_critical_options = bool(
    st.session_state.get(
        "profile_lock_critical_options_main",
        bool(profile_safety_saved.get("lock_critical_options", False)),
    )
)

st.sidebar.header("Nastavení")
do_split_emails = st.sidebar.checkbox(
    "Rozdělit více emailů na více řádků",
    value=bool(profile_ui_saved.get("do_split_emails", True)),
    key="do_split_emails",
)
do_split_names = st.sidebar.checkbox(
    "Rozdělit jména (tituly/jméno/příjmení)",
    value=bool(profile_ui_saved.get("do_split_names", True)),
    key="do_split_names",
)
do_bucket_country = st.sidebar.checkbox(
    "Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)",
    value=bool(profile_ui_saved.get("do_bucket_country", True)),
    key="do_bucket_country",
)
if "output_encoding" not in st.session_state:
    st.session_state["output_encoding"] = str(profile_ui_saved.get("output_encoding", "cp1250")).strip() or "cp1250"
output_encoding = st.sidebar.selectbox(
    "Kódování výstupních CSV",
    options=["cp1250", "utf-8", "utf-8-sig"],
    key="output_encoding",
)
dedup_options = ["Bez deduplikace", "Ponechat první výskyt", "Ponechat poslední výskyt"]
default_dedup_label = str(profile_ui_saved.get("dedup_label", "Ponechat poslední výskyt")).strip()
if default_dedup_label not in dedup_options:
    default_dedup_label = "Ponechat poslední výskyt"
dedup_label = st.sidebar.selectbox(
    "Deduplikace emailů ve výstupu",
    options=dedup_options,
    index=dedup_options.index(default_dedup_label),
    key="dedup_label",
)
dedup_keep = {
    "Bez deduplikace": "none",
    "Ponechat první výskyt": "first",
    "Ponechat poslední výskyt": "last",
}[dedup_label]

saved_api_credentials = load_saved_api_credentials(API_CREDENTIALS_PATH)
remember_api_credentials = st.sidebar.checkbox(
    "Pamatovat SE API údaje lokálně",
    value=bool(saved_api_credentials.get("username", "") or saved_api_credentials.get("api_key", "")),
)
st.sidebar.caption(f"Lokální soubor: `{API_CREDENTIALS_PATH}` ")
if st.sidebar.button("Smazat uložené SE API údaje"):
    try:
        clear_api_credentials(API_CREDENTIALS_PATH)
        st.sidebar.success("Uložené SE API údaje byly smazány.")
        st.rerun()
    except Exception as exc:
        st.sidebar.error(f"Nepodařilo se smazat uložené údaje: {exc}")

top_flow_col_left, top_flow_col_right = st.columns([1.55, 1], gap="large")
with top_flow_col_left:
    with card_container():
        st.markdown("### 1) Nahraj zdrojové CSV soubory (1 nebo více)")
        st.caption(
            f"Aktivní profil: **{active_profile_name}** (`{active_profile_id}`) | "
            f"složka: `{ACTIVE_PROFILE_DIR}`"
        )
        source_files = st.file_uploader("Zdrojové CSV", type=["csv"], accept_multiple_files=True)

with top_flow_col_right:
    with card_container():
        st.markdown("### 2) Režim běhu")
        execution_mode_options = [
            "API kontrolní běh (jen validace + náhled)",
            "API bezpečný import (staging + testovací dávka)",
            "API plný import (schvalování + testovací dávka)",
            "CSV export (záloha)",
        ]
        execution_mode_by_label = {
            "API kontrolní běh (jen validace + náhled)": "api_dry_run",
            "API bezpečný import (staging + testovací dávka)": "api_safe_import",
            "API plný import (schvalování + testovací dávka)": "api_full_import",
            "CSV export (záloha)": "csv_fallback",
        }
        execution_mode_label_by_code = {v: k for k, v in execution_mode_by_label.items()}
        default_execution_mode_code = str(profile_api_saved.get("execution_mode", "api_safe_import")).strip() or "api_safe_import"
        default_execution_mode_label = execution_mode_label_by_code.get(
            default_execution_mode_code,
            "API bezpečný import (staging + testovací dávka)",
        )
        execution_mode_label = st.radio(
            "Vyber akci po transformaci dat",
            options=execution_mode_options,
            index=execution_mode_options.index(default_execution_mode_label),
            key="execution_mode_label",
            disabled=profile_lock_critical_options,
        )
execution_mode = execution_mode_by_label[execution_mode_label]

api_step_container = card_container()
run_step_container = card_container()

with st.expander("Nastavení schématu (volitelné)", expanded=False):
    st.markdown("### Nastavení schématu (volitelné)")
    st.caption("Tato sekce není součást běžného flow. Používá se hlavně jako fallback při výpadku API.")
    st.markdown("#### Schéma sloupců (CSV záloha)")
    cached_schema, cached_schema_meta = load_cached_schema(SCHEMA_CACHE_PATH)
    use_cached_schema = st.checkbox(
        "Použít uložené schéma z CSV zálohy (bez nahrávání exportu)",
        value=bool(profile_ui_saved.get("use_cached_schema", (cached_schema is not None))),
        disabled=(cached_schema is None),
        key="use_cached_schema",
        help="Použije se jen jako záloha. Pokud je aktivní API schéma, má přednost.",
    )
    export_file = None
    st.markdown("##### Detaily CSV záložního schématu")
    with st.container():
        if cached_schema is None:
            st.caption("Uložené CSV schéma zatím neexistuje.")
        else:
            st.caption(f"Uložené CSV schéma: {len(cached_schema.columns)} sloupců (`{SCHEMA_CACHE_PATH}`)")
            st.caption(
                "Metadata: "
                f"verze={cached_schema_meta.get('version', '?')}, "
                f"uloženo={cached_schema_meta.get('saved_at', '')}, "
                f"zdroj={cached_schema_meta.get('source_file', '')}"
            )
            if st.button("Smazat uložené CSV schéma"):
                try:
                    if SCHEMA_CACHE_PATH.exists():
                        SCHEMA_CACHE_PATH.unlink()
                    st.success("Uložené CSV schéma bylo smazáno.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Nepodařilo se smazat uložené CSV schéma: {exc}")
    
        export_file = st.file_uploader(
            "Volitelně nahraj SmartEmailing export CSV (záložní schéma pro tento běh)",
            type=["csv"],
            accept_multiple_files=False,
        )
    
    csv_schema = None
    if export_file is not None:
        try:
            rr = read_csv_best_effort(export_file.getvalue())
            export_df = clean_columns(rr.df)
            csv_schema = schema_from_export_df(export_df)
            st.success(
                f"Načteno CSV schéma z nahraného souboru: {len(csv_schema.columns)} sloupců "
                f"(oddělovač '{rr.delimiter}', kódování '{rr.encoding}')"
            )
            save_uploaded_schema = st.checkbox("Uložit nahrané CSV schéma jako výchozí pro příště", value=True)
            if save_uploaded_schema:
                try:
                    save_cached_schema(
                        SCHEMA_CACHE_PATH,
                        csv_schema,
                        source_file=export_file.name,
                        source_kind="csv_upload",
                    )
                    st.caption(f"CSV schéma uloženo do `{SCHEMA_CACHE_PATH}`.")
                except Exception as exc:
                    st.error(f"Nepodařilo se uložit CSV schéma: {exc}")
        except Exception as exc:
            st.error(f"Nepodařilo se načíst exportní CSV pro záložní schéma: {exc}")
    
    if csv_schema is None and use_cached_schema and cached_schema is not None:
        csv_schema = cached_schema
        st.success(f"Použito uložené CSV záložní schéma: {len(csv_schema.columns)} sloupců")
    
    schema = csv_schema
    schema_origin = "csv_fallback" if csv_schema is not None else "none"
    
    schema_api_cfg = CFG.get("smartemailing", {}).get("api", {})
    required_min_custom_fields = to_int(schema_api_cfg.get("required_min_custom_fields", 1), 1)
    custom_fields_endpoint_candidates = [
        str(x).strip()
        for x in schema_api_cfg.get("custom_fields_endpoint_candidates", ["/api/v3/customfields", "/api/v3/custom-fields"])
        if str(x).strip()
    ]
    custom_fields_search_endpoint_candidates = [
        str(x).strip()
        for x in schema_api_cfg.get(
            "custom_fields_search_endpoint_candidates",
            [],
        )
        if str(x).strip()
    ]
    
    st.markdown("#### Schéma ze SmartEmailing API (doporučeno)")
    cached_api_schema, cached_api_schema_meta = load_cached_schema(API_SCHEMA_CACHE_PATH)
    use_api_schema = st.checkbox(
        "Načítat schéma přímo ze SmartEmailing API",
        value=bool(profile_ui_saved.get("use_api_schema", True)),
        key="use_api_schema",
    )
    api_username = ""
    api_key = ""
    api_base_url = str(saved_api_credentials.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL
    refresh_api_schema_on_generate = True
    use_api_cache_on_error = True
    api_schema_for_run = None
    api_schema_fetch_attempted = False
    api_schema_fetch_error = ""
    api_schema_from_cache_preview = False
    
    if use_api_schema:
        st.markdown("##### Detaily API schématu")
        with st.container():
            api_username = st.text_input(
                "SmartEmailing uživatelské jméno",
                value=str(saved_api_credentials.get("username", "")).strip(),
                key="schema_api_username",
            )
            api_key = st.text_input(
                "SmartEmailing API klíč",
                value=str(saved_api_credentials.get("api_key", "")).strip(),
                type="password",
                key="schema_api_key",
            )
            api_base_url = st.text_input(
                "API základní URL",
                value=str(saved_api_credentials.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL,
                key="schema_api_base_url",
            )
            save_schema_api_credentials = st.button("Uložit API údaje na disk", key="save_api_credentials_schema")
            refresh_api_schema_on_generate = st.checkbox(
                "Před exportem načíst schéma z API znovu",
                value=bool(profile_ui_saved.get("refresh_api_schema_on_generate", True)),
                key="refresh_api_schema_on_generate",
            )
            use_api_cache_on_error = st.checkbox(
                "Při chybě API použít schéma z API mezipaměti",
                value=bool(profile_ui_saved.get("use_api_cache_on_error", True)),
                key="use_api_cache_on_error",
            )
    
            if save_schema_api_credentials:
                if str(api_username).strip() and str(api_key).strip():
                    try:
                        save_api_credentials(API_CREDENTIALS_PATH, api_username, api_key, api_base_url)
                        st.success(f"API údaje byly uloženy do `{API_CREDENTIALS_PATH}`.")
                    except Exception as exc:
                        st.error(f"Nepodařilo se uložit API údaje: {exc}")
                else:
                    st.error("Pro uložení vyplň uživatelské jméno i API klíč.")
    
            if remember_api_credentials and str(api_username).strip() and str(api_key).strip():
                try:
                    save_api_credentials(API_CREDENTIALS_PATH, api_username, api_key, api_base_url)
                except Exception as exc:
                    st.warning(f"Nepodařilo se uložit API údaje lokálně: {exc}")
    
            if cached_api_schema is None:
                st.caption("Uložená API mezipaměť schématu zatím neexistuje.")
            else:
                st.caption(f"Uložené API schéma: {len(cached_api_schema.columns)} sloupců (`{API_SCHEMA_CACHE_PATH}`)")
                st.caption(
                    "Metadata: "
                    f"verze={cached_api_schema_meta.get('version', '?')}, "
                    f"uloženo={cached_api_schema_meta.get('saved_at', '')}, "
                    f"zdroj={cached_api_schema_meta.get('source_file', '')}, "
                    f"vlastní_pole={cached_api_schema_meta.get('custom_field_count', '?')}"
                )
    
            c1, c2, c3 = st.columns(3)
            with c1:
                test_ping = st.button("Test API (ping)")
            with c2:
                fetch_api_schema_now = st.button("Načíst schéma z API teď")
            with c3:
                clear_api_cache = st.button("Smazat API mezipaměť schématu")
            probe_custom_fields_now = st.button("Diagnostika endpointů vlastních polí")
    
            if clear_api_cache:
                try:
                    if API_SCHEMA_CACHE_PATH.exists():
                        API_SCHEMA_CACHE_PATH.unlink()
                    st.success("API mezipaměť schématu byla smazána.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Nepodařilo se smazat API mezipaměť schématu: {exc}")
    
            if test_ping:
                try:
                    ping_client = SmartEmailingApiClient(
                        SmartEmailingCredentials(
                            username=str(api_username).strip(),
                            api_key=str(api_key).strip(),
                            base_url=str(api_base_url).strip() or DEFAULT_BASE_URL,
                        )
                    )
                    ping_data = ping_client.ping()
                    st.success(f"API ping OK: {ping_data}")
                except Exception as exc:
                    st.error(f"API ping selhal: {exc}")
    
            if fetch_api_schema_now:
                api_schema_fetch_attempted = True
                try:
                    api_schema_for_run, api_meta = fetch_schema_from_api(api_username, api_key, api_base_url)
                    save_cached_schema(
                        API_SCHEMA_CACHE_PATH,
                        api_schema_for_run,
                        source_file="smartemailing_api",
                        source_kind="api_live",
                        extra_meta=api_meta,
                    )
                    st.success(
                        f"Načteno API schéma: {len(api_schema_for_run.columns)} sloupců "
                        f"(vlastní pole: {api_meta.get('custom_field_count', 0)})"
                    )
                except Exception as exc:
                    api_schema_fetch_error = str(exc)
                    st.error(f"Nepodařilo se načíst schéma z API: {exc}")
    
            if probe_custom_fields_now:
                try:
                    probe_client = SmartEmailingApiClient(
                        SmartEmailingCredentials(
                            username=str(api_username).strip(),
                            api_key=str(api_key).strip(),
                            base_url=str(api_base_url).strip() or DEFAULT_BASE_URL,
                        )
                    )
                    probe_rows = probe_client.probe_custom_fields_endpoints(
                        endpoint_candidates=custom_fields_endpoint_candidates,
                        search_endpoint_candidates=custom_fields_search_endpoint_candidates,
                    )
                    st.markdown("#### Diagnostika endpointů vlastních polí")
                    st.dataframe(to_streamlit_safe_dataframe(pd.DataFrame(probe_rows)), use_container_width=True)
                except Exception as exc:
                    st.error(f"Nepodařilo se provést diagnostiku endpointů vlastních polí: {exc}")
    
            # Pro běžný náhled použij naposledy načtené API schéma (pokud je validní).
            # Při spuštění se stejně načte čerstvé, pokud je zapnuté refresh_api_schema_on_generate.
            if api_schema_for_run is None and cached_api_schema is not None:
                cached_custom_fields = to_int(cached_api_schema_meta.get("custom_field_count", 0), 0)
                if cached_custom_fields >= required_min_custom_fields:
                    api_schema_for_run = cached_api_schema
                    api_schema_from_cache_preview = True
                    st.caption(f"Použito uložené API schéma z mezipaměti: {len(api_schema_for_run.columns)} sloupců")
                elif api_schema_fetch_attempted:
                    st.warning(
                        "API mezipaměť má příliš málo vlastních polí "
                        f"({cached_custom_fields} < {required_min_custom_fields}), ignoruji ji."
                    )
    
    if api_schema_for_run is not None:
        schema = api_schema_for_run
        schema_origin = "smartemailing_api_cache" if api_schema_from_cache_preview else "smartemailing_api"
    elif use_api_schema and schema is not None:
        if api_schema_fetch_attempted and api_schema_fetch_error:
            st.warning(
                "Schéma z API se nepodařilo načíst, pokračuje se s CSV záložním schématem. "
                f"Detail: {api_schema_fetch_error}"
            )
        elif not refresh_api_schema_on_generate:
            st.info(
                "Aktivní je CSV záložní schéma. Pro přepnutí na API schéma klikni na "
                "`Načíst schéma z API teď`."
            )
    
    if schema is not None:
        schema_origin_label = {
            "csv_fallback": "CSV záloha",
            "smartemailing_api": "SmartEmailing API",
            "smartemailing_api_cache": "SmartEmailing API (mezipaměť)",
            "none": "bez schématu",
        }.get(schema_origin, str(schema_origin))
        st.caption(f"Aktivní schéma: {len(schema.columns)} sloupců (`{schema_origin_label}`)")
    else:
        st.warning("Není načtené žádné schéma. Nahraj export CSV nebo zapni načítání schématu z API.")
    
api_cfg = CFG.get("smartemailing", {}).get("api", {})
api_mode_enabled = execution_mode != "csv_fallback"
import_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("import_endpoint_candidates", ["/api/v3/import"])
    if str(x).strip()
]
custom_fields_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("custom_fields_endpoint_candidates", ["/api/v3/customfields", "/api/v3/custom-fields"])
    if str(x).strip()
]
custom_fields_search_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get(
        "custom_fields_search_endpoint_candidates",
        [],
    )
    if str(x).strip()
]
contact_lists_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("contact_lists_endpoint_candidates", ["/api/v3/contactlists", "/api/v3/contact-lists"])
    if str(x).strip()
]
contact_lists_search_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("contact_lists_search_endpoint_candidates", ["/api/v3/contactlists/search", "/api/v3/contact-lists/search"])
    if str(x).strip()
]
contacts_in_list_endpoint_templates = [
    str(x).strip()
    for x in api_cfg.get(
        "contacts_in_list_endpoint_templates",
        ["/api/v3/contactlists/{list_id}/contacts", "/api/v3/contact-lists/{list_id}/contacts"],
    )
    if str(x).strip()
]
contacts_in_list_search_endpoint_templates = [
    str(x).strip()
    for x in api_cfg.get(
        "contacts_in_list_search_endpoint_templates",
        ["/api/v3/contactlists/{list_id}/contacts/search", "/api/v3/contact-lists/{list_id}/contacts/search"],
    )
    if str(x).strip()
]
contacts_detail_endpoint_templates = [
    str(x).strip()
    for x in api_cfg.get(
        "contacts_detail_endpoint_templates",
        ["/api/v3/contacts/{contact_id}", "/api/v3/contacts/{id}", "/api/v3/contact/{contact_id}", "/api/v3/contact/{id}"],
    )
    if str(x).strip()
]
contacts_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("contacts_endpoint_candidates", ["/api/v3/contacts", "/api/v3/contact"])
    if str(x).strip()
]
contacts_search_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("contacts_search_endpoint_candidates", ["/api/v3/contacts/search", "/api/v3/contact/search"])
    if str(x).strip()
]
# Blacklist lookup must stay strict to avoid false positives on legacy aliases.
# We intentionally use only the documented contacts endpoints.
blacklist_contacts_endpoint_candidates = [
    endpoint
    for endpoint in contacts_endpoint_candidates
    if endpoint.strip().casefold() == "/api/v3/contacts"
]
if not blacklist_contacts_endpoint_candidates:
    blacklist_contacts_endpoint_candidates = ["/api/v3/contacts"]

blacklist_contacts_search_endpoint_candidates = [
    endpoint
    for endpoint in contacts_search_endpoint_candidates
    if endpoint.strip().casefold() == "/api/v3/contacts/search"
]
if not blacklist_contacts_search_endpoint_candidates:
    blacklist_contacts_search_endpoint_candidates = ["/api/v3/contacts/search"]

contact_custom_field_values_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get(
        "contact_custom_field_values_endpoint_candidates",
        [
            "/api/v3/contact-customfield-values",
            "/api/v3/contactcustomfieldvalues",
            "/api/v3/contact-customfields",
            "/api/v3/contactcustomfields",
            "/api/v3/customfield-values",
            "/api/v3/customfieldvalues",
        ],
    )
    if str(x).strip()
]
contact_custom_field_values_search_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get(
        "contact_custom_field_values_search_endpoint_candidates",
        [
            "/api/v3/contact-customfield-values/search",
            "/api/v3/contactcustomfieldvalues/search",
            "/api/v3/contact-customfields/search",
            "/api/v3/contactcustomfields/search",
            "/api/v3/customfield-values/search",
            "/api/v3/customfieldvalues/search",
        ],
    )
    if str(x).strip()
]
strict_custom_fields = bool(profile_api_saved.get("strict_custom_fields", api_cfg.get("strict_custom_fields", True)))
list_status = str(profile_api_saved.get("list_status", api_cfg.get("list_status", "confirmed"))).strip() or "confirmed"
auto_create_unknown_program_fields_default = bool(
    profile_api_saved.get("auto_create_unknown_program_fields", api_cfg.get("auto_create_unknown_program_fields", True))
)
auto_create_program_field_type = str(api_cfg.get("auto_create_program_field_type", "text")).strip() or "text"
custom_field_create_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("custom_field_create_endpoint_candidates", ["/api/v3/customfields", "/api/v3/custom-fields"])
    if str(x).strip()
]
array_custom_field_names = [
    str(x).strip()
    for x in api_cfg.get("array_custom_field_names", [])
    if str(x).strip()
]
array_value_split_separators = [
    str(x).strip()
    for x in api_cfg.get("array_value_split_separators", [",", ";", "|", "/"])
    if str(x).strip()
]
managed_empty_custom_field_name_pattern = str(
    api_cfg.get("managed_empty_custom_field_name_pattern", DEFAULT_MANAGED_EMPTY_CUSTOM_FIELD_NAME_PATTERN)
).strip()
field_map_cfg = CFG.get("smartemailing", {}).get("field_map", {})
api_system_field_map_cfg = api_cfg.get("system_field_map", {})
programs_cfg = CFG.get("smartemailing", {}).get("programs", {})
default_exclude_columns_from_api_import: list[str] = []
combined_field_name = str(programs_cfg.get("combined_field_name", "")).strip()
if programs_cfg.get("also_fill_combined_field") and combined_field_name:
    default_exclude_columns_from_api_import.append(combined_field_name)
configured_exclude_columns_from_api_import = [
    str(col).strip()
    for col in api_cfg.get("exclude_columns_from_api_import", [])
    if str(col).strip()
]
global_exclude_columns_from_api_import = list(
    dict.fromkeys(default_exclude_columns_from_api_import + configured_exclude_columns_from_api_import)
)
configured_ignore_missing_custom = [
    str(col).strip()
    for col in api_cfg.get("ignore_missing_custom_for_columns", [])
    if str(col).strip()
]
profile_use_system_field_map_override_default = bool(
    profile_api_saved.get("use_profile_system_field_map", False)
)
profile_system_field_map_override_saved = (
    profile_api_saved.get("system_field_map", {})
    if isinstance(profile_api_saved.get("system_field_map", {}), dict)
    else {}
)
api_system_field_map_profile_override = {
    str(k).strip(): str(v).strip()
    for k, v in profile_system_field_map_override_saved.items()
    if str(k).strip() and str(v).strip()
}
profile_use_exclude_columns_override_default = bool(
    profile_api_saved.get("use_profile_exclude_columns", False)
)
profile_exclude_columns_override_saved = [
    str(col).strip()
    for col in profile_api_saved.get("exclude_columns_from_api_import", [])
    if str(col).strip()
]
profile_exclude_columns_override_values = list(profile_exclude_columns_override_saved)
profile_use_system_field_map_override = bool(profile_use_system_field_map_override_default)
profile_use_exclude_columns_override = bool(profile_use_exclude_columns_override_default)

api_system_field_map_for_run = (
    dict(api_system_field_map_profile_override)
    if profile_use_system_field_map_override and api_system_field_map_profile_override
    else dict(api_system_field_map_cfg)
)
exclude_columns_from_api_import = (
    list(dict.fromkeys(profile_exclude_columns_override_values))
    if profile_use_exclude_columns_override
    else list(global_exclude_columns_from_api_import)
)
ignore_missing_custom_for_columns = build_ignore_missing_custom_for_columns(
    field_map_cfg=field_map_cfg,
    api_system_field_map_cfg=api_system_field_map_for_run,
    configured_ignore_missing_custom=configured_ignore_missing_custom,
)
program_custom_field_allowlist_ids = load_program_custom_fields_allowlist(PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH)

api_import_username = ""
api_import_key = ""
api_import_base_url = DEFAULT_BASE_URL
staging_list_value = ""
bucket_routing_saved_raw = (
    profile_api_saved.get("bucket_list_values", {})
    if isinstance(profile_api_saved.get("bucket_list_values", {}), dict)
    else {}
)
bucket_routing_list_values: dict[str, str] = {
    bucket: str(bucket_routing_saved_raw.get(bucket, "")).strip()
    for bucket in COUNTRY_BUCKET_KEYS
}
bucket_favorite_lists_saved_raw = (
    profile_api_saved.get("bucket_favorite_list_ids", {})
    if isinstance(profile_api_saved.get("bucket_favorite_list_ids", {}), dict)
    else {}
)
bucket_favorite_list_ids_by_bucket_default: dict[str, list[str]] = {
    bucket: sorted(
        {
            str(x).strip()
            for x in (
                bucket_favorite_lists_saved_raw.get(bucket, [])
                if isinstance(bucket_favorite_lists_saved_raw.get(bucket, []), list)
                else []
            )
            if str(x).strip()
        }
    )
    for bucket in COUNTRY_BUCKET_KEYS
}
bucket_routing_enabled = bool(do_bucket_country)
staging_tag = str(profile_api_saved.get("staging_tag", "")).strip()
api_canary_size = to_int(profile_api_saved.get("canary_size", api_cfg.get("canary_size", 50)), 50)
api_batch_size = to_int(profile_api_saved.get("batch_size", api_cfg.get("batch_size", 500)), 500)
api_max_contacts_safe = to_int(profile_api_saved.get("max_contacts_safe", api_cfg.get("max_contacts_safe", 2000)), 2000)
api_max_contacts_full = to_int(profile_api_saved.get("max_contacts_full", api_cfg.get("max_contacts_full", 10000)), 10000)
diff_preflight_enabled_default = bool(
    profile_api_saved.get("diff_preflight_enabled", api_cfg.get("diff_preflight_enabled", True))
)
diff_send_only_changes_default = bool(
    profile_api_saved.get("diff_send_only_changes", api_cfg.get("diff_send_only_changes", True))
)
diff_fallback_send_all_on_error_default = bool(
    profile_api_saved.get("diff_fallback_send_all_on_error", api_cfg.get("diff_fallback_send_all_on_error", True))
)
skip_blacklisted_contacts_default = bool(
    profile_api_saved.get("skip_blacklisted_contacts", api_cfg.get("skip_blacklisted_contacts", True))
)
clear_removed_program_custom_fields_default = bool(
    profile_api_saved.get(
        "clear_removed_program_custom_fields_enabled",
        api_cfg.get("clear_removed_program_custom_fields_enabled", True),
    )
)
diff_page_limit = to_int(profile_api_saved.get("diff_page_limit", api_cfg.get("diff_page_limit", 100)), 100)
diff_max_pages = to_int(profile_api_saved.get("diff_max_pages", api_cfg.get("diff_max_pages", 100)), 100)
diff_target_email_batch_size = to_int(
    profile_api_saved.get("diff_target_email_batch_size", api_cfg.get("diff_target_email_batch_size", 50)),
    50,
)
api_read_parallel_workers = to_int(
    profile_api_saved.get("api_read_parallel_workers", api_cfg.get("api_read_parallel_workers", 6)),
    6,
)
clear_allowed_name_prefixes_default = [
    str(x).strip()
    for x in profile_api_saved.get("clear_allowed_name_prefixes", api_cfg.get("clear_allowed_name_prefixes", []))
    if str(x).strip()
]
diff_preflight_enabled = diff_preflight_enabled_default
diff_send_only_changes = diff_send_only_changes_default
diff_fallback_send_all_on_error = diff_fallback_send_all_on_error_default
skip_blacklisted_contacts = skip_blacklisted_contacts_default
clear_removed_program_custom_fields = clear_removed_program_custom_fields_default
clear_allowed_name_prefixes = list(clear_allowed_name_prefixes_default)
favorites_storage_mode = "profile"
favorites_active_preset_id = "(žádný)"
allowlist_storage_mode = "profile"
allowlist_active_preset_id = "(žádný)"
active_selected_preset_id = "(žádný)"
runtime_selected_preset: dict[str, Any] | None = None
safe_confirm = False
full_confirm = False
full_phrase_input = ""
full_operator = ""
full_approver = ""
full_second_approval_input = ""
auto_create_unknown_program_fields = auto_create_unknown_program_fields_default
auto_add_created_program_fields_to_allowlist = bool(
    profile_api_saved.get(
        "auto_add_created_program_fields_to_allowlist",
        api_cfg.get("auto_add_created_program_fields_to_allowlist", True),
    )
)

if api_mode_enabled:
    active_selected_preset_id = str(
        st.session_state.get(
            "profile_selected_preset_id",
            str(profile_ui_saved.get("selected_preset_id", "(žádný)")).strip() or "(žádný)",
        )
    ).strip() or "(žádný)"
    runtime_profile_presets = [
        x for x in load_profile_presets(PROFILE_SETTINGS_PATH) if isinstance(x, dict) and str(x.get("id", "")).strip()
    ]
    runtime_selected_preset = next(
        (x for x in runtime_profile_presets if str(x.get("id", "")).strip() == active_selected_preset_id),
        None,
    )
    if active_selected_preset_id != "(žádný)" and runtime_selected_preset is not None:
        favorites_storage_mode = "preset"
        favorites_active_preset_id = active_selected_preset_id
    else:
        favorites_storage_mode = "profile"
        favorites_active_preset_id = "(žádný)"

    disk_favorite_list_ids = load_api_list_favorites(API_LIST_FAVORITES_PATH)
    preset_favorite_list_ids = load_api_favorite_list_ids_from_preset(runtime_selected_preset)
    has_preset_favorite_list_ids_definition = preset_has_api_favorite_list_ids_definition(runtime_selected_preset)
    effective_favorite_list_ids = (
        set(preset_favorite_list_ids)
        if favorites_storage_mode == "preset" and has_preset_favorite_list_ids_definition
        else set(disk_favorite_list_ids)
    )
    favorite_list_source_key = (
        f"{active_profile_id}|{favorites_storage_mode}|{favorites_active_preset_id}|"
        f"{'preset' if has_preset_favorite_list_ids_definition else 'profile_file'}"
    )

    preset_bucket_favorite_ids = load_api_bucket_favorite_ids_from_preset(runtime_selected_preset)
    has_preset_bucket_favorite_ids_definition = preset_has_api_bucket_favorite_ids_definition(runtime_selected_preset)
    effective_bucket_favorite_ids_by_bucket = (
        normalize_api_bucket_favorite_ids_map(preset_bucket_favorite_ids)
        if favorites_storage_mode == "preset" and has_preset_bucket_favorite_ids_definition
        else normalize_api_bucket_favorite_ids_map(bucket_favorite_list_ids_by_bucket_default)
    )
    bucket_favorite_source_key = (
        f"{active_profile_id}|{favorites_storage_mode}|{favorites_active_preset_id}|"
        f"{'preset' if has_preset_bucket_favorite_ids_definition else 'profile_settings'}"
    )

    disk_allowlist_ids = load_program_custom_fields_allowlist(PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH)
    preset_allowlist_ids = load_allowlist_ids_from_preset(runtime_selected_preset)
    has_preset_allowlist_definition = preset_has_allowlist_definition(runtime_selected_preset)
    if active_selected_preset_id != "(žádný)" and runtime_selected_preset is not None:
        allowlist_storage_mode = "preset"
        allowlist_active_preset_id = active_selected_preset_id
    else:
        allowlist_storage_mode = "profile"
        allowlist_active_preset_id = "(žádný)"
    effective_allowlist_ids = (
        set(preset_allowlist_ids)
        if allowlist_storage_mode == "preset" and has_preset_allowlist_definition
        else set(disk_allowlist_ids)
    )
    allowlist_runtime_source_key = (
        f"{active_profile_id}|{allowlist_storage_mode}|{allowlist_active_preset_id}|"
        f"{'preset' if has_preset_allowlist_definition else 'disk'}"
    )

    preset_values_for_runtime = (
        runtime_selected_preset.get("values", {})
        if isinstance(runtime_selected_preset, dict)
        and isinstance(runtime_selected_preset.get("values", {}), dict)
        else {}
    )
    has_preset_system_map_definition = (
        "use_profile_system_field_map_main" in preset_values_for_runtime
        or "profile_system_field_map_yaml_main" in preset_values_for_runtime
    )
    profile_system_map_yaml_default = yaml.safe_dump(
        api_system_field_map_profile_override
        if api_system_field_map_profile_override
        else api_system_field_map_cfg,
        allow_unicode=True,
        sort_keys=False,
    )
    effective_use_profile_system_map = (
        bool(preset_values_for_runtime.get("use_profile_system_field_map_main", profile_use_system_field_map_override_default))
        if active_selected_preset_id != "(žádný)"
        and runtime_selected_preset is not None
        and has_preset_system_map_definition
        else bool(profile_use_system_field_map_override_default)
    )
    effective_profile_system_map_yaml = (
        str(preset_values_for_runtime.get("profile_system_field_map_yaml_main", profile_system_map_yaml_default))
        if active_selected_preset_id != "(žádný)"
        and runtime_selected_preset is not None
        and has_preset_system_map_definition
        else str(profile_system_map_yaml_default)
    )
    system_map_runtime_source_key = (
        f"{active_profile_id}|{active_selected_preset_id}|"
        f"{'preset' if has_preset_system_map_definition else 'profile'}"
    )

    has_preset_exclude_columns_definition = (
        "use_profile_exclude_columns_main" in preset_values_for_runtime
        or "profile_exclude_columns_text_main" in preset_values_for_runtime
    )
    profile_exclude_columns_text_default = "\n".join(
        profile_exclude_columns_override_values
        if profile_exclude_columns_override_values
        else global_exclude_columns_from_api_import
    )
    effective_use_profile_exclude_columns = (
        bool(preset_values_for_runtime.get("use_profile_exclude_columns_main", profile_use_exclude_columns_override_default))
        if active_selected_preset_id != "(žádný)"
        and runtime_selected_preset is not None
        and has_preset_exclude_columns_definition
        else bool(profile_use_exclude_columns_override_default)
    )
    effective_profile_exclude_columns_text = (
        str(preset_values_for_runtime.get("profile_exclude_columns_text_main", profile_exclude_columns_text_default))
        if active_selected_preset_id != "(žádný)"
        and runtime_selected_preset is not None
        and has_preset_exclude_columns_definition
        else str(profile_exclude_columns_text_default)
    )
    exclude_columns_runtime_source_key = (
        f"{active_profile_id}|{active_selected_preset_id}|"
        f"{'preset' if has_preset_exclude_columns_definition else 'profile'}"
    )

    if "api_contact_lists_cache" not in st.session_state:
        st.session_state.api_contact_lists_cache = []
    if "api_list_favorite_ids" not in st.session_state:
        st.session_state.api_list_favorite_ids = sorted(effective_favorite_list_ids)
    if str(st.session_state.get("_runtime_favorite_list_source_key", "")).strip() != favorite_list_source_key:
        st.session_state.api_list_favorite_ids = sorted(effective_favorite_list_ids)
        st.session_state["_runtime_favorite_list_source_key"] = favorite_list_source_key
    if "api_bucket_favorite_list_ids_by_bucket" not in st.session_state:
        st.session_state.api_bucket_favorite_list_ids_by_bucket = normalize_api_bucket_favorite_ids_map(
            effective_bucket_favorite_ids_by_bucket
        )
    if str(st.session_state.get("_runtime_bucket_favorite_source_key", "")).strip() != bucket_favorite_source_key:
        st.session_state.api_bucket_favorite_list_ids_by_bucket = normalize_api_bucket_favorite_ids_map(
            effective_bucket_favorite_ids_by_bucket
        )
        st.session_state["_runtime_bucket_favorite_source_key"] = bucket_favorite_source_key
    if "program_custom_fields_allowlist_ids" not in st.session_state:
        st.session_state.program_custom_fields_allowlist_ids = sorted(effective_allowlist_ids)
    if str(st.session_state.get("_runtime_allowlist_source_key", "")).strip() != allowlist_runtime_source_key:
        st.session_state.program_custom_fields_allowlist_ids = sorted(effective_allowlist_ids)
        st.session_state["program_custom_fields_allowlist_checkbox_seed"] = (
            int(st.session_state.get("program_custom_fields_allowlist_checkbox_seed", 0)) + 1
        )
        st.session_state["_runtime_allowlist_source_key"] = allowlist_runtime_source_key
    if "use_profile_system_field_map_main" not in st.session_state:
        st.session_state["use_profile_system_field_map_main"] = bool(effective_use_profile_system_map)
    if "profile_system_field_map_yaml_main" not in st.session_state:
        st.session_state["profile_system_field_map_yaml_main"] = str(effective_profile_system_map_yaml)
    if str(st.session_state.get("_runtime_system_map_source_key", "")).strip() != system_map_runtime_source_key:
        st.session_state["use_profile_system_field_map_main"] = bool(effective_use_profile_system_map)
        st.session_state["profile_system_field_map_yaml_main"] = str(effective_profile_system_map_yaml)
        st.session_state["_runtime_system_map_source_key"] = system_map_runtime_source_key
    if "use_profile_exclude_columns_main" not in st.session_state:
        st.session_state["use_profile_exclude_columns_main"] = bool(effective_use_profile_exclude_columns)
    if "profile_exclude_columns_text_main" not in st.session_state:
        st.session_state["profile_exclude_columns_text_main"] = str(effective_profile_exclude_columns_text)
    if str(st.session_state.get("_runtime_exclude_columns_source_key", "")).strip() != exclude_columns_runtime_source_key:
        st.session_state["use_profile_exclude_columns_main"] = bool(effective_use_profile_exclude_columns)
        st.session_state["profile_exclude_columns_text_main"] = str(effective_profile_exclude_columns_text)
        st.session_state["_runtime_exclude_columns_source_key"] = exclude_columns_runtime_source_key
    if "program_custom_fields_catalog" not in st.session_state:
        st.session_state.program_custom_fields_catalog = []
    if "program_custom_fields_catalog_meta" not in st.session_state:
        st.session_state.program_custom_fields_catalog_meta = {}
    if "full_import_approval_code" not in st.session_state:
        st.session_state.full_import_approval_code = hashlib.sha1(
            datetime.now(timezone.utc).isoformat().encode("utf-8")
        ).hexdigest()[:8].upper()
    with st.expander("Detaily API importu a bezpečnostních voleb", expanded=False):
        creds_col_1, creds_col_2 = st.columns(2, gap="medium")
        with creds_col_1:
            api_import_username = st.text_input(
                "API uživatelské jméno (pro import)",
                value=(
                    str(api_username).strip()
                    if str(api_username).strip()
                    else str(saved_api_credentials.get("username", "")).strip()
                ),
                key="import_api_username",
            )
            api_import_key = st.text_input(
                "API klíč (pro import)",
                value=(
                    str(api_key).strip()
                    if str(api_key).strip()
                    else str(saved_api_credentials.get("api_key", "")).strip()
                ),
                type="password",
                key="import_api_key",
            )
        with creds_col_2:
            api_import_base_url = st.text_input(
                "API základní URL (pro import)",
                value=(
                    str(api_base_url).strip()
                    if str(api_base_url).strip()
                    else str(saved_api_credentials.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL
                ),
                key="import_api_base_url",
            )
            save_import_api_credentials = st.button("Uložit API údaje na disk", key="save_api_credentials_import")

        if save_import_api_credentials:
            if str(api_import_username).strip() and str(api_import_key).strip():
                try:
                    save_api_credentials(API_CREDENTIALS_PATH, api_import_username, api_import_key, api_import_base_url)
                    st.success(f"API údaje byly uloženy do `{API_CREDENTIALS_PATH}`.")
                except Exception as exc:
                    st.error(f"Nepodařilo se uložit API údaje: {exc}")
            else:
                st.error("Pro uložení vyplň uživatelské jméno i API klíč.")

        if remember_api_credentials and str(api_import_username).strip() and str(api_import_key).strip():
            try:
                save_api_credentials(API_CREDENTIALS_PATH, api_import_username, api_import_key, api_import_base_url)
            except Exception as exc:
                st.warning(f"Nepodařilo se uložit API údaje lokálně: {exc}")

        api_mode_options_col_1, api_mode_options_col_2 = st.columns(2, gap="medium")
        with api_mode_options_col_1:
            strict_custom_fields = st.checkbox(
                "Striktní kontrola custom fields (chybějící pole = issue)",
                value=bool(strict_custom_fields),
                key="strict_custom_fields_main",
                disabled=profile_lock_critical_options,
            )
        with api_mode_options_col_2:
            list_status_options = ["confirmed", "unconfirmed"]
            default_list_status = list_status if list_status in list_status_options else "confirmed"
            list_status = st.selectbox(
                "Výchozí status kontaktu v listu",
                options=list_status_options,
                index=list_status_options.index(default_list_status),
                key="list_status_main",
                disabled=profile_lock_critical_options,
            )

        mapping_col, exclude_col = st.columns(2, gap="medium")
        with mapping_col:
            profile_use_system_field_map_override = st.checkbox(
                "Použít vlastní mapování systémových polí v tomto profilu",
                value=profile_use_system_field_map_override_default,
                key="use_profile_system_field_map_main",
                disabled=profile_lock_critical_options,
            )
            default_system_map_for_editor = (
                api_system_field_map_profile_override
                if api_system_field_map_profile_override
                else api_system_field_map_cfg
            )
            profile_system_field_map_yaml_text = st.text_area(
                "Mapování systémových polí (YAML slovník: zdrojový_název -> api_klíč)",
                value=yaml.safe_dump(default_system_map_for_editor, allow_unicode=True, sort_keys=False),
                key="profile_system_field_map_yaml_main",
                height=160,
                disabled=(not profile_use_system_field_map_override) or profile_lock_critical_options,
            )
            if profile_use_system_field_map_override:
                try:
                    parsed_system_field_map = yaml.safe_load(profile_system_field_map_yaml_text) or {}
                    if not isinstance(parsed_system_field_map, dict):
                        raise ValueError("Mapování musí být YAML slovník.")
                    api_system_field_map_profile_override = {
                        str(k).strip(): str(v).strip()
                        for k, v in parsed_system_field_map.items()
                        if str(k).strip() and str(v).strip()
                    }
                    if not api_system_field_map_profile_override:
                        raise ValueError("Mapování je prázdné.")
                    api_system_field_map_for_run = dict(api_system_field_map_profile_override)
                except Exception as exc:
                    st.error(
                        "Neplatné profilové mapování systémových polí, používám globální mapování z `mappings.yaml`. "
                        f"Detail: {exc}"
                    )
                    profile_use_system_field_map_override = False
                    api_system_field_map_for_run = dict(api_system_field_map_cfg)
                    api_system_field_map_profile_override = {}
            else:
                api_system_field_map_for_run = dict(api_system_field_map_cfg)
                api_system_field_map_profile_override = {}

        with exclude_col:
            profile_use_exclude_columns_override = st.checkbox(
                "Použít vlastní seznam vyloučených sloupců z API importu v tomto profilu",
                value=profile_use_exclude_columns_override_default,
                key="use_profile_exclude_columns_main",
                disabled=profile_lock_critical_options,
            )
            default_exclude_for_editor = (
                profile_exclude_columns_override_values
                if profile_exclude_columns_override_values
                else global_exclude_columns_from_api_import
            )
            profile_exclude_columns_text = st.text_area(
                "Vyloučené sloupce z API importu (1 sloupec na řádek)",
                value="\n".join(default_exclude_for_editor),
                key="profile_exclude_columns_text_main",
                height=160,
                disabled=(not profile_use_exclude_columns_override) or profile_lock_critical_options,
            )
            if profile_use_exclude_columns_override:
                profile_exclude_columns_override_values = [
                    str(x).strip()
                    for x in str(profile_exclude_columns_text).splitlines()
                    if str(x).strip()
                ]
                exclude_columns_from_api_import = list(dict.fromkeys(profile_exclude_columns_override_values))
            else:
                profile_exclude_columns_override_values = []
                exclude_columns_from_api_import = list(global_exclude_columns_from_api_import)

        ignore_missing_custom_for_columns = build_ignore_missing_custom_for_columns(
            field_map_cfg=field_map_cfg,
            api_system_field_map_cfg=api_system_field_map_for_run,
            configured_ignore_missing_custom=configured_ignore_missing_custom,
        )
        st.caption(
            "Aktivní systémové mapování: "
            f"{len(api_system_field_map_for_run)} klíčů | "
            f"vyloučené sloupce: {len(exclude_columns_from_api_import)}."
        )

        batch_col_1, batch_col_2 = st.columns(2)
        with batch_col_1:
            api_canary_size = int(
                st.number_input(
                    "Velikost testovací dávky (první dávka)",
                    min_value=0,
                    value=max(0, api_canary_size),
                    step=10,
                    key="api_canary_size_main",
                    disabled=profile_lock_critical_options,
                )
            )
        with batch_col_2:
            api_batch_size = int(
                st.number_input(
                    "Velikost dávky",
                    min_value=1,
                    value=max(1, api_batch_size),
                    step=100,
                    key="api_batch_size_main",
                    disabled=profile_lock_critical_options,
                )
            )
        st.markdown("#### Správa aplikačních custom fields (allowlist)")
        st.caption(
            "Bezpečnostní pravidlo: diff/clear odebraných kódů aplikací se aplikuje jen na custom field ID z tohoto seznamu."
        )
        allowlist_selected_preset_id = str(
            st.session_state.get("profile_selected_preset_id", "(žádný)")
        ).strip() or "(žádný)"
        allowlist_profile_presets = [
            x for x in load_profile_presets(PROFILE_SETTINGS_PATH) if isinstance(x, dict) and str(x.get("id", "")).strip()
        ]
        allowlist_selected_preset = next(
            (x for x in allowlist_profile_presets if str(x.get("id", "")).strip() == allowlist_selected_preset_id),
            None,
        )
        allowlist_storage_mode = (
            "preset"
            if allowlist_selected_preset_id != "(žádný)" and allowlist_selected_preset is not None
            else "profile"
        )
        allowlist_active_preset_id = (
            allowlist_selected_preset_id if allowlist_storage_mode == "preset" else "(žádný)"
        )
        preset_allowlist_ids_now = load_allowlist_ids_from_preset(allowlist_selected_preset)
        preset_allowlist_defined_now = preset_has_allowlist_definition(allowlist_selected_preset)
        if allowlist_storage_mode == "preset":
            if preset_allowlist_defined_now:
                st.caption(
                    "Ukládání allowlistu: aktivní preset "
                    f"`{allowlist_active_preset_id}` (per preset)."
                )
            else:
                st.caption(
                    "Ukládání allowlistu: aktivní preset "
                    f"`{allowlist_active_preset_id}` (per preset). "
                    "Preset zatím nemá vlastní allowlist, aktuálně je použit fallback z profilového souboru."
                )
        else:
            st.caption(
                "Ukládání allowlistu: per profil "
                f"(`{PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH}`)."
            )
        load_program_custom_fields_catalog = st.button(
            "Načíst custom fields z API (pro allowlist)",
            key="load_program_custom_fields_catalog_btn",
        )
        if load_program_custom_fields_catalog:
            if not (str(api_import_username).strip() and str(api_import_key).strip()):
                st.error("Pro načtení custom fields vyplň API uživatelské jméno a API klíč.")
            else:
                try:
                    preserved_allowlist_ids = {
                        str(x).strip()
                        for x in st.session_state.get("program_custom_fields_allowlist_ids", [])
                        if str(x).strip()
                    }
                    catalog_client = SmartEmailingApiClient(
                        SmartEmailingCredentials(
                            username=str(api_import_username).strip(),
                            api_key=str(api_import_key).strip(),
                            base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                        )
                    )
                    fetched_catalog = catalog_client.fetch_custom_fields(
                        endpoint_candidates=custom_fields_endpoint_candidates,
                        search_endpoint_candidates=custom_fields_search_endpoint_candidates,
                    )
                    st.session_state.program_custom_fields_catalog = fetched_catalog
                    st.session_state.program_custom_fields_catalog_meta = {
                        "loaded_at": datetime.now(timezone.utc).isoformat(),
                        "count": len(fetched_catalog),
                    }
                    # Keep allowlist stable by ID across API refreshes and rebuild checkboxes from these IDs.
                    st.session_state.program_custom_fields_allowlist_ids = sorted(preserved_allowlist_ids)
                    st.session_state["program_custom_fields_allowlist_checkbox_seed"] = (
                        int(st.session_state.get("program_custom_fields_allowlist_checkbox_seed", 0)) + 1
                    )
                    st.success(f"Načteno custom fields pro allowlist: {len(fetched_catalog)}")
                except Exception as exc:
                    st.error(f"Nepodařilo se načíst custom fields pro allowlist: {exc}")

        catalog_rows = st.session_state.get("program_custom_fields_catalog", [])
        allowlist_ids_current = {
            str(x).strip()
            for x in st.session_state.get("program_custom_fields_allowlist_ids", [])
            if str(x).strip()
        }
        id_to_label: dict[str, str] = {}
        for item in catalog_rows:
            field_id = str(item.get("id", "")).strip()
            field_name = str(item.get("name", "")).strip() or "(bez názvu)"
            if not field_id:
                continue
            id_to_label[field_id] = f"{field_name} (id={field_id})"

        option_ids = sorted(
            set(id_to_label.keys()) | set(allowlist_ids_current),
            key=lambda raw_id: (0, -int(raw_id), raw_id.casefold()) if str(raw_id).isdigit() else (1, 0, str(raw_id).casefold()),
        )
        if option_ids:
            allowlist_checkbox_seed = int(st.session_state.get("program_custom_fields_allowlist_checkbox_seed", 0))
            fallback_filter = st.text_input(
                "Filtr seznamu allowlist (název nebo ID)",
                key=f"program_custom_fields_allowlist_filter_{allowlist_checkbox_seed}",
            ).strip().casefold()
            visible_option_ids: list[str] = []
            for field_id in option_ids:
                field_id_str = str(field_id).strip()
                label = id_to_label.get(field_id_str, f"(nenačtené) id={field_id_str}")
                match_text = f"{label} {field_id_str}".casefold()
                if fallback_filter and fallback_filter not in match_text:
                    continue
                visible_option_ids.append(field_id_str)

            bulk_col_1, bulk_col_2, bulk_col_3 = st.columns([1, 1, 3], gap="small")
            with bulk_col_1:
                mark_all_clicked = st.button(
                    "Označit vše",
                    key=f"allowlist_mark_all_btn_{allowlist_checkbox_seed}",
                    disabled=profile_lock_critical_options or not visible_option_ids,
                )
            with bulk_col_2:
                unmark_all_clicked = st.button(
                    "Odznačit vše",
                    key=f"allowlist_unmark_all_btn_{allowlist_checkbox_seed}",
                    disabled=profile_lock_critical_options or not visible_option_ids,
                )
            with bulk_col_3:
                st.caption(
                    "Hromadné akce pracují s aktuálně zobrazeným seznamem (respektují filtr). "
                    "Na disk se zapisuje jen tlačítkem níže."
                )

            shown_count = 0
            for field_id in option_ids:
                field_id_str = str(field_id).strip()
                label = id_to_label.get(field_id_str, f"(nenačtené) id={field_id_str}")
                checkbox_key = f"program_custom_fields_allowlist_checkbox_{allowlist_checkbox_seed}_{field_id_str}"
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = field_id_str in allowlist_ids_current
                match_text = f"{label} {field_id_str}".casefold()
                if fallback_filter and fallback_filter not in match_text:
                    continue
                if mark_all_clicked:
                    st.session_state[checkbox_key] = True
                elif unmark_all_clicked:
                    st.session_state[checkbox_key] = False
                st.checkbox(label, key=checkbox_key)
                shown_count += 1
            if fallback_filter:
                st.caption(f"Filtrováno: {shown_count} položek z {len(option_ids)}.")
            selected_allowlist_ids = []
            for field_id in option_ids:
                field_id_str = str(field_id).strip()
                checkbox_key = f"program_custom_fields_allowlist_checkbox_{allowlist_checkbox_seed}_{field_id_str}"
                selected = (
                    bool(st.session_state.get(checkbox_key))
                    if checkbox_key in st.session_state
                    else (field_id_str in allowlist_ids_current)
                )
                if selected:
                    selected_allowlist_ids.append(field_id_str)
            st.session_state.program_custom_fields_allowlist_ids = sorted(
                {str(x).strip() for x in selected_allowlist_ids if str(x).strip()}
            )
        else:
            st.caption("Seznam custom fields pro allowlist zatím není načtený.")

        program_allowlist_cols = st.columns(3)
        with program_allowlist_cols[0]:
            if st.button("Uložit allowlist", key="save_program_custom_fields_allowlist_btn"):
                try:
                    allowlist_to_save = {
                        str(x).strip()
                        for x in st.session_state.get("program_custom_fields_allowlist_ids", [])
                        if str(x).strip()
                    }
                    if allowlist_storage_mode == "preset" and allowlist_selected_preset is not None:
                        selected_idx = next(
                            (
                                idx
                                for idx, item in enumerate(allowlist_profile_presets)
                                if str(item.get("id", "")).strip() == allowlist_active_preset_id
                            ),
                            None,
                        )
                        if selected_idx is None:
                            st.error("Aktivní preset pro allowlist nebyl nalezen.")
                        else:
                            save_allowlist_ids_to_preset(
                                allowlist_profile_presets[selected_idx],
                                allowlist_to_save,
                            )
                            save_profile_presets(
                                PROFILE_SETTINGS_PATH,
                                allowlist_profile_presets,
                                profile_id=active_profile_id,
                                profile_name=active_profile_name,
                            )
                            st.success(
                                "Allowlist uložen do aktivního presetu: "
                                f"`{allowlist_active_preset_id}` ({len(allowlist_to_save)} položek)."
                            )
                    else:
                        save_program_custom_fields_allowlist(PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH, allowlist_to_save)
                        st.success(
                            f"Allowlist uložen: {len(allowlist_to_save)} položek "
                            f"(`{PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH}`)"
                        )
                except Exception as exc:
                    st.error(f"Nepodařilo se uložit allowlist: {exc}")
        with program_allowlist_cols[1]:
            if st.button("Načíst allowlist", key="reload_program_custom_fields_allowlist_btn"):
                if allowlist_storage_mode == "preset" and allowlist_selected_preset is not None:
                    if preset_has_allowlist_definition(allowlist_selected_preset):
                        loaded_ids = load_allowlist_ids_from_preset(allowlist_selected_preset)
                    else:
                        loaded_ids = load_program_custom_fields_allowlist(PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH)
                else:
                    loaded_ids = load_program_custom_fields_allowlist(PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH)
                st.session_state.program_custom_fields_allowlist_ids = sorted(loaded_ids)
                st.session_state["program_custom_fields_allowlist_checkbox_seed"] = (
                    int(st.session_state.get("program_custom_fields_allowlist_checkbox_seed", 0)) + 1
                )
                if allowlist_storage_mode == "preset" and allowlist_selected_preset is not None:
                    st.success(
                        "Allowlist načten pro aktivní preset "
                        f"`{allowlist_active_preset_id}`: {len(loaded_ids)} položek."
                    )
                else:
                    st.success(f"Allowlist načten z disku: {len(loaded_ids)} položek.")
                st.rerun()
        with program_allowlist_cols[2]:
            if st.button("Smazat allowlist", key="delete_program_custom_fields_allowlist_btn"):
                try:
                    if allowlist_storage_mode == "preset" and allowlist_selected_preset is not None:
                        selected_idx = next(
                            (
                                idx
                                for idx, item in enumerate(allowlist_profile_presets)
                                if str(item.get("id", "")).strip() == allowlist_active_preset_id
                            ),
                            None,
                        )
                        if selected_idx is None:
                            st.error("Aktivní preset pro allowlist nebyl nalezen.")
                        else:
                            target_preset = allowlist_profile_presets[selected_idx]
                            preset_values = target_preset.get("values", {})
                            if not isinstance(preset_values, dict):
                                preset_values = {}
                            preset_values["program_custom_fields_allowlist_ids"] = []
                            target_preset["values"] = preset_values
                            target_preset["updated_at"] = datetime.now(timezone.utc).isoformat()
                            save_profile_presets(
                                PROFILE_SETTINGS_PATH,
                                allowlist_profile_presets,
                                profile_id=active_profile_id,
                                profile_name=active_profile_name,
                            )
                            st.session_state.program_custom_fields_allowlist_ids = []
                            st.success(
                                "Allowlist v aktivním presetu byl smazán: "
                                f"`{allowlist_active_preset_id}`."
                            )
                    elif PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH.exists():
                        PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH.unlink()
                        st.success(f"Soubor allowlistu smazán: `{PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH}`")
                    else:
                        st.info("Soubor allowlistu na disku neexistuje.")
                except Exception as exc:
                    st.error(f"Nepodařilo se smazat allowlist z disku: {exc}")

        current_allowlist_count = len(
            {
                str(x).strip()
                for x in st.session_state.get("program_custom_fields_allowlist_ids", [])
                if str(x).strip()
            }
        )
        if allowlist_storage_mode == "preset" and allowlist_selected_preset is not None:
            st.caption(
                "Aktivní allowlist: "
                f"{current_allowlist_count} custom field ID "
                f"(preset `{allowlist_active_preset_id}`)."
            )
        else:
            st.caption(
                f"Aktivní allowlist: {current_allowlist_count} custom field ID "
                f"(`{PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH}`)."
            )

    program_custom_field_allowlist_ids = {
        str(x).strip()
        for x in st.session_state.get("program_custom_fields_allowlist_ids", [])
        if str(x).strip()
    }

    with api_step_container:
        st.markdown("### 3) Import do SmartEmailingu přes API")

        if "api_contact_lists_cache_meta" not in st.session_state:
            st.session_state.api_contact_lists_cache_meta = {}
        if "staging_list_manual" not in st.session_state:
            st.session_state.staging_list_manual = str(profile_api_saved.get("staging_list_value", "")).strip()
        if "staging_list_select" not in st.session_state:
            st.session_state.staging_list_select = "(ručně)"

        load_lists = st.button("Obnovit listy z API", key="refresh_api_lists_main", disabled=profile_lock_critical_options)
        credentials_ready_for_lists = bool(str(api_import_username).strip()) and bool(str(api_import_key).strip())
        lists_fingerprint = hashlib.sha256(
            (
                f"{str(api_import_username).strip()}|"
                f"{str(api_import_key).strip()}|"
                f"{str(api_import_base_url).strip() or DEFAULT_BASE_URL}"
            ).encode("utf-8")
        ).hexdigest()
        cached_fingerprint = str(st.session_state.get("api_contact_lists_cache_meta", {}).get("fingerprint", ""))
        should_auto_load = credentials_ready_for_lists and cached_fingerprint != lists_fingerprint

        if (load_lists or should_auto_load) and credentials_ready_for_lists:
            try:
                list_client = SmartEmailingApiClient(
                    SmartEmailingCredentials(
                        username=str(api_import_username).strip(),
                        api_key=str(api_import_key).strip(),
                        base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                    )
                )
                fetched_lists = list_client.fetch_contact_lists(
                    endpoint_candidates=contact_lists_endpoint_candidates,
                    search_endpoint_candidates=contact_lists_search_endpoint_candidates,
                )
                st.session_state.api_contact_lists_cache = fetched_lists
                st.session_state.api_contact_lists_cache_meta = {
                    "fingerprint": lists_fingerprint,
                    "loaded_at": datetime.now(timezone.utc).isoformat(),
                }
                if load_lists:
                    st.success(f"Načteno listů: {len(fetched_lists)}")
            except Exception as exc:
                st.error(f"Nepodařilo se načíst listy: {exc}")

        lists_cache = st.session_state.get("api_contact_lists_cache", [])
        hidden_segment_lists = 0
        lists_cache_for_staging: list[dict[str, Any]] = []
        for item in lists_cache:
            list_name = str(item.get("name", "")).strip()
            if list_name.casefold().startswith("segment id #"):
                hidden_segment_lists += 1
                continue
            lists_cache_for_staging.append(item)
        favorite_list_ids = {
            str(x).strip()
            for x in st.session_state.get("api_list_favorite_ids", [])
            if str(x).strip()
        }
        bucket_routing_enabled = bool(do_bucket_country)
        if bucket_routing_enabled:
            st.caption(
                "Bucket routing pro API je aktivní, protože je zapnuto "
                "`Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)` v levém panelu."
            )
        else:
            st.caption(
                "Bucket routing pro API je vypnutý. Zapni "
                "`Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)` v levém panelu."
            )
        label_to_list: dict[str, dict[str, str]] = {}
        labels: list[str] = []
        list_options = ["(ručně)"]

        def _persist_staging_list_to_active_preset(list_id: str) -> None:
            target_list_id = str(list_id).strip()
            if active_selected_preset_id == "(žádný)" or runtime_selected_preset is None:
                return
            try:
                presets_for_save = [
                    x
                    for x in load_profile_presets(PROFILE_SETTINGS_PATH)
                    if isinstance(x, dict) and str(x.get("id", "")).strip()
                ]
                preset_idx = next(
                    (
                        idx
                        for idx, item in enumerate(presets_for_save)
                        if str(item.get("id", "")).strip() == str(active_selected_preset_id).strip()
                    ),
                    None,
                )
                if preset_idx is None:
                    return
                preset_values = presets_for_save[preset_idx].get("values", {})
                if not isinstance(preset_values, dict):
                    preset_values = {}
                if str(preset_values.get("staging_list_manual", "")).strip() == target_list_id:
                    return
                preset_values["staging_list_manual"] = target_list_id
                presets_for_save[preset_idx]["values"] = preset_values
                presets_for_save[preset_idx]["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_profile_presets(
                    PROFILE_SETTINGS_PATH,
                    presets_for_save,
                    profile_id=active_profile_id,
                    profile_name=active_profile_name,
                )
            except Exception as exc:
                st.warning(f"Nepodařilo se uložit staging seznam do aktivního presetu: {exc}")

        if lists_cache_for_staging:
            def _list_sort_tuple(item: dict[str, Any]) -> tuple[int, int, int, str]:
                raw_id = str(item.get("id", "")).strip()
                name = str(item.get("name", "")).strip().casefold()
                favorite_rank = 0 if raw_id in favorite_list_ids else 1
                try:
                    return (favorite_rank, 0, -int(raw_id), name)
                except Exception:
                    return (favorite_rank, 1, 0, name)

            for item in sorted(lists_cache_for_staging, key=_list_sort_tuple):
                list_id = str(item.get("id", "")).strip()
                list_name = str(item.get("name", "")).strip() or "(bez názvu)"
                favorite_prefix = "★ " if list_id in favorite_list_ids else ""
                label = f"{favorite_prefix}{list_name} (id={list_id})"
                labels.append(label)
                label_to_list[label] = {"id": list_id, "name": list_name}
            list_options = ["(ručně)"] + labels

            if not bucket_routing_enabled:
                favorite_items = [
                    item
                    for item in sorted(lists_cache_for_staging, key=_list_sort_tuple)
                    if str(item.get("id", "")).strip() in favorite_list_ids
                ]
                if favorite_items:
                    st.caption("Rychlý výběr oblíbených")
                    quick_cols = st.columns(min(3, len(favorite_items)))
                    for idx, item in enumerate(favorite_items):
                        list_id = str(item.get("id", "")).strip()
                        list_name = str(item.get("name", "")).strip() or "(bez názvu)"
                        quick_label = f"★ {list_name} ({list_id})"
                        selected_label_with_id = f"★ {list_name} (id={list_id})"
                        with quick_cols[idx % len(quick_cols)]:
                            if st.button(
                                quick_label,
                                key=f"quick_select_favorite_{list_id}",
                                disabled=profile_lock_critical_options,
                            ):
                                if selected_label_with_id in ["(ručně)"] + labels:
                                    st.session_state["staging_list_select"] = selected_label_with_id
                                st.session_state["staging_list_manual"] = list_id
                                _persist_staging_list_to_active_preset(list_id)
                                st.rerun()

                manual_selected_id = str(st.session_state.get("staging_list_manual", "")).strip()
                current_selected_label = str(st.session_state.get("staging_list_select", "(ručně)")).strip() or "(ručně)"
                matched_label_for_manual = ""
                if manual_selected_id:
                    matched_label_for_manual = next(
                        (
                            label
                            for label, meta in label_to_list.items()
                            if str(meta.get("id", "")).strip() == manual_selected_id
                        ),
                        "",
                    )
                current_selected_id = (
                    str(label_to_list.get(current_selected_label, {}).get("id", "")).strip()
                    if current_selected_label != "(ručně)"
                    else ""
                )
                if matched_label_for_manual and current_selected_id != manual_selected_id:
                    st.session_state["staging_list_select"] = matched_label_for_manual
                elif not matched_label_for_manual and current_selected_label not in list_options:
                    st.session_state["staging_list_select"] = "(ručně)"
                elif not manual_selected_id and current_selected_label not in list_options:
                    st.session_state["staging_list_select"] = "(ručně)"

                selected_list_label = st.selectbox(
                    "Vyber staging seznam ze SmartEmailingu",
                    options=list_options,
                    key="staging_list_select",
                    disabled=profile_lock_critical_options,
                )
                if selected_list_label != "(ručně)":
                    selected = label_to_list.get(selected_list_label, {"id": "", "name": ""})
                    selected_id = str(selected.get("id", "")).strip()
                    selected_name = str(selected.get("name", "")).strip()
                    if selected_id:
                        st.session_state["staging_list_manual"] = selected_id
                        _persist_staging_list_to_active_preset(selected_id)
                        st.caption(f"Vybraný staging seznam: `{selected_name}` (id `{selected_id}`)")
                        is_favorite = selected_id in favorite_list_ids
                        toggle_fav_label = "★ Odebrat z oblíbených" if is_favorite else "☆ Přidat do oblíbených"
                        fav_col_1, fav_col_2 = st.columns([1, 4])
                        with fav_col_1:
                            if st.button(
                                toggle_fav_label,
                                key=f"toggle_api_favorite_list_{selected_id}",
                                disabled=profile_lock_critical_options,
                            ):
                                if is_favorite:
                                    favorite_list_ids.discard(selected_id)
                                else:
                                    favorite_list_ids.add(selected_id)
                                st.session_state["api_list_favorite_ids"] = sorted(favorite_list_ids)
                                try:
                                    if favorites_storage_mode == "preset" and favorites_active_preset_id != "(žádný)":
                                        presets_for_save = [
                                            x
                                            for x in load_profile_presets(PROFILE_SETTINGS_PATH)
                                            if isinstance(x, dict) and str(x.get("id", "")).strip()
                                        ]
                                        preset_idx = next(
                                            (
                                                idx
                                                for idx, item in enumerate(presets_for_save)
                                                if str(item.get("id", "")).strip() == favorites_active_preset_id
                                            ),
                                            None,
                                        )
                                        if preset_idx is None:
                                            raise RuntimeError("Aktivní preset pro uložení oblíbených seznamů nebyl nalezen.")
                                        save_api_favorite_list_ids_to_preset(
                                            presets_for_save[preset_idx],
                                            favorite_list_ids,
                                        )
                                        save_profile_presets(
                                            PROFILE_SETTINGS_PATH,
                                            presets_for_save,
                                            profile_id=active_profile_id,
                                            profile_name=active_profile_name,
                                        )
                                    else:
                                        save_api_list_favorites(API_LIST_FAVORITES_PATH, favorite_list_ids)
                                except Exception as exc:
                                    st.error(f"Nepodařilo se uložit oblíbené seznamy: {exc}")
                                st.rerun()
                        with fav_col_2:
                            st.caption("Oblíbené seznamy jsou označené `★` a řadí se vždy nahoře.")
                if hidden_segment_lists > 0:
                    st.caption(
                        f"Skryto dynamických segment listů (`Segment id #`): {hidden_segment_lists}."
                    )
        else:
            if lists_cache and hidden_segment_lists > 0:
                st.caption(
                    "Načtené listy jsou jen dynamické segmenty (`Segment id #`) a byly skryté."
                )
            else:
                st.caption("Listy zatím nejsou načtené. Vyplň API údaje a klikni na `Obnovit listy z API`.")

        if not bucket_routing_enabled:
            staging_input_col, staging_tag_col = st.columns([2, 1], gap="medium")
            with staging_input_col:
                staging_list_value = st.text_input(
                    "Staging seznam ID nebo název (bezpečný/plný režim)",
                    key="staging_list_manual",
                    disabled=profile_lock_critical_options,
                    help="Doporučeno: použít staging seznam, ne produkční seznam.",
                )
                _persist_staging_list_to_active_preset(staging_list_value)
            with staging_tag_col:
                staging_tag = st.text_input(
                    "Staging štítek (volitelný)",
                    value=str(profile_api_saved.get("staging_tag", "")).strip(),
                    key="staging_tag_input",
                    disabled=profile_lock_critical_options,
                    placeholder="např. ITF_IMPORT_STAGING",
                    help="Některé účty podporují tagy v importních datech.",
                )
        else:
            staging_list_value = str(st.session_state.get("staging_list_manual", "")).strip()
            st.caption("Nastav cílový list pro každý bucket. Použije se jen bucket, který má data.")
            bucket_favorite_lists_state_raw = st.session_state.get("api_bucket_favorite_list_ids_by_bucket", {})
            bucket_favorite_ids_by_bucket: dict[str, set[str]] = {}
            for bucket in COUNTRY_BUCKET_KEYS:
                raw_ids = (
                    bucket_favorite_lists_state_raw.get(bucket, [])
                    if isinstance(bucket_favorite_lists_state_raw, dict)
                    else []
                )
                if not isinstance(raw_ids, list):
                    raw_ids = []
                bucket_favorite_ids_by_bucket[bucket] = {
                    str(x).strip() for x in raw_ids if str(x).strip()
                }

            def _persist_bucket_favorites_state(persist_to_storage: bool = False) -> None:
                normalized_bucket_favorites = {
                    bucket: sorted(
                        {
                            str(x).strip()
                            for x in bucket_favorite_ids_by_bucket.get(bucket, set())
                            if str(x).strip()
                        }
                    )
                    for bucket in COUNTRY_BUCKET_KEYS
                }
                st.session_state["api_bucket_favorite_list_ids_by_bucket"] = normalized_bucket_favorites
                if not persist_to_storage:
                    return
                try:
                    if favorites_storage_mode == "preset" and favorites_active_preset_id != "(žádný)":
                        presets_for_save = [
                            x
                            for x in load_profile_presets(PROFILE_SETTINGS_PATH)
                            if isinstance(x, dict) and str(x.get("id", "")).strip()
                        ]
                        preset_idx = next(
                            (
                                idx
                                for idx, item in enumerate(presets_for_save)
                                if str(item.get("id", "")).strip() == favorites_active_preset_id
                            ),
                            None,
                        )
                        if preset_idx is None:
                            raise RuntimeError(
                                "Aktivní preset pro uložení bucket oblíbených seznamů nebyl nalezen."
                            )
                        save_api_bucket_favorite_ids_to_preset(
                            presets_for_save[preset_idx],
                            normalized_bucket_favorites,
                        )
                        save_profile_presets(
                            PROFILE_SETTINGS_PATH,
                            presets_for_save,
                            profile_id=active_profile_id,
                            profile_name=active_profile_name,
                        )
                except Exception as exc:
                    st.error(f"Nepodařilo se uložit bucket oblíbené seznamy: {exc}")

            def _bucket_select(bucket_key: str, title: str, session_key: str) -> str:
                bucket_favorites = set(bucket_favorite_ids_by_bucket.get(bucket_key, set()))

                def _bucket_sort(item: dict[str, Any]) -> tuple[int, int, int, str]:
                    raw_id = str(item.get("id", "")).strip()
                    name = str(item.get("name", "")).strip().casefold()
                    favorite_rank = 0 if raw_id in bucket_favorites else 1
                    try:
                        return (favorite_rank, 0, -int(raw_id), name)
                    except Exception:
                        return (favorite_rank, 1, 0, name)

                bucket_label_to_list: dict[str, dict[str, str]] = {}
                bucket_labels: list[str] = []
                for item in sorted(lists_cache_for_staging, key=_bucket_sort):
                    list_id = str(item.get("id", "")).strip()
                    list_name = str(item.get("name", "")).strip() or "(bez názvu)"
                    favorite_prefix = "★ " if list_id in bucket_favorites else ""
                    label = f"{favorite_prefix}{list_name} (id={list_id})"
                    bucket_labels.append(label)
                    bucket_label_to_list[label] = {"id": list_id, "name": list_name}

                label_by_list_id = {
                    str(item.get("id", "")).strip(): label
                    for label, item in bucket_label_to_list.items()
                    if str(item.get("id", "")).strip()
                }
                current_value = str(bucket_routing_list_values.get(bucket_key, "")).strip()
                bucket_options = ["(není vybráno)"] + bucket_labels
                option_to_list_id: dict[str, str] = {"(není vybráno)": ""}
                for label in bucket_labels:
                    option_to_list_id[label] = str(bucket_label_to_list.get(label, {}).get("id", "")).strip()

                default_label = "(není vybráno)"
                if current_value:
                    matched_label = label_by_list_id.get(current_value, "")
                    if matched_label:
                        default_label = matched_label
                    else:
                        unknown_label = f"(uloženo) id={current_value}"
                        if unknown_label not in option_to_list_id:
                            bucket_options.append(unknown_label)
                            option_to_list_id[unknown_label] = current_value
                        default_label = unknown_label

                if st.session_state.get(session_key) not in bucket_options:
                    st.session_state[session_key] = default_label

                selected_label = st.selectbox(
                    title,
                    options=bucket_options,
                    key=session_key,
                    disabled=profile_lock_critical_options,
                )
                selected_list_id = str(option_to_list_id.get(selected_label, "")).strip()
                if selected_list_id:
                    is_favorite = selected_list_id in bucket_favorites
                    toggle_label = "★ Odebrat z oblíbených (bucket)" if is_favorite else "☆ Přidat do oblíbených (bucket)"
                    if st.button(
                        toggle_label,
                        key=f"toggle_bucket_favorite_{bucket_key}_{selected_list_id}",
                        disabled=profile_lock_critical_options,
                    ):
                        if is_favorite:
                            bucket_favorite_ids_by_bucket[bucket_key].discard(selected_list_id)
                        else:
                            bucket_favorite_ids_by_bucket[bucket_key].add(selected_list_id)
                        _persist_bucket_favorites_state(persist_to_storage=True)
                        st.rerun()
                return selected_list_id

            bucket_col_1, bucket_col_2, bucket_col_3 = st.columns(3)
            with bucket_col_1:
                bucket_routing_list_values["CZ_SK"] = _bucket_select(
                    bucket_key="CZ_SK",
                    title="List pro CZ_SK",
                    session_key="api_bucket_select_cz_sk_main",
                )
            with bucket_col_2:
                bucket_routing_list_values["DE_AT_CH"] = _bucket_select(
                    bucket_key="DE_AT_CH",
                    title="List pro DE_AT_CH",
                    session_key="api_bucket_select_de_at_ch_main",
                )
            with bucket_col_3:
                bucket_routing_list_values["EN"] = _bucket_select(
                    bucket_key="EN",
                    title="List pro EN",
                    session_key="api_bucket_select_en_main",
                )
            _persist_bucket_favorites_state()
            bucket_meta_col, staging_tag_col = st.columns([2, 1], gap="medium")
            with bucket_meta_col:
                st.caption("Staging štítek je volitelný a použije se pro všechny aktivní bucket trasy.")
            with staging_tag_col:
                staging_tag = st.text_input(
                    "Staging štítek (volitelný)",
                    value=str(profile_api_saved.get("staging_tag", "")).strip(),
                    key="staging_tag_input",
                    disabled=profile_lock_critical_options,
                    placeholder="např. ITF_IMPORT_STAGING",
                    help="Některé účty podporují tagy v importních datech.",
                )

        st.markdown("#### Porovnání před importem (diff)")
        diff_col_1, diff_col_2 = st.columns(2, gap="medium")
        with diff_col_1:
            diff_preflight_enabled = st.checkbox(
                "Před importem porovnat připravené kontakty s kontakty v cílovém staging seznamu",
                value=diff_preflight_enabled_default,
                key="diff_preflight_enabled_main",
                disabled=profile_lock_critical_options,
                help="Načte kontakty ze staging seznamu a vyhodnotí nové / aktualizované / beze změny.",
            )
            diff_send_only_changes = st.checkbox(
                "Odesílat jen nové a změněné kontakty (beze změny přeskočit)",
                value=diff_send_only_changes_default,
                key="diff_send_only_changes_main",
                disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
            )
            skip_blacklisted_contacts = st.checkbox(
                "Přeskočit kontakty, které jsou ve SmartEmailingu na blacklistu",
                value=skip_blacklisted_contacts_default,
                key="skip_blacklisted_contacts_main",
                disabled=profile_lock_critical_options,
                help="Kontakty s `blacklisted=1` se vyřadí ještě před diffem a importem.",
            )
        with diff_col_2:
            diff_fallback_send_all_on_error = st.checkbox(
                "Při chybě diff porovnání pokračovat odesláním bez diffu (fallback)",
                value=diff_fallback_send_all_on_error_default,
                key="diff_fallback_send_all_on_error_main",
                disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
            )
            clear_removed_program_custom_fields = st.checkbox(
                "Při diffu mazat odebrané kódy aplikací (jen vybrané custom fields)",
                value=clear_removed_program_custom_fields_default,
                key="clear_removed_program_custom_fields_main",
                disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
                help=(
                    "Bezpečnostní režim: maže jen custom fieldy z explicitního allowlistu aplikačních polí. "
                    "Ostatních custom fields se nedotýká."
                ),
            )
            clear_allowed_prefixes_raw = st.text_input(
                "Hard-guard: prefixy názvů pro mazání",
                value=",".join(clear_allowed_name_prefixes_default),
                key="clear_allowed_name_prefixes_main",
                disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
                help="Volitelné. Odděl čárkou. Mazání se omezí jen na allowlist ID s názvem začínajícím některým prefixem.",
            )
        clear_allowed_name_prefixes = [
            str(x).strip()
            for x in str(clear_allowed_prefixes_raw).split(",")
            if str(x).strip()
        ]
        diff_limit_col_1, diff_limit_col_2, diff_limit_col_3, diff_limit_col_4 = st.columns(4, gap="small")
        with diff_limit_col_1:
            diff_page_limit = int(
                st.number_input(
                    "Diff: kontaktů/stránku",
                    min_value=10,
                    value=max(10, diff_page_limit),
                    step=10,
                    key="diff_page_limit_main",
                    disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
                )
            )
        with diff_limit_col_2:
            diff_max_pages = int(
                st.number_input(
                    "Diff: max stránek",
                    min_value=1,
                    value=max(1, diff_max_pages),
                    step=5,
                    key="diff_max_pages_main",
                    disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
                )
            )
        with diff_limit_col_3:
            diff_target_email_batch_size = int(
                st.number_input(
                    "Diff: batch emailů",
                    min_value=1,
                    value=max(1, diff_target_email_batch_size),
                    step=10,
                    key="diff_target_email_batch_size_main",
                    disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
                )
            )
        with diff_limit_col_4:
            api_read_parallel_workers = int(
                st.number_input(
                    "API čtení: vlákna",
                    min_value=1,
                    value=max(1, api_read_parallel_workers),
                    step=1,
                    key="api_read_parallel_workers_main",
                    disabled=(not diff_preflight_enabled) or profile_lock_critical_options,
                )
            )

        lists_loaded_count = len(lists_cache_for_staging)
        api_creds_filled = bool(str(api_import_username).strip()) and bool(str(api_import_key).strip())
        st.caption(
            "Rychlý stav API importu: "
            f"údaje {'vyplněny' if api_creds_filled else 'chybí'}, "
            f"načtené seznamy: {lists_loaded_count}."
            + (f" (skryté segmenty: {hidden_segment_lists})" if hidden_segment_lists > 0 else "")
        )
        st.markdown("#### Limity kontaktů pro import")
        limit_col_safe, limit_col_full = st.columns(2)
        with limit_col_safe:
            api_max_contacts_safe = int(
                st.number_input(
                    "Limit pro bezpečný import",
                    min_value=1,
                    value=max(1, api_max_contacts_safe),
                    step=100,
                    key="safe_import_limit_main",
                    disabled=profile_lock_critical_options,
                )
            )
        with limit_col_full:
            api_max_contacts_full = int(
                st.number_input(
                    "Limit pro plný import",
                    min_value=1,
                    value=max(1, api_max_contacts_full),
                    step=100,
                    key="full_import_limit_main",
                    disabled=profile_lock_critical_options,
                )
            )
        auto_create_unknown_program_fields = st.checkbox(
            "Automaticky vytvořit chybějící vlastní pole pro nové kódy aplikací",
            value=auto_create_unknown_program_fields_default,
            key="auto_create_unknown_program_fields_main",
            disabled=profile_lock_critical_options,
            help=f"Vytvoří nové custom fieldy v SmartEmailingu jako typ '{auto_create_program_field_type}'.",
        )
        auto_add_created_program_fields_to_allowlist = st.checkbox(
            "Automaticky přidat nově vytvořená aplikační pole do allowlistu",
            value=auto_add_created_program_fields_to_allowlist,
            key="auto_add_created_program_fields_to_allowlist_main",
            disabled=profile_lock_critical_options,
            help="Po vytvoření pole pro nový kód aplikace se jeho custom field ID uloží do allowlistu.",
        )

        if execution_mode == "api_full_import":
            full_phrase_required = str(
                api_cfg.get("required_confirmation_phrase_full", "FULL IMPORT DO SMARTEMAILINGU")
            )
            st.markdown("#### Povinné potvrzení pro plný import")
            full_confirm = st.checkbox(
                "Rozumím dopadu: plný import může přepsat více polí dle pravidel.",
                value=False,
                key="full_import_confirm_main",
            )
            full_phrase_input = st.text_input(
                f"Opiš potvrzovací frázi: {full_phrase_required}",
                value="",
                key="full_import_phrase_main",
            )
            full_operator = st.text_input(
                "Operátor (kdo import spouští)",
                value="",
                key="full_import_operator_main",
            )
            full_approver = st.text_input(
                "Schvalovatel (4 oči)",
                value="",
                key="full_import_approver_main",
            )
            approval_code = st.session_state.full_import_approval_code
            full_second_approval_input = st.text_input(
                f"Schvalovací kód (4 oči): {approval_code}",
                value="",
                key="full_import_approval_code_main",
            )

profile_presets_current = load_profile_presets(PROFILE_SETTINGS_PATH)
if favorites_storage_mode == "preset" and favorites_active_preset_id != "(žádný)":
    bucket_favorite_list_ids_for_profile_save = normalize_api_bucket_favorite_ids_map(
        bucket_favorite_list_ids_by_bucket_default
    )
else:
    bucket_favorite_list_ids_for_profile_save = normalize_api_bucket_favorite_ids_map(
        st.session_state.get("api_bucket_favorite_list_ids_by_bucket", {})
    )
if active_selected_preset_id != "(žádný)" and runtime_selected_preset is not None:
    use_profile_system_field_map_for_profile_save = bool(
        profile_api_saved.get("use_profile_system_field_map", False)
    )
    system_field_map_for_profile_save = dict(api_system_field_map_profile_override)
    use_profile_exclude_columns_for_profile_save = bool(
        profile_api_saved.get("use_profile_exclude_columns", False)
    )
    exclude_columns_for_profile_save = list(profile_exclude_columns_override_saved)
else:
    use_profile_system_field_map_for_profile_save = bool(profile_use_system_field_map_override)
    system_field_map_for_profile_save = (
        dict(api_system_field_map_profile_override)
        if profile_use_system_field_map_override
        else {}
    )
    use_profile_exclude_columns_for_profile_save = bool(profile_use_exclude_columns_override)
    exclude_columns_for_profile_save = (
        [str(x).strip() for x in profile_exclude_columns_override_values if str(x).strip()]
        if profile_use_exclude_columns_override
        else []
    )
profile_settings_to_save = {
    "ui": {
        "do_split_emails": bool(do_split_emails),
        "do_split_names": bool(do_split_names),
        "do_bucket_country": bool(do_bucket_country),
        "output_encoding": str(output_encoding).strip() or "cp1250",
        "dedup_label": str(dedup_label).strip(),
        "selected_preset_id": str(st.session_state.get("profile_selected_preset_id", "(žádný)")).strip() or "(žádný)",
        "use_cached_schema": bool(use_cached_schema),
        "use_api_schema": bool(use_api_schema),
        "refresh_api_schema_on_generate": bool(refresh_api_schema_on_generate),
        "use_api_cache_on_error": bool(use_api_cache_on_error),
        "history_filter_active_profile": bool(
            st.session_state.get(
                "history_filter_active_profile",
                bool(profile_ui_saved.get("history_filter_active_profile", False)),
            )
        ),
    },
    "api": {
        "execution_mode": str(execution_mode).strip(),
        "strict_custom_fields": bool(strict_custom_fields),
        "list_status": str(list_status).strip() or "confirmed",
        "staging_list_value": str(st.session_state.get("staging_list_manual", "")).strip(),
        "bucket_routing_enabled": bool(bucket_routing_enabled),
        "bucket_list_values": {
            bucket: str(bucket_routing_list_values.get(bucket, "")).strip()
            for bucket in COUNTRY_BUCKET_KEYS
            if str(bucket_routing_list_values.get(bucket, "")).strip()
        },
        "bucket_favorite_list_ids": {
            bucket: list(bucket_favorite_list_ids_for_profile_save.get(bucket, []))
            for bucket in COUNTRY_BUCKET_KEYS
        },
        "staging_tag": str(staging_tag).strip(),
        "canary_size": int(api_canary_size),
        "batch_size": int(api_batch_size),
        "max_contacts_safe": int(api_max_contacts_safe),
        "max_contacts_full": int(api_max_contacts_full),
        "diff_preflight_enabled": bool(diff_preflight_enabled),
        "diff_send_only_changes": bool(diff_send_only_changes),
        "diff_fallback_send_all_on_error": bool(diff_fallback_send_all_on_error),
        "skip_blacklisted_contacts": bool(skip_blacklisted_contacts),
        "clear_removed_program_custom_fields_enabled": bool(clear_removed_program_custom_fields),
        "clear_allowed_name_prefixes": [str(x).strip() for x in clear_allowed_name_prefixes if str(x).strip()],
        "diff_page_limit": int(diff_page_limit),
        "diff_max_pages": int(diff_max_pages),
        "diff_target_email_batch_size": int(diff_target_email_batch_size),
        "api_read_parallel_workers": int(api_read_parallel_workers),
        "auto_create_unknown_program_fields": bool(auto_create_unknown_program_fields),
        "auto_add_created_program_fields_to_allowlist": bool(auto_add_created_program_fields_to_allowlist),
        "use_profile_system_field_map": bool(use_profile_system_field_map_for_profile_save),
        "system_field_map": dict(system_field_map_for_profile_save),
        "use_profile_exclude_columns": bool(use_profile_exclude_columns_for_profile_save),
        "exclude_columns_from_api_import": list(exclude_columns_for_profile_save),
    },
    "safety": {
        "lock_critical_options": bool(
            st.session_state.get(
                "profile_lock_critical_options_main",
                bool(profile_safety_saved.get("lock_critical_options", False)),
            )
        ),
    },
}
profile_settings_save_ok = False
profile_settings_save_error = ""
try:
    save_profile_settings(
        PROFILE_SETTINGS_PATH,
        settings=profile_settings_to_save,
        profile_id=active_profile_id,
        profile_name=active_profile_name,
        presets=profile_presets_current,
    )
    profile_settings_save_ok = True
except Exception as exc:
    profile_settings_save_error = str(exc)
    st.warning(f"Nepodařilo se uložit profilová nastavení: {exc}")

if st.session_state.pop("manual_profile_save_requested", False):
    if profile_settings_save_ok:
        st.sidebar.success("Aktuální nastavení profilu bylo ručně uloženo.")
    else:
        st.sidebar.error(
            "Ruční uložení nastavení profilu selhalo: "
            + (profile_settings_save_error or "neznámá chyba")
        )

can_load_schema_during_generate = use_api_schema and bool(str(api_username).strip()) and bool(str(api_key).strip())
api_credentials_ready = bool(str(api_import_username).strip()) and bool(str(api_import_key).strip())
generate_disabled = (not source_files) or (schema is None and not can_load_schema_during_generate) or (
    api_mode_enabled and not api_credentials_ready
)

if "pending_custom_fields_to_create" not in st.session_state:
    st.session_state["pending_custom_fields_to_create"] = []
if "pending_custom_fields_fingerprint" not in st.session_state:
    st.session_state["pending_custom_fields_fingerprint"] = ""
if "approved_custom_fields_fingerprint" not in st.session_state:
    st.session_state["approved_custom_fields_fingerprint"] = ""
if "auto_resume_run_after_custom_fields_confirm" not in st.session_state:
    st.session_state["auto_resume_run_after_custom_fields_confirm"] = False
if "pending_api_import_confirmation" not in st.session_state:
    st.session_state["pending_api_import_confirmation"] = {}
if "pending_api_import_confirmation_fingerprint" not in st.session_state:
    st.session_state["pending_api_import_confirmation_fingerprint"] = ""
if "approved_api_import_confirmation_fingerprint" not in st.session_state:
    st.session_state["approved_api_import_confirmation_fingerprint"] = ""
if "auto_resume_run_after_api_import_confirm" not in st.session_state:
    st.session_state["auto_resume_run_after_api_import_confirm"] = False
if "safe_import_confirm_latched" not in st.session_state:
    st.session_state["safe_import_confirm_latched"] = False
if "diff_preview_rows" not in st.session_state:
    st.session_state["diff_preview_rows"] = []
if "diff_preview_summary" not in st.session_state:
    st.session_state["diff_preview_summary"] = {}
if "diff_preview_error" not in st.session_state:
    st.session_state["diff_preview_error"] = ""
if "diff_preview_detail_map" not in st.session_state:
    st.session_state["diff_preview_detail_map"] = {}
if "diff_preview_selected_email" not in st.session_state:
    st.session_state["diff_preview_selected_email"] = ""

with run_step_container:
    init_run_log_state()
    run_status_placeholder = st.empty()
    st.markdown("#### Log běhu")
    run_log_placeholder = st.empty()

    def render_run_log_panel() -> None:
        log_entries = st.session_state.get("run_log_entries", [])
        with run_log_placeholder.container():
            if isinstance(log_entries, list) and len(log_entries) > 0:
                st.dataframe(
                    to_streamlit_safe_dataframe(pd.DataFrame(log_entries)),
                    use_container_width=True,
                    hide_index=True,
                    height=220,
                )
            else:
                st.caption("Zatím bez hlášek běhu.")

    class RunStatusProxy:
        def __init__(self, placeholder: Any) -> None:
            self._placeholder = placeholder

        def _emit(self, level: str, message: str) -> None:
            append_run_log_entry(level, message)
            render_run_log_panel()
            level_key = str(level).strip().lower()
            if level_key == "success":
                self._placeholder.success(message)
            elif level_key == "warning":
                self._placeholder.warning(message)
            elif level_key == "error":
                self._placeholder.error(message)
            else:
                self._placeholder.info(message)

        def info(self, message: str) -> None:
            self._emit("info", message)

        def warning(self, message: str) -> None:
            self._emit("warning", message)

        def error(self, message: str) -> None:
            self._emit("error", message)

        def success(self, message: str) -> None:
            self._emit("success", message)

    run_status_box = RunStatusProxy(run_status_placeholder)
    render_run_log_panel()

    if api_mode_enabled and not api_credentials_ready:
        run_status_box.warning("Pro API režim vyplň API uživatelské jméno + API klíč.")
    if execution_mode not in {"api_safe_import", "api_full_import"}:
        st.session_state["pending_api_import_confirmation"] = {}
        st.session_state["pending_api_import_confirmation_fingerprint"] = ""
        st.session_state["approved_api_import_confirmation_fingerprint"] = ""
        st.session_state["auto_resume_run_after_api_import_confirm"] = False
        st.session_state["diff_preview_rows"] = []
        st.session_state["diff_preview_summary"] = {}
        st.session_state["diff_preview_error"] = ""
        st.session_state["diff_preview_detail_map"] = {}
        st.session_state["diff_preview_selected_email"] = ""
        st.session_state.pop("diff_preview_editor", None)
    pending_fields = [str(x).strip() for x in st.session_state.get("pending_custom_fields_to_create", []) if str(x).strip()]
    pending_fingerprint = str(st.session_state.get("pending_custom_fields_fingerprint", "")).strip()
    pending_custom_fields_action = bool(pending_fields and pending_fingerprint)

    pending_import_confirmation = st.session_state.get("pending_api_import_confirmation", {})
    pending_import_fingerprint = str(st.session_state.get("pending_api_import_confirmation_fingerprint", "")).strip()
    pending_api_import_action = bool(
        execution_mode in {"api_safe_import", "api_full_import"}
        and isinstance(pending_import_confirmation, dict)
        and pending_import_confirmation
        and pending_import_fingerprint
    )

    preview_summary = st.session_state.get("diff_preview_summary", {})
    preview_rows = st.session_state.get("diff_preview_rows", [])
    preview_error = str(st.session_state.get("diff_preview_error", "")).strip()
    if execution_mode in {"api_safe_import", "api_full_import"} and (preview_summary or preview_rows or preview_error):
        st.markdown("#### Diff preview před importem (limit 200)")
        if isinstance(preview_summary, dict) and preview_summary:
            preview_metric_1, preview_metric_2, preview_metric_3, preview_metric_4 = st.columns(4)
            preview_metric_1.metric("Nové", int(preview_summary.get("new_contacts", 0)))
            preview_metric_2.metric("Aktualizace", int(preview_summary.get("updated_contacts", 0)))
            preview_metric_3.metric("Beze změny", int(preview_summary.get("unchanged_contacts", 0)))
            preview_metric_4.metric("K odeslání", int(preview_summary.get("contacts_to_send", 0)))
            preview_bucket_routing = preview_summary.get("bucket_routing", {})
            if isinstance(preview_bucket_routing, dict) and preview_bucket_routing:
                preview_routing_label = ", ".join(
                    [
                        f"{bucket}→{str(preview_bucket_routing.get(bucket, '')).strip()}"
                        for bucket in COUNTRY_BUCKET_KEYS
                        if str(preview_bucket_routing.get(bucket, "")).strip()
                    ]
                )
                st.caption(
                    f"Bucket routing: {preview_routing_label or 'není'} | "
                    f"Diff status: {preview_summary.get('diff_status', '') or 'n/a'} | "
                    f"Vytvořeno: {preview_summary.get('generated_at', '') or 'n/a'}"
                )
            else:
                st.caption(
                    f"Staging list ID: {preview_summary.get('list_id', '') or 'není'} | "
                    f"Diff status: {preview_summary.get('diff_status', '') or 'n/a'} | "
                    f"Vytvořeno: {preview_summary.get('generated_at', '') or 'n/a'}"
                )
            if bool(preview_summary.get("clear_removed_program_custom_fields_enabled", False)):
                st.caption(
                    "Čištění odebraných kódů aplikací je zapnuté: "
                    f"{int(preview_summary.get('removed_program_custom_fields', 0))} změn."
                )
            if (
                not bool(preview_summary.get("custom_fields_compare_enabled", True))
                and int(preview_summary.get("matched_existing_contacts", 0)) > 0
            ):
                st.warning(
                    "U existujících kontaktů se nepodařilo získat custom fields (nebo je kontakty nemají). "
                    "Diff porovnání custom fields bylo přeskočeno a fallback běží jako odeslání všech připravených kontaktů."
                )
            preview_route_summaries = preview_summary.get("route_summaries", [])
            if isinstance(preview_route_summaries, list) and preview_route_summaries:
                st.caption("Diff souhrn po bucketech/listových trasách")
                st.dataframe(
                    to_streamlit_safe_dataframe(pd.DataFrame(preview_route_summaries)),
                    use_container_width=True,
                    height=210,
                )
        if preview_error:
            st.warning(preview_error)
        if preview_rows:
            preview_df = to_streamlit_safe_dataframe(pd.DataFrame(preview_rows))
            selected_preview_email = str(st.session_state.get("diff_preview_selected_email", "")).strip()
            selected_preview_email_key = normalize_email_key(selected_preview_email)
            preview_ui_df = preview_df.copy()
            preview_ui_df.insert(
                0,
                "🔎",
                [
                    normalize_email_key(email) == selected_preview_email_key
                    for email in preview_ui_df.get("email", pd.Series(dtype=str)).tolist()
                ],
            )
            edited_preview_df = preview_ui_df
            try:
                edited_preview_df = st.data_editor(
                    preview_ui_df,
                    use_container_width=True,
                    hide_index=True,
                    height=320,
                    key="diff_preview_editor",
                    disabled=[col for col in preview_ui_df.columns if col != "🔎"],
                    column_config={
                        "🔎": st.column_config.CheckboxColumn(
                            "🔎",
                            help="Zaškrtni kontakt pro zobrazení detailu změn.",
                            default=False,
                            width="small",
                        )
                    },
                )
            except Exception:
                st.dataframe(preview_df, use_container_width=True, height=320)

            try:
                selected_from_table = [
                    normalize_scalar_for_diff(x)
                    for x in edited_preview_df.loc[edited_preview_df["🔎"] == True, "email"].tolist()
                    if normalize_scalar_for_diff(x)
                ]
            except Exception:
                selected_from_table = []

            resolved_from_table = ""
            if selected_from_table:
                selected_unique: list[str] = []
                for email in selected_from_table:
                    normalized = normalize_scalar_for_diff(email)
                    if normalized and normalized not in selected_unique:
                        selected_unique.append(normalized)
                if len(selected_unique) == 1:
                    resolved_from_table = selected_unique[0]
                else:
                    previous_key = normalize_email_key(selected_preview_email)
                    alternatives = [
                        email
                        for email in selected_unique
                        if normalize_email_key(email) != previous_key
                    ]
                    resolved_from_table = alternatives[-1] if alternatives else selected_unique[-1]
                    st.caption("V tabulce může být aktivní jen 1 kontakt. Použit je poslední výběr.")

            if normalize_email_key(resolved_from_table) != normalize_email_key(selected_preview_email):
                selected_preview_email = resolved_from_table
                st.session_state["diff_preview_selected_email"] = selected_preview_email

            email_options = ["(nevybráno)"] + [
                normalize_scalar_for_diff(x) for x in preview_df.get("email", pd.Series(dtype=str)).tolist()
            ]
            default_email = (
                selected_preview_email
                if selected_preview_email and selected_preview_email in email_options
                else "(nevybráno)"
            )
            desired_selectbox_value = default_email
            current_selectbox_value = str(st.session_state.get("diff_preview_detail_email_select", "")).strip()
            if current_selectbox_value not in email_options:
                current_selectbox_value = ""
            if current_selectbox_value != desired_selectbox_value:
                st.session_state["diff_preview_detail_email_select"] = desired_selectbox_value
            selected_from_control = st.selectbox(
                "Vyber kontakt pro detail diffu",
                options=email_options,
                index=email_options.index(
                    str(st.session_state.get("diff_preview_detail_email_select", desired_selectbox_value)).strip()
                    if str(st.session_state.get("diff_preview_detail_email_select", desired_selectbox_value)).strip() in email_options
                    else desired_selectbox_value
                ),
                key="diff_preview_detail_email_select",
            )
            if selected_from_control == "(nevybráno)":
                selected_preview_email = ""
                st.session_state["diff_preview_selected_email"] = ""
            else:
                selected_preview_email = selected_from_control
                st.session_state["diff_preview_selected_email"] = selected_from_control

            preview_detail_map = (
                st.session_state.get("diff_preview_detail_map", {})
                if isinstance(st.session_state.get("diff_preview_detail_map", {}), dict)
                else {}
            )
            selected_preview_email_key = normalize_email_key(selected_preview_email)
            selected_preview_detail = (
                preview_detail_map.get(selected_preview_email_key, {})
                if selected_preview_email_key
                else {}
            )
            if selected_preview_email_key:
                st.markdown(f"##### Detail změn pro `{selected_preview_email}`")
                field_diff_rows = (
                    selected_preview_detail.get("field_diffs", [])
                    if isinstance(selected_preview_detail, dict)
                    else []
                )
                if isinstance(field_diff_rows, list) and field_diff_rows:
                    detail_df = to_streamlit_safe_dataframe(pd.DataFrame(field_diff_rows))
                    detail_df = detail_df.rename(
                        columns={
                            "field": "pole",
                            "before": "původní_hodnota",
                            "after": "nová_hodnota",
                        }
                    )
                    st.dataframe(
                        detail_df,
                        use_container_width=True,
                        hide_index=True,
                        height=min(420, 110 + len(detail_df) * 32),
                    )
                else:
                    st.caption(
                        "Detail `původní -> nová` není pro tento kontakt dostupný "
                        "(typicky nové kontakty nebo fallback bez detailních field diffů)."
                    )

    auto_resume_requested = bool(st.session_state.get("auto_resume_run_after_custom_fields_confirm", False)) or bool(
        st.session_state.get("auto_resume_run_after_api_import_confirm", False)
    )
    blocking_action_active = bool(pending_custom_fields_action or pending_api_import_action)

    if blocking_action_active:
        with st.container(border=True):
            st.markdown("#### Čekající akce")
            if pending_custom_fields_action:
                st.warning(
                    "Před pokračováním potvrď vytvoření nových vlastních polí ve SmartEmailingu."
                )
                st.caption(f"Nová pole k vytvoření: {len(pending_fields)}")
                with st.expander("Seznam nových polí", expanded=False):
                    st.code("\n".join([f"- {x}" for x in pending_fields]), language="text")
                confirm_col, cancel_col = st.columns(2)
                with confirm_col:
                    if st.button("Potvrdit vytvoření a pokračovat", key="confirm_custom_fields_create"):
                        st.session_state["approved_custom_fields_fingerprint"] = pending_fingerprint
                        st.session_state["auto_resume_run_after_custom_fields_confirm"] = True
                        st.session_state["pending_custom_fields_to_create"] = []
                        st.session_state["pending_custom_fields_fingerprint"] = ""
                        st.rerun()
                with cancel_col:
                    if st.button("Zrušit vytvoření polí", key="cancel_custom_fields_create"):
                        st.session_state["approved_custom_fields_fingerprint"] = ""
                        st.session_state["auto_resume_run_after_custom_fields_confirm"] = False
                        st.session_state["pending_custom_fields_to_create"] = []
                        st.session_state["pending_custom_fields_fingerprint"] = ""
                        run_status_box.warning("Vytvoření nových vlastních polí bylo zrušeno.")
            elif pending_api_import_action:
                st.warning(
                    "Import čeká na finální potvrzení odeslání do SmartEmailingu API."
                )
                metric_col_1, metric_col_2, metric_col_3, metric_col_4 = st.columns(4)
                metric_col_1.metric("Kontakty k odeslání", int(pending_import_confirmation.get("contacts_total", 0)))
                metric_col_2.metric("S custom fields", int(pending_import_confirmation.get("contacts_with_custom_fields", 0)))
                metric_col_3.metric("Se seznamem", int(pending_import_confirmation.get("contacts_with_list_assignment", 0)))
                metric_col_4.metric("Chyby payloadu", int(pending_import_confirmation.get("issues_count", 0)))
                with st.expander("Detail potvrzení importu", expanded=False):
                    pending_bucket_routing = pending_import_confirmation.get("bucket_routing", {})
                    if isinstance(pending_bucket_routing, dict) and pending_bucket_routing:
                        routing_label = ", ".join(
                            [
                                f"{bucket}→{str(pending_bucket_routing.get(bucket, '')).strip()}"
                                for bucket in COUNTRY_BUCKET_KEYS
                                if str(pending_bucket_routing.get(bucket, "")).strip()
                            ]
                        )
                        st.caption(
                            f"Režim: {pending_import_confirmation.get('mode_label', '')} | "
                            f"Bucket routing: {routing_label or 'není'} | "
                            f"Staging tag: {pending_import_confirmation.get('staging_tag', '') or 'není'}"
                        )
                    else:
                        st.caption(
                            f"Režim: {pending_import_confirmation.get('mode_label', '')} | "
                            f"Staging list ID: {pending_import_confirmation.get('staging_list_id', '') or 'není'} | "
                            f"Staging tag: {pending_import_confirmation.get('staging_tag', '') or 'není'}"
                        )
                    st.caption(
                        f"Canary dávka: {pending_import_confirmation.get('canary_size', 0)} | "
                        f"Velikost dávky: {pending_import_confirmation.get('batch_size', 0)} | "
                        f"Limit režimu: {pending_import_confirmation.get('max_contacts_limit', 0)}"
                    )
                    if bool(pending_import_confirmation.get("diff_available", False)):
                        st.caption(
                            "Diff porovnání: "
                            f"existující={int(pending_import_confirmation.get('diff_existing_contacts', 0))}, "
                            f"nové={int(pending_import_confirmation.get('diff_new_contacts', 0))}, "
                            f"aktualizace={int(pending_import_confirmation.get('diff_updated_contacts', 0))}, "
                            f"beze změny={int(pending_import_confirmation.get('diff_unchanged_contacts', 0))}."
                        )
                    payload_system_fields = [
                        str(x).strip()
                        for x in pending_import_confirmation.get("payload_system_fields", [])
                        if str(x).strip()
                    ]
                    if payload_system_fields:
                        st.caption(
                            "Systémová pole v payloadu: "
                            + ", ".join(payload_system_fields[:12])
                            + (" ..." if len(payload_system_fields) > 12 else "")
                        )
                    emails_preview = [
                        str(x).strip()
                        for x in pending_import_confirmation.get("emails_preview", [])
                        if str(x).strip()
                    ]
                    if emails_preview:
                        st.code("\n".join([f"- {x}" for x in emails_preview]), language="text")
                    note = str(pending_import_confirmation.get("new_vs_update_note", "")).strip()
                    if note:
                        st.info(note)
                confirm_import_col, cancel_import_col = st.columns(2)
                with confirm_import_col:
                    if st.button(
                        "Potvrdit API import a pokračovat",
                        key="confirm_api_import_send",
                        type="primary",
                    ):
                        st.session_state["approved_api_import_confirmation_fingerprint"] = pending_import_fingerprint
                        st.session_state["auto_resume_run_after_api_import_confirm"] = True
                        st.session_state["pending_api_import_confirmation"] = {}
                        st.session_state["pending_api_import_confirmation_fingerprint"] = ""
                        st.rerun()
                with cancel_import_col:
                    if st.button("Zrušit API import", key="cancel_api_import_send"):
                        st.session_state["approved_api_import_confirmation_fingerprint"] = ""
                        st.session_state["auto_resume_run_after_api_import_confirm"] = False
                        st.session_state["pending_api_import_confirmation"] = {}
                        st.session_state["pending_api_import_confirmation_fingerprint"] = ""
                        run_status_box.warning("API import byl zrušen před odesláním.")

    if execution_mode == "api_safe_import":
        safe_confirm = st.checkbox(
            "Rozumím dopadu: bezpečný import běží jen jako přidání/aktualizace, bez mazání.",
            value=False,
            key="safe_import_confirm_main",
        )

    run_controls_disabled = bool(generate_disabled or (blocking_action_active and not auto_resume_requested))
    diff_preview_clicked = False
    run_button_col, preview_button_col = st.columns(2)
    with run_button_col:
        run_clicked = st.button("Spustit zpracování", type="primary", disabled=run_controls_disabled)
    with preview_button_col:
        diff_preview_clicked = st.button(
            "Načíst diff preview (bez importu)",
            disabled=(
                run_controls_disabled
                or execution_mode not in {"api_safe_import", "api_full_import"}
                or not diff_preflight_enabled
            ),
            help="Spočítá diff proti vybranému staging seznamu a zobrazí prvních 200 řádků nad tlačítky.",
        )

    if auto_resume_requested:
        run_clicked = True
        st.session_state["auto_resume_run_after_custom_fields_confirm"] = False
        st.session_state["auto_resume_run_after_api_import_confirm"] = False

preview_only = diff_preview_clicked and not run_clicked
safe_confirm_effective = bool(safe_confirm)
if run_clicked and not preview_only and execution_mode == "api_safe_import":
    if not auto_resume_requested:
        st.session_state["safe_import_confirm_latched"] = bool(st.session_state.get("safe_import_confirm_main", False))
    safe_confirm_effective = bool(st.session_state.get("safe_import_confirm_latched", False))

if run_clicked or diff_preview_clicked:
    clear_run_log_state()
    render_run_log_panel()
    if preview_only:
        st.session_state["pending_api_import_confirmation"] = {}
        st.session_state["pending_api_import_confirmation_fingerprint"] = ""
        st.session_state["approved_api_import_confirmation_fingerprint"] = ""
        st.session_state["auto_resume_run_after_api_import_confirm"] = False
        if not diff_preflight_enabled:
            run_status_box.error("Diff preview nelze spočítat: zapni volbu porovnání před importem (diff).")
            st.stop()
        if bucket_routing_enabled and not any(str(v).strip() for v in bucket_routing_list_values.values()):
            run_status_box.error(
                "Diff preview nelze spočítat: při zapnutém bucket routingu vyplň aspoň jeden cílový list."
            )
            st.stop()
        if (not bucket_routing_enabled) and not str(staging_list_value).strip():
            run_status_box.error("Diff preview nelze spočítat: vyber staging seznam.")
            st.stop()
        run_status_box.info("Načítám diff preview (bez odeslání importu do API).")

    if not preview_only and execution_mode == "api_safe_import" and not safe_confirm_effective:
        run_status_box.error(
            "Bezpečný import nelze spustit: zaškrtni povinné potvrzení "
            "`Rozumím dopadu: bezpečný import běží jen jako přidání/aktualizace, bez mazání.`"
        )
        st.stop()

    active_schema = schema
    run_schema_origin = schema_origin
    if use_api_schema and refresh_api_schema_on_generate:
        try:
            active_schema, api_meta = fetch_schema_from_api(
                api_username,
                api_key,
                api_base_url,
                read_only=bool(preview_only or execution_mode == "api_dry_run"),
            )
            run_schema_origin = "smartemailing_api"
            save_cached_schema(
                API_SCHEMA_CACHE_PATH,
                active_schema,
                source_file="smartemailing_api",
                source_kind="api_live",
                extra_meta=api_meta,
            )
            run_status_box.info(
                f"Před exportem načteno čerstvé API schéma: {len(active_schema.columns)} sloupců "
                f"(vlastní pole: {api_meta.get('custom_field_count', 0)})"
            )
        except Exception as exc:
            if use_api_cache_on_error and cached_api_schema is not None:
                cached_custom_fields = to_int(cached_api_schema_meta.get("custom_field_count", 0), 0)
                if cached_custom_fields >= required_min_custom_fields:
                    active_schema = cached_api_schema
                    run_schema_origin = "smartemailing_api_cache"
                    run_status_box.warning(f"Online obnovení API schématu selhalo ({exc}), používám API mezipaměť.")
                else:
                    run_status_box.warning(
                        "Online obnovení API schématu selhalo "
                        f"({exc}) a API mezipaměť má jen {cached_custom_fields} vlastních polí, "
                        "používám aktuální záložní schéma."
                    )
            elif active_schema is not None:
                run_status_box.warning(f"Online obnovení API schématu selhalo ({exc}), používám aktuální záložní schéma.")
            else:
                run_status_box.error(f"Online obnovení API schématu selhalo ({exc}) a není dostupné záložní schéma.")
                st.stop()

    if active_schema is None:
        run_status_box.error("Nelze pokračovat bez schématu sloupců.")
        st.stop()

    all_import_rows = []
    invalid_all = []
    unknown_all = []
    file_errors = []
    processed_files = 0
    source_rows_total = 0
    rows_after_email_processing = 0
    email_counts: dict[str, int] = {}
    email_source_files: dict[str, set[str]] = {}
    row_order_counter = 0

    for sf in source_files:
        try:
            rr = read_csv_best_effort(sf.getvalue())
            df = clean_columns(rr.df)

            # detect source and normalize
            source = detect_source(df, CFG["sources"])
            norm = normalize_df(df, source)
            norm["source_file"] = sf.name
            norm["source_row_index"] = pd.Series(range(1, len(norm) + 1), index=norm.index)
            source_rows_total += len(norm)

            # partner -> notes (optional)
            partner_company = (
                norm.get("partner_company", pd.Series([""] * len(norm), index=norm.index))
                .fillna("")
                .astype(str)
                .str.strip()
            )
            norm["notes"] = ""
            norm.loc[partner_company != "", "notes"] = "Partner: " + partner_company[partner_company != ""]

            # transforms
            if do_split_emails:
                expanded, invalid = split_emails(norm, CFG["transforms"]["email"]["split_separators"])
            else:
                expanded, invalid = validate_emails_without_split(norm)
            invalid_all.append(invalid)
            rows_after_email_processing += len(expanded)

            if do_split_names:
                expanded = apply_name_split(expanded, CFG["transforms"]["name"])
            else:
                expanded["title_before"] = ""
                expanded["first_name"] = ""
                expanded["last_name"] = ""
                expanded["title_after"] = ""

            # country bucket
            if do_bucket_country:
                expanded = apply_country_bucket(expanded, CFG["transforms"]["country_bucket"])
            else:
                expanded["country_bucket"] = "EN"

            # incremental duplicate stats (memory-friendly)
            emails = expanded.get("email", pd.Series([""] * len(expanded), index=expanded.index)).fillna("").astype(str).str.strip()
            emails = emails[emails != ""]
            if len(emails) > 0:
                vc = emails.value_counts()
                for email, count in vc.items():
                    email_counts[email] = email_counts.get(email, 0) + int(count)
                    if email not in email_source_files:
                        email_source_files[email] = set()
                    email_source_files[email].add(sf.name)

            # build SmartEmailing import
            import_df, unknown = build_import_df(expanded, active_schema, CFG)
            unknown_all.append(unknown)
            import_df["country_bucket"] = expanded.get("country_bucket", pd.Series(["EN"] * len(expanded), index=expanded.index)).tolist()
            import_df["__source_file"] = expanded.get(
                "source_file",
                pd.Series([sf.name] * len(expanded), index=expanded.index),
            ).tolist()
            import_df["__source_row_index"] = expanded.get(
                "source_row_index",
                pd.Series([""] * len(expanded), index=expanded.index),
            ).tolist()
            import_df["__row_order"] = pd.Series(range(row_order_counter, row_order_counter + len(import_df)), index=import_df.index)
            row_order_counter += len(import_df)
            all_import_rows.append(import_df)

            processed_files += 1
            run_status_box.info(
                f"{sf.name}: detekováno jako {source.name}, řádků po transformacích: {len(expanded)}"
            )
        except Exception as exc:
            file_errors.append({"source_file": sf.name, "error": str(exc)})
            run_status_box.error(f"{sf.name}: nepodařilo se zpracovat ({exc})")
            continue

    if all_import_rows:
        final_import_df = pd.concat(all_import_rows, ignore_index=True)
    else:
        final_import_df = pd.DataFrame(columns=active_schema.columns + ["country_bucket", "__row_order"])

    field_map = CFG.get("smartemailing", {}).get("field_map", {})
    email_export_column = next((se_col for se_col, internal_col in field_map.items() if internal_col == "email"), None)
    dedup_removed_rows = 0
    if dedup_keep != "none":
        if email_export_column and email_export_column in final_import_df.columns:
            final_import_df, dedup_removed_rows = deduplicate_import_df(final_import_df, email_export_column, dedup_keep)
        else:
            run_status_box.warning(
                "Deduplikace je zapnutá, ale ve schématu nebyl nalezen emailový sloupec pro deduplikaci."
            )

    if len(final_import_df) > 0:
        bucket_series = final_import_df.get(
            "country_bucket",
            pd.Series(["EN"] * len(final_import_df), index=final_import_df.index),
        )
        import_only_df = final_import_df.drop(columns=["country_bucket"], errors="ignore")
        final_parts = split_by_bucket(import_only_df, bucket_series)
    else:
        final_parts = {"CZ_SK": pd.DataFrame(), "DE_AT_CH": pd.DataFrame(), "EN": pd.DataFrame()}

    invalid_frames = [d for d in invalid_all if len(d) > 0]
    invalid_df = pd.concat(invalid_frames, ignore_index=True) if invalid_frames else pd.DataFrame()
    unknown_frames = [u for u in unknown_all if len(u) > 0]
    unknown_df = pd.concat(unknown_frames, ignore_index=True) if unknown_frames else pd.DataFrame()

    if (
        api_mode_enabled
        and execution_mode in {"api_safe_import", "api_full_import"}
        and not preview_only
        and auto_create_unknown_program_fields
        and len(unknown_df) > 0
    ):
        try:
            auto_client = SmartEmailingApiClient(
                SmartEmailingCredentials(
                    username=str(api_import_username).strip(),
                    api_key=str(api_import_key).strip(),
                    base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                )
            )
            existing_custom_fields = auto_client.fetch_custom_fields(
                endpoint_candidates=custom_fields_endpoint_candidates,
                search_endpoint_candidates=custom_fields_search_endpoint_candidates,
            )
            existing_custom_field_names = {
                str(x.get("name", "")).strip().casefold()
                for x in existing_custom_fields
                if str(x.get("name", "")).strip()
            }
            unknown_codes = sorted(
                {
                    str(x).strip()
                    for x in unknown_df.get("unknown_code", pd.Series(dtype=str)).tolist()
                    if str(x).strip()
                }
            )
            codes_to_create = [code for code in unknown_codes if code.casefold() not in existing_custom_field_names]
            if codes_to_create:
                create_fingerprint = hashlib.sha256("\n".join(codes_to_create).encode("utf-8")).hexdigest()
                approved_fingerprint = str(st.session_state.get("approved_custom_fields_fingerprint", "")).strip()
                if approved_fingerprint != create_fingerprint:
                    st.session_state["pending_custom_fields_to_create"] = codes_to_create
                    st.session_state["pending_custom_fields_fingerprint"] = create_fingerprint
                    run_status_box.warning(
                        "Před pokračováním potvrď vytvoření nových vlastních polí "
                        "(zobrazeno nad tlačítkem Spustit zpracování)."
                    )
                    st.rerun()
                st.session_state["approved_custom_fields_fingerprint"] = ""
            created_codes: list[str] = []
            created_field_ids: set[str] = set()
            create_errors: list[str] = []
            for code in codes_to_create:
                try:
                    created_field = auto_client.create_custom_field(
                        name=code,
                        field_type=auto_create_program_field_type,
                        endpoint_candidates=custom_field_create_endpoint_candidates,
                    )
                    created_codes.append(code)
                    created_field_id = str(created_field.get("id", "")).strip() if isinstance(created_field, dict) else ""
                    if created_field_id:
                        created_field_ids.add(created_field_id)
                except Exception as exc:
                    create_errors.append(f"{code}: {exc}")

            if auto_add_created_program_fields_to_allowlist and created_field_ids:
                updated_allowlist_ids = set(program_custom_field_allowlist_ids) | set(created_field_ids)
                program_custom_field_allowlist_ids = set(updated_allowlist_ids)
                st.session_state.program_custom_fields_allowlist_ids = sorted(updated_allowlist_ids)
                st.session_state["program_custom_fields_allowlist_checkbox_seed"] = (
                    int(st.session_state.get("program_custom_fields_allowlist_checkbox_seed", 0)) + 1
                )
                try:
                    if allowlist_storage_mode == "preset" and allowlist_active_preset_id != "(žádný)":
                        presets_for_save = [
                            x
                            for x in load_profile_presets(PROFILE_SETTINGS_PATH)
                            if isinstance(x, dict) and str(x.get("id", "")).strip()
                        ]
                        preset_idx = next(
                            (
                                idx
                                for idx, item in enumerate(presets_for_save)
                                if str(item.get("id", "")).strip() == allowlist_active_preset_id
                            ),
                            None,
                        )
                        if preset_idx is None:
                            raise RuntimeError(
                                "Aktivní preset pro uložení allowlistu nebyl nalezen."
                            )
                        save_allowlist_ids_to_preset(
                            presets_for_save[preset_idx],
                            updated_allowlist_ids,
                        )
                        save_profile_presets(
                            PROFILE_SETTINGS_PATH,
                            presets_for_save,
                            profile_id=active_profile_id,
                            profile_name=active_profile_name,
                        )
                        run_status_box.info(
                            "Nově vytvořená aplikační pole byla přidána do allowlistu "
                            f"aktivního presetu `{allowlist_active_preset_id}`: {len(created_field_ids)}."
                        )
                    else:
                        save_program_custom_fields_allowlist(
                            PROGRAM_CUSTOM_FIELDS_ALLOWLIST_PATH,
                            updated_allowlist_ids,
                        )
                        run_status_box.info(
                            "Nově vytvořená aplikační pole byla přidána do allowlistu: "
                            f"{len(created_field_ids)}."
                        )
                except Exception as exc:
                    run_status_box.warning(
                        "Nově vytvořená pole vznikla, ale nepodařilo se uložit allowlist: "
                        f"{exc}"
                    )

            resolved_codes = {
                str(code).strip().casefold()
                for code in (set(created_codes) | (set(unknown_codes) - set(codes_to_create)))
                if str(code).strip()
            }
            if resolved_codes and {"__source_file", "__source_row_index"}.issubset(set(final_import_df.columns)):
                fill_value_tpl = str(CFG.get("smartemailing", {}).get("programs", {}).get("fill_value", "{code}"))
                fill_value_tpl = fill_value_tpl or "{code}"
                for code in sorted({str(x).strip() for x in unknown_codes if str(x).strip()}):
                    if code.casefold() in resolved_codes and code not in final_import_df.columns:
                        final_import_df[code] = ""
                resolved_mask = (
                    unknown_df.get("unknown_code", pd.Series(dtype=str))
                    .astype(str)
                    .str.strip()
                    .str.casefold()
                    .isin(resolved_codes)
                )
                for _, unknown_row in unknown_df.loc[resolved_mask].iterrows():
                    code = str(unknown_row.get("unknown_code", "")).strip()
                    if not code:
                        continue
                    source_file = str(unknown_row.get("source_file", "")).strip()
                    source_row_index = str(unknown_row.get("source_row_index", "")).strip()
                    mask = (
                        final_import_df["__source_file"].astype(str).str.strip().eq(source_file)
                        & final_import_df["__source_row_index"].astype(str).str.strip().eq(source_row_index)
                    )
                    final_import_df.loc[mask, code] = fill_value_tpl.format(code=code)

                unknown_df = unknown_df.loc[~resolved_mask].reset_index(drop=True)

            if created_codes:
                run_status_box.info(
                    "Automaticky vytvořena nová vlastní pole pro kódy aplikací: "
                    + ", ".join(created_codes[:10])
                    + ("..." if len(created_codes) > 10 else "")
                )
            if create_errors:
                run_status_box.warning(
                    "Některá vlastní pole se nepodařilo vytvořit: "
                    + " | ".join(create_errors[:3])
                    + (" ..." if len(create_errors) > 3 else "")
                )
        except Exception as exc:
            run_status_box.warning(f"Automatické vytváření vlastních polí selhalo: {exc}")

    if len(final_import_df) > 0:
        bucket_series = final_import_df.get(
            "country_bucket",
            pd.Series(["EN"] * len(final_import_df), index=final_import_df.index),
        )
        import_only_df = final_import_df.drop(columns=["country_bucket", "__source_file", "__source_row_index"], errors="ignore")
        final_parts = split_by_bucket(import_only_df, bucket_series)
    else:
        final_parts = {"CZ_SK": pd.DataFrame(), "DE_AT_CH": pd.DataFrame(), "EN": pd.DataFrame()}

    duplicates_df = find_duplicates_from_stats(email_counts, email_source_files)
    duplicate_extra_rows = int((duplicates_df["count"] - 1).clip(lower=0).sum()) if len(duplicates_df) > 0 else 0

    summary_metrics = {
        "source_files_total": len(source_files),
        "source_files_processed": processed_files,
        "source_files_failed": len(file_errors),
        "input_rows_total": source_rows_total,
        "rows_after_email_processing": rows_after_email_processing,
        "invalid_email_rows": len(invalid_df),
        "unknown_program_codes": len(unknown_df),
        "duplicate_email_keys": len(duplicates_df),
        "duplicate_extra_rows": duplicate_extra_rows,
        "dedup_mode": dedup_keep,
        "dedup_removed_rows": dedup_removed_rows,
        "output_rows_CZ_SK": len(final_parts["CZ_SK"]),
        "output_rows_DE_AT_CH": len(final_parts["DE_AT_CH"]),
        "output_rows_EN": len(final_parts["EN"]),
        "output_rows_total": len(final_parts["CZ_SK"]) + len(final_parts["DE_AT_CH"]) + len(final_parts["EN"]),
    }
    summary_metrics["execution_mode"] = execution_mode

    api_contacts: list[dict[str, Any]] = []
    api_contacts_preview: list[dict[str, Any]] = []
    api_batch_results: list[dict[str, Any]] = []
    api_import_batch_results: list[dict[str, Any]] = []
    api_clear_batch_results: list[dict[str, Any]] = []
    api_contacts_sent_import = 0
    api_contacts_sent_clear = 0
    api_issues: list[dict[str, Any]] = []
    api_diff_summary: dict[str, Any] = {}
    api_clear_operations: list[dict[str, str]] = []
    api_diff_error = ""
    api_error = ""
    api_block_reason = ""
    api_status = "not_requested"
    api_resolved_list_id = ""
    api_bucket_routing_map: dict[str, str] = {}
    api_bucket_contacts_prepared: dict[str, int] = {}
    api_missing_bucket_lists_with_data: list[str] = []
    api_route_details: list[dict[str, Any]] = []
    api_ping = {}
    extra_report_frames: list[pd.DataFrame] = []

    if api_mode_enabled:
        try:
            client = SmartEmailingApiClient(
                SmartEmailingCredentials(
                    username=str(api_import_username).strip(),
                    api_key=str(api_import_key).strip(),
                    base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                ),
                read_only=bool(preview_only or execution_mode == "api_dry_run"),
            )
            if client.read_only:
                run_status_box.info(
                    "API klient běží v read-only režimu: v diff preview/dry-run jsou blokovaná všechna write API volání."
                )
            if preview_only or execution_mode == "api_dry_run":
                def _blocked_write_api_call(*_args: Any, **_kwargs: Any) -> Any:
                    raise RuntimeError(
                        "Safety guard: write volání na SmartEmailing API je v dry-run/diff preview režimu zakázané."
                    )

                # Hard safety brake: even if a future code path regresses, no write call can pass in preview/dry-run.
                client.import_contacts_canary = _blocked_write_api_call  # type: ignore[method-assign]
                client.import_contacts_batch = _blocked_write_api_call  # type: ignore[method-assign]
                client.create_custom_field = _blocked_write_api_call  # type: ignore[method-assign]
            api_ping = client.ping()
            custom_fields = client.fetch_custom_fields(
                endpoint_candidates=custom_fields_endpoint_candidates,
                search_endpoint_candidates=custom_fields_search_endpoint_candidates,
            )
            min_custom_fields = 1
            try:
                min_custom_fields = int(api_cfg.get("required_min_custom_fields", 1))
            except Exception:
                min_custom_fields = 1
            if min_custom_fields > 0 and len(custom_fields) < min_custom_fields:
                raise SmartEmailingApiError(
                    f"API vrátilo jen {len(custom_fields)} vlastních polí, minimum je {min_custom_fields}."
                )

            route_definitions: list[dict[str, Any]] = []
            if bucket_routing_enabled:
                for bucket in COUNTRY_BUCKET_KEYS:
                    raw_df = final_parts.get(bucket, pd.DataFrame())
                    if not isinstance(raw_df, pd.DataFrame) or len(raw_df) == 0:
                        continue
                    route_list_value = str(bucket_routing_list_values.get(bucket, "")).strip()
                    resolved_route_list_id = (
                        client.resolve_contact_list_id(
                            route_list_value,
                            endpoint_candidates=contact_lists_endpoint_candidates,
                            search_endpoint_candidates=contact_lists_search_endpoint_candidates,
                        )
                        if route_list_value
                        else ""
                    )
                    route_import_df = raw_df.drop(columns=["__row_order"], errors="ignore")
                    if exclude_columns_from_api_import:
                        route_import_df = route_import_df.drop(columns=exclude_columns_from_api_import, errors="ignore")
                    route_definitions.append(
                        {
                            "bucket": bucket,
                            "route_name": f"bucket:{bucket}",
                            "list_value": route_list_value,
                            "resolved_list_id": resolved_route_list_id,
                            "import_df": route_import_df,
                        }
                    )
                    if resolved_route_list_id:
                        api_bucket_routing_map[bucket] = resolved_route_list_id
                api_resolved_list_id = ""
            else:
                api_resolved_list_id = (
                    client.resolve_contact_list_id(
                        staging_list_value,
                        endpoint_candidates=contact_lists_endpoint_candidates,
                        search_endpoint_candidates=contact_lists_search_endpoint_candidates,
                    )
                    if str(staging_list_value).strip()
                    else ""
                )
                import_for_api = final_import_df.drop(
                    columns=["country_bucket", "__row_order", "__source_file", "__source_row_index"],
                    errors="ignore",
                )
                if exclude_columns_from_api_import:
                    import_for_api = import_for_api.drop(columns=exclude_columns_from_api_import, errors="ignore")
                route_definitions.append(
                    {
                        "bucket": "ALL",
                        "route_name": "single",
                        "list_value": str(staging_list_value).strip(),
                        "resolved_list_id": api_resolved_list_id,
                        "import_df": import_for_api,
                    }
                )

            for route in route_definitions:
                route_bucket = str(route.get("bucket", "")).strip() or "ALL"
                route_import_df = route.get("import_df")
                if not isinstance(route_import_df, pd.DataFrame):
                    route_import_df = pd.DataFrame()
                route_list_id = str(route.get("resolved_list_id", "")).strip()
                route_contacts, route_issues = build_api_contacts_from_import_df(
                    import_df=route_import_df,
                    api_system_field_map=api_system_field_map_for_run,
                    custom_fields=custom_fields,
                    list_id=route_list_id,
                    list_status=list_status,
                    tag=str(staging_tag).strip(),
                    strict_custom_fields=strict_custom_fields,
                    ignore_missing_custom_for_columns=ignore_missing_custom_for_columns,
                    array_custom_field_names=array_custom_field_names,
                    array_value_split_separators=array_value_split_separators,
                    managed_empty_custom_field_name_pattern=managed_empty_custom_field_name_pattern,
                    managed_custom_field_ids_allowlist=program_custom_field_allowlist_ids,
                )
                route["contacts"] = route_contacts
                route["issues"] = route_issues
                route["contacts_prepared"] = len(route_contacts)
                api_bucket_contacts_prepared[route_bucket] = int(len(route_contacts))
                if (
                    bucket_routing_enabled
                    and execution_mode in {"api_safe_import", "api_full_import"}
                    and len(route_contacts) > 0
                    and not route_list_id
                ):
                    api_missing_bucket_lists_with_data.append(route_bucket)
                api_contacts.extend(route_contacts)
                api_issues.extend(route_issues)
            api_route_details = route_definitions

            summary_metrics["api_diff_enabled"] = int(bool(diff_preflight_enabled))
            summary_metrics["api_diff_send_only_changes"] = int(bool(diff_send_only_changes))
            summary_metrics["api_diff_fallback_on_error"] = int(bool(diff_fallback_send_all_on_error))
            summary_metrics["api_diff_target_email_batch_size"] = int(max(1, diff_target_email_batch_size))
            summary_metrics["api_read_parallel_workers"] = int(max(1, api_read_parallel_workers))
            summary_metrics["api_bucket_routing_enabled"] = int(bool(bucket_routing_enabled))
            summary_metrics["api_bucket_routes_total"] = int(len(api_route_details))
            summary_metrics["api_bucket_routes_with_contacts"] = int(
                len([x for x in api_route_details if int(x.get("contacts_prepared", 0) or 0) > 0])
            )
            summary_metrics["api_bucket_routes_missing_list"] = int(len(api_missing_bucket_lists_with_data))
            summary_metrics["api_bucket_routing_lists"] = ",".join(
                [
                    f"{bucket}:{str(api_bucket_routing_map.get(bucket, '')).strip()}"
                    for bucket in COUNTRY_BUCKET_KEYS
                    if str(api_bucket_routing_map.get(bucket, "")).strip()
                ]
            )
            for bucket in COUNTRY_BUCKET_KEYS:
                summary_metrics[f"api_bucket_prepared_{bucket}"] = int(api_bucket_contacts_prepared.get(bucket, 0))
            summary_metrics["api_skip_blacklisted_contacts_enabled"] = int(bool(skip_blacklisted_contacts))
            summary_metrics["api_blacklisted_lookup_status"] = "disabled"
            summary_metrics["api_blacklisted_contacts_found"] = 0
            summary_metrics["api_blacklisted_contacts_skipped"] = 0
            summary_metrics["api_clear_removed_program_custom_fields_enabled"] = int(
                bool(clear_removed_program_custom_fields)
            )
            summary_metrics["api_diff_existing_contacts"] = 0
            summary_metrics["api_diff_new_contacts"] = 0
            summary_metrics["api_diff_updated_contacts"] = 0
            summary_metrics["api_diff_unchanged_contacts"] = 0
            summary_metrics["api_diff_filtered_out"] = 0
            summary_metrics["api_diff_removed_program_custom_fields"] = 0
            summary_metrics["api_diff_removed_nonclearable_custom_fields"] = 0
            summary_metrics["api_diff_status"] = "disabled"
            summary_metrics["api_program_allowlist_total"] = int(len(program_custom_field_allowlist_ids))
            summary_metrics["api_program_allowlist_hardguard_filtered_out"] = 0
            summary_metrics["api_program_allowlist_hardguard_prefixes"] = ",".join(
                [str(x).strip() for x in clear_allowed_name_prefixes if str(x).strip()]
            )

            available_custom_field_ids = {
                str(field.get("id", "")).strip()
                for field in custom_fields
                if str(field.get("id", "")).strip()
            }
            custom_field_name_by_id = {
                str(field.get("id", "")).strip(): str(field.get("name", "")).strip()
                for field in custom_fields
                if str(field.get("id", "")).strip()
            }
            program_custom_field_ids_for_clear: set[str] = set()
            if clear_removed_program_custom_fields:
                program_custom_field_ids_for_clear = (
                    set(program_custom_field_allowlist_ids) & set(available_custom_field_ids)
                )
                hardguard_prefixes = [
                    str(x).strip().casefold()
                    for x in clear_allowed_name_prefixes
                    if str(x).strip()
                ]
                if hardguard_prefixes and program_custom_field_ids_for_clear:
                    before_hardguard = len(program_custom_field_ids_for_clear)
                    program_custom_field_ids_for_clear = {
                        field_id
                        for field_id in program_custom_field_ids_for_clear
                        if any(
                            str(custom_field_name_by_id.get(field_id, "")).strip().casefold().startswith(prefix)
                            for prefix in hardguard_prefixes
                        )
                    }
                    filtered_out = max(0, before_hardguard - len(program_custom_field_ids_for_clear))
                    summary_metrics["api_program_allowlist_hardguard_filtered_out"] = int(filtered_out)
                    if filtered_out > 0:
                        run_status_box.warning(
                            "Hard-guard pro mazání odfiltroval část allowlistu podle prefixů názvů polí. "
                            f"Odfiltrováno ID: {filtered_out}."
                        )
                missing_allowlist_ids = sorted(
                    set(program_custom_field_allowlist_ids) - set(available_custom_field_ids)
                )
                summary_metrics["api_program_allowlist_active"] = int(len(program_custom_field_ids_for_clear))
                summary_metrics["api_program_allowlist_missing"] = int(len(missing_allowlist_ids))
                if not program_custom_field_ids_for_clear:
                    run_status_box.warning(
                        "Mazání odebraných kódů aplikací je zapnuté, ale allowlist aplikačních polí je prázdný "
                        "nebo neodpovídá aktuálním custom fields v SmartEmailingu."
                    )
                elif missing_allowlist_ids:
                    run_status_box.warning(
                        "Část allowlist ID nebyla nalezena v aktuálním API schématu custom fields. "
                        f"Neaktivních ID: {len(missing_allowlist_ids)}."
                    )
            else:
                summary_metrics["api_program_allowlist_active"] = 0
                summary_metrics["api_program_allowlist_missing"] = 0

            if skip_blacklisted_contacts and api_contacts:
                try:
                    import_email_keys_for_blacklist = {
                        normalize_email_key(contact.get("emailaddress", ""))
                        for contact in api_contacts
                        if normalize_email_key(contact.get("emailaddress", ""))
                    }
                    blacklisted_email_keys = client.fetch_blacklisted_email_keys(
                        email_keys=import_email_keys_for_blacklist,
                        endpoint_candidates=blacklist_contacts_endpoint_candidates,
                        search_endpoint_candidates=blacklist_contacts_search_endpoint_candidates,
                        max_workers=api_read_parallel_workers,
                    )
                    summary_metrics["api_blacklisted_lookup_status"] = "ok"
                    summary_metrics["api_blacklisted_contacts_found"] = int(len(blacklisted_email_keys))
                    if blacklisted_email_keys:
                        blacklisted_emails_preview = sorted(
                            {
                                normalize_scalar_for_diff(contact.get("emailaddress", ""))
                                for contact in api_contacts
                                if normalize_email_key(contact.get("emailaddress", "")) in blacklisted_email_keys
                            }
                        )[:200]
                        before_blacklist_filter = len(api_contacts)
                        api_contacts = [
                            contact
                            for contact in api_contacts
                            if normalize_email_key(contact.get("emailaddress", "")) not in blacklisted_email_keys
                        ]
                        for route in api_route_details:
                            route_contacts = route.get("contacts", [])
                            if not isinstance(route_contacts, list):
                                route_contacts = []
                            route_contacts = [
                                contact
                                for contact in route_contacts
                                if normalize_email_key(contact.get("emailaddress", "")) not in blacklisted_email_keys
                            ]
                            route["contacts"] = route_contacts
                            route["contacts_prepared"] = int(len(route_contacts))
                            route_bucket = str(route.get("bucket", "")).strip() or "ALL"
                            api_bucket_contacts_prepared[route_bucket] = int(len(route_contacts))
                        skipped_blacklisted = before_blacklist_filter - len(api_contacts)
                        summary_metrics["api_blacklisted_contacts_skipped"] = int(skipped_blacklisted)
                        extra_report_frames.append(
                            pd.DataFrame(
                                {
                                    "type": "api_blacklisted_skipped",
                                    "row_index": "",
                                    "detail": "kontakt přeskočen (blacklisted=1)",
                                    "email_raw": blacklisted_emails_preview,
                                    "company": "",
                                    "source_file": "",
                                    "source_row_index": "",
                                }
                            )
                        )
                        run_status_box.info(
                            "Blacklist filtr: přeskočeno kontaktů "
                            f"{skipped_blacklisted} (blacklisted=1). "
                            + (
                                f"Např.: {', '.join(blacklisted_emails_preview[:5])}"
                                if blacklisted_emails_preview
                                else ""
                            )
                        )
                except Exception as exc:
                    summary_metrics["api_blacklisted_lookup_status"] = "error"
                    summary_metrics["api_blacklisted_lookup_error"] = str(exc)
                    run_status_box.warning(
                        f"Nepodařilo se ověřit blacklist kontakty přes API, pokračuji bez filtru. Detail: {exc}"
                    )

            api_contacts = [
                contact
                for route in api_route_details
                for contact in (
                    route.get("contacts", [])
                    if isinstance(route.get("contacts", []), list)
                    else []
                )
            ]

            if diff_preflight_enabled:
                try:
                    routes_for_diff = [
                        route
                        for route in api_route_details
                        if isinstance(route.get("contacts", []), list) and len(route.get("contacts", [])) > 0
                    ]
                    missing_diff_lists = [
                        str(route.get("bucket", "")).strip() or "ALL"
                        for route in routes_for_diff
                        if not str(route.get("resolved_list_id", "")).strip()
                    ]
                    if missing_diff_lists:
                        summary_metrics["api_diff_status"] = "skipped_no_list"
                        if bucket_routing_enabled:
                            run_status_box.warning(
                                "Diff preflight je zapnutý, ale některé buckety nemají vybraný list: "
                                + ", ".join(missing_diff_lists)
                            )
                        else:
                            run_status_box.warning("Diff preflight je zapnutý, ale není vybraný staging seznam.")
                    else:
                        aggregated_new_contacts: list[dict[str, Any]] = []
                        aggregated_updated_contacts: list[dict[str, Any]] = []
                        aggregated_unchanged_contacts: list[dict[str, Any]] = []
                        aggregated_contacts_to_send: list[dict[str, Any]] = []
                        aggregated_updated_details: list[dict[str, Any]] = []
                        aggregated_clear_operations: list[dict[str, Any]] = []
                        aggregated_existing_total = 0
                        aggregated_existing_with_custom_fields = 0
                        aggregated_removed_nonclearable_custom_fields_total = 0
                        aggregated_compare_enabled = True
                        route_diff_summaries: list[dict[str, Any]] = []

                        for route in routes_for_diff:
                            route_bucket = str(route.get("bucket", "")).strip() or "ALL"
                            route_list_id = str(route.get("resolved_list_id", "")).strip()
                            route_contacts = route.get("contacts", [])
                            if not isinstance(route_contacts, list):
                                route_contacts = []

                            import_email_keys = {
                                normalize_email_key(contact.get("emailaddress", ""))
                                for contact in route_contacts
                                if normalize_email_key(contact.get("emailaddress", ""))
                            }
                            existing_contacts = client.fetch_contacts_in_list(
                                list_id=route_list_id,
                                page_limit=diff_page_limit,
                                max_pages=diff_max_pages,
                                endpoint_templates=contacts_in_list_endpoint_templates,
                                search_endpoint_templates=contacts_in_list_search_endpoint_templates,
                                detail_endpoint_templates=contacts_detail_endpoint_templates,
                                enrich_only_email_keys=import_email_keys,
                                contacts_endpoint_candidates=contacts_endpoint_candidates,
                                contacts_search_endpoint_candidates=contacts_search_endpoint_candidates,
                                custom_field_values_endpoint_candidates=contact_custom_field_values_endpoint_candidates,
                                custom_field_values_search_endpoint_candidates=contact_custom_field_values_search_endpoint_candidates,
                                target_email_batch_size=diff_target_email_batch_size,
                                read_parallel_workers=api_read_parallel_workers,
                                prefer_targeted_search=True,
                            )
                            route_diff_summary = diff_api_contacts(
                                import_contacts=route_contacts,
                                existing_contacts=existing_contacts,
                                array_value_split_separators=array_value_split_separators,
                                clearable_custom_field_ids=program_custom_field_ids_for_clear,
                            )
                            route["diff_summary"] = route_diff_summary

                            route_new_contacts = list(route_diff_summary.get("new_contacts", []))
                            route_updated_contacts = list(route_diff_summary.get("updated_contacts", []))
                            route_unchanged_contacts = list(route_diff_summary.get("unchanged_contacts", []))
                            route_contacts_to_send = (
                                list(route_diff_summary.get("contacts_to_send", []))
                                if (
                                    diff_send_only_changes
                                    and bool(route_diff_summary.get("custom_fields_compare_enabled", True))
                                )
                                else list(route_contacts)
                            )
                            route_compare_enabled = bool(route_diff_summary.get("custom_fields_compare_enabled", True))

                            route["contacts"] = route_contacts_to_send
                            route["contacts_prepared"] = int(len(route_contacts_to_send))
                            if route_bucket in COUNTRY_BUCKET_KEYS:
                                api_bucket_contacts_prepared[route_bucket] = int(len(route_contacts_to_send))

                            route_clear_ops = [
                                op
                                for op in route_diff_summary.get("clear_operations", [])
                                if isinstance(op, dict)
                                and str(op.get("field_id", "")).strip()
                                and str(op.get("email_key", "")).strip()
                            ]
                            route_summary = {
                                "bucket": route_bucket,
                                "list_id": route_list_id,
                                "existing": int(route_diff_summary.get("existing_total", 0)),
                                "new": len(route_new_contacts),
                                "updated": len(route_updated_contacts),
                                "unchanged": len(route_unchanged_contacts),
                                "to_send": len(route_contacts_to_send),
                                "compare_enabled": int(route_compare_enabled),
                                "removed_program_custom_fields": len(route_clear_ops),
                            }
                            route_diff_summaries.append(route_summary)

                            aggregated_existing_total += int(route_diff_summary.get("existing_total", 0))
                            aggregated_existing_with_custom_fields += int(
                                route_diff_summary.get("existing_contacts_with_custom_fields", 0)
                            )
                            aggregated_removed_nonclearable_custom_fields_total += int(
                                route_diff_summary.get("removed_nonclearable_custom_fields_total", 0)
                            )
                            aggregated_compare_enabled = aggregated_compare_enabled and route_compare_enabled
                            aggregated_new_contacts.extend(route_new_contacts)
                            aggregated_updated_contacts.extend(route_updated_contacts)
                            aggregated_unchanged_contacts.extend(route_unchanged_contacts)
                            aggregated_contacts_to_send.extend(route_contacts_to_send)
                            aggregated_updated_details.extend(list(route_diff_summary.get("updated_details", [])))
                            aggregated_clear_operations.extend(route_clear_ops)

                        api_contacts = list(aggregated_contacts_to_send)
                        api_diff_summary = {
                            "existing_total": int(aggregated_existing_total),
                            "new_contacts": aggregated_new_contacts,
                            "updated_contacts": aggregated_updated_contacts,
                            "unchanged_contacts": aggregated_unchanged_contacts,
                            "contacts_to_send": aggregated_contacts_to_send,
                            "updated_details": aggregated_updated_details,
                            "clear_operations": aggregated_clear_operations,
                            "removed_nonclearable_custom_fields_total": int(
                                aggregated_removed_nonclearable_custom_fields_total
                            ),
                            "custom_fields_compare_enabled": bool(aggregated_compare_enabled),
                            "existing_contacts_with_custom_fields": int(aggregated_existing_with_custom_fields),
                            "route_summaries": route_diff_summaries,
                        }

                        summary_metrics["api_diff_status"] = "ok"
                        summary_metrics["api_diff_existing_contacts"] = int(api_diff_summary.get("existing_total", 0))
                        summary_metrics["api_diff_new_contacts"] = int(len(api_diff_summary.get("new_contacts", [])))
                        summary_metrics["api_diff_updated_contacts"] = int(len(api_diff_summary.get("updated_contacts", [])))
                        summary_metrics["api_diff_unchanged_contacts"] = int(len(api_diff_summary.get("unchanged_contacts", [])))
                        summary_metrics["api_diff_filtered_out"] = int(
                            len(api_diff_summary.get("unchanged_contacts", []))
                            if diff_send_only_changes and bool(api_diff_summary.get("custom_fields_compare_enabled", True))
                            else 0
                        )
                        extra_report_frames.append(
                            pd.DataFrame(
                                {
                                    "type": "api_diff_summary",
                                    "row_index": "",
                                    "detail": (
                                        f"existing={summary_metrics['api_diff_existing_contacts']}, "
                                        f"new={summary_metrics['api_diff_new_contacts']}, "
                                        f"updated={summary_metrics['api_diff_updated_contacts']}, "
                                        f"unchanged={summary_metrics['api_diff_unchanged_contacts']}"
                                    ),
                                    "email_raw": "",
                                    "company": "",
                                    "source_file": "",
                                    "source_row_index": "",
                                },
                                index=[0],
                            )
                        )
                        if bucket_routing_enabled and route_diff_summaries:
                            route_diff_rows = [
                                f"{row['bucket']}: list={row['list_id']} existing={row['existing']} new={row['new']} "
                                f"updated={row['updated']} unchanged={row['unchanged']} send={row['to_send']}"
                                for row in route_diff_summaries
                            ]
                            extra_report_frames.append(
                                pd.DataFrame(
                                    {
                                        "type": "api_diff_route_summary",
                                        "row_index": "",
                                        "detail": route_diff_rows[:200],
                                        "email_raw": "",
                                        "company": "",
                                        "source_file": "",
                                        "source_row_index": "",
                                    }
                                )
                            )

                        updated_details = list(api_diff_summary.get("updated_details", []))
                        if updated_details:
                            extra_report_frames.append(
                                pd.DataFrame(
                                    {
                                        "type": "api_diff_updated_fields",
                                        "row_index": "",
                                        "detail": [
                                            ", ".join([str(x).strip() for x in row.get("changed_fields", []) if str(x).strip()])
                                            for row in updated_details[:200]
                                        ],
                                        "email_raw": [str(row.get("email", "")).strip() for row in updated_details[:200]],
                                        "company": "",
                                        "source_file": "",
                                        "source_row_index": "",
                                    }
                                )
                            )

                        clear_operations = [
                            op
                            for op in api_diff_summary.get("clear_operations", [])
                            if isinstance(op, dict)
                            and str(op.get("field_id", "")).strip()
                            and str(op.get("email_key", "")).strip()
                        ]
                        summary_metrics["api_diff_removed_program_custom_fields"] = int(len(clear_operations))
                        summary_metrics["api_diff_removed_nonclearable_custom_fields"] = int(
                            api_diff_summary.get("removed_nonclearable_custom_fields_total", 0)
                        )
                        if (
                            clear_removed_program_custom_fields
                            and bool(api_diff_summary.get("custom_fields_compare_enabled", True))
                            and clear_operations
                        ):
                            api_clear_operations = list(clear_operations)
                            run_status_box.info(
                                "Diff detekoval odebrané kódy aplikací: "
                                f"pro vyčištění bude použito {len(clear_operations)} API update operací "
                                "s prázdnou hodnotou custom fieldu (jen hodnoty na konkrétních kontaktech)."
                            )
                        else:
                            api_clear_operations = []
                        if clear_removed_program_custom_fields and int(
                            summary_metrics.get("api_diff_removed_nonclearable_custom_fields", 0)
                        ) > 0:
                            run_status_box.warning(
                                "Diff detekoval i odebrané custom fields mimo allowlist aplikačních polí. "
                                f"Tyto změny se nemažou: {int(summary_metrics.get('api_diff_removed_nonclearable_custom_fields', 0))}."
                            )

                        if execution_mode in {"api_safe_import", "api_full_import"}:
                            run_status_box.info(
                                "Diff preflight: "
                                f"nové={summary_metrics['api_diff_new_contacts']}, "
                                f"aktualizace={summary_metrics['api_diff_updated_contacts']}, "
                                f"beze změny={summary_metrics['api_diff_unchanged_contacts']}."
                                + (
                                    " Odesílám jen nové+změněné."
                                    if diff_send_only_changes and bool(api_diff_summary.get("custom_fields_compare_enabled", True))
                                    else " Odesílám vše."
                                )
                            )
                            if (
                                not bool(api_diff_summary.get("custom_fields_compare_enabled", True))
                                and int(api_diff_summary.get("matched_existing_contacts", 0)) > 0
                            ):
                                run_status_box.warning(
                                    "U existujících kontaktů se nepodařilo získat custom fields "
                                    "(nebo je kontakty nemají). Porovnání custom fields bylo v diffu přeskočeno "
                                    "a z bezpečnostního fallbacku se odesílají všechny připravené kontakty."
                                )
                except Exception as exc:
                    api_diff_error = str(exc)
                    summary_metrics["api_diff_status"] = "error"
                    summary_metrics["api_diff_error"] = api_diff_error
                    extra_report_frames.append(
                        pd.DataFrame(
                            {
                                "type": "api_diff_error",
                                "row_index": "",
                                "detail": api_diff_error,
                                "email_raw": "",
                                "company": "",
                                "source_file": "",
                                "source_row_index": "",
                            },
                            index=[0],
                        )
                    )
                    if execution_mode in {"api_safe_import", "api_full_import"}:
                        if diff_fallback_send_all_on_error:
                            run_status_box.warning(
                                f"Diff preflight selhal ({api_diff_error}), pokračuji fallbackem bez diff filtru."
                            )
                        else:
                            raise SmartEmailingApiError(
                                f"Diff preflight selhal a fallback je vypnutý: {api_diff_error}"
                            ) from exc
            api_contacts_preview = api_contacts[:50]
            summary_metrics["api_bucket_routes_with_contacts"] = int(
                len(
                    [
                        x
                        for x in api_route_details
                        if isinstance(x.get("contacts", []), list) and len(x.get("contacts", [])) > 0
                    ]
                )
            )
            summary_metrics["api_bucket_routes_missing_list"] = int(len(api_missing_bucket_lists_with_data))
            for bucket in COUNTRY_BUCKET_KEYS:
                summary_metrics[f"api_bucket_prepared_{bucket}"] = int(api_bucket_contacts_prepared.get(bucket, 0))
            summary_metrics["api_ping_status"] = str(api_ping.get("status", "")) if isinstance(api_ping, dict) else ""
            summary_metrics["api_custom_fields"] = len(custom_fields)
            summary_metrics["api_contacts_prepared"] = len(api_contacts)
            summary_metrics["api_payload_issues"] = len(api_issues)
            summary_metrics["api_staging_list_id"] = (
                api_resolved_list_id
                if not bucket_routing_enabled
                else ",".join(
                    [
                        f"{bucket}:{str(api_bucket_routing_map.get(bucket, '')).strip()}"
                        for bucket in COUNTRY_BUCKET_KEYS
                        if str(api_bucket_routing_map.get(bucket, "")).strip()
                    ]
                )
            )
            summary_metrics["api_bucket_routing_lists_resolved"] = ",".join(
                [
                    f"{bucket}:{str(api_bucket_routing_map.get(bucket, '')).strip()}"
                    for bucket in COUNTRY_BUCKET_KEYS
                    if str(api_bucket_routing_map.get(bucket, "")).strip()
                ]
            )
            summary_metrics["api_staging_tag"] = str(staging_tag).strip()

            if api_issues:
                extra_report_frames.append(api_issues_to_report_df(api_issues))

            if preview_only:
                diff_status = str(summary_metrics.get("api_diff_status", "")).strip()
                preview_rows = build_diff_preview_rows(api_diff_summary, limit=200) if diff_status == "ok" else []
                preview_error = ""
                if diff_status == "error":
                    preview_error = f"Diff preview selhal: {api_diff_error or 'neznámá chyba'}"
                elif diff_status == "skipped_no_list":
                    if bucket_routing_enabled:
                        preview_error = (
                            "Diff preview nelze spočítat: některé buckety s daty nemají vybraný cílový list."
                        )
                    else:
                        preview_error = "Diff preview nelze spočítat: není vybraný staging seznam."
                elif diff_status == "disabled":
                    preview_error = "Diff preview je vypnutý. Zapni porovnání před importem (diff)."

                st.session_state["diff_preview_rows"] = preview_rows
                st.session_state["diff_preview_detail_map"] = (
                    build_diff_preview_detail_map(api_diff_summary) if diff_status == "ok" else {}
                )
                st.session_state.pop("diff_preview_editor", None)
                if (
                    str(st.session_state.get("diff_preview_selected_email", "")).strip()
                    and normalize_email_key(st.session_state.get("diff_preview_selected_email", ""))
                    not in st.session_state["diff_preview_detail_map"]
                ):
                    st.session_state["diff_preview_selected_email"] = ""
                st.session_state["diff_preview_summary"] = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "list_id": api_resolved_list_id,
                    "bucket_routing": {
                        bucket: str(api_bucket_routing_map.get(bucket, "")).strip()
                        for bucket in COUNTRY_BUCKET_KEYS
                        if str(api_bucket_routing_map.get(bucket, "")).strip()
                    },
                    "diff_status": diff_status,
                    "existing_contacts": int(summary_metrics.get("api_diff_existing_contacts", 0)),
                    "new_contacts": int(summary_metrics.get("api_diff_new_contacts", 0)),
                    "updated_contacts": int(summary_metrics.get("api_diff_updated_contacts", 0)),
                    "unchanged_contacts": int(summary_metrics.get("api_diff_unchanged_contacts", 0)),
                    "contacts_to_send": len(api_contacts),
                    "send_only_changes": bool(diff_send_only_changes),
                    "custom_fields_compare_enabled": bool(api_diff_summary.get("custom_fields_compare_enabled", True)),
                    "matched_existing_contacts": int(api_diff_summary.get("matched_existing_contacts", 0)),
                    "existing_contacts_with_custom_fields": int(api_diff_summary.get("existing_contacts_with_custom_fields", 0)),
                    "removed_program_custom_fields": int(
                        summary_metrics.get("api_diff_removed_program_custom_fields", 0)
                    ),
                    "clear_removed_program_custom_fields_enabled": bool(clear_removed_program_custom_fields),
                    "route_summaries": list(api_diff_summary.get("route_summaries", []))
                    if isinstance(api_diff_summary.get("route_summaries", []), list)
                    else [],
                }
                st.session_state["diff_preview_error"] = preview_error

                if preview_error:
                    run_status_box.warning(preview_error)
                else:
                    run_status_box.success(
                        "Diff preview připraven: "
                        f"nové={summary_metrics['api_diff_new_contacts']}, "
                        f"aktualizace={summary_metrics['api_diff_updated_contacts']}, "
                        f"beze změny={summary_metrics['api_diff_unchanged_contacts']}."
                    )
                st.rerun()

            api_missing_bucket_lists_with_data = sorted(
                {
                    str(route.get("bucket", "")).strip() or "ALL"
                    for route in api_route_details
                    if isinstance(route.get("contacts", []), list)
                    and len(route.get("contacts", [])) > 0
                    and not str(route.get("resolved_list_id", "")).strip()
                }
            )
            summary_metrics["api_bucket_routes_missing_list"] = int(len(api_missing_bucket_lists_with_data))

            if execution_mode == "api_dry_run":
                api_status = "dry_run_ok"
            else:
                max_contacts_limit = api_max_contacts_safe if execution_mode == "api_safe_import" else api_max_contacts_full
                block_reason = ""
                if len(api_contacts) == 0:
                    block_reason = "API import je blokovaný: žádné kontakty k odeslání."
                elif len(api_contacts) > max_contacts_limit:
                    block_reason = (
                        f"API import je blokovaný: počet kontaktů ({len(api_contacts)}) "
                        f"překračuje limit režimu ({max_contacts_limit})."
                    )
                elif execution_mode == "api_safe_import":
                    if not safe_confirm_effective:
                        block_reason = "Bezpečný import je blokovaný: chybí potvrzení dopadu."
                    elif bucket_routing_enabled and api_missing_bucket_lists_with_data:
                        block_reason = (
                            "Bezpečný import je blokovaný: chybí cílový list pro buckety s daty: "
                            + ", ".join(sorted(set(api_missing_bucket_lists_with_data)))
                            + "."
                        )
                    elif (not bucket_routing_enabled) and (not api_resolved_list_id):
                        block_reason = "Bezpečný import je blokovaný: vyber staging seznam."
                elif execution_mode == "api_full_import":
                    full_phrase_required = str(
                        api_cfg.get("required_confirmation_phrase_full", "FULL IMPORT DO SMARTEMAILINGU")
                    )
                    approval_code = str(st.session_state.get("full_import_approval_code", "")).strip()
                    if not full_confirm:
                        block_reason = "Plný import je blokovaný: chybí potvrzení dopadu."
                    elif full_phrase_input.strip() != full_phrase_required:
                        block_reason = "Plný import je blokovaný: špatná potvrzovací fráze."
                    elif not full_operator.strip() or not full_approver.strip():
                        block_reason = "Plný import je blokovaný: vyplň operátora i schvalovatele."
                    elif full_operator.strip().casefold() == full_approver.strip().casefold():
                        block_reason = "Plný import je blokovaný: operátor a schvalovatel musí být různé osoby (4 oči)."
                    elif full_second_approval_input.strip() != approval_code:
                        block_reason = "Plný import je blokovaný: neplatný schvalovací kód (4 oči)."
                    elif bucket_routing_enabled and api_missing_bucket_lists_with_data:
                        block_reason = (
                            "Plný import je blokovaný: chybí cílový list pro buckety s daty: "
                            + ", ".join(sorted(set(api_missing_bucket_lists_with_data)))
                            + "."
                        )
                    elif (not bucket_routing_enabled) and (not api_resolved_list_id):
                        block_reason = "Plný import je blokovaný: vyber staging seznam."

                if block_reason:
                    api_status = "blocked"
                    api_block_reason = block_reason
                    st.session_state["pending_api_import_confirmation"] = {}
                    st.session_state["pending_api_import_confirmation_fingerprint"] = ""
                    extra_report_frames.append(
                        pd.DataFrame(
                            {
                                "type": "api_blocked",
                                "row_index": "",
                                "detail": block_reason,
                                "email_raw": "",
                                "company": "",
                                "source_file": "",
                                "source_row_index": "",
                            },
                            index=[0],
                        )
                    )
                    run_status_box.warning(block_reason)
                else:
                    if execution_mode in {"api_safe_import", "api_full_import"}:
                        import_confirmation_fingerprint = compute_import_confirmation_fingerprint(
                            execution_mode=execution_mode,
                            list_id=api_resolved_list_id,
                            tag=str(staging_tag).strip(),
                            canary_size=api_canary_size,
                            batch_size=api_batch_size,
                            max_contacts_limit=max_contacts_limit,
                            contacts=api_contacts,
                            bucket_routing=(
                                {
                                    bucket: str(api_bucket_routing_map.get(bucket, "")).strip()
                                    for bucket in COUNTRY_BUCKET_KEYS
                                    if str(api_bucket_routing_map.get(bucket, "")).strip()
                                }
                                if bucket_routing_enabled
                                else {}
                            ),
                        )
                        approved_import_fingerprint = str(
                            st.session_state.get("approved_api_import_confirmation_fingerprint", "")
                        ).strip()
                        if approved_import_fingerprint != import_confirmation_fingerprint:
                            st.session_state["pending_api_import_confirmation"] = build_import_confirmation_summary(
                                execution_mode=execution_mode,
                                list_id=api_resolved_list_id,
                                tag=str(staging_tag).strip(),
                                canary_size=api_canary_size,
                                batch_size=api_batch_size,
                                max_contacts_limit=max_contacts_limit,
                                contacts=api_contacts,
                                issues_count=len(api_issues),
                                diff_existing_contacts=(
                                    int(summary_metrics.get("api_diff_existing_contacts", 0))
                                    if str(summary_metrics.get("api_diff_status", "")).strip() == "ok"
                                    else None
                                ),
                                diff_new_contacts=(
                                    int(summary_metrics.get("api_diff_new_contacts", 0))
                                    if str(summary_metrics.get("api_diff_status", "")).strip() == "ok"
                                    else None
                                ),
                                diff_updated_contacts=(
                                    int(summary_metrics.get("api_diff_updated_contacts", 0))
                                    if str(summary_metrics.get("api_diff_status", "")).strip() == "ok"
                                    else None
                                ),
                                diff_unchanged_contacts=(
                                    int(summary_metrics.get("api_diff_unchanged_contacts", 0))
                                    if str(summary_metrics.get("api_diff_status", "")).strip() == "ok"
                                    else None
                                ),
                                diff_removed_program_custom_fields=(
                                    int(summary_metrics.get("api_diff_removed_program_custom_fields", 0))
                                    if str(summary_metrics.get("api_diff_status", "")).strip() == "ok"
                                    else None
                                ),
                                clear_removed_program_custom_fields_enabled=bool(
                                    clear_removed_program_custom_fields
                                ),
                                bucket_routing=(
                                    {
                                        bucket: str(api_bucket_routing_map.get(bucket, "")).strip()
                                        for bucket in COUNTRY_BUCKET_KEYS
                                        if str(api_bucket_routing_map.get(bucket, "")).strip()
                                    }
                                    if bucket_routing_enabled
                                    else {}
                                ),
                                bucket_contacts=(
                                    {
                                        str(route.get("bucket", "")).strip() or "ALL": int(
                                            route.get("contacts_prepared", 0) or 0
                                        )
                                        for route in api_route_details
                                    }
                                    if api_route_details
                                    else {}
                                ),
                            )
                            st.session_state["pending_api_import_confirmation_fingerprint"] = import_confirmation_fingerprint
                            run_status_box.warning(
                                "Před odesláním do SmartEmailingu API potvrď souhrn importu "
                                "(zobrazeno nad tlačítkem Spustit zpracování)."
                            )
                            st.rerun()
                        st.session_state["approved_api_import_confirmation_fingerprint"] = ""

                    api_batch_results = []
                    route_batches_total = 0
                    routes_for_send = [
                        route
                        for route in api_route_details
                        if isinstance(route.get("contacts", []), list) and len(route.get("contacts", [])) > 0
                    ]
                    if not routes_for_send:
                        routes_for_send = [
                            {
                                "bucket": "ALL",
                                "route_name": "single",
                                "resolved_list_id": api_resolved_list_id,
                                "contacts": list(api_contacts),
                            }
                        ]
                    for route in routes_for_send:
                        route_bucket = str(route.get("bucket", "")).strip() or "ALL"
                        route_name = str(route.get("route_name", "")).strip() or route_bucket
                        route_list_id = str(route.get("resolved_list_id", "")).strip()
                        route_contacts = route.get("contacts", [])
                        if not isinstance(route_contacts, list) or not route_contacts:
                            continue
                        batch_results = client.import_contacts_canary(
                            contacts=route_contacts,
                            canary_size=api_canary_size,
                            batch_size=api_batch_size,
                            update_existing=True,
                            skip_invalid_contacts=True,
                            endpoint_candidates=import_endpoint_candidates,
                        )
                        for x in batch_results:
                            route_batches_total += 1
                            api_batch_results.append(
                                {
                                    "endpoint": x.endpoint,
                                    "payload_variant": x.payload_variant,
                                    "operation": "import",
                                    "sent_contacts": x.sent_contacts,
                                    "batch_index": route_batches_total,
                                    "route_batch_index": x.batch_index,
                                    "canary": x.canary,
                                    "started_at": x.started_at,
                                    "finished_at": x.finished_at,
                                    "response": x.response,
                                    "route": route_name,
                                    "route_bucket": route_bucket,
                                    "route_list_id": route_list_id,
                                }
                            )
                    api_status = "import_ok"
                    summary_metrics["api_batches"] = len(api_batch_results)
                    summary_metrics["api_contacts_sent"] = int(sum(x["sent_contacts"] for x in api_batch_results))
                    summary_metrics["api_custom_field_clear_requested"] = int(len(api_clear_operations))
                    summary_metrics["api_custom_field_clear_done"] = 0
                    summary_metrics["api_custom_field_clear_errors"] = 0
                    if clear_removed_program_custom_fields and api_clear_operations:
                        clear_errors: list[str] = []
                        clear_contacts_by_email: dict[str, dict[str, Any]] = {}
                        for op in api_clear_operations:
                            field_id = str(op.get("field_id", "")).strip()
                            email = str(op.get("email", "")).strip()
                            contact_id = str(op.get("contact_id", "")).strip()
                            email_key = normalize_email_key(email)
                            if not email_key:
                                clear_errors.append(
                                    f"{contact_id or 'unknown'}: chybí email pro customfield {field_id}"
                                )
                                continue
                            if not field_id:
                                clear_errors.append(f"{email}: chybí field_id pro clear operaci")
                                continue
                            current = clear_contacts_by_email.get(email_key)
                            if current is None:
                                current = {
                                    "email": email,
                                    "contact_id": contact_id,
                                    "field_ids": set(),
                                }
                                clear_contacts_by_email[email_key] = current
                            current["field_ids"].add(field_id)

                        clear_requested_fields = int(
                            sum(len(v.get("field_ids", set())) for v in clear_contacts_by_email.values())
                        )
                        summary_metrics["api_custom_field_clear_requested"] = clear_requested_fields
                        cleared_fields = 0
                        clear_calls_ok = 0
                        for email_key, item in clear_contacts_by_email.items():
                            email = str(item.get("email", "")).strip()
                            contact_id = str(item.get("contact_id", "")).strip()
                            field_ids = sorted(
                                {
                                    str(x).strip()
                                    for x in item.get("field_ids", set())
                                    if str(x).strip()
                                }
                            )
                            if not email or not field_ids:
                                continue
                            clear_contact_payload = {
                                "emailaddress": email,
                                "customfields": [{"id": field_id, "value": ""} for field_id in field_ids],
                            }
                            try:
                                response_payload, endpoint, payload_variant = client.import_contacts_batch(
                                    contacts=[clear_contact_payload],
                                    update_existing=True,
                                    skip_invalid_contacts=True,
                                    endpoint_candidates=import_endpoint_candidates,
                                )
                                clear_calls_ok += 1
                                cleared_fields += len(field_ids)
                                api_batch_results.append(
                                    {
                                        "endpoint": endpoint,
                                        "payload_variant": f"{payload_variant}|clear_customfield_empty_value",
                                        "operation": "clear_customfield_empty_value",
                                        "sent_contacts": 1,
                                        "batch_index": len(api_batch_results) + 1,
                                        "canary": False,
                                        "started_at": "",
                                        "finished_at": "",
                                        "response": response_payload,
                                    }
                                )
                            except Exception as exc:
                                clear_errors.append(
                                    f"{email or contact_id or email_key}: nepodařilo se vyčistit customfieldy "
                                    f"{', '.join(field_ids)} přes API import ({exc})"
                                )
                        summary_metrics["api_batches_clear"] = int(clear_calls_ok)
                        summary_metrics["api_custom_field_clear_done"] = int(cleared_fields)
                        summary_metrics["api_custom_field_clear_errors"] = int(len(clear_errors))
                        if clear_errors:
                            api_status = "import_ok_clear_failed"
                            summary_metrics["api_custom_field_clear_error_detail"] = str(clear_errors[0])
                            extra_report_frames.append(
                                pd.DataFrame(
                                    {
                                        "type": "api_custom_field_clear_error",
                                        "row_index": "",
                                        "detail": clear_errors[:200],
                                        "email_raw": "",
                                        "company": "",
                                        "source_file": "",
                                        "source_row_index": "",
                                    }
                                )
                            )
                            run_status_box.warning(
                                "Mazání odebraných kódů aplikací částečně selhalo. "
                                f"První chyba: {clear_errors[0]}"
                            )
                        else:
                            run_status_box.info(
                                "Vyčištěné hodnoty odebraných kódů aplikací "
                                f"(přes API import s prázdnou hodnotou): {cleared_fields} "
                                f"(kontaktových volání: {clear_calls_ok})."
                            )

        except Exception as exc:
            api_status = "failed"
            api_error = str(exc)
            summary_metrics["api_error"] = api_error
            extra_report_frames.append(
                pd.DataFrame(
                    {
                        "type": "api_error",
                        "row_index": "",
                        "detail": api_error,
                        "email_raw": "",
                        "company": "",
                        "source_file": "",
                        "source_row_index": "",
                    },
                    index=[0],
                )
            )

    if api_mode_enabled and api_batch_results:
        api_import_batch_results = [
            x
            for x in api_batch_results
            if str(x.get("operation", "import")).strip() != "clear_customfield_empty_value"
        ]
        api_clear_batch_results = [
            x
            for x in api_batch_results
            if str(x.get("operation", "")).strip() == "clear_customfield_empty_value"
        ]
    else:
        api_import_batch_results = []
        api_clear_batch_results = []

    api_contacts_sent_import = int(sum(int(x.get("sent_contacts", 0) or 0) for x in api_import_batch_results))
    api_contacts_sent_clear = int(sum(int(x.get("sent_contacts", 0) or 0) for x in api_clear_batch_results))
    summary_metrics["api_contacts_sent"] = api_contacts_sent_import
    summary_metrics["api_contacts_sent_clear"] = api_contacts_sent_clear
    summary_metrics["api_batches"] = len(api_import_batch_results)
    summary_metrics["api_batches_clear"] = len(api_clear_batch_results)

    summary_metrics["api_status"] = api_status

    if api_mode_enabled:
        if api_status == "import_ok":
            run_status_box.success(
                f"Běh dokončen: API import OK. Odesláno kontaktů: "
                f"{api_contacts_sent_import}."
                + (
                    f" Clear operace: {api_contacts_sent_clear}."
                    if api_contacts_sent_clear > 0
                    else ""
                )
            )
        elif api_status == "import_ok_clear_failed":
            run_status_box.warning(
                "API import proběhl, ale část mazání odebraných kódů aplikací selhala. "
                "Detail je v reportu."
            )
        elif api_status == "dry_run_ok":
            run_status_box.success(
                f"Běh dokončen: API dry-run OK. Připraveno kontaktů: {len(api_contacts)}."
            )
        elif api_status == "blocked":
            if api_block_reason:
                run_status_box.warning(f"Běh dokončen se zablokovaným API importem: {api_block_reason}")
            else:
                run_status_box.warning("Běh dokončen se zablokovaným API importem. Zkontroluj důvod v reportu.")
        elif api_status == "failed":
            run_status_box.error(f"Běh selhal: {api_error}")
        else:
            run_status_box.info(f"Běh dokončen se stavem: {api_status}")
    else:
        run_status_box.success(
            f"Běh dokončen: CSV export připraven. Výstupních řádků: {summary_metrics.get('output_rows_total', 0)}."
        )

    report_df = build_report(invalid_df, unknown_df, duplicates_df, summary_metrics=summary_metrics)
    if file_errors:
        extra_report_frames.append(
            pd.DataFrame(
                {
                    "type": "source_file_error",
                    "row_index": "",
                    "detail": [x["error"] for x in file_errors],
                    "email_raw": "",
                    "company": "",
                    "source_file": [x["source_file"] for x in file_errors],
                    "source_row_index": "",
                }
            )
        )
    for frame in extra_report_frames:
        report_df = pd.concat([report_df, frame], ignore_index=True)

    if processed_files == 0:
        run_status_box.warning("Nepodařilo se úspěšně zpracovat žádný zdrojový soubor. Zkontroluj report.")

    # build ZIP
    zip_buf = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for k, part_df in final_parts.items():
                out_name = f"import_{k}.csv"
                export_df = drop_empty_columns(part_df.drop(columns=["__row_order"], errors="ignore"))
                csv_bytes = dataframe_to_csv_bytes(export_df, sep=";", encoding=output_encoding)
                zf.writestr(out_name, csv_bytes)

            zf.writestr("report.csv", dataframe_to_csv_bytes(report_df, sep=";", encoding=output_encoding))
            if api_mode_enabled:
                zf.writestr(
                    "api_contacts_preview.json",
                    json.dumps(api_contacts_preview, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                zf.writestr(
                    "api_batch_results.json",
                    json.dumps(api_batch_results, ensure_ascii=False, indent=2).encode("utf-8"),
                )
    except UnicodeEncodeError as exc:
        run_status_box.error(f"Běh selhal: vybrané kódování '{output_encoding}' neumí některé znaky.")
        st.error(f"Vybrané kódování '{output_encoding}' neumí některé znaky v datech: {exc}")
        st.stop()
    except ValueError as exc:
        run_status_box.error(f"Běh selhal: {exc}")
        st.error(str(exc))
        st.stop()

    st.success("Hotovo. Stáhni ZIP se soubory pro import.")
    st.download_button(
        "Stáhnout ZIP",
        data=zip_buf.getvalue(),
        file_name="smartemailing_import.zip",
        mime="application/zip",
    )
    st.markdown("### Přehled (náhled)")
    st.dataframe(report_df, use_container_width=True)

    if api_mode_enabled:
        st.markdown("### API výstup")
        api_summary = {
            "stav": api_status,
            "pripravenych_kontaktu": len(api_contacts),
            "odeslanych_kontaktu": api_contacts_sent_import,
            "davky": len(api_import_batch_results),
            "staging_list_id": api_resolved_list_id if not bucket_routing_enabled else "",
            "staging_listy_podle_bucketu": (
                {
                    bucket: str(api_bucket_routing_map.get(bucket, "")).strip()
                    for bucket in COUNTRY_BUCKET_KEYS
                    if str(api_bucket_routing_map.get(bucket, "")).strip()
                }
                if bucket_routing_enabled
                else {}
            ),
            "bucket_routing_zapnuto": int(bool(bucket_routing_enabled)),
            "bucket_pripraveno_cz_sk": summary_metrics.get("api_bucket_prepared_CZ_SK", 0),
            "bucket_pripraveno_de_at_ch": summary_metrics.get("api_bucket_prepared_DE_AT_CH", 0),
            "bucket_pripraveno_en": summary_metrics.get("api_bucket_prepared_EN", 0),
            "bucket_chybi_listu": summary_metrics.get("api_bucket_routes_missing_list", 0),
            "staging_tag": str(staging_tag).strip(),
            "api_chyba": api_error,
            "api_duvod_blokace": api_block_reason,
            "diff_status": summary_metrics.get("api_diff_status", ""),
            "diff_existujici_kontakty": summary_metrics.get("api_diff_existing_contacts", 0),
            "diff_nove_kontakty": summary_metrics.get("api_diff_new_contacts", 0),
            "diff_aktualizace": summary_metrics.get("api_diff_updated_contacts", 0),
            "diff_beze_zmeny": summary_metrics.get("api_diff_unchanged_contacts", 0),
            "diff_filtrovano_pryc": summary_metrics.get("api_diff_filtered_out", 0),
            "diff_odstranene_kody_aplikaci": summary_metrics.get("api_diff_removed_program_custom_fields", 0),
            "diff_odstranene_customfields_mimo_pattern": summary_metrics.get(
                "api_diff_removed_nonclearable_custom_fields", 0
            ),
            "diff_odstranene_customfields_mimo_allowlist": summary_metrics.get(
                "api_diff_removed_nonclearable_custom_fields", 0
            ),
            "diff_cisteni_odstranenych_kodu_zapnuto": int(bool(clear_removed_program_custom_fields)),
            "api_cisteni_custom_field_requested": summary_metrics.get("api_custom_field_clear_requested", 0),
            "api_cisteni_custom_field_done": summary_metrics.get("api_custom_field_clear_done", 0),
            "api_cisteni_custom_field_errors": summary_metrics.get("api_custom_field_clear_errors", 0),
            "api_cisteni_custom_field_error_detail": summary_metrics.get("api_custom_field_clear_error_detail", ""),
            "api_cisteni_kontaktu_odeslano": api_contacts_sent_clear,
            "api_cisteni_davky": len(api_clear_batch_results),
            "allowlist_aplikacnich_poli_total": summary_metrics.get("api_program_allowlist_total", 0),
            "allowlist_aplikacnich_poli_aktivni": summary_metrics.get("api_program_allowlist_active", 0),
            "allowlist_aplikacnich_poli_chybejici": summary_metrics.get("api_program_allowlist_missing", 0),
            "allowlist_hardguard_prefixy": summary_metrics.get("api_program_allowlist_hardguard_prefixes", ""),
            "allowlist_hardguard_odfiltrovano": summary_metrics.get(
                "api_program_allowlist_hardguard_filtered_out", 0
            ),
            "blacklist_filtr_zapnut": int(bool(skip_blacklisted_contacts)),
            "blacklist_lookup_status": summary_metrics.get("api_blacklisted_lookup_status", ""),
            "blacklist_nalezeno": summary_metrics.get("api_blacklisted_contacts_found", 0),
            "blacklist_preskoceno": summary_metrics.get("api_blacklisted_contacts_skipped", 0),
            "diff_chyba": api_diff_error,
        }
        st.json(api_summary)
        if api_diff_summary:
            updated_details = api_diff_summary.get("updated_details", [])
            if updated_details:
                st.markdown("#### Diff: aktualizované kontakty (náhled)")
                st.dataframe(
                    to_streamlit_safe_dataframe(pd.DataFrame(updated_details[:100])),
                    use_container_width=True,
                )
        st.markdown("#### Výsledky testovací dávky a dalších dávek")
        st.dataframe(to_streamlit_safe_dataframe(pd.DataFrame(api_batch_results)), use_container_width=True)
        st.markdown("#### Náhled kontrolního běhu (prvních 50 kontaktů)")
        st.json(api_contacts_preview)

    try:
        append_job_history(
            {
                "mode": execution_mode,
                "status": api_status if api_mode_enabled else "csv_export_ok",
                "profile_id": active_profile_id,
                "profile_name": active_profile_name,
                "preset_id": (
                    str(active_selected_preset_id).strip()
                    if str(active_selected_preset_id).strip() not in {"", "(žádný)"}
                    else ""
                ),
                "preset_name": (
                    str(runtime_selected_preset.get("name", "")).strip()
                    if isinstance(runtime_selected_preset, dict)
                    else ""
                ),
                "schema_origin": run_schema_origin,
                "source_files_total": len(source_files),
                "source_files_processed": processed_files,
                "source_files_failed": len(file_errors),
                "output_rows_total": summary_metrics.get("output_rows_total", 0),
                "api_contacts_prepared": len(api_contacts),
                "api_contacts_sent": api_contacts_sent_import,
                "api_batches": len(api_import_batch_results),
                "api_contacts_sent_clear": api_contacts_sent_clear,
                "api_batches_clear": len(api_clear_batch_results),
                "api_canary_size": api_canary_size if api_mode_enabled else 0,
                "api_batch_size": api_batch_size if api_mode_enabled else 0,
                "api_staging_list_id": api_resolved_list_id,
                "api_staging_tag": str(staging_tag).strip(),
                "api_diff_status": summary_metrics.get("api_diff_status", ""),
                "api_diff_new_contacts": summary_metrics.get("api_diff_new_contacts", 0),
                "api_diff_updated_contacts": summary_metrics.get("api_diff_updated_contacts", 0),
                "api_diff_unchanged_contacts": summary_metrics.get("api_diff_unchanged_contacts", 0),
                "api_diff_removed_program_custom_fields": summary_metrics.get(
                    "api_diff_removed_program_custom_fields", 0
                ),
                "api_diff_removed_nonclearable_custom_fields": summary_metrics.get(
                    "api_diff_removed_nonclearable_custom_fields", 0
                ),
                "api_clear_removed_program_custom_fields_enabled": int(
                    bool(clear_removed_program_custom_fields)
                ),
                "api_custom_field_clear_requested": summary_metrics.get("api_custom_field_clear_requested", 0),
                "api_custom_field_clear_done": summary_metrics.get("api_custom_field_clear_done", 0),
                "api_custom_field_clear_errors": summary_metrics.get("api_custom_field_clear_errors", 0),
                "api_program_allowlist_total": summary_metrics.get("api_program_allowlist_total", 0),
                "api_program_allowlist_active": summary_metrics.get("api_program_allowlist_active", 0),
                "api_program_allowlist_missing": summary_metrics.get("api_program_allowlist_missing", 0),
                "api_program_allowlist_hardguard_prefixes": summary_metrics.get(
                    "api_program_allowlist_hardguard_prefixes", ""
                ),
                "api_program_allowlist_hardguard_filtered_out": summary_metrics.get(
                    "api_program_allowlist_hardguard_filtered_out", 0
                ),
                "api_skip_blacklisted_contacts_enabled": summary_metrics.get(
                    "api_skip_blacklisted_contacts_enabled", 0
                ),
                "api_blacklisted_lookup_status": summary_metrics.get("api_blacklisted_lookup_status", ""),
                "api_blacklisted_contacts_found": summary_metrics.get("api_blacklisted_contacts_found", 0),
                "api_blacklisted_contacts_skipped": summary_metrics.get("api_blacklisted_contacts_skipped", 0),
                "error": api_error,
            }
        )
    except Exception as exc:
        st.warning(f"Nepodařilo se uložit historii běhů: {exc}")

history_header_col, history_action_col = st.columns([3, 1])
with history_header_col:
    st.markdown("### Historie běhů")
with history_action_col:
    if st.button("Smazat historii běhů", key="clear_job_history_btn"):
        try:
            clear_job_history()
            st.success("Historie běhů byla smazána.")
            st.rerun()
        except Exception as exc:
            st.error(f"Nepodařilo se smazat historii běhů: {exc}")

history_rows = load_job_history(limit=50)
history_only_active_profile = st.checkbox(
    "Zobrazit historii jen pro aktivní profil",
    value=bool(profile_ui_saved.get("history_filter_active_profile", False)),
    key="history_filter_active_profile",
)
if history_only_active_profile:
    history_rows = [
        row
        for row in history_rows
        if str(row.get("profile_id", "")).strip() == str(active_profile_id).strip()
    ]
history_alerts = summarize_job_alerts(history_rows)
if history_alerts["recent_failures"] >= 3:
    st.error("Upozornění: posledních 10 běhů obsahuje 3+ selhání.")
elif history_alerts["failure_rate"] >= 0.3 and history_alerts["total"] >= 5:
    st.warning("Upozornění: míra selhání v historii je >= 30 %. Zkontroluj API konfiguraci a data.")
else:
    st.caption("Historie běhů bez kritického alertu.")

if history_rows:
    history_df = pd.DataFrame(history_rows)
    if not history_df.empty:
        preset_name_series = history_df.get("preset_name", pd.Series(index=history_df.index, dtype="object")).fillna("")
        preset_id_series = history_df.get("preset_id", pd.Series(index=history_df.index, dtype="object")).fillna("")
        preset_display = preset_name_series.astype(str).str.strip()
        needs_fallback = preset_display == ""
        preset_display.loc[needs_fallback] = (
            preset_id_series.astype(str).str.strip().replace({"(žádný)": "", "none": "", "None": ""})
        ).loc[needs_fallback]
        needs_fallback = preset_display == ""
        preset_display.loc[needs_fallback] = "(žádný)"

        if "profile_id" in history_df.columns:
            profile_idx = list(history_df.columns).index("profile_id")
            history_df.insert(profile_idx, "preset", preset_display)
            history_df = history_df.drop(columns=["profile_id"])
        elif "preset" not in history_df.columns:
            history_df.insert(0, "preset", preset_display)

        drop_tech_cols = [col for col in ["preset_id", "preset_name"] if col in history_df.columns]
        if drop_tech_cols:
            history_df = history_df.drop(columns=drop_tech_cols)
    st.dataframe(to_streamlit_safe_dataframe(history_df), use_container_width=True)
else:
    st.caption("Historie běhů je zatím prázdná.")
