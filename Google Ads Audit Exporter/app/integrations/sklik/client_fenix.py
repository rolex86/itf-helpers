from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.integrations.sklik.errors import SklikApiError, SklikPartialFailure
from app.integrations.sklik.normalizers import normalize_fenix_list
from app.integrations.sklik.rate_limit import compute_backoff_seconds


class SklikFenixClient:
    def __init__(
        self,
        refresh_token: str,
        base_url: str,
        timeout: int,
        max_retries: int,
        user_agent: str,
        user_id: str | None = None,
    ) -> None:
        self.refresh_token = str(refresh_token or "").strip()
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = int(timeout)
        self.max_retries = int(max_retries)
        self.user_agent = str(user_agent or "").strip()
        self.user_id = str(user_id or "").strip()
        self.access_token = ""
        self.access_token_expires_at: datetime | None = None
        self.http = requests.Session()
        self.http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
        )

    def _token_url(self) -> str:
        return f"{self.base_url}/user/token"

    def refresh_access_token(self) -> str:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.refresh_token}",
        }
        payload: dict[str, Any] = {
            "grant_type": "client_credentials",
        }

        response = self.http.post(
            self._token_url(),
            data=payload,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, dict):
            raise SklikApiError("Fenix /user/token nevr?til JSON objekt.", payload=data)

        token = str(data.get("access_token") or "").strip()
        if not token:
            raise SklikApiError("Fenix /user/token nevr?til access_token.", payload=data)

        self.access_token = token
        expires_in = int(data.get("expires_in") or 300)
        self.access_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
        return token

    def get_access_token(self) -> str:
        if self.access_token and self.access_token_expires_at and self.access_token_expires_at > datetime.now(timezone.utc):
            return self.access_token
        return self.refresh_access_token()

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        normalized_method = str(method or "GET").upper()
        if normalized_method != "GET":
            raise SklikApiError("Fenix client je read-only; povolený je pouze GET mimo token endpoint.")

        url = f"{self.base_url}/{str(path or '').lstrip('/')}"
        token = self.get_access_token()

        for attempt in range(1, self.max_retries + 2):
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            response = self.http.request(
                normalized_method,
                url,
                params=params or {},
                json=json,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 401:
                if attempt > 1:
                    raise SklikApiError(
                        f"Fenix request {path} selhal HTTP 401 i po refreshi tokenu.",
                        status_code=401,
                        recoverable=False,
                    )
                token = self.refresh_access_token()
                continue

            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt > self.max_retries:
                    raise SklikApiError(
                        f"Fenix request {path} selhal HTTP {response.status_code}.",
                        status_code=response.status_code,
                        recoverable=True,
                    )
                time.sleep(compute_backoff_seconds(attempt))
                continue

            if response.status_code in {403, 404, 409}:
                raise SklikPartialFailure(
                    f"Fenix endpoint {path} vrátil HTTP {response.status_code}.",
                    status_code=response.status_code,
                    payload=response.text,
                    recoverable=True,
                )

            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, (dict, list)):
                return payload
            return {"result": payload}

        raise SklikApiError(f"Fenix request {path} exceeded retry budget.")

    def get_api_home(self) -> dict[str, Any]:
        payload = self.request("GET", "")
        return payload if isinstance(payload, dict) else {"items": payload}

    def get_user_me(self) -> dict[str, Any]:
        payload = self.request("GET", "user/me")
        return payload if isinstance(payload, dict) else {"items": payload}

    def list_nakupy_premises(self) -> list[dict[str, Any]]:
        raise SklikPartialFailure(
            "FENIX_PREMISES_AUTODISCOVERY_NOT_CONFIRMED_BY_PUBLIC_DOCS",
            recoverable=True,
        )

    def list_nakupy_campaigns(self, premise_id: str | int) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "nakupy/campaigns",
            params={"premiseId": int(premise_id)},
        )
        return normalize_fenix_list(payload)

    def get_nakupy_campaign(self, premise_id: str | int, campaign_id: str | int) -> dict[str, Any]:
        campaigns = self.list_nakupy_campaigns(premise_id)
        for campaign in campaigns:
            if str(campaign.get("id") or "") == str(campaign_id):
                return campaign
        raise SklikPartialFailure(
            f"Fenix campaign {campaign_id} for premise {premise_id} nebyla nalezena.",
            recoverable=True,
        )

    def list_feed_statuses(self, premise_id: str | int) -> list[dict[str, Any]]:
        raise SklikPartialFailure(
            "Fenix feed status endpoint není potvrzený veřejnou dokumentací / OpenAPI.",
            recoverable=True,
        )

    def get_campaign_stats(self, premise_id: str | int, date_from: str, date_to: str, granularity: str) -> list[dict[str, Any]]:
        raise SklikPartialFailure(
            "Fenix campaign stats endpoint není potvrzený veřejnou dokumentací / OpenAPI.",
            recoverable=True,
        )

    def list_gdpr_withdrawals(self, premise_id: str | int, date_from: str, date_to: str) -> list[dict[str, Any]]:
        raise SklikPartialFailure(
            "Fenix GDPR withdrawals endpoint není potvrzený veřejnou dokumentací / OpenAPI.",
            recoverable=True,
        )
