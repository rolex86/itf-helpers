from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from app.config.env_settings import GoogleAdsEnvConfig

GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


@dataclass(slots=True)
class SearchConsoleClientConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    site_url: str
    enabled: bool


@dataclass(slots=True)
class SearchConsoleConnectionResult:
    ok: bool
    message: str
    instructions: list[str]
    selected_property: dict[str, Any] | None
    available_properties: list[dict[str, Any]]


class SearchConsoleApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class SearchConsoleApiClient:
    def __init__(self, config: SearchConsoleClientConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._cached_access_token: str | None = None

    @classmethod
    def from_env_config(cls, env_config: GoogleAdsEnvConfig) -> "SearchConsoleApiClient":
        return cls(
            SearchConsoleClientConfig(
                client_id=env_config.client_id,
                client_secret=env_config.client_secret,
                refresh_token=env_config.refresh_token,
                site_url=env_config.gsc_site_url,
                enabled=env_config.gsc_enabled,
            )
        )

    def is_enabled(self) -> bool:
        return bool(self.config.enabled)

    def has_required_credentials(self) -> bool:
        return bool(
            self.config.client_id
            and self.config.client_secret
            and self.config.refresh_token
            and self.config.site_url
        )

    def list_sites(self) -> list[dict[str, Any]]:
        payload = self._get_json("https://www.googleapis.com/webmasters/v3/sites")
        rows = payload.get("siteEntry", []) or []
        return [
            {
                "site_url": row.get("siteUrl", ""),
                "permission_level": row.get("permissionLevel", ""),
            }
            for row in rows
        ]

    def query_search_analytics(
        self,
        *,
        start_date: str,
        end_date: str,
        dimensions: list[str],
        row_limit: int = 25000,
        start_row: int = 0,
    ) -> list[dict[str, Any]]:
        site_url = quote(self.config.site_url, safe="")
        url = f"https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": start_row,
            "type": "web",
        }
        payload = self._post_json(url, body)
        rows: list[dict[str, Any]] = []
        for row in payload.get("rows", []) or []:
            keys = row.get("keys", []) or []
            record: dict[str, Any] = {}
            for index, dimension in enumerate(dimensions):
                record[dimension] = keys[index] if index < len(keys) else ""
            record["clicks"] = row.get("clicks", 0)
            record["impressions"] = row.get("impressions", 0)
            record["ctr"] = row.get("ctr", 0)
            record["position"] = row.get("position", 0)
            rows.append(record)
        return rows

    def test_connection(self) -> SearchConsoleConnectionResult:
        if not self.config.enabled:
            return SearchConsoleConnectionResult(
                ok=False,
                message="Search Console modul je vypnuty.",
                instructions=[
                    "V .env nastav GSC_ENABLED=true.",
                    "Vypln GSC_SITE_URL a pouzij OAuth refresh token se scope https://www.googleapis.com/auth/webmasters.readonly.",
                ],
                selected_property=None,
                available_properties=[],
            )

        if not self.has_required_credentials():
            return SearchConsoleConnectionResult(
                ok=False,
                message="Chybi Search Console konfigurace nebo OAuth udaje.",
                instructions=[
                    "Vypln GSC_SITE_URL.",
                    "Pouzij OAuth refresh token se scope https://www.googleapis.com/auth/webmasters.readonly.",
                ],
                selected_property=None,
                available_properties=[],
            )

        try:
            sites = self.list_sites()
            selected = next((site for site in sites if site.get("site_url") == self.config.site_url), None)
            if selected is None:
                raise SearchConsoleApiError(
                    "Search Console property nebyla mezi dostupnymi properties nalezena.",
                    status_code=404,
                )
            self.query_search_analytics(
                start_date="2026-01-01",
                end_date="2026-01-07",
                dimensions=["date"],
                row_limit=1,
            )
            return SearchConsoleConnectionResult(
                ok=True,
                message="Pristup do Search Console property byl potvrzen.",
                instructions=[],
                selected_property=selected,
                available_properties=sites,
            )
        except SearchConsoleApiError as exc:
            return SearchConsoleConnectionResult(
                ok=False,
                message=exc.message,
                instructions=self._build_instructions(exc),
                selected_property=None,
                available_properties=[],
            )

    def _build_instructions(self, exc: SearchConsoleApiError) -> list[str]:
        if exc.status_code == 401:
            return [
                "OAuth refresh token neni platny nebo nema Search Console scope.",
                "Vygeneruj refresh token se scope https://www.googleapis.com/auth/webmasters.readonly.",
            ]
        if exc.status_code == 403:
            return [
                "Google ucet nema pristup k dane Search Console property.",
                "Zkontroluj opravneni v Search Console a scope https://www.googleapis.com/auth/webmasters.readonly.",
            ]
        if exc.status_code == 404:
            return [
                "Property nebyla nalezena mezi dostupnymi sites.",
                "Over GSC_SITE_URL a zkus nejdriv vypsat dostupne properties v UI.",
            ]
        return [
            "Over Search Console API pristup v Google Cloud projektu.",
            "Zkontroluj scope https://www.googleapis.com/auth/webmasters.readonly.",
        ]

    def _access_token(self) -> str:
        if self._cached_access_token:
            return self._cached_access_token
        response = self._session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise SearchConsoleApiError(
                "Nepodarilo se ziskat OAuth access token pro Search Console API.",
                status_code=response.status_code,
                details=response.text,
            )
        payload = response.json()
        self._cached_access_token = str(payload.get("access_token") or "").strip()
        return self._cached_access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self._session.get(url, headers=self._headers(), timeout=60)
        if response.status_code >= 400:
            raise SearchConsoleApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _post_json(self, url: str, json_payload: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(url, headers=self._headers(), json=json_payload, timeout=120)
        if response.status_code >= 400:
            raise SearchConsoleApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Search Console API request failed with HTTP {response.status_code}."
        error = payload.get("error", {})
        return str(error.get("message") or f"Search Console API request failed with HTTP {response.status_code}.")
