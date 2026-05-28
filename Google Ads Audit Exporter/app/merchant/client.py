from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config.env_settings import GoogleAdsEnvConfig
from app.utils.retry import run_http_request_with_retry

CONTENT_SCOPE = "https://www.googleapis.com/auth/content"


@dataclass(slots=True)
class MerchantClientConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    merchant_account_id: str
    enabled: bool


@dataclass(slots=True)
class MerchantConnectionResult:
    ok: bool
    message: str
    instructions: list[str]
    selected_account: dict[str, Any] | None
    available_accounts: list[dict[str, Any]]
    service_count: int = 0


class MerchantApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def _resource_name(account_id: str) -> str:
    return account_id if str(account_id).startswith("accounts/") else f"accounts/{account_id}"


def _safe_text(value: object) -> str:
    return str(value or "").strip()


class MerchantApiClient:
    def __init__(self, config: MerchantClientConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._cached_access_token: str | None = None

    @classmethod
    def from_env_config(cls, env_config: GoogleAdsEnvConfig) -> "MerchantApiClient":
        return cls(
            MerchantClientConfig(
                client_id=env_config.client_id,
                client_secret=env_config.client_secret,
                refresh_token=env_config.refresh_token,
                merchant_account_id=env_config.merchant_account_id,
                enabled=env_config.merchant_enabled,
            )
        )

    def is_enabled(self) -> bool:
        return bool(self.config.enabled)

    def has_required_credentials(self) -> bool:
        return bool(
            self.config.client_id
            and self.config.client_secret
            and self.config.refresh_token
            and self.config.merchant_account_id
        )

    def list_accessible_accounts(self) -> list[dict[str, Any]]:
        items = self._paginate_json(
            "https://merchantapi.googleapis.com/accounts/v1/accounts",
            list_key="accounts",
            params={"pageSize": 250},
        )
        return [self._account_to_view(account) for account in items]

    def list_subaccounts(self, provider_account_id: str) -> list[dict[str, Any]]:
        target = _resource_name(provider_account_id)
        items = self._paginate_json(
            f"https://merchantapi.googleapis.com/accounts/v1/{target}:listSubaccounts",
            list_key="accounts",
            params={"pageSize": 250},
        )
        return [self._account_to_view(account) for account in items]

    def get_account(self, account_id: str | None = None) -> dict[str, Any]:
        target = _resource_name(account_id or self.config.merchant_account_id)
        response = self._get_json(f"https://merchantapi.googleapis.com/accounts/v1/{target}")
        return self._account_to_view(response)

    def list_account_services(self, account_id: str | None = None) -> list[dict[str, Any]]:
        target = _resource_name(account_id or self.config.merchant_account_id)
        return self._paginate_json(
            f"https://merchantapi.googleapis.com/accounts/v1/{target}/services",
            list_key="accountServices",
            params={"pageSize": 250},
        )

    def list_products(self, account_id: str | None = None) -> list[dict[str, Any]]:
        target = _resource_name(account_id or self.config.merchant_account_id)
        return self._paginate_json(
            f"https://merchantapi.googleapis.com/products/v1/{target}/products",
            list_key="products",
            params={"pageSize": 250},
        )

    def list_aggregate_product_statuses(self, account_id: str | None = None) -> list[dict[str, Any]]:
        target = _resource_name(account_id or self.config.merchant_account_id)
        return self._paginate_json(
            f"https://merchantapi.googleapis.com/issueresolution/v1/{target}/aggregateProductStatuses",
            list_key="aggregateProductStatuses",
            params={"pageSize": 250},
        )

    def test_connection(self, account_id: str | None = None) -> MerchantConnectionResult:
        instructions: list[str] = []
        if not self.config.enabled:
            return MerchantConnectionResult(
                ok=False,
                message="Merchant Center modul je vypnuty.",
                instructions=[
                    "V .env nastav GOOGLE_MERCHANT_ENABLED=true.",
                    "Vypln Merchant account ID a pouzij OAuth refresh token se scope https://www.googleapis.com/auth/content.",
                ],
                selected_account=None,
                available_accounts=[],
            )

        if not self.has_required_credentials():
            return MerchantConnectionResult(
                ok=False,
                message="Chybi Merchant konfigurace nebo OAuth udaje.",
                instructions=[
                    "Vypln MERCHANT_CENTER_ACCOUNT_ID.",
                    "Pouzij stejne OAuth client ID/secret a refresh token jako pro Google API, ale se scope https://www.googleapis.com/auth/content.",
                ],
                selected_account=None,
                available_accounts=[],
            )

        try:
            available_accounts = self.list_accessible_accounts()
            selected_account = self.get_account(account_id)
            services = self.list_account_services(account_id)
            return MerchantConnectionResult(
                ok=True,
                message="Pristup do Merchant Center byl potvrzen.",
                instructions=[],
                selected_account=selected_account,
                available_accounts=available_accounts,
                service_count=len(services),
            )
        except MerchantApiError as exc:
            instructions.extend(self._build_instructions(exc))
            return MerchantConnectionResult(
                ok=False,
                message=exc.message,
                instructions=instructions,
                selected_account=None,
                available_accounts=[],
            )

    def _account_to_view(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": account.get("name", ""),
            "account_id": _safe_text(account.get("accountId")) or _safe_text(account.get("name")).replace("accounts/", ""),
            "account_name": account.get("accountName") or account.get("displayName") or "",
            "homepage": account.get("homepage") or account.get("websiteUrl") or "",
            "website_url": account.get("homepage") or account.get("websiteUrl") or "",
            "time_zone": account.get("timeZone") or "",
            "language_code": account.get("languageCode") or "",
            "listing_type": account.get("listingType") or "",
        }

    def _build_instructions(self, exc: MerchantApiError) -> list[str]:
        if exc.status_code == 401:
            return [
                "OAuth refresh token neni platny nebo chybi pristupovy scope.",
                "Vygeneruj refresh token se scope https://www.googleapis.com/auth/content.",
            ]
        if exc.status_code == 403:
            return [
                "OAuth token nema pristup k Merchant Center uctu nebo chybi opravneni.",
                "Zkontroluj sdileni Merchant uctu pro dany Google ucet a scope https://www.googleapis.com/auth/content.",
            ]
        if exc.status_code == 404:
            return [
                "Merchant account ID nebyl nalezen nebo neni pristupny z aktualniho Google uctu.",
                "Over MERCHANT_CENTER_ACCOUNT_ID a zkus vypsat dostupne ucty.",
            ]
        return [
            "Over Merchant API pristup v Google Cloud projektu.",
            "Zkus nejdriv vypsat dostupne Merchant ucty a zkontroluj scope https://www.googleapis.com/auth/content.",
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
            ),
        )
        if token_response.status_code >= 400:
            raise MerchantApiError(
                "Nepodarilo se ziskat OAuth access token pro Merchant API.",
                status_code=token_response.status_code,
                details=token_response.text,
            )
        payload = token_response.json()
        self._cached_access_token = _safe_text(payload.get("access_token"))
        return self._cached_access_token

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "application/json",
        }
        response = run_http_request_with_retry(
            lambda: self._session.get(url, headers=headers, params=params, timeout=60),
        )
        if response.status_code >= 400:
            raise MerchantApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _paginate_json(
        self,
        url: str,
        *,
        list_key: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        merged_params = dict(params or {})
        items: list[dict[str, Any]] = []
        while True:
            payload = self._get_json(url, params=merged_params)
            page_items = payload.get(list_key, [])
            if isinstance(page_items, list):
                items.extend(page_items)
            next_token = payload.get("nextPageToken")
            if not next_token:
                break
            merged_params["pageToken"] = next_token
        return items

    def _error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Merchant API request failed with HTTP {response.status_code}."
        error = payload.get("error", {})
        return str(error.get("message") or f"Merchant API request failed with HTTP {response.status_code}.")
