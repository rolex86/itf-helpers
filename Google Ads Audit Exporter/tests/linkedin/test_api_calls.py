from datetime import date

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.conversions import fetch_conversions_for_accounts
from app.integrations.linkedin.discovery import run_linkedin_discovery
from app.integrations.linkedin.lead_sync import fetch_lead_forms, fetch_lead_responses
from app.integrations.linkedin.models import LinkedInConnection, LinkedInRuntimeConfig
from app.integrations.linkedin.reporting import LinkedInDateRange, build_reporting_exports


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def paginate_cursor(self, path, *, params=None, page_size=100, extra_headers=None, expected_status=(200,)):
        self.calls.append(
            {
                "method": "paginate_cursor",
                "path": path,
                "params": dict(params or {}),
                "page_size": page_size,
                "extra_headers": dict(extra_headers or {}),
            }
        )
        if path == "adAccounts":
            return iter([{"id": "123", "name": "Account", "account": "urn:li:sponsoredAccount:123"}])
        return iter([])

    def paginate_start_count(self, path, *, params=None, count=100, extra_headers=None, expected_status=(200,)):
        self.calls.append(
            {
                "method": "paginate_start_count",
                "path": path,
                "params": dict(params or {}),
                "count": count,
                "extra_headers": dict(extra_headers or {}),
            }
        )
        return iter([])

    def get(self, path, *, params=None, expected_status=(200,), extra_headers=None):
        self.calls.append(
            {
                "method": "get",
                "path": path,
                "params": dict(params or {}),
                "extra_headers": dict(extra_headers or {}),
            }
        )
        return {"elements": []}


def test_client_finder_adds_restli_header(monkeypatch) -> None:
    connection = LinkedInConnection(key="main", label="Main")
    runtime = LinkedInRuntimeConfig()
    client = LinkedInRestClient(connection=connection, runtime_config=runtime, access_token="token")
    captured = {}

    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    client.finder("adAccounts/123/creatives", "criteria", params={"foo": "bar"})
    assert captured["method"] == "GET"
    assert captured["path"] == "adAccounts/123/creatives"
    assert captured["kwargs"]["params"]["q"] == "criteria"
    assert captured["kwargs"]["extra_headers"]["X-RestLi-Method"] == "FINDER"


def test_discovery_uses_expected_endpoint_shapes(monkeypatch) -> None:
    client = RecordingClient()
    monkeypatch.setattr("app.integrations.linkedin.discovery.fetch_organizations", lambda client: [])
    monkeypatch.setattr(
        "app.integrations.linkedin.discovery.fetch_conversions_for_accounts",
        lambda client, account_ids, campaign_ids_by_account=None: ([], [], [], [], [], []),
    )
    monkeypatch.setattr("app.integrations.linkedin.discovery.fetch_lead_forms", lambda client, owner_urns: ([], [], []))

    run_linkedin_discovery(connection_key="main", client=client)

    assert any(call["path"] == "adAccounts" and call["params"] == {"q": "search"} for call in client.calls)
    assert any(call["path"] == "adAccountUsers" and call["params"] == {"q": "authenticatedUser"} for call in client.calls)
    assert any(call["path"] == "adAccountUsers" and call["params"] == {"q": "accounts", "accounts": "urn:li:sponsoredAccount:123"} for call in client.calls)
    assert any(call["path"] == "adAccounts/123/adCampaignGroups" for call in client.calls)
    assert any(call["path"] == "adAccounts/123/adCampaigns" for call in client.calls)
    assert any(call["path"] == "adAccounts/123/creatives" and call["params"] == {"q": "criteria"} and call["extra_headers"].get("X-RestLi-Method") == "FINDER" for call in client.calls)


def test_conversions_use_expected_params() -> None:
    client = RecordingClient()
    fetch_conversions_for_accounts(client, account_ids=["123"], campaign_ids_by_account={"123": ["777"]})
    assert any(call["path"] == "conversions" and call["params"] == {"q": "account", "account": "urn:li:sponsoredAccount:123"} for call in client.calls)
    assert any(call["path"] == "campaignConversions" and call["params"]["q"] == "campaigns" and "urn:li:sponsoredCampaign:777" in call["params"]["campaigns"] for call in client.calls)
    assert any(call["path"] == "insightTagDomains" and call["params"] == {"q": "account", "account": "urn:li:sponsoredAccount:123"} for call in client.calls)


def test_lead_forms_use_owner_wrapper() -> None:
    client = RecordingClient()
    fetch_lead_forms(client, owner_urns=["urn:li:sponsoredAccount:123"])
    assert any(call["path"] == "leadForms" and call["params"]["q"] == "owner" and call["params"]["owner"] == "(sponsoredAccount:urn:li:sponsoredAccount:123)" for call in client.calls)


def test_lead_form_responses_use_owner_and_lead_type() -> None:
    client = RecordingClient()
    fetch_lead_responses(
        client,
        forms=[
            {
                "owner_urn": "urn:li:organization:555",
                "versionedLeadGenFormUrn": "urn:li:leadGenForm:999:1",
            }
        ],
        limited_to_test_leads=True,
    )
    assert any(
        call["path"] == "leadFormResponses"
        and call["params"]["q"] == "owner"
        and call["params"]["owner"] == "(organization:urn:li:organization:555)"
        and call["params"]["leadType"] == "(leadType:SPONSORED)"
        and call["params"]["versionedLeadGenFormUrn"] == "urn:li:leadGenForm:999:1"
        for call in client.calls
    )


def test_reporting_calls_adanalytics_with_get() -> None:
    client = RecordingClient()
    build_reporting_exports(
        client,
        account_ids=["123"],
        date_range=LinkedInDateRange(start=date(2026, 1, 1), end=date(2026, 1, 10)),
    )
    analytics_calls = [call for call in client.calls if call["path"] == "adAnalytics"]
    assert analytics_calls
    assert all(call["method"] == "get" for call in analytics_calls)
