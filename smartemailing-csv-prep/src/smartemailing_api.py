from __future__ import annotations

import base64
import json
import re
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
IMPORT_CONTACTS_ENDPOINTS = ["/api/v3/import"]


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

    for values in candidates:
        if not isinstance(values, list):
            continue
        dict_items = [x for x in values if isinstance(x, dict)]
        if dict_items:
            return dict_items
    return []


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
    array_value_split_separators_list = [str(x) for x in (array_value_split_separators or [",", ";", "|", "/"]) if str(x)]

    mapped_source_columns = {str(src).strip() for src in api_system_field_map.keys() if str(src).strip()}
    contacts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for row_index, row in import_df.iterrows():
        contact: dict[str, Any] = {}

        for source_col, api_key in api_system_field_map.items():
            src = str(source_col).strip()
            key = str(api_key).strip()
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
            value = str(row.get(col, "")).strip()
            if not value:
                continue
            field = custom_fields_by_name.get(str(col).strip().casefold())
            if field is None:
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

        if list_id:
            contact["contactlists"] = [{"id": list_id, "status": list_status}]

        if tag:
            # Some API accounts support tags directly in import payload.
            contact["tags"] = [tag]

        contacts.append(contact)

    return contacts, issues


class SmartEmailingApiClient:
    def __init__(self, credentials: SmartEmailingCredentials, timeout_sec: int = 20) -> None:
        self.credentials = credentials
        self.base_url = normalize_base_url(credentials.base_url)
        self.timeout_sec = int(timeout_sec)

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        query = query or {}
        full_query = parse.urlencode(query, doseq=True)
        url = f"{self.base_url}{path}"
        if full_query:
            url = f"{url}?{full_query}"

        payload_bytes = None
        if body is not None:
            payload_bytes = json.dumps(body).encode("utf-8")

        req = request.Request(url=url, method=method.upper(), data=payload_bytes)
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
        import_rows = self._to_import_rows(contacts)
        settings = {
            "update": bool(update_existing),
        }
        payload_variants: list[tuple[str, dict[str, Any]]] = []

        import_payload = {"settings": settings, "data": import_rows}
        payload_variants.append(("import_data", import_payload))

        import_payload_no_settings = {"data": import_rows}
        payload_variants.append(("import_data_no_settings", import_payload_no_settings))

        fallback_errors: list[tuple[str, str, SmartEmailingApiError]] = []
        for variant_name, payload in payload_variants:
            for endpoint in endpoints:
                try:
                    response_payload = self._request_json("POST", endpoint, body=payload)
                    if not isinstance(response_payload, dict):
                        raise SmartEmailingApiError("Import endpoint vrátil neočekávaný typ odpovědi.")
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

    @staticmethod
    def _has_explicit_pagination_meta(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            return False
        keys = {"total_pages", "page_count", "last_page", "next_page", "total_count", "total"}
        return any(key in meta for key in keys)
