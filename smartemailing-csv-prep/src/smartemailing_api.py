from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, List
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://app.smartemailing.cz"

PING_ENDPOINT = "/api/v3/ping"
CUSTOM_FIELDS_ENDPOINTS = ["/api/v3/customfields", "/api/v3/custom-fields"]
CONTACT_LISTS_ENDPOINTS = ["/api/v3/contactlists", "/api/v3/contact-lists"]
IMPORT_CONTACTS_ENDPOINTS = ["/api/v3/import", "/api/v3/imports", "/api/v3/import-contacts"]


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
        candidates.append([data])

    for key in ["items", "customfields", "custom_fields", "contactlists", "contact_lists", "results", "records"]:
        values = payload.get(key)
        if isinstance(values, list):
            candidates.append(values)

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
    for key in keys:
        value = item.get(key)
        if value is None and attrs_dict:
            value = attrs_dict.get(key)
        if value is None:
            continue
        as_str = str(value).strip()
        if as_str:
            return as_str
    return ""


def _pick_id(item: dict[str, Any], keys: list[str]) -> str:
    attrs = item.get("attributes")
    attrs_dict = attrs if isinstance(attrs, dict) else {}
    for key in keys:
        value = item.get(key)
        if value is None and attrs_dict:
            value = attrs_dict.get(key)
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
        name = _pick_value(item, ["name", "title", "label"])
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


def build_api_contacts_from_import_df(
    import_df: Any,
    api_system_field_map: dict[str, str],
    custom_fields: list[dict[str, str]],
    list_id: str = "",
    list_status: str = "confirmed",
    tag: str = "",
    strict_custom_fields: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Converts final import dataframe to API contact payload list.
    Returns (contacts, issues).
    """
    custom_fields_by_name = {str(x.get("name", "")).strip().casefold(): x for x in custom_fields}

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
                custom_values.append({"id": field_id, "value": value})
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
    ) -> list[dict[str, str]]:
        return self._fetch_paginated_with_fallback(
            endpoints=endpoint_candidates or CUSTOM_FIELDS_ENDPOINTS,
            item_extractor=extract_custom_fields,
            page_limit=page_limit,
            max_pages=max_pages,
            empty_message="Nepodařilo se načíst custom fields ze SmartEmailing API.",
        )

    def fetch_custom_field_names(
        self,
        page_limit: int = 100,
        max_pages: int = 30,
        endpoint_candidates: list[str] | None = None,
    ) -> list[str]:
        return [
            x["name"]
            for x in self.fetch_custom_fields(
                page_limit=page_limit,
                max_pages=max_pages,
                endpoint_candidates=endpoint_candidates,
            )
        ]

    def fetch_contact_lists(
        self,
        page_limit: int = 100,
        max_pages: int = 30,
        endpoint_candidates: list[str] | None = None,
    ) -> list[dict[str, str]]:
        return self._fetch_paginated_with_fallback(
            endpoints=endpoint_candidates or CONTACT_LISTS_ENDPOINTS,
            item_extractor=extract_contact_lists,
            page_limit=page_limit,
            max_pages=max_pages,
            empty_message="Nepodařilo se načíst contact listy ze SmartEmailing API.",
        )

    def resolve_contact_list_id(self, list_name_or_id: str, endpoint_candidates: list[str] | None = None) -> str:
        wanted = str(list_name_or_id).strip()
        if not wanted:
            return ""
        lists = self.fetch_contact_lists(endpoint_candidates=endpoint_candidates)
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
        Tries multiple payload variants and endpoint candidates.
        Returns (response_payload, endpoint, payload_variant).
        """
        endpoints = endpoint_candidates or IMPORT_CONTACTS_ENDPOINTS
        settings = {
            "update": bool(update_existing),
            "skip_invalid_contacts": bool(skip_invalid_contacts),
        }
        payload_variants = [
            ("flat", {"settings": settings, "contacts": contacts}),
            ("data_wrap", {"data": {"settings": settings, "contacts": contacts}}),
        ]

        last_error: SmartEmailingApiError | None = None
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
                        last_error = exc
                        continue
                    raise

        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError("Nepodařilo se odeslat batch kontaktů do SmartEmailing API.")

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
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            payload = self._request_json(
                "GET",
                path,
                query={"page": page, "limit": page_limit},
            )
            page_rows = item_extractor(payload)
            for row in page_rows:
                dedupe_key = "|".join([str(v).strip() for v in row.values()])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(row)

            if not self._has_more_pages(payload, current_page=page, limit=page_limit, received_count=len(page_rows)):
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

        return received_count >= limit
