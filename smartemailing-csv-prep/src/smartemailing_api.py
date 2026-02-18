from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, List
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://app.smartemailing.cz"

PING_ENDPOINT = "/api/v3/ping"
CUSTOM_FIELDS_ENDPOINTS = ["/api/v3/customfields", "/api/v3/custom-fields"]
CUSTOM_FIELDS_SEARCH_ENDPOINTS = ["/api/v3/customfields/search", "/api/v3/custom-fields/search"]
CONTACT_LISTS_ENDPOINTS = ["/api/v3/contactlists", "/api/v3/contact-lists"]
CONTACT_LISTS_SEARCH_ENDPOINTS = ["/api/v3/contactlists/search", "/api/v3/contact-lists/search"]
CONTACTS_ENDPOINTS = ["/api/v3/contacts", "/api/v3/contact"]
CONTACTS_SEARCH_ENDPOINTS = ["/api/v3/contacts/search", "/api/v3/contact/search"]
CONTACT_CUSTOMFIELD_VALUES_ENDPOINTS = [
    "/api/v3/contact-customfield-values",
    "/api/v3/contactcustomfieldvalues",
    "/api/v3/contact-customfields",
    "/api/v3/contactcustomfields",
    "/api/v3/customfield-values",
    "/api/v3/customfieldvalues",
]
CONTACT_CUSTOMFIELD_VALUES_SEARCH_ENDPOINTS = [
    "/api/v3/contact-customfield-values/search",
    "/api/v3/contactcustomfieldvalues/search",
    "/api/v3/contact-customfields/search",
    "/api/v3/contactcustomfields/search",
    "/api/v3/customfield-values/search",
    "/api/v3/customfieldvalues/search",
]
DEFAULT_MANAGED_EMPTY_CUSTOM_FIELD_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{1,}$"
IMPORT_CONTACTS_ENDPOINTS = ["/api/v3/import"]
CONTACTS_IN_LIST_ENDPOINT_TEMPLATES = [
    "/api/v3/contactlists/{list_id}/contacts",
    "/api/v3/contact-lists/{list_id}/contacts",
]
CONTACTS_IN_LIST_SEARCH_ENDPOINT_TEMPLATES = [
    "/api/v3/contactlists/{list_id}/contacts/search",
    "/api/v3/contact-lists/{list_id}/contacts/search",
]
CONTACT_DETAIL_ENDPOINT_TEMPLATES = [
    "/api/v3/contacts/{contact_id}",
    "/api/v3/contacts/{id}",
    "/api/v3/contact/{contact_id}",
    "/api/v3/contact/{id}",
]
SYSTEM_FIELD_KEY_ALIASES = {
    "city": "town",
}


@dataclass(frozen=True)
class SmartEmailingCredentials:
    username: str
    api_key: str
    base_url: str = DEFAULT_BASE_URL


class SmartEmailingApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class ImportBatchResult:
    endpoint: str
    payload_variant: str
    response: dict[str, Any]
    sent_contacts: int
    batch_index: int
    canary: bool
    started_at: str
    finished_at: str


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_base_url(base_url: str) -> str:
    cleaned = str(base_url).strip()
    if not cleaned:
        return DEFAULT_BASE_URL
    return cleaned.rstrip("/")


def build_basic_auth_header(username: str, api_key: str) -> str:
    token = base64.b64encode(f"{username}:{api_key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if not isinstance(payload, dict):
        return []

    candidates: list[Any] = []
    data = payload.get("data")
    if isinstance(data, list):
        candidates.append(data)
    elif isinstance(data, dict):
        for key in [
            "items",
            "customfields",
            "custom_fields",
            "contactlists",
            "contact_lists",
            "results",
            "records",
        ]:
            values = data.get(key)
            if isinstance(values, list):
                candidates.append(values)
            elif isinstance(values, dict):
                dict_values = [x for x in values.values() if isinstance(x, dict)]
                if dict_values:
                    candidates.append(dict_values)
        dict_values = [x for x in data.values() if isinstance(x, dict)]
        if dict_values:
            candidates.append(dict_values)
        candidates.append([data])

    for key in ["items", "customfields", "custom_fields", "contactlists", "contact_lists", "results", "records", "collection"]:
        values = payload.get(key)
        if isinstance(values, list):
            candidates.append(values)
        elif isinstance(values, dict):
            dict_values = [x for x in values.values() if isinstance(x, dict)]
            if dict_values:
                candidates.append(dict_values)

    for key in ["item", "customfield", "custom_field", "contactlist", "contact_list"]:
        single = payload.get(key)
        if isinstance(single, dict):
            candidates.append([single])

    # Some endpoints return a single object at root level.
    candidates.append([payload])

    for values in candidates:
        if not isinstance(values, list):
            continue
        dict_items = [x for x in values if isinstance(x, dict)]
        if dict_items:
            return dict_items
    return []


def _collect_contact_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [item]
    attrs = item.get("attributes")
    if isinstance(attrs, dict):
        sources.append(attrs)
    for nested_key in ["data", "item", "contact", "record"]:
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
            nested_attrs = nested.get("attributes")
            if isinstance(nested_attrs, dict):
                sources.append(nested_attrs)
    return sources


def _pick_value(item: dict[str, Any], keys: list[str]) -> str:
    attrs = item.get("attributes")
    attrs_dict = attrs if isinstance(attrs, dict) else {}
    nested_dicts: list[dict[str, Any]] = []
    for nested_key in ["data", "customfield", "custom_field", "contactlist", "contact_list", "item"]:
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            nested_dicts.append(nested)
    nested_attrs = [
        nested.get("attributes") for nested in nested_dicts if isinstance(nested.get("attributes"), dict)
    ]
    for key in keys:
        value = item.get(key)
        if value is None and attrs_dict:
            value = attrs_dict.get(key)
        if value is None:
            for nested in nested_dicts:
                value = nested.get(key)
                if value is not None:
                    break
        if value is None:
            for nested_attr in nested_attrs:
                value = nested_attr.get(key)
                if value is not None:
                    break
        if value is None:
            continue
        as_str = str(value).strip()
        if as_str:
            return as_str
    return ""


def _pick_id(item: dict[str, Any], keys: list[str]) -> str:
    attrs = item.get("attributes")
    attrs_dict = attrs if isinstance(attrs, dict) else {}
    nested_dicts: list[dict[str, Any]] = []
    for nested_key in ["data", "customfield", "custom_field", "contactlist", "contact_list", "item"]:
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            nested_dicts.append(nested)
    nested_attrs = [
        nested.get("attributes") for nested in nested_dicts if isinstance(nested.get("attributes"), dict)
    ]
    for key in keys:
        value = item.get(key)
        if value is None and attrs_dict:
            value = attrs_dict.get(key)
        if value is None:
            for nested in nested_dicts:
                value = nested.get(key)
                if value is not None:
                    break
        if value is None:
            for nested_attr in nested_attrs:
                value = nested_attr.get(key)
                if value is not None:
                    break
        if value in [None, ""]:
            continue
        as_str = str(value).strip()
        if as_str:
            return as_str
    return ""


def _pick_raw_value(item: dict[str, Any], keys: list[str]) -> Any:
    attrs = item.get("attributes")
    attrs_dict = attrs if isinstance(attrs, dict) else {}
    nested_dicts: list[dict[str, Any]] = []
    for nested_key in ["data", "customfield", "custom_field", "contactlist", "contact_list", "item", "contact", "record"]:
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            nested_dicts.append(nested)
    nested_attrs = [
        nested.get("attributes") for nested in nested_dicts if isinstance(nested.get("attributes"), dict)
    ]
    for key in keys:
        if key in item and item.get(key) not in [None, ""]:
            return item.get(key)
        if attrs_dict and key in attrs_dict and attrs_dict.get(key) not in [None, ""]:
            return attrs_dict.get(key)
        for nested in nested_dicts:
            if key in nested and nested.get(key) not in [None, ""]:
                return nested.get(key)
        for nested_attr in nested_attrs:
            if key in nested_attr and nested_attr.get(key) not in [None, ""]:
                return nested_attr.get(key)
    return None


def _extract_customfields_from_contact_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources = _collect_contact_sources(item)

    containers: list[Any] = []
    for source in sources:
        for key in ["customfields", "custom_fields"]:
            value = source.get(key)
            if value is not None:
                containers.append(value)

    custom_by_id: dict[str, Any] = {}

    def add_custom_value(field_id: str, value: Any) -> None:
        resolved_id = str(field_id).strip()
        if not resolved_id or value in [None, ""]:
            return
        custom_by_id[resolved_id] = value

    for container in containers:
        if isinstance(container, list):
            for raw_item in container:
                if not isinstance(raw_item, dict):
                    continue
                field_id = _pick_id(raw_item, ["id", "customfield_id", "custom_field_id"])
                field_value = _pick_raw_value(
                    raw_item,
                    ["value", "values", "customfield_value", "custom_field_value"],
                )
                if field_value is None:
                    continue
                add_custom_value(field_id, field_value)
        elif isinstance(container, dict):
            for key, value in container.items():
                if isinstance(value, dict):
                    field_id = _pick_id(value, ["id", "customfield_id", "custom_field_id"]) or str(key).strip()
                    field_value = _pick_raw_value(
                        value,
                        ["value", "values", "customfield_value", "custom_field_value"],
                    )
                    if field_value is None:
                        continue
                    add_custom_value(field_id, field_value)
                else:
                    add_custom_value(str(key).strip(), value)

    return [{"id": field_id, "value": value} for field_id, value in custom_by_id.items()]


def _contact_item_has_customfields_key(item: dict[str, Any]) -> bool:
    for source in _collect_contact_sources(item):
        if "customfields" in source or "custom_fields" in source:
            return True
    return False


def _extract_tags_from_contact_item(item: dict[str, Any]) -> list[str]:
    sources = _collect_contact_sources(item)

    tags: set[str] = set()
    for source in sources:
        raw_tags = source.get("tags")
        if raw_tags is None:
            continue
        if isinstance(raw_tags, str):
            value = raw_tags.strip()
            if value:
                tags.add(value)
            continue
        if isinstance(raw_tags, list):
            for raw_tag in raw_tags:
                if isinstance(raw_tag, dict):
                    tag_value = _pick_value(raw_tag, ["name", "tag", "value", "title", "label"])
                    if tag_value:
                        tags.add(tag_value)
                else:
                    value = str(raw_tag).strip()
                    if value:
                        tags.add(value)
    return sorted(tags)


def extract_contacts(payload: Any) -> list[dict[str, Any]]:
    system_keys = [
        "name",
        "surname",
        "titlesbefore",
        "titlesafter",
        "company",
        "town",
        "city",
        "country",
        "notes",
        "phone",
        "mobile",
        "cellphone",
        "street",
        "address",
        "zip",
        "postalcode",
        "state",
        "blacklisted",
    ]

    by_email: dict[str, dict[str, Any]] = {}
    for item in _extract_items(payload):
        email = _pick_value(item, ["emailaddress", "email", "email_address"])
        if not email:
            continue
        email_key = str(email).strip().casefold()
        if not email_key:
            continue

        row = by_email.get(email_key, {"emailaddress": str(email).strip()})
        contact_id = _pick_id(item, ["id", "contact_id"])
        if contact_id and not str(row.get("id", "")).strip():
            row["id"] = str(contact_id).strip()
        for key in system_keys:
            value = _pick_raw_value(item, [key])
            if value in [None, ""]:
                continue
            value_str = str(value).strip()
            if not value_str:
                continue
            resolved_key = SYSTEM_FIELD_KEY_ALIASES.get(key.casefold(), key)
            if resolved_key == "blacklisted":
                current_value = str(row.get(resolved_key, "")).strip()
                if not current_value:
                    row[resolved_key] = value_str
                elif _is_truthy_flag(current_value) and not _is_truthy_flag(value_str):
                    # Conservative merge: if duplicate rows disagree, keep non-blacklisted value.
                    # This prevents false positives when one duplicate contact row is stale/blacklisted.
                    row[resolved_key] = value_str
                continue
            if not str(row.get(resolved_key, "")).strip():
                row[resolved_key] = value_str

        extracted_custom_fields = _extract_customfields_from_contact_item(item)
        if extracted_custom_fields:
            merged_custom_map: dict[str, Any] = {
                str(x.get("id", "")).strip(): x.get("value")
                for x in row.get("customfields", [])
                if isinstance(x, dict) and str(x.get("id", "")).strip()
            }
            for custom_field in extracted_custom_fields:
                field_id = str(custom_field.get("id", "")).strip()
                if not field_id:
                    continue
                merged_custom_map[field_id] = custom_field.get("value")
            row["customfields"] = [{"id": field_id, "value": value} for field_id, value in merged_custom_map.items()]
        elif _contact_item_has_customfields_key(item) and "customfields" not in row:
            row["customfields"] = []

        extracted_tags = _extract_tags_from_contact_item(item)
        if extracted_tags:
            merged_tags = {str(x).strip() for x in row.get("tags", []) if str(x).strip()}
            merged_tags.update(extracted_tags)
            row["tags"] = sorted(merged_tags)

        by_email[email_key] = row

    return list(by_email.values())


def extract_contact_custom_field_values(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in _extract_items(payload):
        pair_id = _pick_id(item, ["id", "contact_customfield_value_id", "contactcustomfieldvalue_id"])
        contact_id = _pick_id(item, ["contact_id", "contactid"])
        customfield_id = _pick_id(item, ["customfield_id", "custom_field_id", "field_id"])
        if not contact_id or not customfield_id:
            continue

        value = _pick_raw_value(item, ["value", "customfield_value", "custom_field_value"])
        option_id = _pick_id(item, ["customfield_options_id", "custom_field_options_id", "option_id"])
        if value in [None, ""] and option_id:
            value = option_id
        if value is None:
            value = ""

        dedupe_key = f"{contact_id}:{customfield_id}:{value}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(
            {
                "pair_id": str(pair_id).strip(),
                "contact_id": str(contact_id).strip(),
                "customfield_id": str(customfield_id).strip(),
                "value": value,
            }
        )

    return rows


def extract_custom_fields(payload: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _extract_items(payload):
        field_id = _pick_id(item, ["id", "customfield_id", "custom_field_id"])
        name = _pick_value(
            item,
            ["name", "label", "title", "custom_field_name", "customfield_name", "field_name"],
        )
        field_type = _pick_value(item, ["type", "field_type"])
        if not name:
            continue
        dedupe_key = f"{field_id}:{name}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append({"id": field_id, "name": name, "type": field_type})
    return rows


def extract_custom_field_names(payload: Any) -> list[str]:
    return [x["name"] for x in extract_custom_fields(payload)]


def extract_contact_lists(payload: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _extract_items(payload):
        list_id = _pick_id(item, ["id", "contactlist_id", "contact_list_id"])
        name = _pick_value(item, ["name", "title", "label", "contactlist_name", "contact_list_name", "list_name"])
        if not list_id and not name:
            continue
        dedupe_key = f"{list_id}:{name}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append({"id": list_id, "name": name})
    return rows


def combine_schema_columns(system_columns: Iterable[str], custom_field_columns: Iterable[str]) -> List[str]:
    out: list[str] = []
    seen: set[str] = set()

    for columns in [system_columns, custom_field_columns]:
        for raw in columns:
            col = str(raw).strip()
            if not col or col in seen:
                continue
            seen.add(col)
            out.append(col)

    return out


def _unique_ordered(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _reorder_preferred(values: list[str], preferred: str) -> list[str]:
    out = _unique_ordered(values)
    preferred_value = str(preferred).strip()
    if preferred_value and preferred_value in out:
        out.remove(preferred_value)
        out.insert(0, preferred_value)
    return out


def _chunked(values: list[str], chunk_size: int) -> list[list[str]]:
    size = max(1, int(chunk_size))
    return [values[i : i + size] for i in range(0, len(values), size)]


def _is_truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return int(value) != 0
        except Exception:
            return bool(value)
    normalized = str(value).strip().casefold()
    if not normalized:
        return False
    return normalized in {"1", "true", "yes", "y", "on", "ano", "blacklisted", "blocked"}


def _custom_field_expects_array(
    field: dict[str, str],
    forced_array_custom_field_names: set[str],
) -> bool:
    field_name = str(field.get("name", "")).strip().casefold()
    if field_name and field_name in forced_array_custom_field_names:
        return True
    field_type = str(field.get("type", "")).strip().casefold()
    if not field_type:
        return False
    return any(token in field_type for token in ["array", "multi", "multiple", "multiselect", "checkbox", "select_many"])


def _parse_array_custom_field_value(raw_value: str, split_separators: list[str]) -> list[str]:
    value = str(raw_value).strip()
    if not value:
        return []

    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                out = [str(x).strip() for x in parsed if str(x).strip()]
                if out:
                    return out
        except Exception:
            pass

    separators = [str(x) for x in split_separators if str(x)]
    if not separators:
        return [value]
    pattern = "|".join(re.escape(x) for x in separators)
    parts = re.split(pattern, value)
    return [p.strip() for p in parts if p.strip()]


def build_api_contacts_from_import_df(
    import_df: Any,
    api_system_field_map: dict[str, str],
    custom_fields: list[dict[str, str]],
    list_id: str = "",
    list_status: str = "confirmed",
    tag: str = "",
    strict_custom_fields: bool = True,
    ignore_missing_custom_for_columns: Iterable[str] | None = None,
    array_custom_field_names: Iterable[str] | None = None,
    array_value_split_separators: Iterable[str] | None = None,
    managed_empty_custom_field_name_pattern: str = DEFAULT_MANAGED_EMPTY_CUSTOM_FIELD_NAME_PATTERN,
    managed_custom_field_ids_allowlist: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Converts final import dataframe to API contact payload list.
    Returns (contacts, issues).
    """
    custom_fields_by_name = {str(x.get("name", "")).strip().casefold(): x for x in custom_fields}
    ignore_missing_custom_for_columns_set = {
        str(x).strip().casefold()
        for x in (ignore_missing_custom_for_columns or [])
        if str(x).strip()
    }
    forced_array_custom_field_names = {
        str(x).strip().casefold()
        for x in (array_custom_field_names or [])
        if str(x).strip()
    }
    has_managed_allowlist = managed_custom_field_ids_allowlist is not None
    managed_custom_field_allowlist_ids = {
        str(x).strip()
        for x in (managed_custom_field_ids_allowlist or [])
        if str(x).strip()
    }
    array_value_split_separators_list = [str(x) for x in (array_value_split_separators or [",", ";", "|", "/"]) if str(x)]
    managed_empty_name_regex = None
    try:
        if str(managed_empty_custom_field_name_pattern).strip():
            managed_empty_name_regex = re.compile(str(managed_empty_custom_field_name_pattern).strip())
    except Exception:
        managed_empty_name_regex = None

    mapped_source_columns = {str(src).strip() for src in api_system_field_map.keys() if str(src).strip()}
    contacts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for row_index, row in import_df.iterrows():
        contact: dict[str, Any] = {}
        managed_custom_field_ids: set[str] = set()

        for source_col, api_key in api_system_field_map.items():
            src = str(source_col).strip()
            key = str(api_key).strip()
            key_alias = SYSTEM_FIELD_KEY_ALIASES.get(key.casefold(), "")
            if key_alias:
                key = key_alias
            if not src or not key:
                continue
            if src not in import_df.columns:
                continue
            value = str(row.get(src, "")).strip()
            if not value:
                continue
            contact[key] = value

        email = str(contact.get("emailaddress", "")).strip()
        if not email:
            issues.append({"row_index": int(row_index), "issue": "missing_emailaddress", "detail": ""})
            continue

        custom_values: list[dict[str, Any]] = []
        for col in import_df.columns:
            if col in mapped_source_columns:
                continue
            field = custom_fields_by_name.get(str(col).strip().casefold())
            if field is None:
                value = str(row.get(col, "")).strip()
                if not value:
                    continue
                if str(col).strip().casefold() in ignore_missing_custom_for_columns_set:
                    continue
                if strict_custom_fields:
                    issues.append(
                        {
                            "row_index": int(row_index),
                            "issue": "missing_custom_field",
                            "detail": str(col),
                        }
                    )
                continue

            field_id = str(field.get("id", "")).strip()
            value = str(row.get(col, "")).strip()
            include_in_managed_set = False
            if has_managed_allowlist:
                include_in_managed_set = bool(field_id and field_id in managed_custom_field_allowlist_ids)
            else:
                include_in_managed_set = bool(value)
                if not include_in_managed_set and managed_empty_name_regex is not None:
                    include_in_managed_set = bool(managed_empty_name_regex.fullmatch(str(col).strip()))
            if field_id and include_in_managed_set:
                managed_custom_field_ids.add(field_id)
            if not value:
                continue
            if field_id:
                field_value: Any = value
                if _custom_field_expects_array(field, forced_array_custom_field_names):
                    parsed_values = _parse_array_custom_field_value(value, array_value_split_separators_list)
                    if not parsed_values:
                        continue
                    field_value = parsed_values
                custom_values.append({"id": field_id, "value": field_value})
            elif strict_custom_fields:
                issues.append(
                    {
                        "row_index": int(row_index),
                        "issue": "missing_custom_field_id",
                        "detail": str(col),
                    }
                )

        if custom_values:
            contact["customfields"] = custom_values
        if managed_custom_field_ids:
            contact["__managed_custom_field_ids"] = sorted(managed_custom_field_ids)

        if list_id:
            contact["contactlists"] = [{"id": list_id, "status": list_status}]

        if tag:
            # Some API accounts support tags directly in import payload.
            contact["tags"] = [tag]

        contacts.append(contact)

    return contacts, issues


class SmartEmailingApiClient:
    def __init__(
        self,
        credentials: SmartEmailingCredentials,
        timeout_sec: int = 20,
        read_only: bool = False,
    ) -> None:
        self.credentials = credentials
        self.base_url = normalize_base_url(credentials.base_url)
        self.timeout_sec = int(timeout_sec)
        self.read_only = bool(read_only)
        self._import_preferred_endpoint: str = ""
        self._import_preferred_payload_variant: str = ""
        self._contacts_lookup_preferred_get_endpoint: str = ""
        self._contacts_lookup_preferred_post_endpoint: str = ""
        self._contacts_lookup_preferred_query_key: str = ""
        self._contacts_lookup_preferred_include_key: str = ""
        self._contacts_lookup_preferred_body_variant: str = ""
        self._contacts_in_list_targeted_preferred_endpoint: str = ""
        self._contacts_in_list_targeted_preferred_body_variant: str = ""
        self._contacts_in_list_targeted_preferred_include_variant: str = ""

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        method_upper = str(method).strip().upper() or "GET"
        if self.read_only and method_upper != "GET":
            raise SmartEmailingApiError(
                f"SmartEmailing API read-only režim: {method_upper} {path} je zakázané.",
                status_code=405,
            )

        query = query or {}
        full_query = parse.urlencode(query, doseq=True)
        url = f"{self.base_url}{path}"
        if full_query:
            url = f"{url}?{full_query}"

        payload_bytes = None
        if body is not None:
            payload_bytes = json.dumps(body).encode("utf-8")

        req = request.Request(url=url, method=method_upper, data=payload_bytes)
        req.add_header("Accept", "application/json")
        req.add_header("Authorization", build_basic_auth_header(self.credentials.username, self.credentials.api_key))
        if body is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with request.urlopen(req, timeout=self.timeout_sec) as response:
                raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            raise SmartEmailingApiError(
                f"SmartEmailing API HTTP {exc.code} for {path}",
                status_code=int(exc.code),
                body=body_text,
            ) from exc
        except error.URLError as exc:
            raise SmartEmailingApiError(f"Nepodařilo se spojit se SmartEmailing API: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SmartEmailingApiError(f"SmartEmailing API vrátilo neplatný JSON pro {path}") from exc

    def _request_with_endpoint_fallback(
        self,
        method: str,
        endpoints: list[str],
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        fallback_statuses: set[int] | None = None,
    ) -> tuple[Any, str]:
        fallback_statuses = fallback_statuses or {404, 405}
        last_error: SmartEmailingApiError | None = None

        for path in endpoints:
            try:
                payload = self._request_json(method, path, query=query, body=body)
                return payload, path
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                if exc.status_code is not None and int(exc.status_code) in fallback_statuses:
                    last_error = exc
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError("Nepodařilo se najít funkční API endpoint.")

    def ping(self) -> dict[str, Any]:
        data = self._request_json("GET", PING_ENDPOINT)
        if isinstance(data, dict):
            return data
        raise SmartEmailingApiError("Neočekávaná odpověď z /api/v3/ping")

    def fetch_custom_fields(
        self,
        page_limit: int = 100,
        max_pages: int = 30,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
    ) -> list[dict[str, str]]:
        get_endpoints = CUSTOM_FIELDS_ENDPOINTS if endpoint_candidates is None else endpoint_candidates
        post_endpoints = (
            CUSTOM_FIELDS_SEARCH_ENDPOINTS if search_endpoint_candidates is None else search_endpoint_candidates
        )
        if self.read_only:
            post_endpoints = []

        best_rows: list[dict[str, str]] = []
        any_success = False
        last_error: SmartEmailingApiError | None = None

        for endpoint in get_endpoints:
            try:
                rows = self._fetch_paginated_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_custom_fields,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                any_success = True
                if len(rows) > len(best_rows):
                    best_rows = rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        for endpoint in post_endpoints:
            try:
                rows = self._fetch_paginated_post_search_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_custom_fields,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                any_success = True
                if len(rows) > len(best_rows):
                    best_rows = rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        if any_success:
            return best_rows
        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError("Nepodařilo se načíst custom fields ze SmartEmailing API.")

    def fetch_custom_field_names(
        self,
        page_limit: int = 100,
        max_pages: int = 30,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
    ) -> list[str]:
        return [
            x["name"]
            for x in self.fetch_custom_fields(
                page_limit=page_limit,
                max_pages=max_pages,
                endpoint_candidates=endpoint_candidates,
                search_endpoint_candidates=search_endpoint_candidates,
            )
        ]

    def create_custom_field(
        self,
        name: str,
        field_type: str = "text",
        endpoint_candidates: list[str] | None = None,
    ) -> dict[str, str]:
        field_name = str(name).strip()
        resolved_type = str(field_type).strip() or "text"
        if not field_name:
            raise ValueError("Název custom fieldu nesmí být prázdný.")

        endpoints = endpoint_candidates or CUSTOM_FIELDS_ENDPOINTS
        payload_variants: list[tuple[str, dict[str, Any]]] = [
            ("flat", {"name": field_name, "type": resolved_type}),
            ("data_wrap", {"data": {"name": field_name, "type": resolved_type}}),
            ("customfield_wrap", {"customfield": {"name": field_name, "type": resolved_type}}),
        ]

        fallback_errors: list[tuple[str, str, SmartEmailingApiError]] = []
        for endpoint in endpoints:
            for payload_variant, payload in payload_variants:
                try:
                    response_payload = self._request_json("POST", endpoint, body=payload)
                    created = extract_custom_fields(response_payload)
                    if created:
                        return created[0]
                    return {"id": "", "name": field_name, "type": resolved_type}
                except SmartEmailingApiError as exc:
                    if exc.status_code in {401, 403}:
                        raise
                    message_lc = str(exc).casefold()
                    body_lc = str(exc.body or "").casefold()
                    if exc.status_code == 409 or "exist" in message_lc or "exist" in body_lc:
                        return {"id": "", "name": field_name, "type": resolved_type}
                    if exc.status_code in {400, 404, 405, 415, 422}:
                        fallback_errors.append((endpoint, payload_variant, exc))
                        continue
                    raise

        if fallback_errors:
            non_404 = [x for x in fallback_errors if x[2].status_code not in {404, 405}]
            chosen_endpoint, chosen_variant, chosen_error = (non_404[0] if non_404 else fallback_errors[-1])
            detail = chosen_error.body.strip()
            detail_preview = f" Detail API: {detail[:300]}" if detail else ""
            raise SmartEmailingApiError(
                f"Nepodařilo se vytvořit custom field '{field_name}': {chosen_error}. "
                f"Vybraná chyba: {chosen_endpoint} ({chosen_variant}).{detail_preview}",
                status_code=chosen_error.status_code,
                body=chosen_error.body,
            )
        raise SmartEmailingApiError(f"Nepodařilo se vytvořit custom field '{field_name}'.")

    def fetch_contact_lists(
        self,
        page_limit: int = 100,
        max_pages: int = 30,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
    ) -> list[dict[str, str]]:
        get_endpoints = CONTACT_LISTS_ENDPOINTS if endpoint_candidates is None else endpoint_candidates
        post_endpoints = (
            CONTACT_LISTS_SEARCH_ENDPOINTS if search_endpoint_candidates is None else search_endpoint_candidates
        )
        if self.read_only:
            post_endpoints = []

        best_rows: list[dict[str, str]] = []
        any_success = False
        last_error: SmartEmailingApiError | None = None

        for endpoint in get_endpoints:
            try:
                rows = self._fetch_paginated_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contact_lists,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                any_success = True
                if len(rows) > len(best_rows):
                    best_rows = rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        for endpoint in post_endpoints:
            try:
                rows = self._fetch_paginated_post_search_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contact_lists,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                any_success = True
                if len(rows) > len(best_rows):
                    best_rows = rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        if any_success:
            return best_rows
        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError("Nepodařilo se načíst contact listy ze SmartEmailing API.")

    def fetch_contacts(
        self,
        page_limit: int = 100,
        max_pages: int = 100,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
        email_keys_filter: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        get_endpoints = CONTACTS_ENDPOINTS if endpoint_candidates is None else endpoint_candidates
        post_endpoints = CONTACTS_SEARCH_ENDPOINTS if search_endpoint_candidates is None else search_endpoint_candidates
        if self.read_only:
            post_endpoints = []

        wanted = {
            str(x).strip().casefold()
            for x in (email_keys_filter or set())
            if str(x).strip()
        }
        by_email: dict[str, dict[str, Any]] = {}
        any_success = False
        last_error: SmartEmailingApiError | None = None

        def merge_rows(rows: list[dict[str, Any]]) -> None:
            for row in rows:
                email_key = str(row.get("emailaddress", "")).strip().casefold()
                if not email_key:
                    continue
                if wanted and email_key not in wanted:
                    continue
                existing = by_email.get(email_key)
                if existing is None:
                    by_email[email_key] = row
                    continue
                existing_has_cf = isinstance(existing.get("customfields"), list)
                row_has_cf = isinstance(row.get("customfields"), list)
                if row_has_cf and not existing_has_cf:
                    by_email[email_key] = row
                    continue
                merged = dict(existing)
                for key, value in row.items():
                    if value in [None, "", []]:
                        continue
                    if key == "customfields" and existing_has_cf and not row_has_cf:
                        continue
                    merged[key] = value
                by_email[email_key] = merged

        for endpoint in get_endpoints:
            try:
                rows = self._fetch_paginated_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contacts,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                merge_rows(rows)
                any_success = True
                if wanted and len(by_email) >= len(wanted):
                    return list(by_email.values())
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        for endpoint in post_endpoints:
            try:
                rows = self._fetch_paginated_post_search_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contacts,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                merge_rows(rows)
                any_success = True
                if wanted and len(by_email) >= len(wanted):
                    return list(by_email.values())
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        if any_success:
            return list(by_email.values())
        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError("Nepodařilo se načíst kontakty ze SmartEmailing API.")

    def fetch_contacts_by_emails(
        self,
        email_keys: set[str],
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
        max_workers: int = 6,
    ) -> dict[str, dict[str, Any]]:
        get_endpoints_raw = CONTACTS_ENDPOINTS if endpoint_candidates is None else endpoint_candidates
        post_endpoints_raw = CONTACTS_SEARCH_ENDPOINTS if search_endpoint_candidates is None else search_endpoint_candidates
        get_endpoints = _reorder_preferred(
            [str(x).strip() for x in get_endpoints_raw if str(x).strip()],
            self._contacts_lookup_preferred_get_endpoint,
        )
        post_endpoints = _reorder_preferred(
            [str(x).strip() for x in post_endpoints_raw if str(x).strip()],
            self._contacts_lookup_preferred_post_endpoint,
        )
        if self.read_only:
            post_endpoints = []

        wanted = {
            str(x).strip().casefold()
            for x in (email_keys or set())
            if str(x).strip()
        }
        found: dict[str, dict[str, Any]] = {}
        if not wanted:
            return found

        def pick_matching_contact(payload: Any, email_key: str) -> dict[str, Any] | None:
            rows = extract_contacts(payload)
            for row in rows:
                row_email_key = str(row.get("emailaddress", "")).strip().casefold()
                if row_email_key == email_key:
                    return row
            return None

        def build_include_extra(include_key: str) -> dict[str, Any]:
            key = str(include_key).strip()
            if key == "include":
                return {"include": "customfields"}
            if key == "expand":
                return {"expand": "customfields"}
            if key == "with":
                return {"with": "customfields"}
            if key == "customfields":
                return {"customfields": 1}
            return {}

        def build_query_base(variant_key: str, email_key: str) -> dict[str, Any]:
            key = str(variant_key).strip()
            if key == "emailaddress":
                return {"emailaddress": email_key}
            if key == "email":
                return {"email": email_key}
            if key == "search":
                return {"search": email_key}
            if key == "query":
                return {"query": email_key}
            if key == "q":
                return {"q": email_key}
            return {}

        def build_body_base(variant_key: str, email_key: str) -> dict[str, Any]:
            key = str(variant_key).strip()
            if key == "emailaddress":
                return {"emailaddress": email_key}
            if key == "email":
                return {"email": email_key}
            if key == "search_emailaddress":
                return {"search": {"emailaddress": email_key}}
            if key == "search_email":
                return {"search": {"email": email_key}}
            if key == "filter_emailaddress":
                return {"filter": {"emailaddress": email_key}}
            if key == "filter_email":
                return {"filter": {"email": email_key}}
            if key == "where_emailaddress":
                return {"where": {"emailaddress": email_key}}
            if key == "where_email":
                return {"where": {"email": email_key}}
            if key == "query":
                return {"query": email_key}
            return {}

        include_variant_keys = ["", "include", "expand", "with", "customfields"]
        query_variant_keys = ["emailaddress", "email", "search", "query", "q"]
        body_variant_keys = [
            "emailaddress",
            "email",
            "search_emailaddress",
            "search_email",
            "filter_emailaddress",
            "filter_email",
            "where_emailaddress",
            "where_email",
            "query",
        ]

        def search_one_email(email_key: str) -> dict[str, Any] | None:
            preferred_get_endpoint = str(self._contacts_lookup_preferred_get_endpoint).strip()
            preferred_query_key = str(self._contacts_lookup_preferred_query_key).strip()
            preferred_include_key = str(self._contacts_lookup_preferred_include_key).strip()
            if preferred_get_endpoint and preferred_query_key:
                query = build_query_base(preferred_query_key, email_key)
                query.update(build_include_extra(preferred_include_key))
                if query:
                    try:
                        payload = self._request_json("GET", preferred_get_endpoint, query=query)
                        matched = pick_matching_contact(payload, email_key)
                        if matched is not None:
                            return matched
                    except SmartEmailingApiError as exc:
                        if exc.status_code in {401, 403}:
                            raise

            preferred_post_endpoint = str(self._contacts_lookup_preferred_post_endpoint).strip()
            preferred_body_variant = str(self._contacts_lookup_preferred_body_variant).strip()
            preferred_include_key = str(self._contacts_lookup_preferred_include_key).strip()
            if post_endpoints and preferred_post_endpoint and preferred_body_variant:
                body = build_body_base(preferred_body_variant, email_key)
                body.update(build_include_extra(preferred_include_key))
                if body:
                    try:
                        payload = self._request_json("POST", preferred_post_endpoint, body=body)
                        matched = pick_matching_contact(payload, email_key)
                        if matched is not None:
                            return matched
                    except SmartEmailingApiError as exc:
                        if exc.status_code in {401, 403}:
                            raise

            for endpoint in get_endpoints:
                for query_key in query_variant_keys:
                    for include_key in include_variant_keys:
                        query = build_query_base(query_key, email_key)
                        if not query:
                            continue
                        query.update(build_include_extra(include_key))
                        try:
                            payload = self._request_json("GET", endpoint, query=query)
                        except SmartEmailingApiError as exc:
                            if exc.status_code in {401, 403}:
                                raise
                            continue
                        matched = pick_matching_contact(payload, email_key)
                        if matched is not None:
                            self._contacts_lookup_preferred_get_endpoint = endpoint
                            self._contacts_lookup_preferred_query_key = query_key
                            self._contacts_lookup_preferred_include_key = include_key
                            return matched

            for endpoint in post_endpoints:
                for body_variant in body_variant_keys:
                    for include_key in include_variant_keys:
                        body = build_body_base(body_variant, email_key)
                        if not body:
                            continue
                        body.update(build_include_extra(include_key))
                        try:
                            payload = self._request_json("POST", endpoint, body=body)
                        except SmartEmailingApiError as exc:
                            if exc.status_code in {401, 403}:
                                raise
                            continue
                        matched = pick_matching_contact(payload, email_key)
                        if matched is not None:
                            self._contacts_lookup_preferred_post_endpoint = endpoint
                            self._contacts_lookup_preferred_body_variant = body_variant
                            self._contacts_lookup_preferred_include_key = include_key
                            return matched
            return None

        wanted_list = sorted(wanted)
        resolved_max_workers = max(1, int(max_workers))
        if resolved_max_workers == 1 or len(wanted_list) <= 1:
            for email_key in wanted_list:
                matched = search_one_email(email_key)
                if matched is not None:
                    found[email_key] = matched
            return found

        with ThreadPoolExecutor(max_workers=min(resolved_max_workers, len(wanted_list))) as pool:
            future_to_email = {pool.submit(search_one_email, email_key): email_key for email_key in wanted_list}
            for future in as_completed(future_to_email):
                email_key = future_to_email[future]
                try:
                    matched = future.result()
                except SmartEmailingApiError as exc:
                    if exc.status_code in {401, 403}:
                        raise
                    continue
                except Exception:
                    continue
                if matched is not None:
                    found[email_key] = matched

        return found

    def fetch_blacklisted_email_keys(
        self,
        email_keys: set[str],
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
        max_workers: int = 6,
    ) -> set[str]:
        wanted = {
            str(x).strip().casefold()
            for x in (email_keys or set())
            if str(x).strip()
        }
        if not wanted:
            return set()
        rows_by_email = self.fetch_contacts_by_emails(
            email_keys=wanted,
            endpoint_candidates=endpoint_candidates,
            search_endpoint_candidates=search_endpoint_candidates,
            max_workers=max_workers,
        )
        blacklisted: set[str] = set()
        for email_key, row in rows_by_email.items():
            if _is_truthy_flag(row.get("blacklisted")):
                blacklisted.add(str(email_key).strip().casefold())
        return blacklisted

    def fetch_custom_field_values_for_contacts(
        self,
        contact_ids: set[str],
        page_limit: int = 100,
        max_pages: int = 100,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        wanted_ids = {str(x).strip() for x in (contact_ids or set()) if str(x).strip()}
        if not wanted_ids:
            return {}

        get_endpoints = (
            CONTACT_CUSTOMFIELD_VALUES_ENDPOINTS if endpoint_candidates is None else endpoint_candidates
        )
        post_endpoints = (
            CONTACT_CUSTOMFIELD_VALUES_SEARCH_ENDPOINTS
            if search_endpoint_candidates is None
            else search_endpoint_candidates
        )
        if self.read_only:
            post_endpoints = []

        by_contact_id: dict[str, dict[str, dict[str, Any]]] = {contact_id: {} for contact_id in wanted_ids}

        def merge_rows(rows: list[dict[str, Any]]) -> None:
            for row in rows:
                contact_id = str(row.get("contact_id", "")).strip()
                field_id = str(row.get("customfield_id", "")).strip()
                if not contact_id or not field_id or contact_id not in wanted_ids:
                    continue
                value = row.get("value")
                pair_id = str(row.get("pair_id", "")).strip()
                by_contact_id.setdefault(contact_id, {})
                by_contact_id[contact_id][field_id] = {"value": value, "pair_id": pair_id}

        for endpoint in get_endpoints:
            try:
                rows = self._fetch_paginated_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contact_custom_field_values,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                merge_rows(rows)
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                continue

        body_variants_bulk: list[dict[str, Any]] = [
            {"contact_ids": sorted(wanted_ids)},
            {"contact_ids": [int(x) for x in sorted(wanted_ids) if str(x).isdigit()]},
            {"search": {"contact_ids": sorted(wanted_ids)}},
            {"filter": {"contact_ids": sorted(wanted_ids)}},
            {"where": {"contact_ids": sorted(wanted_ids)}},
        ]
        body_variants_bulk = [x for x in body_variants_bulk if x]

        for endpoint in post_endpoints:
            for base_body in body_variants_bulk:
                try:
                    rows = self._fetch_paginated_post_search_from_endpoint_custom_body(
                        path=endpoint,
                        item_extractor=extract_contact_custom_field_values,
                        page_limit=page_limit,
                        max_pages=max_pages,
                        base_body=base_body,
                    )
                    merge_rows(rows)
                except SmartEmailingApiError as exc:
                    if exc.status_code in {401, 403}:
                        raise
                    continue

        missing_ids = [x for x in sorted(wanted_ids) if not by_contact_id.get(x)]
        if missing_ids:
            for endpoint in post_endpoints:
                for contact_id in missing_ids:
                    body_variants_single: list[dict[str, Any]] = [
                        {"contact_id": contact_id},
                        {"contact_id": int(contact_id)} if str(contact_id).isdigit() else {},
                        {"search": {"contact_id": contact_id}},
                        {"filter": {"contact_id": contact_id}},
                        {"where": {"contact_id": contact_id}},
                    ]
                    body_variants_single = [x for x in body_variants_single if x]
                    for base_body in body_variants_single:
                        try:
                            rows = self._fetch_paginated_post_search_from_endpoint_custom_body(
                                path=endpoint,
                                item_extractor=extract_contact_custom_field_values,
                                page_limit=page_limit,
                                max_pages=max_pages,
                                base_body=base_body,
                            )
                            merge_rows(rows)
                            if by_contact_id.get(contact_id):
                                break
                        except SmartEmailingApiError as exc:
                            if exc.status_code in {401, 403}:
                                raise
                            continue
                    if by_contact_id.get(contact_id):
                        continue

        result: dict[str, list[dict[str, Any]]] = {}
        for contact_id, field_map in by_contact_id.items():
            if not field_map:
                continue
            result[contact_id] = [
                {
                    "id": str(field_id).strip(),
                    "value": value_data.get("value"),
                    "pair_id": str(value_data.get("pair_id", "")).strip(),
                }
                for field_id, value_data in field_map.items()
                if str(field_id).strip()
            ]
        return result

    def resolve_contact_list_id(
        self,
        list_name_or_id: str,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
    ) -> str:
        wanted = str(list_name_or_id).strip()
        if not wanted:
            return ""
        lists = self.fetch_contact_lists(
            endpoint_candidates=endpoint_candidates,
            search_endpoint_candidates=search_endpoint_candidates,
        )
        lower_wanted = wanted.casefold()
        for item in lists:
            if str(item.get("id", "")).strip() == wanted:
                return str(item.get("id", "")).strip()
            if str(item.get("name", "")).strip().casefold() == lower_wanted:
                return str(item.get("id", "")).strip()
        return ""

    def fetch_contacts_in_list(
        self,
        list_id: str,
        page_limit: int = 100,
        max_pages: int = 100,
        endpoint_templates: list[str] | None = None,
        search_endpoint_templates: list[str] | None = None,
        detail_endpoint_templates: list[str] | None = None,
        enrich_only_email_keys: set[str] | None = None,
        contacts_endpoint_candidates: list[str] | None = None,
        contacts_search_endpoint_candidates: list[str] | None = None,
        custom_field_values_endpoint_candidates: list[str] | None = None,
        custom_field_values_search_endpoint_candidates: list[str] | None = None,
        target_email_batch_size: int = 50,
        read_parallel_workers: int = 6,
        prefer_targeted_search: bool = True,
    ) -> list[dict[str, Any]]:
        resolved_list_id = str(list_id).strip()
        if not resolved_list_id:
            return []

        get_templates = (
            CONTACTS_IN_LIST_ENDPOINT_TEMPLATES if endpoint_templates is None else endpoint_templates
        )
        post_templates = (
            CONTACTS_IN_LIST_SEARCH_ENDPOINT_TEMPLATES
            if search_endpoint_templates is None
            else search_endpoint_templates
        )
        get_endpoints = self._resolve_list_id_endpoint_templates(get_templates, resolved_list_id)
        post_endpoints = self._resolve_list_id_endpoint_templates(post_templates, resolved_list_id)
        if self.read_only:
            post_endpoints = []
            prefer_targeted_search = False

        resolved_contacts_search_endpoint_candidates = (
            [] if self.read_only else contacts_search_endpoint_candidates
        )
        resolved_custom_field_values_search_endpoint_candidates = (
            [] if self.read_only else custom_field_values_search_endpoint_candidates
        )

        target_email_keys = {
            str(x).strip().casefold()
            for x in (enrich_only_email_keys or set())
            if str(x).strip()
        }

        if prefer_targeted_search and target_email_keys:
            targeted_contacts, targeted_supported = self._fetch_contacts_in_list_targeted_by_emails(
                list_id=resolved_list_id,
                target_email_keys=target_email_keys,
                search_endpoints=post_endpoints,
                page_limit=page_limit,
                max_pages=max_pages,
                target_email_batch_size=target_email_batch_size,
                read_parallel_workers=read_parallel_workers,
            )
            if targeted_supported:
                return self._enrich_contacts_with_contact_details(
                    contacts=targeted_contacts,
                    detail_endpoint_templates=detail_endpoint_templates,
                    enrich_only_email_keys=target_email_keys,
                    page_limit=page_limit,
                    max_pages=max_pages,
                    contacts_endpoint_candidates=contacts_endpoint_candidates,
                    contacts_search_endpoint_candidates=resolved_contacts_search_endpoint_candidates,
                    custom_field_values_endpoint_candidates=custom_field_values_endpoint_candidates,
                    custom_field_values_search_endpoint_candidates=resolved_custom_field_values_search_endpoint_candidates,
                    read_parallel_workers=read_parallel_workers,
                )

        best_rows: list[dict[str, Any]] = []
        any_success = False
        last_error: SmartEmailingApiError | None = None

        for endpoint in get_endpoints:
            try:
                rows = self._fetch_paginated_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contacts,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                any_success = True
                if len(rows) > len(best_rows):
                    best_rows = rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        for endpoint in post_endpoints:
            try:
                rows = self._fetch_paginated_post_search_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_contacts,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                any_success = True
                if len(rows) > len(best_rows):
                    best_rows = rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        if any_success:
            return self._enrich_contacts_with_contact_details(
                contacts=best_rows,
                detail_endpoint_templates=detail_endpoint_templates,
                enrich_only_email_keys=enrich_only_email_keys,
                page_limit=page_limit,
                max_pages=max_pages,
                contacts_endpoint_candidates=contacts_endpoint_candidates,
                contacts_search_endpoint_candidates=resolved_contacts_search_endpoint_candidates,
                custom_field_values_endpoint_candidates=custom_field_values_endpoint_candidates,
                custom_field_values_search_endpoint_candidates=resolved_custom_field_values_search_endpoint_candidates,
                read_parallel_workers=read_parallel_workers,
            )
        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError(
            f"Nepodařilo se načíst kontakty ze seznamu {resolved_list_id} ze SmartEmailing API."
        )

    def _fetch_contacts_in_list_targeted_by_emails(
        self,
        list_id: str,
        target_email_keys: set[str],
        search_endpoints: list[str],
        page_limit: int = 100,
        max_pages: int = 100,
        target_email_batch_size: int = 50,
        read_parallel_workers: int = 6,
    ) -> tuple[list[dict[str, Any]], bool]:
        targets = sorted({str(x).strip().casefold() for x in (target_email_keys or set()) if str(x).strip()})
        if not targets:
            return [], True
        endpoints = _unique_ordered([str(x).strip() for x in search_endpoints if str(x).strip()])
        if not endpoints:
            return [], False

        preferred_endpoint = str(self._contacts_in_list_targeted_preferred_endpoint).strip()
        endpoints = _reorder_preferred(endpoints, preferred_endpoint)

        batches = _chunked(targets, max(1, int(target_email_batch_size)))
        out_by_email: dict[str, dict[str, Any]] = {}
        success_count = 0
        failed_batches = 0
        last_error: SmartEmailingApiError | None = None

        def merge_target_row(row: dict[str, Any]) -> None:
            email_key = str(row.get("emailaddress", "")).strip().casefold()
            if not email_key:
                return
            existing = out_by_email.get(email_key)
            if existing is None:
                out_by_email[email_key] = row
                return
            existing_has_cf = isinstance(existing.get("customfields"), list)
            row_has_cf = isinstance(row.get("customfields"), list)
            if row_has_cf and not existing_has_cf:
                out_by_email[email_key] = row
                return
            merged = dict(existing)
            for key, value in row.items():
                if value in [None, "", []]:
                    continue
                if key == "customfields" and existing_has_cf and not row_has_cf:
                    continue
                merged[key] = value
            out_by_email[email_key] = merged

        def query_one_batch(batch: list[str]) -> list[dict[str, Any]]:
            rows_out: list[dict[str, Any]] = []
            batch_values = [str(x).strip().casefold() for x in batch if str(x).strip()]
            batch_values = [x for x in _unique_ordered(batch_values) if x]
            batch_set = set(batch_values)
            if not batch_set:
                return rows_out

            body_variants: list[tuple[str, dict[str, Any]]] = [
                ("search_emailaddress_list", {"search": {"emailaddress": list(batch_values)}}),
                ("search_email_list", {"search": {"email": list(batch_values)}}),
                ("filter_emailaddress_list", {"filter": {"emailaddress": list(batch_values)}}),
                ("filter_email_list", {"filter": {"email": list(batch_values)}}),
                ("where_emailaddress_list", {"where": {"emailaddress": list(batch_values)}}),
                ("where_email_list", {"where": {"email": list(batch_values)}}),
                ("emails_list", {"emails": list(batch_values)}),
                ("emailaddress_list", {"emailaddress": list(batch_values)}),
            ]
            if len(batch_values) == 1:
                single = batch_values[0]
                body_variants.extend(
                    [
                        ("search_emailaddress_single", {"search": {"emailaddress": single}}),
                        ("search_email_single", {"search": {"email": single}}),
                        ("filter_emailaddress_single", {"filter": {"emailaddress": single}}),
                        ("filter_email_single", {"filter": {"email": single}}),
                        ("where_emailaddress_single", {"where": {"emailaddress": single}}),
                        ("where_email_single", {"where": {"email": single}}),
                        ("emailaddress_single", {"emailaddress": single}),
                        ("email_single", {"email": single}),
                    ]
                )

            include_variants: list[tuple[str, dict[str, Any]]] = [
                ("none", {}),
                ("include", {"include": "customfields"}),
                ("expand", {"expand": "customfields"}),
                ("with", {"with": "customfields"}),
                ("customfields", {"customfields": 1}),
            ]

            preferred_body = str(self._contacts_in_list_targeted_preferred_body_variant).strip()
            if preferred_body:
                body_variants = sorted(
                    body_variants,
                    key=lambda item: 0 if str(item[0]).strip() == preferred_body else 1,
                )
            preferred_include = str(self._contacts_in_list_targeted_preferred_include_variant).strip()
            if preferred_include:
                include_variants = sorted(
                    include_variants,
                    key=lambda item: 0 if str(item[0]).strip() == preferred_include else 1,
                )

            local_success = 0
            local_last_error: SmartEmailingApiError | None = None
            for endpoint in endpoints:
                for body_variant_name, base_body in body_variants:
                    for include_variant_name, include_extra in include_variants:
                        body = dict(base_body)
                        body.update(include_extra)
                        body.setdefault("page", 1)
                        body.setdefault("limit", page_limit)
                        try:
                            payload = self._request_json("POST", endpoint, body=body)
                        except SmartEmailingApiError as exc:
                            if exc.status_code in {401, 403}:
                                raise
                            if exc.status_code in {404, 405}:
                                local_last_error = exc
                                continue
                            local_last_error = exc
                            continue
                        local_success += 1
                        rows = extract_contacts(payload)
                        for row in rows:
                            email_key = str(row.get("emailaddress", "")).strip().casefold()
                            if email_key and email_key in batch_set:
                                rows_out.append(row)
                        if rows_out:
                            self._contacts_in_list_targeted_preferred_endpoint = endpoint
                            self._contacts_in_list_targeted_preferred_body_variant = body_variant_name
                            self._contacts_in_list_targeted_preferred_include_variant = include_variant_name
                            return rows_out
            if local_success == 0 and local_last_error is not None:
                raise local_last_error
            return rows_out

        resolved_workers = max(1, int(read_parallel_workers))
        if resolved_workers == 1 or len(batches) <= 1:
            for batch in batches:
                try:
                    rows = query_one_batch(batch)
                    success_count += 1
                    for row in rows:
                        merge_target_row(row)
                except SmartEmailingApiError as exc:
                    if exc.status_code in {401, 403}:
                        raise
                    failed_batches += 1
                    last_error = exc
                    continue
        else:
            with ThreadPoolExecutor(max_workers=min(resolved_workers, len(batches))) as pool:
                future_to_batch = {pool.submit(query_one_batch, batch): batch for batch in batches}
                for future in as_completed(future_to_batch):
                    try:
                        rows = future.result()
                        success_count += 1
                        for row in rows:
                            merge_target_row(row)
                    except SmartEmailingApiError as exc:
                        if exc.status_code in {401, 403}:
                            raise
                        failed_batches += 1
                        last_error = exc
                        continue
                    except Exception:
                        failed_batches += 1
                        continue

        if failed_batches > 0:
            return [], False
        if success_count > 0:
            return list(out_by_email.values()), True
        if last_error is not None:
            return [], False
        return [], False

    def fetch_contact_details_by_ids(
        self,
        contact_ids: list[str],
        endpoint_templates: list[str] | None = None,
        read_parallel_workers: int = 6,
    ) -> dict[str, dict[str, Any]]:
        templates = CONTACT_DETAIL_ENDPOINT_TEMPLATES if endpoint_templates is None else endpoint_templates
        unique_ids = [
            x
            for x in _unique_ordered([str(raw_contact_id).strip() for raw_contact_id in contact_ids])
            if x
        ]
        details: dict[str, dict[str, Any]] = {}

        def fetch_one_contact(contact_id: str) -> tuple[str, dict[str, Any] | None]:
            endpoints = self._resolve_contact_id_endpoint_templates(templates, contact_id)
            for endpoint in endpoints:
                try:
                    payload = self._request_json("GET", endpoint)
                except SmartEmailingApiError as exc:
                    if exc.status_code in {401, 403}:
                        raise
                    if exc.status_code in {404, 405}:
                        continue
                    continue

                contacts = extract_contacts(payload)
                if not contacts:
                    continue
                contact = contacts[0]
                resolved_id = str(contact.get("id", "")).strip() or contact_id
                contact["id"] = resolved_id
                return contact_id, contact
            return contact_id, None

        resolved_workers = max(1, int(read_parallel_workers))
        if resolved_workers == 1 or len(unique_ids) <= 1:
            for contact_id in unique_ids:
                lookup_id, contact = fetch_one_contact(contact_id)
                if contact is None:
                    continue
                resolved_id = str(contact.get("id", "")).strip() or lookup_id
                details[lookup_id] = contact
                details[resolved_id] = contact
            return details

        with ThreadPoolExecutor(max_workers=min(resolved_workers, len(unique_ids))) as pool:
            future_to_contact_id = {
                pool.submit(fetch_one_contact, contact_id): contact_id for contact_id in unique_ids
            }
            for future in as_completed(future_to_contact_id):
                lookup_id = future_to_contact_id[future]
                try:
                    _, contact = future.result()
                except SmartEmailingApiError as exc:
                    if exc.status_code in {401, 403}:
                        raise
                    continue
                except Exception:
                    continue
                if contact is None:
                    continue
                resolved_id = str(contact.get("id", "")).strip() or lookup_id
                details[lookup_id] = contact
                details[resolved_id] = contact

        return details

    def import_contacts_batch(
        self,
        contacts: list[dict[str, Any]],
        update_existing: bool = True,
        skip_invalid_contacts: bool = True,
        endpoint_candidates: list[str] | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """
        Tries documented import payload variants and endpoint candidates.
        Returns (response_payload, endpoint, payload_variant).
        """
        endpoints = endpoint_candidates or IMPORT_CONTACTS_ENDPOINTS
        endpoints = _reorder_preferred(
            [str(x).strip() for x in endpoints if str(x).strip()],
            self._import_preferred_endpoint,
        )
        import_rows = self._to_import_rows(contacts)
        settings = {
            "update": bool(update_existing),
        }
        payload_variants: list[tuple[str, dict[str, Any]]] = []

        import_payload = {"settings": settings, "data": import_rows}
        payload_variants.append(("import_data", import_payload))

        import_payload_no_settings = {"data": import_rows}
        payload_variants.append(("import_data_no_settings", import_payload_no_settings))
        if self._import_preferred_payload_variant:
            preferred_name = str(self._import_preferred_payload_variant).strip()
            payload_variants = sorted(
                payload_variants,
                key=lambda item: 0 if str(item[0]).strip() == preferred_name else 1,
            )

        attempt_pairs: list[tuple[str, str, dict[str, Any]]] = []
        seen_attempts: set[str] = set()

        preferred_endpoint = str(self._import_preferred_endpoint).strip()
        preferred_variant = str(self._import_preferred_payload_variant).strip()
        if preferred_endpoint and preferred_variant:
            preferred_payload = next((x[1] for x in payload_variants if str(x[0]).strip() == preferred_variant), None)
            if preferred_payload is not None and preferred_endpoint in endpoints:
                dedupe_key = f"{preferred_endpoint}|{preferred_variant}"
                attempt_pairs.append((preferred_endpoint, preferred_variant, preferred_payload))
                seen_attempts.add(dedupe_key)

        for variant_name, payload in payload_variants:
            for endpoint in endpoints:
                dedupe_key = f"{endpoint}|{variant_name}"
                if dedupe_key in seen_attempts:
                    continue
                seen_attempts.add(dedupe_key)
                attempt_pairs.append((endpoint, variant_name, payload))

        fallback_errors: list[tuple[str, str, SmartEmailingApiError]] = []
        for endpoint, variant_name, payload in attempt_pairs:
            try:
                response_payload = self._request_json("POST", endpoint, body=payload)
                if not isinstance(response_payload, dict):
                    raise SmartEmailingApiError("Import endpoint vrátil neočekávaný typ odpovědi.")
                self._import_preferred_endpoint = endpoint
                self._import_preferred_payload_variant = variant_name
                return response_payload, endpoint, variant_name
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                # Endpoint/payload mismatch => continue trying.
                if exc.status_code in {400, 404, 405, 415, 422}:
                    fallback_errors.append((endpoint, variant_name, exc))
                    continue
                raise

        if fallback_errors:
            non_404 = [x for x in fallback_errors if x[2].status_code not in {404, 405}]
            chosen_endpoint, chosen_variant, chosen_error = (non_404[0] if non_404 else fallback_errors[-1])
            attempted = ", ".join(
                sorted(
                    {
                        f"{endpoint} ({variant})"
                        for endpoint, variant, _ in fallback_errors
                    }
                )
            )
            detail = chosen_error.body.strip()
            detail_preview = f" Detail API: {detail[:300]}" if detail else ""
            raise SmartEmailingApiError(
                f"{chosen_error}. Zkoušené endpointy/payloady: {attempted}. "
                f"Vybraná chyba: {chosen_endpoint} ({chosen_variant}).{detail_preview}",
                status_code=chosen_error.status_code,
                body=chosen_error.body,
            )
        raise SmartEmailingApiError("Nepodařilo se odeslat batch kontaktů do SmartEmailing API.")

    def _to_import_rows(
        self,
        contacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Convert internal contact payload to /api/v3/import format:
        - top-level "data": [...]
        - per-contact custom fields / contact lists / tags preserved on row level.
        """
        rows: list[dict[str, Any]] = []

        for contact in contacts:
            row: dict[str, Any] = {}
            contactlists = contact.get("contactlists", [])
            customfields = contact.get("customfields", [])
            tags = contact.get("tags", [])

            for key, value in contact.items():
                key_s = str(key).strip()
                if not key_s:
                    continue
                if key_s.startswith("__"):
                    continue
                if key_s in {"customfields", "contactlists", "tags"}:
                    continue
                row[key_s] = value

            if isinstance(customfields, list) and customfields:
                row["customfields"] = customfields
            if isinstance(contactlists, list) and contactlists:
                row["contactlists"] = contactlists
            if isinstance(tags, list) and tags:
                row["tags"] = tags

            rows.append(row)

        return rows

    def import_contacts_canary(
        self,
        contacts: list[dict[str, Any]],
        canary_size: int = 50,
        batch_size: int = 500,
        update_existing: bool = True,
        skip_invalid_contacts: bool = True,
        endpoint_candidates: list[str] | None = None,
    ) -> list[ImportBatchResult]:
        if batch_size <= 0:
            raise ValueError("batch_size musí být > 0")
        if canary_size < 0:
            raise ValueError("canary_size musí být >= 0")

        results: list[ImportBatchResult] = []
        sent = 0
        total = len(contacts)

        def send_slice(slice_contacts: list[dict[str, Any]], batch_index: int, is_canary: bool) -> None:
            if not slice_contacts:
                return
            started = utcnow_iso()
            response_payload, endpoint, variant_name = self.import_contacts_batch(
                contacts=slice_contacts,
                update_existing=update_existing,
                skip_invalid_contacts=skip_invalid_contacts,
                endpoint_candidates=endpoint_candidates,
            )
            finished = utcnow_iso()
            results.append(
                ImportBatchResult(
                    endpoint=endpoint,
                    payload_variant=variant_name,
                    response=response_payload,
                    sent_contacts=len(slice_contacts),
                    batch_index=batch_index,
                    canary=is_canary,
                    started_at=started,
                    finished_at=finished,
                )
            )

        batch_index = 1
        if canary_size > 0 and total > 0:
            canary_contacts = contacts[:canary_size]
            send_slice(canary_contacts, batch_index=batch_index, is_canary=True)
            sent += len(canary_contacts)
            batch_index += 1

        while sent < total:
            chunk = contacts[sent : sent + batch_size]
            send_slice(chunk, batch_index=batch_index, is_canary=False)
            sent += len(chunk)
            batch_index += 1

        return results

    def probe_custom_fields_endpoints(
        self,
        endpoint_candidates: list[str] | None = None,
        search_endpoint_candidates: list[str] | None = None,
        page_limit: int = 100,
    ) -> list[dict[str, Any]]:
        get_endpoints = CUSTOM_FIELDS_ENDPOINTS if endpoint_candidates is None else endpoint_candidates
        post_endpoints = (
            CUSTOM_FIELDS_SEARCH_ENDPOINTS if search_endpoint_candidates is None else search_endpoint_candidates
        )
        rows: list[dict[str, Any]] = []

        for endpoint in get_endpoints:
            try:
                payload = self._request_json("GET", endpoint)
                parsed = self._fetch_paginated_from_endpoint(
                    path=endpoint,
                    item_extractor=extract_custom_fields,
                    page_limit=page_limit,
                    max_pages=5,
                )
                keys = list(payload.keys())[:10] if isinstance(payload, dict) else []
                rows.append(
                    {
                        "method": "GET",
                        "endpoint": endpoint,
                        "status": "ok",
                        "http_status": 200,
                        "parsed_fields": len(parsed),
                        "payload_keys": ",".join([str(k) for k in keys]),
                        "error": "",
                    }
                )
            except SmartEmailingApiError as exc:
                rows.append(
                    {
                        "method": "GET",
                        "endpoint": endpoint,
                        "status": "error",
                        "http_status": exc.status_code or "",
                        "parsed_fields": 0,
                        "payload_keys": "",
                        "error": str(exc),
                    }
                )

        for endpoint in post_endpoints:
            try:
                payload = self._request_json("POST", endpoint, body={"page": 1, "limit": page_limit})
                parsed = extract_custom_fields(payload)
                keys = list(payload.keys())[:10] if isinstance(payload, dict) else []
                rows.append(
                    {
                        "method": "POST",
                        "endpoint": endpoint,
                        "status": "ok",
                        "http_status": 200,
                        "parsed_fields": len(parsed),
                        "payload_keys": ",".join([str(k) for k in keys]),
                        "error": "",
                    }
                )
            except SmartEmailingApiError as exc:
                rows.append(
                    {
                        "method": "POST",
                        "endpoint": endpoint,
                        "status": "error",
                        "http_status": exc.status_code or "",
                        "parsed_fields": 0,
                        "payload_keys": "",
                        "error": str(exc),
                    }
                )

        return rows

    def _fetch_paginated_with_fallback(
        self,
        endpoints: list[str],
        item_extractor: Callable[[Any], list[dict[str, str]]],
        page_limit: int,
        max_pages: int,
        empty_message: str,
    ) -> list[dict[str, str]]:
        last_error: SmartEmailingApiError | None = None

        for path in endpoints:
            try:
                items = self._fetch_paginated_from_endpoint(
                    path=path,
                    item_extractor=item_extractor,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                # Empty list is a valid response for some endpoints (e.g. no custom fields yet).
                return items
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError(empty_message)

    def _fetch_paginated_from_endpoint(
        self,
        path: str,
        item_extractor: Callable[[Any], list[dict[str, str]]],
        page_limit: int,
        max_pages: int,
    ) -> list[dict[str, str]]:
        """
        Robust GET collection loader for SE endpoints:
        1) try plain GET without query,
        2) try page/limit pagination,
        3) try offset/limit pagination.
        Returns the largest successful result set.
        """
        best_rows: list[dict[str, str]] = []
        any_success = False
        last_error: SmartEmailingApiError | None = None

        try:
            rows = self._fetch_single_get_from_endpoint(path=path, item_extractor=item_extractor)
            any_success = True
            if len(rows) > len(best_rows):
                best_rows = rows
        except SmartEmailingApiError as exc:
            if exc.status_code in {401, 403}:
                raise
            last_error = exc

        try:
            rows = self._fetch_paginated_from_endpoint_page(
                path=path,
                item_extractor=item_extractor,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            any_success = True
            if len(rows) > len(best_rows):
                best_rows = rows
        except SmartEmailingApiError as exc:
            if exc.status_code in {401, 403}:
                raise
            last_error = exc

        try:
            rows = self._fetch_paginated_from_endpoint_offset(
                path=path,
                item_extractor=item_extractor,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            any_success = True
            if len(rows) > len(best_rows):
                best_rows = rows
        except SmartEmailingApiError as exc:
            if exc.status_code in {401, 403}:
                raise
            last_error = exc

        if any_success:
            return best_rows
        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError(f"Nepodařilo se načíst data z endpointu {path}.")

    def _fetch_single_get_from_endpoint(
        self,
        path: str,
        item_extractor: Callable[[Any], list[dict[str, str]]],
    ) -> list[dict[str, str]]:
        payload = self._request_json("GET", path)
        return self._dedupe_rows(item_extractor(payload))

    def _fetch_paginated_from_endpoint_page(
        self,
        path: str,
        item_extractor: Callable[[Any], list[dict[str, str]]],
        page_limit: int,
        max_pages: int,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            payload = self._request_json(
                "GET",
                path,
                query={"page": page, "limit": page_limit},
            )
            page_rows = self._dedupe_rows(item_extractor(payload))
            added_new = False
            for row in page_rows:
                dedupe_key = "|".join([str(v).strip() for v in row.values()])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(row)
                added_new = True

            has_more = self._has_more_pages(payload, current_page=page, limit=page_limit, received_count=len(page_rows))
            if has_more:
                continue
            if self._has_explicit_pagination_meta(payload):
                break
            if len(page_rows) == 0:
                break
            # Unknown pagination metadata fallback:
            # keep requesting next page while new rows appear; stop when response repeats/no-new.
            if not added_new:
                break

        return rows

    def _fetch_paginated_from_endpoint_offset(
        self,
        path: str,
        item_extractor: Callable[[Any], list[dict[str, str]]],
        page_limit: int,
        max_pages: int,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for page_index in range(0, max_pages):
            offset = page_index * page_limit
            payload = self._request_json(
                "GET",
                path,
                query={"limit": page_limit, "offset": offset},
            )
            page_rows = self._dedupe_rows(item_extractor(payload))
            added_new = False
            for row in page_rows:
                dedupe_key = "|".join([str(v).strip() for v in row.values()])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(row)
                added_new = True

            has_more = self._has_more_offset_pages(
                payload=payload,
                next_offset=offset + page_limit,
                limit=page_limit,
                received_count=len(page_rows),
            )
            if has_more:
                continue
            if len(page_rows) == 0:
                break
            if not added_new:
                break

        return rows

    def _fetch_paginated_post_search_with_fallback(
        self,
        endpoints: list[str],
        item_extractor: Callable[[Any], list[dict[str, str]]],
        page_limit: int,
        max_pages: int,
        empty_message: str,
    ) -> list[dict[str, str]]:
        last_error: SmartEmailingApiError | None = None
        for path in endpoints:
            try:
                rows = self._fetch_paginated_post_search_from_endpoint(
                    path=path,
                    item_extractor=item_extractor,
                    page_limit=page_limit,
                    max_pages=max_pages,
                )
                return rows
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError(empty_message)

    def _fetch_paginated_post_search_from_endpoint(
        self,
        path: str,
        item_extractor: Callable[[Any], list[dict[str, str]]],
        page_limit: int,
        max_pages: int,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            payload = self._request_json(
                "POST",
                path,
                body={"page": page, "limit": page_limit},
            )
            page_rows = self._dedupe_rows(item_extractor(payload))
            added_new = False
            for row in page_rows:
                dedupe_key = "|".join([str(v).strip() for v in row.values()])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(row)
                added_new = True

            has_more = self._has_more_pages(payload, current_page=page, limit=page_limit, received_count=len(page_rows))
            if has_more:
                continue
            if self._has_explicit_pagination_meta(payload):
                break
            if len(page_rows) == 0:
                break
            if not added_new:
                break

        return rows

    def _fetch_paginated_post_search_from_endpoint_custom_body(
        self,
        path: str,
        item_extractor: Callable[[Any], list[dict[str, Any]]],
        page_limit: int,
        max_pages: int,
        base_body: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            body = {"page": page, "limit": page_limit}
            body.update(base_body or {})
            payload = self._request_json("POST", path, body=body)
            page_rows = item_extractor(payload)
            added_new = False
            for row in page_rows:
                dedupe_key = "|".join([str(v).strip() for v in row.values()])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(row)
                added_new = True

            has_more = self._has_more_pages(payload, current_page=page, limit=page_limit, received_count=len(page_rows))
            if has_more:
                continue
            if self._has_explicit_pagination_meta(payload):
                break
            if len(page_rows) == 0:
                break
            if not added_new:
                break

        return rows

    @staticmethod
    def _has_more_pages(payload: Any, current_page: int, limit: int, received_count: int) -> bool:
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict):
                for key in ["total_pages", "page_count", "last_page"]:
                    value = meta.get(key)
                    if isinstance(value, int):
                        return current_page < value
                    if isinstance(value, str) and value.isdigit():
                        return current_page < int(value)

                next_page = meta.get("next_page")
                if next_page not in [None, "", False]:
                    return True

                total_count = meta.get("total_count")
                if total_count is None:
                    total_count = meta.get("total")
                if isinstance(total_count, int):
                    return current_page * limit < total_count
                if isinstance(total_count, str) and total_count.isdigit():
                    return current_page * limit < int(total_count)

        # Unknown metadata fallback: continue only while full page is returned.
        return bool(limit > 0 and received_count >= limit)

    @staticmethod
    def _has_more_offset_pages(payload: Any, next_offset: int, limit: int, received_count: int) -> bool:
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict):
                next_value = meta.get("next_offset")
                if isinstance(next_value, int):
                    return True
                if isinstance(next_value, str) and next_value.strip().isdigit():
                    return True

                total_count = meta.get("total_count")
                if total_count is None:
                    total_count = meta.get("total")
                if isinstance(total_count, int):
                    return next_offset < total_count
                if isinstance(total_count, str) and total_count.strip().isdigit():
                    return next_offset < int(total_count.strip())

        return bool(limit > 0 and received_count >= limit)

    @staticmethod
    def _dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            dedupe_key = "|".join([str(v).strip() for v in row.values()])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(row)
        return out

    def _enrich_contacts_with_contact_details(
        self,
        contacts: list[dict[str, Any]],
        detail_endpoint_templates: list[str] | None = None,
        enrich_only_email_keys: set[str] | None = None,
        page_limit: int = 100,
        max_pages: int = 100,
        contacts_endpoint_candidates: list[str] | None = None,
        contacts_search_endpoint_candidates: list[str] | None = None,
        custom_field_values_endpoint_candidates: list[str] | None = None,
        custom_field_values_search_endpoint_candidates: list[str] | None = None,
        read_parallel_workers: int = 6,
    ) -> list[dict[str, Any]]:
        if not contacts:
            return contacts

        filter_keys = {
            str(x).strip().casefold()
            for x in (enrich_only_email_keys or set())
            if str(x).strip()
        }
        candidates: list[dict[str, Any]] = []
        for row in contacts:
            email_key = str(row.get("emailaddress", "")).strip().casefold()
            if filter_keys and email_key not in filter_keys:
                continue
            candidates.append(row)

        email_to_detail: dict[str, dict[str, Any]] = {}
        for row in candidates:
            if not isinstance(row.get("customfields"), list):
                continue
            email_key = str(row.get("emailaddress", "")).strip().casefold()
            if email_key and email_key not in email_to_detail:
                email_to_detail[email_key] = dict(row)

        candidate_ids = [str(row.get("id", "")).strip() for row in candidates if str(row.get("id", "")).strip()]
        detail_map: dict[str, dict[str, Any]] = {}
        if candidate_ids:
            detail_map = self.fetch_contact_details_by_ids(
                contact_ids=candidate_ids,
                endpoint_templates=detail_endpoint_templates,
                read_parallel_workers=read_parallel_workers,
            )
        for detail in detail_map.values():
            email_key = str(detail.get("emailaddress", "")).strip().casefold()
            if email_key and email_key not in email_to_detail:
                email_to_detail[email_key] = detail

        unresolved_email_keys = {
            str(row.get("emailaddress", "")).strip().casefold()
            for row in candidates
            if str(row.get("emailaddress", "")).strip()
            and str(row.get("emailaddress", "")).strip().casefold() not in email_to_detail
        }

        if unresolved_email_keys:
            try:
                targeted_contacts = self.fetch_contacts_by_emails(
                    email_keys=unresolved_email_keys,
                    endpoint_candidates=contacts_endpoint_candidates,
                    search_endpoint_candidates=contacts_search_endpoint_candidates,
                    max_workers=read_parallel_workers,
                )
                for email_key, targeted_contact in targeted_contacts.items():
                    if email_key:
                        email_to_detail[email_key] = targeted_contact
            except Exception:
                # Best-effort enrichment only.
                pass

        unresolved_email_keys = {
            x for x in unresolved_email_keys if x and x not in email_to_detail
        }

        if unresolved_email_keys:
            try:
                fallback_contacts = self.fetch_contacts(
                    page_limit=page_limit,
                    max_pages=max_pages,
                    endpoint_candidates=contacts_endpoint_candidates,
                    search_endpoint_candidates=contacts_search_endpoint_candidates,
                    email_keys_filter=unresolved_email_keys,
                )
                for fallback_contact in fallback_contacts:
                    email_key = str(fallback_contact.get("emailaddress", "")).strip().casefold()
                    if email_key:
                        email_to_detail[email_key] = fallback_contact
            except Exception:
                # Best-effort enrichment only.
                pass

        target_ids: set[str] = set()
        for row in candidates:
            row_id = str(row.get("id", "")).strip()
            if row_id:
                target_ids.add(row_id)
        for detail in email_to_detail.values():
            detail_id = str(detail.get("id", "")).strip()
            if detail_id:
                target_ids.add(detail_id)

        if target_ids:
            try:
                values_by_contact_id = self.fetch_custom_field_values_for_contacts(
                    contact_ids=target_ids,
                    page_limit=page_limit,
                    max_pages=max_pages,
                    endpoint_candidates=custom_field_values_endpoint_candidates,
                    search_endpoint_candidates=custom_field_values_search_endpoint_candidates,
                )
            except Exception:
                values_by_contact_id = {}

            if values_by_contact_id:
                for email_key, detail in list(email_to_detail.items()):
                    detail_id = str(detail.get("id", "")).strip()
                    if not detail_id:
                        continue
                    values = values_by_contact_id.get(detail_id, [])
                    if not values:
                        continue
                    existing_values = detail.get("customfields", [])
                    merged_map: dict[str, Any] = {}
                    if isinstance(existing_values, list):
                        for item in existing_values:
                            if not isinstance(item, dict):
                                continue
                            field_id = str(item.get("id", "")).strip()
                            if field_id:
                                merged_map[field_id] = item.get("value")
                    for item in values:
                        field_id = str(item.get("id", "")).strip()
                        if field_id:
                            merged_map[field_id] = item.get("value")
                    detail["customfields"] = [
                        {"id": field_id, "value": value}
                        for field_id, value in merged_map.items()
                        if str(field_id).strip()
                    ]
                    email_to_detail[email_key] = detail

        if not email_to_detail:
            return contacts

        merged: list[dict[str, Any]] = []
        for row in contacts:
            row_id = str(row.get("id", "")).strip()
            row_email_key = str(row.get("emailaddress", "")).strip().casefold()
            detail = detail_map.get(row_id)
            if not detail:
                detail = email_to_detail.get(row_email_key)
            if not detail:
                merged.append(row)
                continue
            merged_row = dict(row)
            for key in [
                "customfields",
                "tags",
                "name",
                "surname",
                "titlesbefore",
                "titlesafter",
                "company",
                "town",
                "country",
                "notes",
                "phone",
                "mobile",
                "street",
                "address",
                "zip",
                "postalcode",
                "state",
            ]:
                value = detail.get(key)
                if value in [None, "", []]:
                    continue
                merged_row[key] = value
            merged.append(merged_row)

        return merged

    @staticmethod
    def _has_explicit_pagination_meta(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            return False
        keys = {"total_pages", "page_count", "last_page", "next_page", "total_count", "total"}
        return any(key in meta for key in keys)

    @staticmethod
    def _resolve_list_id_endpoint_templates(templates: Iterable[str], list_id: str) -> list[str]:
        safe_list_id = parse.quote(str(list_id).strip(), safe="")
        resolved: list[str] = []
        seen: set[str] = set()
        for template in templates:
            template_str = str(template).strip()
            if not template_str:
                continue
            path = template_str.replace("{list_id}", safe_list_id)
            if path in seen:
                continue
            seen.add(path)
            resolved.append(path)
        return resolved

    @staticmethod
    def _resolve_contact_id_endpoint_templates(templates: Iterable[str], contact_id: str) -> list[str]:
        safe_contact_id = parse.quote(str(contact_id).strip(), safe="")
        resolved: list[str] = []
        seen: set[str] = set()
        for template in templates:
            template_str = str(template).strip()
            if not template_str:
                continue
            path = template_str.replace("{contact_id}", safe_contact_id).replace("{id}", safe_contact_id)
            if path in seen:
                continue
            seen.add(path)
            resolved.append(path)
        return resolved
