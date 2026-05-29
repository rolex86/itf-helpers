from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config.env_settings import GoogleAdsEnvConfig
from app.utils.retry import run_http_request_with_retry

GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


@dataclass(slots=True)
class Ga4ClientConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    property_id: str
    enabled: bool


@dataclass(slots=True)
class Ga4ConnectionResult:
    ok: bool
    message: str
    instructions: list[str]
    selected_property: dict[str, Any] | None
    available_properties: list[dict[str, Any]]


class Ga4ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class Ga4ApiClient:
    def __init__(self, config: Ga4ClientConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._cached_access_token: str | None = None

    @classmethod
    def from_env_config(cls, env_config: GoogleAdsEnvConfig) -> "Ga4ApiClient":
        return cls(
            Ga4ClientConfig(
                client_id=env_config.client_id,
                client_secret=env_config.client_secret,
                refresh_token=env_config.refresh_token,
                property_id=env_config.ga4_property_id,
                enabled=env_config.ga4_enabled,
            )
        )

    def is_enabled(self) -> bool:
        return bool(self.config.enabled)

    def has_required_credentials(self) -> bool:
        return bool(
            self.config.client_id
            and self.config.client_secret
            and self.config.refresh_token
            and self.config.property_id
        )

    def list_accessible_properties(self) -> list[dict[str, Any]]:
        url = "https://analyticsadmin.googleapis.com/v1alpha/accountSummaries"
        params: dict[str, Any] = {"pageSize": 200}
        items: list[dict[str, Any]] = []
        while True:
            payload = self._get_json(url, params=params)
            for summary in payload.get("accountSummaries", []) or []:
                account_name = summary.get("displayName") or ""
                account_resource = summary.get("account") or ""
                for prop in summary.get("propertySummaries", []) or []:
                    items.append(
                        {
                            "property_id": str(prop.get("property", "")).replace("properties/", ""),
                            "property_name": prop.get("displayName") or "",
                            "property_resource": prop.get("property") or "",
                            "property_type": prop.get("propertyType") or "",
                            "account_name": account_name,
                            "account_resource": account_resource,
                        }
                    )
            next_token = payload.get("nextPageToken")
            if not next_token:
                break
            params["pageToken"] = next_token
        return items

    def run_report(
        self,
        *,
        dimensions: list[str],
        metrics: list[str],
        date_from: str,
        date_to: str,
        dimension_filter: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        property_id = self.config.property_id
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": date_from, "endDate": date_to}],
            "dimensions": [{"name": name} for name in dimensions],
            "metrics": [{"name": name} for name in metrics],
        }
        if dimension_filter:
            body["dimensionFilter"] = dimension_filter
        if limit is not None:
            body["limit"] = str(limit)

        payload = self._post_json(url, json_payload=body)
        rows: list[dict[str, Any]] = []
        dim_headers = [header.get("name", "") for header in payload.get("dimensionHeaders", [])]
        metric_headers = [header.get("name", "") for header in payload.get("metricHeaders", [])]
        for row in payload.get("rows", []) or []:
            record: dict[str, Any] = {}
            for index, value in enumerate(row.get("dimensionValues", []) or []):
                if index < len(dim_headers):
                    record[dim_headers[index]] = value.get("value", "")
            for index, value in enumerate(row.get("metricValues", []) or []):
                if index < len(metric_headers):
                    record[metric_headers[index]] = value.get("value", "")
            rows.append(record)
        return rows

    def test_connection(self) -> Ga4ConnectionResult:
        if not self.config.enabled:
            return Ga4ConnectionResult(
                ok=False,
                message="GA4 modul je vypnutý.",
                instructions=[
                    "V .env nastav GA4_ENABLED=true.",
                    "Vypln GA4_PROPERTY_ID a pouzij OAuth refresh token se scope https://www.googleapis.com/auth/analytics.readonly.",
                ],
                selected_property=None,
                available_properties=[],
            )

        if not self.has_required_credentials():
            return Ga4ConnectionResult(
                ok=False,
                message="Chybi GA4 konfigurace nebo OAuth udaje.",
                instructions=[
                    "Vypln GA4_PROPERTY_ID.",
                    "Pouzij OAuth refresh token se scope https://www.googleapis.com/auth/analytics.readonly.",
                ],
                selected_property=None,
                available_properties=[],
            )

        try:
            properties = self.list_accessible_properties()
            selected = next(
                (item for item in properties if item.get("property_id") == self.config.property_id),
                None,
            )
            if selected is None:
                raise Ga4ApiError(
                    "GA4 property nebyla mezi dostupnými properties nalezena.",
                    status_code=404,
                )

            self.run_report(
                dimensions=["date"],
                metrics=["sessions"],
                date_from="7daysAgo",
                date_to="today",
                limit=1,
            )
            return Ga4ConnectionResult(
                ok=True,
                message="Přístup do GA4 property byl potvrzen.",
                instructions=[],
                selected_property=selected,
                available_properties=properties,
            )
        except Ga4ApiError as exc:
            return Ga4ConnectionResult(
                ok=False,
                message=exc.message,
                instructions=self._build_instructions(exc),
                selected_property=None,
                available_properties=[],
            )

    def _build_instructions(self, exc: Ga4ApiError) -> list[str]:
        if exc.status_code == 401:
            return [
                "OAuth refresh token nen\u00ed platn\u00fd nebo nem\u00e1 GA4 scope.",
                "Vygeneruj refresh token se scope https://www.googleapis.com/auth/analytics.readonly.",
            ]
        if exc.status_code == 403:
            return [
                "Google \u00fa\u010det nem\u00e1 p\u0159\u00edstup k dan\u00e9 GA4 property nebo chyb\u00ed scope.",
                "Zkontroluj opr\u00e1vn\u011bn\u00ed v GA4 a scope https://www.googleapis.com/auth/analytics.readonly.",
            ]
        if exc.status_code == 404:
            return [
                "GA4 property ID nebylo nalezeno mezi dostupn\u00fdmi properties.",
                "Zkus nejd\u0159\u00edv vypsat dostupn\u00e9 properties a ov\u011b\u0159 GA4_PROPERTY_ID.",
            ]
        return [
            "Ov\u011b\u0159 Analytics Admin API a Analytics Data API p\u0159\u00edstup v Google Cloud projektu.",
            "Zkontroluj scope https://www.googleapis.com/auth/analytics.readonly.",
        ]

    def _access_token(self) -> str:
        if self._cached_access_token:
            return self._cached_access_token

        token_response = run_http_request_with_retry(
            lambda: self._session.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": self.config.refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
        )
        if token_response.status_code >= 400:
            raise Ga4ApiError(
                "Nepodarilo se ziskat OAuth access token pro GA4 API.",
                status_code=token_response.status_code,
                details=token_response.text,
            )
        payload = token_response.json()
        self._cached_access_token = str(payload.get("access_token") or "").strip()
        return self._cached_access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = run_http_request_with_retry(
            lambda: self._session.get(url, headers=self._headers(), params=params, timeout=60)
        )
        if response.status_code >= 400:
            raise Ga4ApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _post_json(self, url: str, json_payload: dict[str, Any]) -> dict[str, Any]:
        response = run_http_request_with_retry(
            lambda: self._session.post(url, headers=self._headers(), json=json_payload, timeout=120)
        )
        if response.status_code >= 400:
            raise Ga4ApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"GA4 API request failed with HTTP {response.status_code}."
        error = payload.get("error", {})
        return str(error.get("message") or f"GA4 API request failed with HTTP {response.status_code}.")
