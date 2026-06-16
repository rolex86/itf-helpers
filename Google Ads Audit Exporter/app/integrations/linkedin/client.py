from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import requests

from app.integrations.linkedin.errors import LinkedInAuthError, LinkedInIntegrationError, LinkedInPermissionError
from app.integrations.linkedin.models import LinkedInConnection, LinkedInRuntimeConfig
from app.integrations.linkedin.rate_limit import raise_if_rate_limited


LOGGER = logging.getLogger("google_ads_audit_exporter")


class LinkedInRestClient:
    def __init__(
        self,
        *,
        connection: LinkedInConnection,
        runtime_config: LinkedInRuntimeConfig,
        access_token: str,
    ) -> None:
        self.connection = connection
        self.runtime_config = runtime_config
        self.access_token = str(access_token or "").strip()
        self._session = requests.Session()
        self._base_url = "https://api.linkedin.com/rest"

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200,),
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            path,
            params=params,
            expected_status=expected_status,
            extra_headers=extra_headers,
        )

    def post(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200, 201, 204),
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            path,
            params=params,
            data=data,
            expected_status=expected_status,
            extra_headers=extra_headers,
        )

    def delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200, 204),
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "DELETE",
            path,
            params=params,
            expected_status=expected_status,
            extra_headers=extra_headers,
        )

    def batch_get(self, path: str, ids: list[str]) -> dict[str, Any]:
        return self.get(path, params={"ids": f"List({','.join(ids)})"})

    def finder(
        self,
        path: str,
        q: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_params = dict(params or {})
        request_params["q"] = q
        return self.get(
            path,
            params=request_params,
            extra_headers={"X-RestLi-Method": "FINDER"},
        )

    def paginate_cursor(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        extra_headers: dict[str, str] | None = None,
        expected_status: tuple[int, ...] = (200,),
    ) -> Iterator[dict[str, Any]]:
        page_token = ""

        while True:
            request_params = dict(params or {})
            request_params.setdefault("pageSize", page_size)

            if page_token:
                request_params["pageToken"] = page_token

            payload = self.get(
                path,
                params=request_params,
                expected_status=expected_status,
                extra_headers=extra_headers,
            )

            elements = self._elements(payload)
            for item in elements:
                if isinstance(item, dict):
                    yield item

            page_token = self._next_page_token(payload)
            if not page_token:
                break

    def paginate_start_count(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        count: int = 100,
        extra_headers: dict[str, str] | None = None,
        expected_status: tuple[int, ...] = (200,),
    ) -> Iterator[dict[str, Any]]:
        start = 0

        while True:
            request_params = dict(params or {})
            request_params.setdefault("count", count)
            request_params["start"] = start

            payload = self.get(
                path,
                params=request_params,
                expected_status=expected_status,
                extra_headers=extra_headers,
            )

            elements = self._elements(payload)
            for item in elements:
                if isinstance(item, dict):
                    yield item

            if not elements:
                break

            paging = payload.get("paging", {}) if isinstance(payload, dict) else {}
            total = int(paging.get("total", 0) or 0)
            start += len(elements)

            if total and start >= total:
                break

            if len(elements) < count:
                break

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        count: int = 100,
    ) -> Iterator[dict[str, Any]]:
        yield from self.paginate_cursor(path, params=params, page_size=count)

    def request_with_query_tunneling(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200,),
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        original_method = method.upper()
        tunnel_headers = {
            "X-HTTP-Method-Override": original_method,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        if extra_headers:
            tunnel_headers.update(extra_headers)

        form_data: dict[str, Any] = {}
        form_data.update(params or {})

        if data:
            form_data.update(data)

        return self._request(
            "POST",
            path,
            params=None,
            data=None,
            form_data=form_data,
            expected_status=expected_status,
            extra_headers=tunnel_headers,
            allow_query_tunneling=False,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200,),
        extra_headers: dict[str, str] | None = None,
        allow_query_tunneling: bool = True,
    ) -> dict[str, Any]:
        clean_path = str(path or "").lstrip("/")
        url = f"{self._base_url}/{clean_path}"

        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)

        sanitized_params = self._sanitize_for_log(params or {})
        sanitized_data = self._sanitize_for_log(data or {})
        sanitized_form_data = self._sanitize_for_log(form_data or {})

        LOGGER.info(
            "LinkedIn %s path=%s params=%s data=%s form_data=%s",
            method.upper(),
            clean_path,
            sanitized_params,
            sanitized_data,
            sanitized_form_data,
        )

        last_error: Exception | None = None
        max_attempts = max(1, int(self.runtime_config.max_retries or 3))

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=data if form_data is None and method.upper() != "GET" else None,
                    data=form_data,
                    headers=headers,
                    timeout=self.runtime_config.request_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                time.sleep(2 ** (attempt - 1))
                continue

            if response.status_code == 414 and allow_query_tunneling:
                return self.request_with_query_tunneling(
                    method,
                    path,
                    params=params,
                    data=data,
                    expected_status=expected_status,
                    extra_headers=extra_headers,
                )

            try:
                payload = response.json() if response.text.strip() else {}
            except ValueError:
                payload = {"message": response.text}

            if response.status_code in expected_status:
                return payload if isinstance(payload, dict) else {"elements": payload}

            error_message = self._error_message(payload, response)
            retry_after = self._retry_after_seconds(response)
            details = self._safe_json(payload)

            try:
                raise_if_rate_limited(response.status_code, error_message, details, retry_after)
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                time.sleep(retry_after or (2 ** (attempt - 1)))
                continue

            if response.status_code == 401:
                raise LinkedInAuthError(
                    error_message,
                    status_code=response.status_code,
                    details=details,
                )

            if response.status_code == 403:
                raise LinkedInPermissionError(
                    error_message,
                    status_code=response.status_code,
                    details=details,
                )

            if response.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < max_attempts:
                time.sleep(retry_after or (2 ** (attempt - 1)))
                continue

            raise LinkedInIntegrationError(
                error_message,
                status_code=response.status_code,
                details=details,
            )

        if last_error:
            raise last_error

        raise LinkedInIntegrationError("LinkedIn request finished without a usable response.")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Linkedin-Version": self.connection.linkedin_api_version or self.runtime_config.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
            "User-Agent": self.connection.user_agent or self.runtime_config.user_agent,
        }

    def _elements(self, payload: dict[str, Any]) -> list[Any]:
        if not isinstance(payload, dict):
            return []

        elements = payload.get("elements", [])
        if isinstance(elements, list):
            return elements

        data = payload.get("data", [])
        if isinstance(data, list):
            return data

        values = payload.get("values", [])
        if isinstance(values, list):
            return values

        return []

    def _next_page_token(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""

        metadata = payload.get("metadata", {})
        if isinstance(metadata, dict):
            token = metadata.get("nextPageToken")
            if token:
                return str(token)

        paging = payload.get("paging", {})
        if isinstance(paging, dict):
            token = paging.get("nextPageToken")
            if token:
                return str(token)

        return ""

    def _sanitize_for_log(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            sanitized: dict[str, Any] = {}
            for key, value in payload.items():
                key_text = str(key)
                if any(token in key_text.lower() for token in ("token", "secret", "code", "authorization", "password")):
                    sanitized[key] = "***"
                else:
                    sanitized[key] = self._sanitize_for_log(value)
            return sanitized

        if isinstance(payload, list):
            return [self._sanitize_for_log(item) for item in payload]

        if isinstance(payload, tuple):
            return tuple(self._sanitize_for_log(item) for item in payload)

        return payload

    def _safe_json(self, payload: Any) -> str:
        sanitized = self._sanitize_for_log(payload)
        try:
            return json.dumps(sanitized, ensure_ascii=False)
        except TypeError:
            return str(sanitized)

    def _error_message(self, payload: Any, response: requests.Response) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error_description", "description"):
                value = payload.get(key)
                if value:
                    return str(value)

            service_error = payload.get("serviceErrorCode")
            if service_error:
                return f"LinkedIn API error {service_error}"

            error_code = payload.get("error")
            if error_code:
                return str(error_code)

        return f"LinkedIn API request failed with HTTP {response.status_code}."

    def _retry_after_seconds(self, response: requests.Response) -> int | None:
        header = response.headers.get("Retry-After")
        if not header:
            return None

        try:
            return int(float(header))
        except (TypeError, ValueError):
            return None