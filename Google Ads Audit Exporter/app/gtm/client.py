from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config.env_settings import GoogleAdsEnvConfig

GTM_READONLY_SCOPE = "https://www.googleapis.com/auth/tagmanager.readonly"


@dataclass(slots=True)
class GtmClientConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    account_id: str
    container_id: str
    enabled: bool


@dataclass(slots=True)
class GtmConnectionResult:
    ok: bool
    message: str
    instructions: list[str]
    selected_account: dict[str, Any] | None
    selected_container: dict[str, Any] | None
    available_accounts: list[dict[str, Any]]


class GtmApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class GtmApiClient:
    def __init__(self, config: GtmClientConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._cached_access_token: str | None = None

    @classmethod
    def from_env_config(cls, env_config: GoogleAdsEnvConfig) -> "GtmApiClient":
        return cls(
            GtmClientConfig(
                client_id=env_config.client_id,
                client_secret=env_config.client_secret,
                refresh_token=env_config.refresh_token,
                account_id=env_config.gtm_account_id,
                container_id=env_config.gtm_container_id,
                enabled=env_config.gtm_enabled,
            )
        )

    def is_enabled(self) -> bool:
        return bool(self.config.enabled)

    def has_required_credentials(self) -> bool:
        return bool(
            self.config.client_id
            and self.config.client_secret
            and self.config.refresh_token
            and self.config.account_id
            and self.config.container_id
        )

    def list_accounts(self) -> list[dict[str, Any]]:
        payload = self._get_json("https://tagmanager.googleapis.com/tagmanager/v1/accounts")
        accounts = payload.get("accounts", []) or payload.get("account", []) or []
        return [
            {
                "account_id": item.get("accountId", ""),
                "name": item.get("name", ""),
                "share_data": item.get("shareData", False),
                "path": item.get("path", ""),
            }
            for item in accounts
        ]

    def list_containers(self, account_id: str | None = None) -> list[dict[str, Any]]:
        target_account = account_id or self.config.account_id
        payload = self._get_json(
            f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{target_account}/containers"
        )
        containers = payload.get("container", []) or []
        return [
            {
                "account_id": item.get("accountId", ""),
                "container_id": item.get("containerId", ""),
                "name": item.get("name", ""),
                "usage_context": " | ".join(item.get("usageContext", []) or []),
                "public_id": item.get("publicId", ""),
                "path": item.get("path", ""),
            }
            for item in containers
        ]

    def list_workspaces(self, account_id: str | None = None, container_id: str | None = None) -> list[dict[str, Any]]:
        target_account = account_id or self.config.account_id
        target_container = container_id or self.config.container_id
        payload = self._get_json(
            f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{target_account}/containers/{target_container}/workspaces"
        )
        workspaces = payload.get("workspace", []) or []
        return [
            {
                "workspace_id": item.get("workspaceId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "path": item.get("path", ""),
            }
            for item in workspaces
        ]

    def list_tags(self, workspace_path: str) -> list[dict[str, Any]]:
        return self._list_workspace_collection(workspace_path=workspace_path, collection="tags", key="tag")

    def list_triggers(self, workspace_path: str) -> list[dict[str, Any]]:
        return self._list_workspace_collection(workspace_path=workspace_path, collection="triggers", key="trigger")

    def list_variables(self, workspace_path: str) -> list[dict[str, Any]]:
        return self._list_workspace_collection(workspace_path=workspace_path, collection="variables", key="variable")

    def list_versions(self, account_id: str | None = None, container_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        target_account = account_id or self.config.account_id
        target_container = container_id or self.config.container_id
        payload = self._get_json(
            f"https://tagmanager.googleapis.com/tagmanager/v1/accounts/{target_account}/containers/{target_container}/versions",
            params={"headers": "true", "includeDeleted": "true"},
        )
        return {
            "containerVersion": payload.get("containerVersion", []) or [],
            "containerVersionHeader": payload.get("containerVersionHeader", []) or [],
        }

    def test_connection(self) -> GtmConnectionResult:
        if not self.config.enabled:
            return GtmConnectionResult(
                ok=False,
                message="GTM modul je vypnuty.",
                instructions=[
                    "V .env nastav GTM_ENABLED=true.",
                    "Vypln GTM_ACCOUNT_ID, GTM_CONTAINER_ID a pouzij OAuth refresh token se scope https://www.googleapis.com/auth/tagmanager.readonly.",
                ],
                selected_account=None,
                selected_container=None,
                available_accounts=[],
            )
        if not self.has_required_credentials():
            return GtmConnectionResult(
                ok=False,
                message="Chybi GTM konfigurace nebo OAuth udaje.",
                instructions=[
                    "Vypln GTM_ACCOUNT_ID a GTM_CONTAINER_ID.",
                    "Pouzij OAuth refresh token se scope https://www.googleapis.com/auth/tagmanager.readonly.",
                ],
                selected_account=None,
                selected_container=None,
                available_accounts=[],
            )
        try:
            accounts = self.list_accounts()
            selected_account = next(
                (item for item in accounts if item.get("account_id") == self.config.account_id),
                None,
            )
            if selected_account is None:
                raise GtmApiError("GTM account nebyl mezi dostupnymi ucty nalezen.", status_code=404)
            containers = self.list_containers(self.config.account_id)
            selected_container = next(
                (item for item in containers if item.get("container_id") == self.config.container_id),
                None,
            )
            if selected_container is None:
                raise GtmApiError("GTM container nebyl mezi dostupnymi kontejnery nalezen.", status_code=404)
            self.list_workspaces(self.config.account_id, self.config.container_id)
            return GtmConnectionResult(
                ok=True,
                message="Pristup do GTM accountu a containeru byl potvrzen.",
                instructions=[],
                selected_account=selected_account,
                selected_container=selected_container,
                available_accounts=accounts,
            )
        except GtmApiError as exc:
            return GtmConnectionResult(
                ok=False,
                message=exc.message,
                instructions=self._build_instructions(exc),
                selected_account=None,
                selected_container=None,
                available_accounts=[],
            )

    def _build_instructions(self, exc: GtmApiError) -> list[str]:
        if exc.status_code == 401:
            return [
                "OAuth refresh token neni platny nebo nema GTM readonly scope.",
                "Vygeneruj refresh token se scope https://www.googleapis.com/auth/tagmanager.readonly.",
            ]
        if exc.status_code == 403:
            return [
                "Google ucet nema pristup do GTM nebo chybi readonly scope.",
                "Zkontroluj opravneni v GTM a scope https://www.googleapis.com/auth/tagmanager.readonly.",
            ]
        if exc.status_code == 404:
            return [
                "Over GTM_ACCOUNT_ID a GTM_CONTAINER_ID.",
                "Zkus v UI nejdriv vypsat dostupne GTM ucty a kontejnery.",
            ]
        return [
            "Over Tag Manager API pristup v Google Cloud projektu.",
            "Zkontroluj scope https://www.googleapis.com/auth/tagmanager.readonly.",
        ]

    def _list_workspace_collection(
        self,
        *,
        workspace_path: str,
        collection: str,
        key: str,
    ) -> list[dict[str, Any]]:
        page_token: str | None = None
        items: list[dict[str, Any]] = []
        while True:
            params = {"pageToken": page_token} if page_token else None
            payload = self._get_json(
                f"https://tagmanager.googleapis.com/tagmanager/v2/{workspace_path}/{collection}",
                params=params,
            )
            items.extend(payload.get(key, []) or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return items

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
            raise GtmApiError(
                "Nepodarilo se ziskat OAuth access token pro GTM API.",
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
        }

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._session.get(url, headers=self._headers(), params=params, timeout=60)
        if response.status_code >= 400:
            raise GtmApiError(
                self._error_message(response),
                status_code=response.status_code,
                details=response.text,
            )
        return response.json()

    def _error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"GTM API request failed with HTTP {response.status_code}."
        error = payload.get("error", {})
        return str(error.get("message") or f"GTM API request failed with HTTP {response.status_code}.")
