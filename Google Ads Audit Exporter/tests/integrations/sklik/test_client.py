from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.integrations.sklik.client_drak import SklikDrakClient
from app.integrations.sklik.client_fenix import SklikFenixClient
from app.integrations.sklik.errors import SklikApiError
from app.integrations.sklik.normalizers import extract_rows, flatten_report_rows, normalize_generic_entity
from app.integrations.sklik.reporting import (
    build_report_create_params,
    build_report_read_params,
    fetch_report_rows,
)


class DummyResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _drak_client() -> SklikDrakClient:
    return SklikDrakClient(
        token="secret",
        base_url="https://api.sklik.cz/drak/json/v5",
        timeout=60,
        max_retries=2,
        user_agent="TestSklik/1.0",
    )


def test_drak_client_builds_json_url_and_string_token(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, json: Any, timeout: int):
        captured["url"] = url
        captured["json"] = json
        return DummyResponse({"session": "sess-1", "status": 200})

    client = _drak_client()
    monkeypatch.setattr(client.http, "post", fake_post)

    client.login_by_token()

    assert captured["url"] == "https://api.sklik.cz/drak/json/v5/client.loginByToken"
    assert captured["json"] == "secret"


def test_drak_client_retries_429(monkeypatch) -> None:
    responses = [
        {"status": 429, "statusMessage": "Too many requests"},
        {"status": 200, "campaigns": [{"id": 1}]},
    ]

    client = _drak_client()
    client.session_id = "sess-1"
    monkeypatch.setattr(client, "_raw_call", lambda method, params: responses.pop(0))
    monkeypatch.setattr("app.integrations.sklik.client_drak.time.sleep", lambda value: None)

    payload = client.call("campaigns.list", None, user_id=123)

    assert payload["campaigns"] == [{"id": 1}]
    assert responses == []


def test_drak_client_relogin_on_401_once(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []
    responses = [
        {"session": "sess-login-1", "status": 200},
        {"status": 401, "statusMessage": "Invalid session"},
        {"session": "sess-login-2", "status": 200},
        {"status": 200, "campaigns": [{"id": 1}]},
    ]

    client = _drak_client()

    def fake_raw_call(method: str, params: Any):
        calls.append((method, params))
        return responses.pop(0)

    monkeypatch.setattr(client, "_raw_call", fake_raw_call)

    client.login_by_token()
    payload = client.call("campaigns.list", None, user_id=123)

    assert payload["campaigns"] == [{"id": 1}]
    assert [item[0] for item in calls].count("client.loginByToken") == 2


def test_drak_status_206_is_warning_not_failure(monkeypatch) -> None:
    client = _drak_client()
    client.session_id = "sess-1"
    monkeypatch.setattr(
        client,
        "_raw_call",
        lambda method, params: {"status": 206, "statusMessage": "Partially OK", "campaigns": []},
    )

    payload = client.call("campaigns.list", None, user_id=123)

    assert payload["_status"] == "success_with_warnings"
    assert payload["_warnings"] == ["Partially OK"]


def test_drak_client_stats_payload_shape(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _drak_client()
    client.session_id = "sess-1"

    def fake_raw_call(method: str, params: Any):
        captured["method"] = method
        captured["params"] = params
        return {"status": 200, "report": []}

    monkeypatch.setattr(client, "_raw_call", fake_raw_call)

    client.client_stats(123, "2026-01-01", "2026-01-31", "daily", include_zbozi=True, split_by_conversions=True)

    assert captured["method"] == "client.stats"
    assert captured["params"] == [
        {"session": "sess-1", "userId": 123},
        {
            "dateFrom": "2026-01-01",
            "dateTo": "2026-01-31",
            "granularity": "daily",
            "includeZbozi": True,
            "splitByConversions": True,
        },
    ]


def test_extract_rows_supports_realistic_drak_shapes() -> None:
    assert extract_rows({"status": 200, "campaigns": [{"id": 1}]}) == [{"id": 1}]
    assert extract_rows({"status": 200, "report": [{"id": 2}]}) == [{"id": 2}]
    assert extract_rows({"status": 200, "conversions": [{"id": 3}]}) == [{"id": 3}]
    assert extract_rows({"status": 200, "predefinedRegions": [{"id": 4}]}) == [{"id": 4}]
    assert extract_rows({"status": 200, "budgets": [{"id": 5}]}) == [{"id": 5}]
    assert extract_rows({"status": 200, "lists": [{"id": 6}]}) == [{"id": 6}]


def test_flatten_report_rows_keeps_conversion_value_in_czk() -> None:
    rows = flatten_report_rows(
        [
            {
                "id": 1,
                "stats": [
                    {
                        "totalMoney": 9166,
                        "conversions": 1,
                        "conversionValue": 46943,
                        "pno": 0.00195,
                    }
                ],
            }
        ],
        user_id=123,
        entity="campaigns",
    )

    assert rows[0]["totalMoney_czk"] == 91.66
    assert rows[0]["cost_raw"] == 9166
    assert rows[0]["cost_czk"] == 91.66
    assert rows[0]["conversionValue_raw"] == 46943
    assert rows[0]["conversionValue_czk"] == 46943.0
    assert rows[0]["conversion_value_raw"] == 46943
    assert rows[0]["conversion_value_czk"] == 46943.0
    assert rows[0]["pno_ratio"] == 0.00195
    assert rows[0]["pno_percent"] == 0.2


def test_normalize_generic_entity_keeps_decimal_conversion_value_in_czk() -> None:
    row = normalize_generic_entity(
        {
            "conversionValue": 46942.74,
            "clickMoney": 9166,
            "pno": 0.00195,
        },
        user_id=123,
    )

    assert row["conversionValue_raw"] == 46942.74
    assert row["conversionValue_czk"] == 46942.74
    assert row["conversion_value_raw"] == 46942.74
    assert row["conversion_value_czk"] == 46942.74
    assert row["cost_raw"] == 9166
    assert row["cost_czk"] == 91.66
    assert row["pno_ratio"] == 0.00195
    assert row["pno_percent"] == 0.2


def test_build_report_params_match_drak_v5_shape() -> None:
    create_params = build_report_create_params(
        date_from="2026-01-01",
        date_to="2026-01-31",
        stat_granularity="daily",
        restriction_filter_extra={"campaignStatus": ["running"]},
    )
    read_params = build_report_read_params(
        report_id="rep-1",
        offset=100,
        limit=5000,
        allow_empty_statistics=True,
        display_columns=["id", "name"],
    )

    assert create_params == [
        {
            "dateFrom": "2026-01-01",
            "dateTo": "2026-01-31",
            "campaignStatus": ["running"],
        },
        {
            "statGranularity": "daily",
            "includeCurrentDayStats": False,
        },
    ]
    assert read_params == [
        "rep-1",
        {
            "offset": 100,
            "limit": 5000,
            "allowEmptyStatistics": True,
            "displayColumns": ["id", "name"],
        },
    ]


def test_drak_413_falls_back_to_daily_windows() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.windows: list[tuple[str, str]] = []

        def call(self, method: str, params: Any, user_id: int | None = None):
            if method.endswith(".createReport"):
                restriction_filter = params[0]
                window = (restriction_filter["dateFrom"], restriction_filter["dateTo"])
                self.windows.append(window)
                if window == ("2026-01-01", "2026-01-10"):
                    raise SklikApiError("Too many items", status_code=413, recoverable=True)
                return {"status": 200, "reportId": f"{window[0]}_{window[1]}"}
            return {"status": 200, "report": [{"id": params[0], "stats": [{"date": params[0], "clicks": 1}]}]}

    client = FakeClient()
    rows = fetch_report_rows(
        client=client,  # type: ignore[arg-type]
        entity="campaigns",
        user_id=123,
        date_from="2026-01-01",
        date_to="2026-01-10",
        granularity="daily",
    )

    assert len(rows) == 10
    assert client.windows[0] == ("2026-01-01", "2026-01-10")
    assert client.windows[1] == ("2026-01-01", "2026-01-01")


def test_fenix_campaigns_refreshes_token_once_after_401(monkeypatch) -> None:
    client = SklikFenixClient(
        refresh_token="refresh",
        base_url="https://api.sklik.cz/v1",
        timeout=60,
        max_retries=1,
        user_agent="TestSklik/1.0",
    )

    token_calls = {"count": 0}
    request_calls = {"count": 0}
    captured: dict[str, Any] = {}

    def fake_refresh() -> str:
        token_calls["count"] += 1
        client.access_token = f"access-{token_calls['count']}"
        client.access_token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        return client.access_token

    def fake_request(method: str, url: str, params=None, json=None, headers=None, timeout=None):
        request_calls["count"] += 1
        captured.setdefault("urls", []).append(url)
        captured.setdefault("params", []).append(params)
        captured.setdefault("auth", []).append(headers.get("Authorization"))
        if request_calls["count"] == 1:
            return DummyResponse({"error": "expired"}, status_code=401)
        return DummyResponse({"items": [{"id": 1}]}, status_code=200)

    monkeypatch.setattr(client, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(client.http, "request", fake_request)
    client.access_token = "stale"
    client.access_token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    payload = client.list_nakupy_campaigns("987")

    assert payload == [{"id": 1}]
    assert token_calls["count"] == 1
    assert captured["urls"] == [
        "https://api.sklik.cz/v1/nakupy/campaigns",
        "https://api.sklik.cz/v1/nakupy/campaigns",
    ]
    assert captured["params"] == [{"premiseId": 987}, {"premiseId": 987}]
