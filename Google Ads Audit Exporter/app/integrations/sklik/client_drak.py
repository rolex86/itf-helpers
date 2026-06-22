from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from app.integrations.sklik.errors import SklikApiError, SklikPartialFailure
from app.integrations.sklik.rate_limit import compute_backoff_seconds


SUCCESS_CODES = {200}
PARTIAL_SUCCESS_CODES = {206}
RELOGIN_CODES = {401}
WARNING_CODES = {301, 403, 404, 406, 409}
SPLIT_CODES = {413}
RETRY_CODES = {429, 500}


def _status_code(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 200

    value = payload.get("status")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)

    value = payload.get("Status")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)

    for key in ("statusCode", "status_code", "code"):
        value = payload.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            pass

    return 200


def _status_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("statusMessage") or payload.get("message") or payload.get("error") or "").strip()


def _extract_session(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("session", "Session"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    for container_key in ("result", "data"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            value = str(container.get("session") or "").strip()
            if value:
                return value
    return ""


class SklikDrakClient:
    def __init__(
        self,
        token: str,
        base_url: str,
        timeout: int,
        max_retries: int,
        user_agent: str,
    ) -> None:
        self.token = str(token or "").strip()
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = int(timeout)
        self.max_retries = int(max_retries)
        self.user_agent = str(user_agent or "").strip()
        self.session_id = ""
        self.http = requests.Session()
        self.http.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
        )

    def _build_url(self, method: str) -> str:
        return f"{self.base_url}/{method}"

    def _raw_call(self, method: str, params: Any) -> dict[str, Any]:
        response = self.http.post(
            self._build_url(method),
            json=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {"result": payload}
        return payload

    def _inject_session(self, method: str, params: list | dict | str | None, user_id: int | None = None) -> Any:
        no_auth_methods = {"client.loginByToken", "api.version"}

        if method in no_auth_methods or not self.session_id:
            return params

        auth: dict[str, Any] = {"session": self.session_id}
        if user_id is not None:
            auth["userId"] = int(user_id)

        if params is None:
            return [auth]
        if isinstance(params, list):
            return [auth, *params]
        if isinstance(params, dict):
            return [auth, params]
        return [auth, params]

    def refresh_session_from_response(self, response: dict[str, Any]) -> None:
        candidate = _extract_session(response)
        if candidate:
            self.session_id = candidate

    def login_by_token(self) -> str:
        payload = self._raw_call("client.loginByToken", self.token)
        self.refresh_session_from_response(payload)

        if not self.session_id:
            raise SklikApiError(
                "Drak loginByToken nevrátil session.",
                status_code=_status_code(payload),
                payload=payload,
                recoverable=False,
            )

        return self.session_id

    def logout(self) -> None:
        if not self.session_id:
            return
        try:
            self.call("client.logout", None)
        finally:
            self.session_id = ""

    def call(
        self,
        method: str,
        params: list | dict | str | None,
        *,
        user_id: int | None = None,
        _relogin_attempted: bool = False,
    ) -> dict[str, Any]:
        request_payload = self._inject_session(method, params, user_id=user_id)

        for attempt in range(1, self.max_retries + 2):
            try:
                payload = self._raw_call(method, request_payload)
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    raise SklikApiError(f"Drak request {method} selhal: {exc}") from exc
                time.sleep(compute_backoff_seconds(attempt))
                continue

            self.refresh_session_from_response(payload)
            status = _status_code(payload)
            message = _status_message(payload) or f"Drak method {method} returned status {status}"
            message = f"{method}: {message}"

            if status in SUCCESS_CODES:
                payload["_status"] = "success"
                return payload

            if status in PARTIAL_SUCCESS_CODES:
                payload.setdefault("_warnings", []).append(message)
                payload["_status"] = "success_with_warnings"
                return payload

            if status in RELOGIN_CODES and not _relogin_attempted and self.token:
                self.login_by_token()
                request_payload = self._inject_session(method, params, user_id=user_id)
                return self.call(method, params, user_id=user_id, _relogin_attempted=True)

            if status in SPLIT_CODES:
                raise SklikApiError(message, status_code=status, payload=payload, recoverable=True)

            if status in WARNING_CODES:
                raise SklikPartialFailure(message, status_code=status, payload=payload, recoverable=True)

            if status in RETRY_CODES:
                if attempt > self.max_retries:
                    raise SklikApiError(message, status_code=status, payload=payload, recoverable=True)
                time.sleep(compute_backoff_seconds(attempt))
                continue

            raise SklikApiError(message, status_code=status, payload=payload, recoverable=False)

        raise SklikApiError(f"Drak method {method} exceeded retry budget.")

    def api_limits(self) -> dict[str, Any]:
        return self.call("api.limits", None)

    def api_version(self) -> dict[str, Any]:
        return self.call("api.version", None)

    def client_get(self) -> dict[str, Any]:
        return self.call("client.get", None)

    def client_stats(
        self,
        user_id: int | None,
        date_from: str,
        date_to: str,
        granularity: str,
        include_zbozi: bool = False,
        split_by_conversions: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "granularity": granularity,
            "includeZbozi": bool(include_zbozi),
            "splitByConversions": bool(split_by_conversions),
        }
        return self.call("client.stats", payload, user_id=user_id)


def timestamped_warning(message: str) -> dict[str, Any]:
    return {
        "message": str(message or "").strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
