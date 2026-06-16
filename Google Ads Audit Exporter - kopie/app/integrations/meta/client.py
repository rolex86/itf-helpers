from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import requests

from app.integrations.meta.errors import MetaIntegrationError, MetaPermissionError
from app.integrations.meta.models import MetaConnection
from app.integrations.meta.rate_limit import raise_if_rate_limited
from app.utils.retry import run_http_request_with_retry


LOGGER = logging.getLogger("google_ads_audit_exporter")

DEFAULT_USER_AGENT = "ITFutureMetaAudit/1.0"

SENSITIVE_PARAM_KEYS = {
    "access_token",
    "app_secret",
    "client_secret",
    "appsecret_proof",
    "authorization",
    "token",
}


class MetaGraphClient:
    def __init__(self, connection: MetaConnection) -> None:
        self.connection = connection
        self._session = requests.Session()
        api_version = str(self.connection.meta_api_version or "v25.0").strip() or "v25.0"
        self._base_url = f"https://graph.facebook.com/{api_version}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_params = self._request_params(params)
        url = self._build_url(path)

        LOGGER.info("Meta GET path=%s params=%s", path, self._safe_log_params(request_params))

        response = run_http_request_with_retry(
            lambda: self._session.get(
                url,
                params=request_params,
                headers=self._headers(),
                timeout=120,
            )
        )
        return self._parse_response(response, path=path)

    def post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        request_data = self._request_params(data)
        url = self._build_url(path)

        LOGGER.info("Meta POST path=%s params=%s", path, self._safe_log_params(request_data))

        response = run_http_request_with_retry(
            lambda: self._session.post(
                url,
                data=request_data,
                headers=self._headers(),
                timeout=120,
            )
        )
        return self._parse_response(response, path=path)

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        payload = self.get(path, params=params)

        while True:
            data = payload.get("data", []) or []
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict):
                        yield row

            next_url = str(((payload.get("paging") or {}).get("next") or "")).strip()
            if not next_url:
                break

            LOGGER.info("Meta paging next path=%s", path)

            response = run_http_request_with_retry(
                lambda: self._session.get(
                    next_url,
                    headers=self._headers(),
                    timeout=120,
                )
            )
            payload = self._parse_response(response, path=path)

    def _headers(self) -> dict[str, str]:
        user_agent = str(self.connection.user_agent or "").strip() or DEFAULT_USER_AGENT
        return {"User-Agent": user_agent}

    def _request_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(params or {})

        # Important: explicit access_token must win.
        # debug_token needs app access token in this field, e.g. APP_ID|APP_SECRET.
        payload.setdefault("access_token", self.connection.access_token)

        if not str(payload.get("access_token") or "").strip():
            raise MetaIntegrationError(
                "Meta access token chybi. Dopln access token v Meta connection nebo ho posli explicitne v requestu.",
                details="Missing access_token before Meta API request.",
            )

        return payload

    def _build_url(self, path: str) -> str:
        clean_path = str(path or "").strip()

        if clean_path.startswith(("http://", "https://")):
            return clean_path

        clean_path = clean_path.lstrip("/")
        return f"{self._base_url}/{clean_path}"

    def _parse_response(self, response: requests.Response, *, path: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": response.text}}

        if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if not isinstance(error, dict):
                error = {"message": str(error)}

            message = str(error.get("message") or f"Meta API request failed for {path}.")
            code = error.get("code")
            subcode = error.get("error_subcode")
            details = json.dumps(error, ensure_ascii=False)

            raise_if_rate_limited(response.status_code, code, message, details)

            if response.status_code == 403:
                raise MetaPermissionError(
                    message,
                    status_code=response.status_code,
                    error_code=code,
                    error_subcode=subcode,
                    details=details,
                )

            raise MetaIntegrationError(
                message,
                status_code=response.status_code,
                error_code=code,
                error_subcode=subcode,
                details=details,
            )

        return payload if isinstance(payload, dict) else {}

    def _safe_log_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._mask_sensitive_values(payload)

    def _mask_sensitive_values(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, nested in value.items():
                key_text = str(key)
                if self._is_sensitive_key(key_text):
                    sanitized[key_text] = "***"
                else:
                    sanitized[key_text] = self._mask_sensitive_values(nested)
            return sanitized

        if isinstance(value, list):
            return [self._mask_sensitive_values(item) for item in value]

        if isinstance(value, tuple):
            return tuple(self._mask_sensitive_values(item) for item in value)

        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.strip().lower()
        return normalized in SENSITIVE_PARAM_KEYS or normalized.endswith("_token") or normalized.endswith("_secret")