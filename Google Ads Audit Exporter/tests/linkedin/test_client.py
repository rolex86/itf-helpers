from __future__ import annotations

from typing import Any

import pytest
import requests

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.models import LinkedInRuntimeConfig


class DummyResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class DummySession:
    def __init__(self, responses: list[DummyResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> DummyResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "kwargs": kwargs,
            }
        )

        if not self.responses:
            raise AssertionError("No dummy response left.")

        return self.responses.pop(0)


def runtime_config() -> LinkedInRuntimeConfig:
    return LinkedInRuntimeConfig(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://example.test/callback",
        api_version="202606",
        user_agent="TestLinkedInAudit/1.0",
        request_timeout_seconds=10,
        max_retries=0,
    )


def test_client_sends_restli_headers() -> None:
    session = DummySession(
        [
            DummyResponse(
                payload={
                    "elements": [],
                }
            )
        ]
    )
    client = LinkedInRestClient(
        config=runtime_config(),
        access_token="access-token",
        session=session,  # type: ignore[arg-type]
    )

    client.get("adAccounts", params={"q": "search"})

    call = session.calls[0]
    headers = call["kwargs"]["headers"]

    assert call["method"] == "GET"
    assert call["url"].endswith("/rest/adAccounts")
    assert headers["Authorization"] == "Bearer access-token"
    assert headers["LinkedIn-Version"] == "202606"
    assert headers["X-Restli-Protocol-Version"] == "2.0.0"
    assert headers["User-Agent"] == "TestLinkedInAudit/1.0"


def test_query_tunneling_uses_post_without_original_url_params() -> None:
    session = DummySession(
        [
            DummyResponse(status_code=414, payload={"message": "URI too long"}, text='{"message":"URI too long"}'),
            DummyResponse(payload={"elements": [{"id": "1"}]}),
        ]
    )
    client = LinkedInRestClient(
        config=runtime_config(),
        access_token="access-token",
        session=session,  # type: ignore[arg-type]
    )

    result = client.get(
        "adAnalytics",
        params={
            "q": "analytics",
            "accounts": "List(urn:li:sponsoredAccount:123)",
            "fields": "impressions,clicks,costInLocalCurrency",
        },
    )

    assert result == {"elements": [{"id": "1"}]}
    assert len(session.calls) == 2

    first_call = session.calls[0]
    second_call = session.calls[1]

    assert first_call["method"] == "GET"
    assert first_call["kwargs"]["params"]["q"] == "analytics"

    assert second_call["method"] == "POST"
    assert second_call["kwargs"].get("params") is None
    assert second_call["kwargs"]["data"]["q"] == "analytics"
    assert second_call["kwargs"]["data"]["accounts"] == "List(urn:li:sponsoredAccount:123)"
    assert second_call["kwargs"]["headers"]["X-HTTP-Method-Override"] == "GET"
    assert second_call["kwargs"]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_paginate_cursor_uses_page_token() -> None:
    session = DummySession(
        [
            DummyResponse(
                payload={
                    "elements": [{"id": "1"}],
                    "metadata": {
                        "nextPageToken": "next-token",
                    },
                }
            ),
            DummyResponse(
                payload={
                    "elements": [{"id": "2"}],
                    "metadata": {},
                }
            ),
        ]
    )
    client = LinkedInRestClient(
        config=runtime_config(),
        access_token="access-token",
        session=session,  # type: ignore[arg-type]
    )

    rows = list(
        client.paginate_cursor(
            "adAccounts/123/adCampaigns",
            params={"q": "search"},
            page_size=100,
        )
    )

    assert rows == [{"id": "1"}, {"id": "2"}]
    assert session.calls[0]["kwargs"]["params"]["pageSize"] == 100
    assert "pageToken" not in session.calls[0]["kwargs"]["params"]
    assert session.calls[1]["kwargs"]["params"]["pageToken"] == "next-token"


def test_paginate_start_count_uses_start_and_count() -> None:
    session = DummySession(
        [
            DummyResponse(
                payload={
                    "elements": [{"id": "1"}],
                    "paging": {
                        "start": 0,
                        "count": 1,
                        "total": 2,
                    },
                }
            ),
            DummyResponse(
                payload={
                    "elements": [{"id": "2"}],
                    "paging": {
                        "start": 1,
                        "count": 1,
                        "total": 2,
                    },
                }
            ),
        ]
    )
    client = LinkedInRestClient(
        config=runtime_config(),
        access_token="access-token",
        session=session,  # type: ignore[arg-type]
    )

    rows = list(
        client.paginate_start_count(
            "leadForms",
            params={"q": "owner", "owner": "(sponsoredAccount:urn:li:sponsoredAccount:123)"},
            count=1,
        )
    )

    assert rows == [{"id": "1"}, {"id": "2"}]
    assert session.calls[0]["kwargs"]["params"]["start"] == 0
    assert session.calls[0]["kwargs"]["params"]["count"] == 1
    assert session.calls[1]["kwargs"]["params"]["start"] == 1
    assert session.calls[1]["kwargs"]["params"]["count"] == 1


def test_http_error_payload_masks_sensitive_values() -> None:
    session = DummySession(
        [
            DummyResponse(
                status_code=400,
                payload={
                    "message": "bad request",
                    "access_token": "secret-access-token",
                    "refresh_token": "secret-refresh-token",
                    "client_secret": "secret-client-secret",
                },
                text='{"message":"bad request"}',
            )
        ]
    )
    client = LinkedInRestClient(
        config=runtime_config(),
        access_token="access-token",
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(Exception) as exc_info:
        client.get("adAccounts", params={"q": "search"})

    message = str(exc_info.value)
    assert "secret-access-token" not in message
    assert "secret-refresh-token" not in message
    assert "secret-client-secret" not in message