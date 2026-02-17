from __future__ import annotations

import hashlib
import io
import json
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
API_LIST_FAVORITES_PATH = Path("config/se_list_favorites.local")


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


def build_system_schema_columns(cfg: dict[str, Any]) -> list[str]:
    se_cfg = cfg.get("smartemailing", {})
    field_map = se_cfg.get("field_map", {})
    base = [str(col).strip() for col in field_map.keys() if str(col).strip()]

    programs_cfg = se_cfg.get("programs", {})
    combined_field = str(programs_cfg.get("combined_field_name", "")).strip()
    if programs_cfg.get("also_fill_combined_field") and combined_field:
        base.append(combined_field)

    return base


def fetch_schema_from_api(username: str, api_key: str, base_url: str) -> tuple[Schema, dict[str, Any]]:
    creds = SmartEmailingCredentials(
        username=str(username).strip(),
        api_key=str(api_key).strip(),
        base_url=str(base_url).strip() or DEFAULT_BASE_URL,
    )
    if not creds.username or not creds.api_key:
        raise ValueError("Vyplň SmartEmailing uživatelské jméno i API klíč.")

    client = SmartEmailingApiClient(creds)
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
    for existing_contact in existing_contacts:
        email_key = normalize_email_key(existing_contact.get("emailaddress", ""))
        if not email_key:
            continue
        if email_key not in existing_by_email:
            existing_by_email[email_key] = existing_contact

    existing_contacts_with_custom_fields = 0
    for existing_contact in existing_by_email.values():
        _, existing_custom_fields, _ = split_contact_for_diff(existing_contact)
        if existing_custom_fields:
            existing_contacts_with_custom_fields += 1
    existing_contacts_with_customfields_key = sum(
        1 for existing_contact in existing_by_email.values() if "customfields" in existing_contact
    )
    import_has_custom_fields = any(
        bool(split_contact_for_diff(import_contact)[1]) for import_contact in import_contacts
    )
    custom_fields_compare_enabled = (
        len(existing_by_email) == 0
        or not import_has_custom_fields
        or existing_contacts_with_custom_fields > 0
        or existing_contacts_with_customfields_key > 0
    )
    managed_custom_field_ids: set[str] = set()
    for import_contact in import_contacts:
        _, import_custom_fields, _ = split_contact_for_diff(import_contact)
        managed_custom_field_ids.update({str(field_id).strip() for field_id in import_custom_fields.keys() if str(field_id).strip()})
        managed_custom_field_ids.update(
            {
                str(field_id).strip()
                for field_id in import_contact.get("__managed_custom_field_ids", [])
                if str(field_id).strip()
            }
        )

    new_contacts: list[dict[str, Any]] = []
    updated_contacts: list[dict[str, Any]] = []
    unchanged_contacts: list[dict[str, Any]] = []
    contacts_to_send: list[dict[str, Any]] = []
    updated_details: list[dict[str, Any]] = []
    unchanged_emails: list[str] = []
    removed_clearable_custom_fields_by_email: dict[str, list[str]] = {}
    removed_nonclearable_custom_fields_by_email: dict[str, list[str]] = {}
    clear_operations: list[dict[str, str]] = []
    clearable_ids = {str(x).strip() for x in (clearable_custom_field_ids or set()) if str(x).strip()}

    for contact in import_contacts:
        email = normalize_scalar_for_diff(contact.get("emailaddress", ""))
        email_key = normalize_email_key(email)
        if not email_key:
            continue

        existing_contact = existing_by_email.get(email_key)
        if existing_contact is None:
            new_contacts.append(contact)
            contacts_to_send.append(contact)
            continue

        import_system, import_custom, import_tags = split_contact_for_diff(contact)
        existing_system, existing_custom, existing_tags = split_contact_for_diff(existing_contact)

        changed_fields: list[str] = []
        for field_name, import_value in import_system.items():
            existing_value = existing_system.get(field_name, "")
            if normalize_scalar_for_diff(import_value) != normalize_scalar_for_diff(existing_value):
                changed_fields.append(field_name)

        if custom_fields_compare_enabled:
            contact_managed_custom_field_ids = {
                str(field_id).strip()
                for field_id in contact.get("__managed_custom_field_ids", [])
                if str(field_id).strip()
            }
            fields_to_compare = sorted(contact_managed_custom_field_ids or managed_custom_field_ids)
            contact_clearable_ids = set(clearable_ids) | set(contact_managed_custom_field_ids)
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
                    changed_fields.append(f"customfield:{field_id}")
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

        if import_tags:
            existing_tags_set = {normalize_scalar_for_diff(x) for x in existing_tags}
            import_tags_set = {normalize_scalar_for_diff(x) for x in import_tags}
            if not import_tags_set.issubset(existing_tags_set):
                changed_fields.append("tags")

        if changed_fields:
            updated_contacts.append(contact)
            contacts_to_send.append(contact)
            updated_details.append(
                {
                    "email": email,
                    "changed_fields": sorted(set(changed_fields)),
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


st.sidebar.header("Nastavení")
do_split_emails = st.sidebar.checkbox("Rozdělit více emailů na více řádků", value=True)
do_split_names = st.sidebar.checkbox("Rozdělit jména (tituly/jméno/příjmení)", value=True)
do_bucket_country = st.sidebar.checkbox("Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)", value=True)
if "output_encoding" not in st.session_state:
    st.session_state["output_encoding"] = "cp1250"
output_encoding = st.sidebar.selectbox(
    "Kódování výstupních CSV",
    options=["cp1250", "utf-8", "utf-8-sig"],
    key="output_encoding",
)
dedup_label = st.sidebar.selectbox(
    "Deduplikace emailů ve výstupu",
    options=["Bez deduplikace", "Ponechat první výskyt", "Ponechat poslední výskyt"],
    index=2,
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

st.markdown("### 1) Nahraj zdrojové CSV soubory (1 nebo více)")
source_files = st.file_uploader("Zdrojové CSV", type=["csv"], accept_multiple_files=True)

mode_step_container = st.container()
api_step_container = st.container()
run_step_container = st.container()

with st.expander("Nastavení schématu (volitelné)", expanded=False):
    st.markdown("### Nastavení schématu (volitelné)")
    st.caption("Tato sekce není součást běžného flow. Používá se hlavně jako fallback při výpadku API.")
    st.markdown("#### Schéma sloupců (CSV záloha)")
    cached_schema, cached_schema_meta = load_cached_schema(SCHEMA_CACHE_PATH)
    use_cached_schema = st.checkbox(
        "Použít uložené schéma z CSV zálohy (bez nahrávání exportu)",
        value=(cached_schema is not None),
        disabled=(cached_schema is None),
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
        value=True,
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
            refresh_api_schema_on_generate = st.checkbox("Před exportem načíst schéma z API znovu", value=True)
            use_api_cache_on_error = st.checkbox("Při chybě API použít schéma z API mezipaměti", value=True)
    
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
                    st.dataframe(pd.DataFrame(probe_rows), use_container_width=True)
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
    
with mode_step_container:
    st.markdown("### 2) Režim běhu")
    execution_mode_label = st.radio(
        "Vyber akci po transformaci dat",
        options=[
            "API kontrolní běh (jen validace + náhled)",
            "API bezpečný import (staging + testovací dávka)",
            "API plný import (schvalování + testovací dávka)",
            "CSV export (záloha)",
        ],
        index=1,
    )
execution_mode = {
    "API kontrolní běh (jen validace + náhled)": "api_dry_run",
    "API bezpečný import (staging + testovací dávka)": "api_safe_import",
    "API plný import (schvalování + testovací dávka)": "api_full_import",
    "CSV export (záloha)": "csv_fallback",
}[execution_mode_label]

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
strict_custom_fields = bool(api_cfg.get("strict_custom_fields", True))
list_status = str(api_cfg.get("list_status", "confirmed")).strip() or "confirmed"
auto_create_unknown_program_fields_default = bool(api_cfg.get("auto_create_unknown_program_fields", True))
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
exclude_columns_from_api_import = list(
    dict.fromkeys(default_exclude_columns_from_api_import + configured_exclude_columns_from_api_import)
)
default_ignore_missing_custom = [
    str(col).strip()
    for col in field_map_cfg.keys()
    if str(col).strip()
    and str(col).strip().casefold() not in {str(x).strip().casefold() for x in api_system_field_map_cfg.keys()}
]
configured_ignore_missing_custom = [
    str(col).strip()
    for col in api_cfg.get("ignore_missing_custom_for_columns", [])
    if str(col).strip()
]
ignore_missing_custom_for_columns = list(
    dict.fromkeys(default_ignore_missing_custom + configured_ignore_missing_custom)
)

api_import_username = ""
api_import_key = ""
api_import_base_url = DEFAULT_BASE_URL
staging_list_value = ""
staging_tag = ""
api_canary_size = to_int(api_cfg.get("canary_size", 50), 50)
api_batch_size = to_int(api_cfg.get("batch_size", 500), 500)
api_max_contacts_safe = to_int(api_cfg.get("max_contacts_safe", 2000), 2000)
api_max_contacts_full = to_int(api_cfg.get("max_contacts_full", 10000), 10000)
diff_preflight_enabled_default = bool(api_cfg.get("diff_preflight_enabled", True))
diff_send_only_changes_default = bool(api_cfg.get("diff_send_only_changes", True))
diff_fallback_send_all_on_error_default = bool(api_cfg.get("diff_fallback_send_all_on_error", True))
clear_removed_program_custom_fields_default = bool(
    api_cfg.get("clear_removed_program_custom_fields_enabled", True)
)
diff_page_limit = to_int(api_cfg.get("diff_page_limit", 100), 100)
diff_max_pages = to_int(api_cfg.get("diff_max_pages", 100), 100)
diff_preflight_enabled = diff_preflight_enabled_default
diff_send_only_changes = diff_send_only_changes_default
diff_fallback_send_all_on_error = diff_fallback_send_all_on_error_default
clear_removed_program_custom_fields = clear_removed_program_custom_fields_default
safe_confirm = False
full_confirm = False
full_phrase_input = ""
full_operator = ""
full_approver = ""
full_second_approval_input = ""
auto_create_unknown_program_fields = auto_create_unknown_program_fields_default

if api_mode_enabled:
    if "api_contact_lists_cache" not in st.session_state:
        st.session_state.api_contact_lists_cache = []
    if "api_list_favorite_ids" not in st.session_state:
        st.session_state.api_list_favorite_ids = sorted(load_api_list_favorites(API_LIST_FAVORITES_PATH))
    if "full_import_approval_code" not in st.session_state:
        st.session_state.full_import_approval_code = hashlib.sha1(
            datetime.now(timezone.utc).isoformat().encode("utf-8")
        ).hexdigest()[:8].upper()
    with st.expander("Detaily API importu a bezpečnostních voleb", expanded=False):
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

        api_canary_size = int(
            st.number_input(
                "Velikost testovací dávky (první dávka)",
                min_value=0,
                value=max(0, api_canary_size),
                step=10,
            )
        )
        api_batch_size = int(
            st.number_input(
                "Velikost dávky",
                min_value=1,
                value=max(1, api_batch_size),
                step=100,
            )
        )

    with api_step_container:
        st.markdown("### 3) Import do SmartEmailingu přes API")

        if "api_contact_lists_cache_meta" not in st.session_state:
            st.session_state.api_contact_lists_cache_meta = {}
        if "staging_list_manual" not in st.session_state:
            st.session_state.staging_list_manual = ""
        if "staging_list_select" not in st.session_state:
            st.session_state.staging_list_select = "(ručně)"

        load_lists = st.button("Obnovit listy z API", key="refresh_api_lists_main")
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
        if lists_cache_for_staging:
            def _list_sort_tuple(item: dict[str, Any]) -> tuple[int, int, int, str]:
                raw_id = str(item.get("id", "")).strip()
                name = str(item.get("name", "")).strip().casefold()
                favorite_rank = 0 if raw_id in favorite_list_ids else 1
                try:
                    return (favorite_rank, 0, -int(raw_id), name)
                except Exception:
                    return (favorite_rank, 1, 0, name)

            label_to_list: dict[str, dict[str, str]] = {}
            labels = []
            for item in sorted(lists_cache_for_staging, key=_list_sort_tuple):
                list_id = str(item.get("id", "")).strip()
                list_name = str(item.get("name", "")).strip() or "(bez názvu)"
                favorite_prefix = "★ " if list_id in favorite_list_ids else ""
                label = f"{favorite_prefix}{list_name} (id={list_id})"
                labels.append(label)
                label_to_list[label] = {"id": list_id, "name": list_name}

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
                        if st.button(quick_label, key=f"quick_select_favorite_{list_id}"):
                            if selected_label_with_id in ["(ručně)"] + labels:
                                st.session_state["staging_list_select"] = selected_label_with_id
                            st.session_state["staging_list_manual"] = list_id
                            st.rerun()

            list_options = ["(ručně)"] + labels
            if st.session_state.get("staging_list_select") not in list_options:
                st.session_state["staging_list_select"] = "(ručně)"
            selected_list_label = st.selectbox(
                "Vyber staging seznam ze SmartEmailingu",
                options=list_options,
                key="staging_list_select",
            )
            if selected_list_label != "(ručně)":
                selected = label_to_list.get(selected_list_label, {"id": "", "name": ""})
                selected_id = str(selected.get("id", "")).strip()
                selected_name = str(selected.get("name", "")).strip()
                if selected_id:
                    st.session_state["staging_list_manual"] = selected_id
                    st.caption(f"Vybraný staging seznam: `{selected_name}` (id `{selected_id}`)")
                    is_favorite = selected_id in favorite_list_ids
                    toggle_fav_label = "★ Odebrat z oblíbených" if is_favorite else "☆ Přidat do oblíbených"
                    fav_col_1, fav_col_2 = st.columns([1, 4])
                    with fav_col_1:
                        if st.button(toggle_fav_label, key=f"toggle_api_favorite_list_{selected_id}"):
                            if is_favorite:
                                favorite_list_ids.discard(selected_id)
                            else:
                                favorite_list_ids.add(selected_id)
                            st.session_state["api_list_favorite_ids"] = sorted(favorite_list_ids)
                            try:
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

        staging_list_value = st.text_input(
            "Staging seznam ID nebo název (bezpečný/plný režim)",
            key="staging_list_manual",
            help="Doporučeno: použít staging seznam, ne produkční seznam.",
        )
        staging_tag = st.text_input(
            "Staging štítek (volitelný)",
            value="",
            placeholder="např. ITF_IMPORT_STAGING",
            help="Některé účty podporují tagy v importních datech.",
        )

        st.markdown("#### Porovnání před importem (diff)")
        diff_preflight_enabled = st.checkbox(
            "Před importem porovnat připravené kontakty s kontakty v cílovém staging seznamu",
            value=diff_preflight_enabled_default,
            key="diff_preflight_enabled_main",
            help="Načte kontakty ze staging seznamu a vyhodnotí nové / aktualizované / beze změny.",
        )
        diff_send_only_changes = st.checkbox(
            "Odesílat jen nové a změněné kontakty (beze změny přeskočit)",
            value=diff_send_only_changes_default,
            key="diff_send_only_changes_main",
            disabled=not diff_preflight_enabled,
        )
        diff_fallback_send_all_on_error = st.checkbox(
            "Při chybě diff porovnání pokračovat odesláním bez diffu (fallback)",
            value=diff_fallback_send_all_on_error_default,
            key="diff_fallback_send_all_on_error_main",
            disabled=not diff_preflight_enabled,
        )
        clear_removed_program_custom_fields = st.checkbox(
            "Při diffu mazat odebrané kódy aplikací (jen programové custom fields)",
            value=clear_removed_program_custom_fields_default,
            key="clear_removed_program_custom_fields_main",
            disabled=not diff_preflight_enabled,
            help=(
                "Bezpečnostní režim: maže jen custom fieldy odpovídající patternu programových kódů "
                "(např. PABC, PHAT). Ostatních custom fields se nedotýká."
            ),
        )
        diff_page_limit = int(
            st.number_input(
                "Diff: počet kontaktů na stránku při načítání staging seznamu",
                min_value=10,
                value=max(10, diff_page_limit),
                step=10,
                key="diff_page_limit_main",
                disabled=not diff_preflight_enabled,
            )
        )
        diff_max_pages = int(
            st.number_input(
                "Diff: max počet stránek pro načtení staging seznamu",
                min_value=1,
                value=max(1, diff_max_pages),
                step=5,
                key="diff_max_pages_main",
                disabled=not diff_preflight_enabled,
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
                )
            )
        auto_create_unknown_program_fields = st.checkbox(
            "Automaticky vytvořit chybějící vlastní pole pro nové kódy aplikací",
            value=auto_create_unknown_program_fields_default,
            key="auto_create_unknown_program_fields_main",
            help=f"Vytvoří nové custom fieldy v SmartEmailingu jako typ '{auto_create_program_field_type}'.",
        )

        if execution_mode == "api_safe_import":
            st.markdown("#### Povinné potvrzení pro bezpečný import")
            safe_confirm = st.checkbox(
                "Rozumím dopadu: bezpečný import běží jen jako přidání/aktualizace, bez mazání.",
                value=False,
                key="safe_import_confirm_main",
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
if "diff_preview_rows" not in st.session_state:
    st.session_state["diff_preview_rows"] = []
if "diff_preview_summary" not in st.session_state:
    st.session_state["diff_preview_summary"] = {}
if "diff_preview_error" not in st.session_state:
    st.session_state["diff_preview_error"] = ""

with run_step_container:
    run_status_box = st.empty()
    if api_mode_enabled and not api_credentials_ready:
        st.warning("Pro API režim vyplň API uživatelské jméno + API klíč.")
    if execution_mode not in {"api_safe_import", "api_full_import"}:
        st.session_state["pending_api_import_confirmation"] = {}
        st.session_state["pending_api_import_confirmation_fingerprint"] = ""
        st.session_state["approved_api_import_confirmation_fingerprint"] = ""
        st.session_state["auto_resume_run_after_api_import_confirm"] = False
        st.session_state["diff_preview_rows"] = []
        st.session_state["diff_preview_summary"] = {}
        st.session_state["diff_preview_error"] = ""
    pending_fields = [str(x).strip() for x in st.session_state.get("pending_custom_fields_to_create", []) if str(x).strip()]
    pending_fingerprint = str(st.session_state.get("pending_custom_fields_fingerprint", "")).strip()
    if pending_fields and pending_fingerprint:
        st.warning(
            "Běh čeká na potvrzení vytvoření nových vlastních polí ve SmartEmailingu. "
            "Zkontroluj seznam níže a potvrď pokračování."
        )
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

    pending_import_confirmation = st.session_state.get("pending_api_import_confirmation", {})
    pending_import_fingerprint = str(st.session_state.get("pending_api_import_confirmation_fingerprint", "")).strip()
    if (
        execution_mode in {"api_safe_import", "api_full_import"}
        and isinstance(pending_import_confirmation, dict)
        and pending_import_confirmation
        and pending_import_fingerprint
    ):
        st.warning(
            "Běh čeká na potvrzení odeslání importu do SmartEmailingu API. "
            "Zkontroluj souhrn a potvrď pokračování."
        )
        metric_col_1, metric_col_2, metric_col_3, metric_col_4 = st.columns(4)
        metric_col_1.metric("Kontakty k odeslání", int(pending_import_confirmation.get("contacts_total", 0)))
        metric_col_2.metric("Kontakty s custom fields", int(pending_import_confirmation.get("contacts_with_custom_fields", 0)))
        metric_col_3.metric("Kontakty se seznamem", int(pending_import_confirmation.get("contacts_with_list_assignment", 0)))
        metric_col_4.metric("Chyby payloadu", int(pending_import_confirmation.get("issues_count", 0)))
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
            if bool(pending_import_confirmation.get("clear_removed_program_custom_fields_enabled", False)):
                st.caption(
                    "Čištění odebraných kódů aplikací: "
                    f"{int(pending_import_confirmation.get('diff_removed_program_custom_fields', 0))} změn."
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
        st.info(str(pending_import_confirmation.get("new_vs_update_note", "")).strip())
        confirm_import_col, cancel_import_col = st.columns(2)
        with confirm_import_col:
            if st.button("Potvrdit API import a pokračovat", key="confirm_api_import_send"):
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
            if not bool(preview_summary.get("custom_fields_compare_enabled", True)):
                st.warning(
                    "API nevrátil custom fields pro existující kontakty ani přes list/detail/email lookup. "
                    "Diff porovnání custom fields bylo přeskočeno a fallback běží jako odeslání všech připravených kontaktů."
                )
        if preview_error:
            st.warning(preview_error)
        if preview_rows:
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, height=320)

    diff_preview_clicked = False
    run_button_col, preview_button_col = st.columns(2)
    with run_button_col:
        run_clicked = st.button("Spustit zpracování", type="primary", disabled=generate_disabled)
    with preview_button_col:
        diff_preview_clicked = st.button(
            "Načíst diff preview (bez importu)",
            disabled=(
                generate_disabled
                or execution_mode not in {"api_safe_import", "api_full_import"}
                or not diff_preflight_enabled
            ),
            help="Spočítá diff proti vybranému staging seznamu a zobrazí prvních 200 řádků nad tlačítky.",
        )
    if bool(st.session_state.get("auto_resume_run_after_custom_fields_confirm", False)) or bool(
        st.session_state.get("auto_resume_run_after_api_import_confirm", False)
    ):
        run_clicked = True
        st.session_state["auto_resume_run_after_custom_fields_confirm"] = False
        st.session_state["auto_resume_run_after_api_import_confirm"] = False

preview_only = diff_preview_clicked and not run_clicked

if run_clicked or diff_preview_clicked:
    if preview_only:
        st.session_state["pending_api_import_confirmation"] = {}
        st.session_state["pending_api_import_confirmation_fingerprint"] = ""
        st.session_state["approved_api_import_confirmation_fingerprint"] = ""
        st.session_state["auto_resume_run_after_api_import_confirm"] = False
        if not diff_preflight_enabled:
            run_status_box.error("Diff preview nelze spočítat: zapni volbu porovnání před importem (diff).")
            st.stop()
        if not str(staging_list_value).strip():
            run_status_box.error("Diff preview nelze spočítat: vyber staging seznam.")
            st.stop()
        run_status_box.info("Načítám diff preview (bez odeslání importu do API).")

    if not preview_only and execution_mode == "api_safe_import" and not safe_confirm:
        run_status_box.error(
            "Bezpečný import nelze spustit: zaškrtni povinné potvrzení "
            "`Rozumím dopadu: bezpečný import běží jen jako přidání/aktualizace, bez mazání.`"
        )
        st.stop()

    active_schema = schema
    run_schema_origin = schema_origin
    if use_api_schema and refresh_api_schema_on_generate:
        try:
            active_schema, api_meta = fetch_schema_from_api(api_username, api_key, api_base_url)
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
            st.info(f"{sf.name}: detekováno jako **{source.name}**, řádků po transformacích: {len(expanded)}")
        except Exception as exc:
            file_errors.append({"source_file": sf.name, "error": str(exc)})
            st.error(f"{sf.name}: nepodařilo se zpracovat ({exc})")
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
            st.warning("Deduplikace je zapnutá, ale ve schématu nebyl nalezen emailový sloupec pro deduplikaci.")

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
            create_errors: list[str] = []
            for code in codes_to_create:
                try:
                    auto_client.create_custom_field(
                        name=code,
                        field_type=auto_create_program_field_type,
                        endpoint_candidates=custom_field_create_endpoint_candidates,
                    )
                    created_codes.append(code)
                except Exception as exc:
                    create_errors.append(f"{code}: {exc}")

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
    api_ping = {}
    extra_report_frames: list[pd.DataFrame] = []

    if api_mode_enabled:
        try:
            client = SmartEmailingApiClient(
                SmartEmailingCredentials(
                    username=str(api_import_username).strip(),
                    api_key=str(api_import_key).strip(),
                    base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                )
            )
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
            api_contacts, api_issues = build_api_contacts_from_import_df(
                import_df=import_for_api,
                api_system_field_map=api_cfg.get("system_field_map", {}),
                custom_fields=custom_fields,
                list_id=api_resolved_list_id,
                list_status=list_status,
                tag=str(staging_tag).strip(),
                strict_custom_fields=strict_custom_fields,
                ignore_missing_custom_for_columns=ignore_missing_custom_for_columns,
                array_custom_field_names=array_custom_field_names,
                array_value_split_separators=array_value_split_separators,
                managed_empty_custom_field_name_pattern=managed_empty_custom_field_name_pattern,
            )

            summary_metrics["api_diff_enabled"] = int(bool(diff_preflight_enabled))
            summary_metrics["api_diff_send_only_changes"] = int(bool(diff_send_only_changes))
            summary_metrics["api_diff_fallback_on_error"] = int(bool(diff_fallback_send_all_on_error))
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

            program_custom_field_ids_for_clear: set[str] = set()
            if clear_removed_program_custom_fields:
                try:
                    program_name_regex = re.compile(managed_empty_custom_field_name_pattern)
                    for field in custom_fields:
                        field_id = str(field.get("id", "")).strip()
                        field_name = str(field.get("name", "")).strip()
                        if field_id and field_name and program_name_regex.fullmatch(field_name):
                            program_custom_field_ids_for_clear.add(field_id)
                except Exception:
                    program_custom_field_ids_for_clear = set()

            if diff_preflight_enabled and api_resolved_list_id:
                try:
                    import_email_keys = {
                        normalize_email_key(contact.get("emailaddress", ""))
                        for contact in api_contacts
                        if normalize_email_key(contact.get("emailaddress", ""))
                    }
                    existing_contacts = client.fetch_contacts_in_list(
                        list_id=api_resolved_list_id,
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
                    )
                    api_diff_summary = diff_api_contacts(
                        import_contacts=api_contacts,
                        existing_contacts=existing_contacts,
                        array_value_split_separators=array_value_split_separators,
                        clearable_custom_field_ids=program_custom_field_ids_for_clear,
                    )
                    summary_metrics["api_diff_status"] = "ok"
                    summary_metrics["api_diff_existing_contacts"] = int(api_diff_summary.get("existing_total", 0))
                    summary_metrics["api_diff_new_contacts"] = int(len(api_diff_summary.get("new_contacts", [])))
                    summary_metrics["api_diff_updated_contacts"] = int(len(api_diff_summary.get("updated_contacts", [])))
                    summary_metrics["api_diff_unchanged_contacts"] = int(len(api_diff_summary.get("unchanged_contacts", [])))
                    summary_metrics["api_diff_filtered_out"] = int(len(api_diff_summary.get("unchanged_contacts", [])))
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
                            "Diff detekoval i odebrané custom fields mimo povolený pattern pro programové kódy. "
                            f"Tyto změny se nemažou: {int(summary_metrics.get('api_diff_removed_nonclearable_custom_fields', 0))}."
                        )

                    if diff_send_only_changes:
                        if bool(api_diff_summary.get("custom_fields_compare_enabled", True)):
                            api_contacts = list(api_diff_summary.get("contacts_to_send", []))
                        else:
                            # Fallback: custom fields couldn't be compared reliably,
                            # keep all prepared contacts to avoid missing updates.
                            summary_metrics["api_diff_filtered_out"] = 0

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
                        if not bool(api_diff_summary.get("custom_fields_compare_enabled", True)):
                            run_status_box.warning(
                                "API nevrátil custom fields pro existující kontakty "
                                "ani přes list/detail/email lookup. Porovnání custom fields bylo v diffu přeskočeno "
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
            elif diff_preflight_enabled and execution_mode in {"api_safe_import", "api_full_import"}:
                summary_metrics["api_diff_status"] = "skipped_no_list"
                run_status_box.warning("Diff preflight je zapnutý, ale není vybraný staging seznam.")

            api_contacts_preview = api_contacts[:50]
            summary_metrics["api_ping_status"] = str(api_ping.get("status", "")) if isinstance(api_ping, dict) else ""
            summary_metrics["api_custom_fields"] = len(custom_fields)
            summary_metrics["api_contacts_prepared"] = len(api_contacts)
            summary_metrics["api_payload_issues"] = len(api_issues)
            summary_metrics["api_staging_list_id"] = api_resolved_list_id
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
                    preview_error = "Diff preview nelze spočítat: není vybraný staging seznam."
                elif diff_status == "disabled":
                    preview_error = "Diff preview je vypnutý. Zapni porovnání před importem (diff)."

                st.session_state["diff_preview_rows"] = preview_rows
                st.session_state["diff_preview_summary"] = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "list_id": api_resolved_list_id,
                    "diff_status": diff_status,
                    "existing_contacts": int(summary_metrics.get("api_diff_existing_contacts", 0)),
                    "new_contacts": int(summary_metrics.get("api_diff_new_contacts", 0)),
                    "updated_contacts": int(summary_metrics.get("api_diff_updated_contacts", 0)),
                    "unchanged_contacts": int(summary_metrics.get("api_diff_unchanged_contacts", 0)),
                    "contacts_to_send": len(api_contacts),
                    "send_only_changes": bool(diff_send_only_changes),
                    "custom_fields_compare_enabled": bool(api_diff_summary.get("custom_fields_compare_enabled", True)),
                    "existing_contacts_with_custom_fields": int(api_diff_summary.get("existing_contacts_with_custom_fields", 0)),
                    "removed_program_custom_fields": int(
                        summary_metrics.get("api_diff_removed_program_custom_fields", 0)
                    ),
                    "clear_removed_program_custom_fields_enabled": bool(clear_removed_program_custom_fields),
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
                    if not safe_confirm:
                        block_reason = "Bezpečný import je blokovaný: chybí potvrzení dopadu."
                    elif not api_resolved_list_id:
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
                    elif not api_resolved_list_id:
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
                            )
                            st.session_state["pending_api_import_confirmation_fingerprint"] = import_confirmation_fingerprint
                            run_status_box.warning(
                                "Před odesláním do SmartEmailingu API potvrď souhrn importu "
                                "(zobrazeno nad tlačítkem Spustit zpracování)."
                            )
                            st.rerun()
                        st.session_state["approved_api_import_confirmation_fingerprint"] = ""

                    batch_results = client.import_contacts_canary(
                        contacts=api_contacts,
                        canary_size=api_canary_size,
                        batch_size=api_batch_size,
                        update_existing=True,
                        skip_invalid_contacts=True,
                        endpoint_candidates=import_endpoint_candidates,
                    )
                    api_batch_results = [
                        {
                            "endpoint": x.endpoint,
                            "payload_variant": x.payload_variant,
                            "operation": "import",
                            "sent_contacts": x.sent_contacts,
                            "batch_index": x.batch_index,
                            "canary": x.canary,
                            "started_at": x.started_at,
                            "finished_at": x.finished_at,
                            "response": x.response,
                        }
                        for x in batch_results
                    ]
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
        st.warning("Nepodařilo se úspěšně zpracovat žádný zdrojový soubor. Zkontroluj report.")

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
            "staging_list_id": api_resolved_list_id,
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
            "diff_cisteni_odstranenych_kodu_zapnuto": int(bool(clear_removed_program_custom_fields)),
            "api_cisteni_custom_field_requested": summary_metrics.get("api_custom_field_clear_requested", 0),
            "api_cisteni_custom_field_done": summary_metrics.get("api_custom_field_clear_done", 0),
            "api_cisteni_custom_field_errors": summary_metrics.get("api_custom_field_clear_errors", 0),
            "api_cisteni_custom_field_error_detail": summary_metrics.get("api_custom_field_clear_error_detail", ""),
            "api_cisteni_kontaktu_odeslano": api_contacts_sent_clear,
            "api_cisteni_davky": len(api_clear_batch_results),
            "diff_chyba": api_diff_error,
        }
        st.json(api_summary)
        if api_diff_summary:
            updated_details = api_diff_summary.get("updated_details", [])
            if updated_details:
                st.markdown("#### Diff: aktualizované kontakty (náhled)")
                st.dataframe(pd.DataFrame(updated_details[:100]), use_container_width=True)
        st.markdown("#### Výsledky testovací dávky a dalších dávek")
        st.dataframe(pd.DataFrame(api_batch_results), use_container_width=True)
        st.markdown("#### Náhled kontrolního běhu (prvních 50 kontaktů)")
        st.json(api_contacts_preview)

    try:
        append_job_history(
            {
                "mode": execution_mode,
                "status": api_status if api_mode_enabled else "csv_export_ok",
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
history_alerts = summarize_job_alerts(history_rows)
if history_alerts["recent_failures"] >= 3:
    st.error("Upozornění: posledních 10 běhů obsahuje 3+ selhání.")
elif history_alerts["failure_rate"] >= 0.3 and history_alerts["total"] >= 5:
    st.warning("Upozornění: míra selhání v historii je >= 30 %. Zkontroluj API konfiguraci a data.")
else:
    st.caption("Historie běhů bez kritického alertu.")

if history_rows:
    st.dataframe(pd.DataFrame(history_rows), use_container_width=True)
else:
    st.caption("Historie běhů je zatím prázdná.")
