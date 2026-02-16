from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Iterable, List
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://app.smartemailing.cz"
CUSTOM_FIELDS_ENDPOINTS = ["/api/v3/customfields", "/api/v3/custom-fields"]


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
        for key in ["items", "customfields", "custom_fields", "results", "records"]:
            values = data.get(key)
            if isinstance(values, list):
                candidates.append(values)
        candidates.append([data])

    for key in ["items", "customfields", "custom_fields", "results", "records"]:
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


def extract_custom_field_names(payload: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for item in _extract_items(payload):
        values: list[Any] = [
            item.get("name"),
            item.get("label"),
            item.get("title"),
            item.get("custom_field_name"),
            item.get("customfield_name"),
            item.get("field_name"),
        ]

        attributes = item.get("attributes")
        if isinstance(attributes, dict):
            values.extend(
                [
                    attributes.get("name"),
                    attributes.get("label"),
                    attributes.get("title"),
                    attributes.get("custom_field_name"),
                    attributes.get("customfield_name"),
                    attributes.get("field_name"),
                ]
            )

        for value in values:
            if value is None:
                continue
            field_name = str(value).strip()
            if not field_name:
                continue
            if field_name not in seen:
                seen.add(field_name)
                names.append(field_name)
            break

    return names


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

    def ping(self) -> dict[str, Any]:
        data = self._request_json("GET", "/api/v3/ping")
        if isinstance(data, dict):
            return data
        raise SmartEmailingApiError("Neočekávaná odpověď z /api/v3/ping")

    def fetch_custom_field_names(
        self,
        page_limit: int = 100,
        max_pages: int = 30,
    ) -> list[str]:
        last_error: SmartEmailingApiError | None = None

        for path in CUSTOM_FIELDS_ENDPOINTS:
            try:
                return self._fetch_custom_field_names_from_endpoint(path, page_limit=page_limit, max_pages=max_pages)
            except SmartEmailingApiError as exc:
                if exc.status_code in {401, 403}:
                    raise
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise SmartEmailingApiError("Nepodařilo se načíst custom fields ze SmartEmailing API.")

    def _fetch_custom_field_names_from_endpoint(
        self,
        path: str,
        page_limit: int,
        max_pages: int,
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            payload = self._request_json(
                "GET",
                path,
                query={"page": page, "limit": page_limit},
            )
            page_names = extract_custom_field_names(payload)
            for name in page_names:
                if name not in seen:
                    seen.add(name)
                    names.append(name)

            if not self._has_more_pages(payload, current_page=page, limit=page_limit, received_count=len(page_names)):
                break

        if names:
            return names
        raise SmartEmailingApiError(f"Endpoint {path} nevrátil žádná custom fields.")

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

        # Fallback heuristic for APIs without metadata.
        return received_count >= limit
