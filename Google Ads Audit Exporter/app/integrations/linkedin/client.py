from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

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
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params, expected_status=expected_status)

    def post(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200, 201, 204),
    ) -> dict[str, Any]:
        return self._request("POST", path, params=params, data=data, expected_status=expected_status)

    def delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200, 204),
    ) -> dict[str, Any]:
        return self._request("DELETE", path, params=params, expected_status=expected_status)

    def batch_get(self, path: str, ids: list[str]) -> dict[str, Any]:
        return self.get(path, params={"ids": f"List({','.join(ids)})"})

    def finder(self, path: str, q: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_params = dict(params or {})
        request_params["q"] = q
        return self.get(path, params=request_params)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        count: int = 100,
    ) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            request_params = dict(params or {})
            request_params.setdefault("count", count)
            request_params["start"] = start
            payload = self.get(path, params=request_params)
            elements = payload.get("elements", []) or payload.get("data", []) or []
            if not isinstance(elements, list):
                break
            for item in elements:
                if isinstance(item, dict):
                    yield item
            paging = payload.get("paging", {}) if isinstance(payload, dict) else {}
            total = int(paging.get("total", 0) or 0)
            if not elements:
                break
            start += len(elements)
            if total and start >= total:
                break
            if len(elements) < count:
                break

    def request_with_query_tunneling(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tunnel_headers = {"X-HTTP-Method-Override": method.upper()}
        return self._request("POST", path, params=params, data=data, extra_headers=tunnel_headers)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200,),
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        clean_path = str(path or "").lstrip("/")
        url = f"{self._base_url}/{clean_path}"
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)

        sanitized_params = self._sanitize_for_log(params or {})
        LOGGER.info("LinkedIn %s path=%s params=%s", method.upper(), clean_path, sanitized_params)

        last_error: Exception | None = None
        max_attempts = max(1, int(self.runtime_config.max_retries or 3))
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=data if method.upper() != "GET" else None,
                    headers=headers,
                    timeout=self.runtime_config.request_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                time.sleep(2 ** (attempt - 1))
                continue

            if response.status_code == 414:
                return self.request_with_query_tunneling(
                    method,
                    path,
                    params=params,
                    data=data,
                )

            try:
                payload = response.json() if response.text.strip() else {}
            except ValueError:
                payload = {"message": response.text}

            if response.status_code in expected_status:
                return payload if isinstance(payload, dict) else {"elements": payload}

            error_message = self._error_message(payload, response)
            retry_after = self._retry_after_seconds(response)
            details = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)
            try:
                raise_if_rate_limited(response.status_code, error_message, details, retry_after)
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                time.sleep(retry_after or (2 ** (attempt - 1)))
                continue

            if response.status_code in {401}:
                raise LinkedInAuthError(error_message, status_code=response.status_code, details=details)
            if response.status_code in {403}:
                raise LinkedInPermissionError(error_message, status_code=response.status_code, details=details)
            raise LinkedInIntegrationError(error_message, status_code=response.status_code, details=details)

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

    def _sanitize_for_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(payload)
        for key in list(sanitized):
            if any(token in key.lower() for token in ("token", "secret", "code", "authorization")):
                sanitized[key] = "***"
        return sanitized

    def _error_message(self, payload: Any, response: requests.Response) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error_description", "description"):
                value = payload.get(key)
                if value:
                    return str(value)
            service_error = payload.get("serviceErrorCode")
            if service_error:
                return f"LinkedIn API error {service_error}"
        return f"LinkedIn API request failed with HTTP {response.status_code}."

    def _retry_after_seconds(self, response: requests.Response) -> int | None:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return int(float(header))
        except (TypeError, ValueError):
            return None

