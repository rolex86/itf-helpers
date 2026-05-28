from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class PageSpeedClientConfig:
    api_key: str
    enabled: bool


class PageSpeedApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class PageSpeedApiClient:
    def __init__(self, config: PageSpeedClientConfig) -> None:
        self.config = config
        self._session = requests.Session()

    def is_enabled(self) -> bool:
        return bool(self.config.enabled)

    def run_pagespeed(
        self,
        *,
        url: str,
        strategy: str,
    ) -> dict[str, Any]:
        params = {
            "url": url,
            "strategy": strategy,
            "category": ["performance", "accessibility", "best-practices", "seo"],
        }
        if self.config.api_key:
            params["key"] = self.config.api_key

        response = self._session.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params=params,
            timeout=180,
        )
        if response.status_code >= 400:
            raise PageSpeedApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"PageSpeed API request failed with HTTP {response.status_code}."
        error = payload.get("error", {})
        return str(error.get("message") or f"PageSpeed API request failed with HTTP {response.status_code}.")
