from __future__ import annotations

from typing import Any

from app.integrations.linkedin.reporting import LinkedInDateRange, build_reporting_exports, resolve_date_range


class DummyLinkedInClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, *, params: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        request_params = dict(params or {})
        self.calls.append({"path": path, "params": request_params})

        if request_params.get("q") == "analytics":
            return {
                "elements": [
                    {
                        "dateRange": {
                            "start": {"year": 2026, "month": 1, "day": 1},
                            "end": {"year": 2026, "month": 1, "day": 31},
                        },
                        "pivotValues": ["urn:li:sponsoredCampaign:123"],
                        "impressions": 1000,
                        "clicks": 50,
                        "costInLocalCurrency": 1000,
                        "externalWebsiteConversions": 2,
                        "oneClickLeads": 1,
                        "totalEngagements": 20,
                    }
                ]
            }

        if request_params.get("q") == "statistics":
            return {
                "elements": [
                    {
                        "dateRange": {
                            "start": {"year": 2026, "month": 1, "day": 1},
                            "end": {"year": 2026, "month": 1, "day": 31},
                        },
                        "pivotValues": ["urn:li:sponsoredCampaign:123", "urn:li:seniority:5"],
                        "impressions": 100,
                        "clicks": 5,
                        "totalEngagements": 2,
                    }
                ]
            }

        return {"elements": []}


def test_resolve_date_range_from_preset() -> None:
    result = resolve_date_range(preset="last_30_days")
    assert result.start <= result.end


def test_resolve_date_range_from_custom_values() -> None:
    result = resolve_date_range(date_from="2026-01-01", date_to="2026-01-31")
    assert result.start.isoformat() == "2026-01-01"
    assert result.end.isoformat() == "2026-01-31"


def test_ad_analytics_uses_accounts_list_and_explicit_fields() -> None:
    client = DummyLinkedInClient()
    date_range = resolve_date_range(date_from="2026-01-01", date_to="2026-01-31")

    datasets, raw_payloads, warnings = build_reporting_exports(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        date_range=date_range,
    )

    analytics_calls = [
        call for call in client.calls if call["path"] == "adAnalytics" and call["params"].get("q") == "analytics"
    ]

    assert analytics_calls
    assert warnings == []
    assert "insights_campaign_daily" in datasets
    assert "insights_campaign_daily_raw" in raw_payloads

    first_call_params = analytics_calls[0]["params"]
    assert first_call_params["accounts"] == "List(urn:li:sponsoredAccount:123456)"
    assert first_call_params["q"] == "analytics"
    assert first_call_params["pivot"] in {"ACCOUNT", "CAMPAIGN", "CREATIVE"}
    assert first_call_params["timeGranularity"] in {"DAILY", "ALL"}
    assert "fields" in first_call_params
    assert "impressions" in first_call_params["fields"]
    assert "clicks" in first_call_params["fields"]
    assert "costInLocalCurrency" in first_call_params["fields"]
    assert "externalWebsiteConversions" in first_call_params["fields"]
    assert "oneClickLeads" in first_call_params["fields"]
    assert "dateRange" in first_call_params


def test_professional_demographics_use_statistics_and_member_pivots() -> None:
    client = DummyLinkedInClient()
    date_range = LinkedInDateRange(
        start=resolve_date_range(date_from="2026-01-01", date_to="2026-01-31").start,
        end=resolve_date_range(date_from="2026-01-01", date_to="2026-01-31").end,
    )

    datasets, raw_payloads, warnings = build_reporting_exports(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        date_range=date_range,
    )

    statistics_calls = [
        call for call in client.calls if call["path"] == "adAnalytics" and call["params"].get("q") == "statistics"
    ]

    assert statistics_calls
    assert warnings == []
    assert "professional_demographics_account" in datasets
    assert "professional_demographics_campaign" in datasets
    assert "professional_demographics_creative" in datasets
    assert "professional_demographics_campaign_raw" in raw_payloads

    first_call_params = statistics_calls[0]["params"]
    assert first_call_params["accounts"] == "List(urn:li:sponsoredAccount:123456)"
    assert first_call_params["q"] == "statistics"
    assert first_call_params["timeGranularity"] == "ALL"
    assert first_call_params["pivots"].startswith("List(")
    assert "MEMBER_" in first_call_params["pivots"]
    assert "fields" in first_call_params
    assert "impressions" in first_call_params["fields"]
    assert "clicks" in first_call_params["fields"]