from __future__ import annotations

from typing import Any

import pytest

from app.integrations.linkedin import discovery


class DummyLinkedInClient:
    def __init__(self) -> None:
        self.cursor_calls: list[dict[str, Any]] = []

    def paginate_cursor(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        self.cursor_calls.append(
            {
                "path": path,
                "params": dict(params or {}),
                "page_size": page_size,
                "extra_headers": dict(extra_headers or {}),
            }
        )

        if path == "adAccounts":
            return [
                {
                    "id": "urn:li:sponsoredAccount:123456",
                    "account": "urn:li:sponsoredAccount:123456",
                    "name": "Test account",
                }
            ]

        if path == "adAccountUsers" and params and params.get("q") == "authenticatedUser":
            return [
                {
                    "account": "urn:li:sponsoredAccount:123456",
                    "role": "ACCOUNT_BILLING_ADMIN",
                }
            ]

        if path == "adAccountUsers" and params and params.get("q") == "accounts":
            return [
                {
                    "account": "urn:li:sponsoredAccount:123456",
                    "role": "ACCOUNT_MANAGER",
                }
            ]

        if path == "adAccounts/123456/adCampaignGroups":
            return [
                {
                    "id": "urn:li:sponsoredCampaignGroup:100",
                    "campaign_group_id": "100",
                    "name": "Campaign group",
                    "status": "ACTIVE",
                }
            ]

        if path == "adAccounts/123456/adCampaigns":
            return [
                {
                    "id": "urn:li:sponsoredCampaign:200",
                    "campaign_id": "200",
                    "name": "Campaign",
                    "status": "ACTIVE",
                }
            ]

        if path == "adAccounts/123456/creatives":
            return [
                {
                    "id": "urn:li:sponsoredCreative:300",
                    "creative_id": "300",
                    "name": "Creative",
                }
            ]

        return []


def test_run_linkedin_discovery_collects_core_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyLinkedInClient()

    monkeypatch.setattr(
        discovery,
        "fetch_organizations",
        lambda client: [
            {
                "organization": "urn:li:organization:987",
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
            }
        ],
    )
    monkeypatch.setattr(
        discovery,
        "fetch_conversions_for_accounts",
        lambda client, account_ids, campaign_ids_by_account: (
            [{"id": "conversion-1", "account_id": account_ids[0]}],
            [{"campaign_id": campaign_ids_by_account[account_ids[0]][0], "conversion_id": "conversion-1"}],
            [{"id": "insight-tag-1", "account_id": account_ids[0]}],
            [{"domain": "example.cz", "account_id": account_ids[0]}],
            [{"account_id": account_ids[0], "permission": "ok"}],
            [],
        ),
    )
    monkeypatch.setattr(
        discovery,
        "fetch_lead_forms",
        lambda client, owner_urns: (
            [{"id": "lead-form-1", "owner_urns": owner_urns}],
            [{"lead_form_id": "lead-form-1", "question": "Email"}],
            [],
        ),
    )

    snapshot = discovery.run_linkedin_discovery(
        connection_key="linkedin-main",
        client=client,  # type: ignore[arg-type]
    )

    assert snapshot.connection_key == "linkedin-main"
    assert snapshot.status == "success"
    assert snapshot.warnings == []

    assert len(snapshot.ad_accounts) == 1
    assert snapshot.ad_accounts[0]["account_id"] == "123456"

    assert len(snapshot.ad_account_users) == 1
    assert len(snapshot.ad_account_roles) == 1
    assert len(snapshot.campaign_groups) == 1
    assert len(snapshot.campaigns) == 1
    assert len(snapshot.creatives) == 1
    assert len(snapshot.creative_content) == 1
    assert len(snapshot.organizations) == 1
    assert len(snapshot.organization_roles) == 1
    assert len(snapshot.conversions) == 1
    assert len(snapshot.campaign_conversions) == 1
    assert len(snapshot.insight_tags) == 1
    assert len(snapshot.insight_tag_domains) == 1
    assert len(snapshot.lead_forms) == 1
    assert len(snapshot.lead_form_questions) == 1

    assert snapshot.raw_snapshots["ad_accounts"]
    assert snapshot.raw_snapshots["campaigns_123456"]
    assert snapshot.raw_snapshots["creatives_123456"]


def test_discovery_uses_expected_linkedin_finders(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyLinkedInClient()

    monkeypatch.setattr(discovery, "fetch_organizations", lambda client: [])
    monkeypatch.setattr(
        discovery,
        "fetch_conversions_for_accounts",
        lambda client, account_ids, campaign_ids_by_account: ([], [], [], [], [], []),
    )
    monkeypatch.setattr(discovery, "fetch_lead_forms", lambda client, owner_urns: ([], [], []))

    discovery.run_linkedin_discovery(
        connection_key="linkedin-main",
        client=client,  # type: ignore[arg-type]
    )

    calls_by_path = {call["path"]: call for call in client.cursor_calls}

    assert calls_by_path["adAccounts"]["params"]["q"] == "search"

    authenticated_user_call = [
        call
        for call in client.cursor_calls
        if call["path"] == "adAccountUsers" and call["params"].get("q") == "authenticatedUser"
    ][0]
    assert authenticated_user_call["page_size"] == 100

    account_roles_call = [
        call
        for call in client.cursor_calls
        if call["path"] == "adAccountUsers" and call["params"].get("q") == "accounts"
    ][0]
    assert account_roles_call["params"]["accounts"] == "urn:li:sponsoredAccount:123456"

    campaign_group_call = calls_by_path["adAccounts/123456/adCampaignGroups"]
    assert campaign_group_call["params"]["q"] == "search"
    assert "search" in campaign_group_call["params"]
    assert campaign_group_call["params"]["sortOrder"] == "DESCENDING"

    campaign_call = calls_by_path["adAccounts/123456/adCampaigns"]
    assert campaign_call["params"]["q"] == "search"
    assert "search" in campaign_call["params"]
    assert campaign_call["params"]["sortOrder"] == "DESCENDING"

    creative_call = calls_by_path["adAccounts/123456/creatives"]
    assert creative_call["params"]["q"] == "criteria"
    assert creative_call["extra_headers"]["X-RestLi-Method"] == "FINDER"


def test_discovery_passes_campaign_ids_to_conversions(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyLinkedInClient()
    captured: dict[str, Any] = {}

    def fake_fetch_conversions_for_accounts(
        client: Any,
        *,
        account_ids: list[str],
        campaign_ids_by_account: dict[str, list[str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        captured["account_ids"] = account_ids
        captured["campaign_ids_by_account"] = campaign_ids_by_account
        return [], [], [], [], [], []

    monkeypatch.setattr(discovery, "fetch_organizations", lambda client: [])
    monkeypatch.setattr(discovery, "fetch_conversions_for_accounts", fake_fetch_conversions_for_accounts)
    monkeypatch.setattr(discovery, "fetch_lead_forms", lambda client, owner_urns: ([], [], []))

    discovery.run_linkedin_discovery(
        connection_key="linkedin-main",
        client=client,  # type: ignore[arg-type]
    )

    assert captured["account_ids"] == ["123456"]
    assert captured["campaign_ids_by_account"] == {"123456": ["200"]}


def test_discovery_builds_lead_form_owner_params(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyLinkedInClient()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        discovery,
        "fetch_organizations",
        lambda client: [
            {
                "organization": "urn:li:organization:987",
                "organization_id": "987",
            }
        ],
    )
    monkeypatch.setattr(
        discovery,
        "fetch_conversions_for_accounts",
        lambda client, account_ids, campaign_ids_by_account: ([], [], [], [], [], []),
    )

    def fake_fetch_lead_forms(
        client: Any,
        *,
        owner_urns: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        captured["owner_urns"] = owner_urns
        return [], [], []

    monkeypatch.setattr(discovery, "fetch_lead_forms", fake_fetch_lead_forms)

    discovery.run_linkedin_discovery(
        connection_key="linkedin-main",
        client=client,  # type: ignore[arg-type]
    )

    assert "(sponsoredAccount:urn:li:sponsoredAccount:123456)" in captured["owner_urns"]
    assert "(organization:urn:li:organization:987)" in captured["owner_urns"]


def test_safe_collect_turns_endpoint_error_into_partial_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingClient(DummyLinkedInClient):
        def paginate_cursor(
            self,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            page_size: int = 100,
            extra_headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            if path == "adAccounts":
                raise RuntimeError("LinkedIn API error")
            return super().paginate_cursor(
                path,
                params=params,
                page_size=page_size,
                extra_headers=extra_headers,
            )

    monkeypatch.setattr(discovery, "fetch_organizations", lambda client: [])
    monkeypatch.setattr(
        discovery,
        "fetch_conversions_for_accounts",
        lambda client, account_ids, campaign_ids_by_account: ([], [], [], [], [], []),
    )
    monkeypatch.setattr(discovery, "fetch_lead_forms", lambda client, owner_urns: ([], [], []))

    snapshot = discovery.run_linkedin_discovery(
        connection_key="linkedin-main",
        client=FailingClient(),  # type: ignore[arg-type]
    )

    assert snapshot.status == "partial"
    assert snapshot.ad_accounts == []
    assert snapshot.raw_snapshots["ad_accounts"] == []
    assert snapshot.warnings
    assert "ad_accounts discovery warning" in snapshot.warnings[0]