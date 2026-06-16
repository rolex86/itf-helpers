from __future__ import annotations

from typing import Any

from app.integrations.linkedin.conversions import fetch_conversions_for_accounts


class DummyLinkedInClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def paginate_cursor(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        request_params = dict(params or {})
        self.calls.append(
            {
                "path": path,
                "params": request_params,
                "page_size": page_size,
                "extra_headers": dict(extra_headers or {}),
            }
        )

        if path == "conversions":
            return [
                {
                    "id": "urn:li:conversion:111",
                    "account": request_params.get("account"),
                    "name": "Lead conversion",
                }
            ]

        if path == "campaignConversions":
            return [
                {
                    "campaign": "urn:li:sponsoredCampaign:200",
                    "conversion": "urn:li:conversion:111",
                }
            ]

        if path == "insightTags":
            return [
                {
                    "id": "urn:li:insightTag:333",
                    "account": request_params.get("account"),
                    "name": "Insight Tag",
                }
            ]

        if path == "insightTagDomains":
            return [
                {
                    "domain": "example.cz",
                    "account": request_params.get("account"),
                }
            ]

        if path == "insightTagsPermission":
            return [
                {
                    "permission": "ALLOWED",
                    "account": request_params.get("account"),
                }
            ]

        return []


def test_fetch_conversions_for_accounts_collects_all_conversion_related_entities() -> None:
    client = DummyLinkedInClient()

    (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        warnings,
    ) = fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        campaign_ids_by_account={
            "123456": ["200"],
        },
    )

    assert warnings == []

    assert len(conversions) == 1
    assert conversions[0]["account_id"] == "123456"
    assert conversions[0]["account_urn"] == "urn:li:sponsoredAccount:123456"

    assert len(campaign_conversions) == 1
    assert campaign_conversions[0]["account_id"] == "123456"
    assert campaign_conversions[0]["campaign_id"] == "200"
    assert campaign_conversions[0]["campaign_urn"] == "urn:li:sponsoredCampaign:200"

    assert len(insight_tags) == 1
    assert insight_tags[0]["account_id"] == "123456"

    assert len(insight_tag_domains) == 1
    assert insight_tag_domains[0]["account_id"] == "123456"
    assert insight_tag_domains[0]["domain"] == "example.cz"

    assert len(insight_tags_permission) == 1
    assert insight_tags_permission[0]["account_id"] == "123456"
    assert insight_tags_permission[0]["permission"] == "ALLOWED"


def test_fetch_conversions_uses_expected_linkedin_finders_and_params() -> None:
    client = DummyLinkedInClient()

    fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        campaign_ids_by_account={
            "123456": ["200", "201"],
        },
    )

    calls_by_path = {call["path"]: call for call in client.calls}

    conversions_call = calls_by_path["conversions"]
    assert conversions_call["params"]["q"] == "account"
    assert conversions_call["params"]["account"] == "urn:li:sponsoredAccount:123456"
    assert conversions_call["page_size"] == 100

    campaign_conversions_call = calls_by_path["campaignConversions"]
    assert campaign_conversions_call["params"]["q"] == "campaigns"
    assert campaign_conversions_call["params"]["campaigns"] == (
        "List(urn:li:sponsoredCampaign:200,urn:li:sponsoredCampaign:201)"
    )
    assert campaign_conversions_call["page_size"] == 100

    insight_tags_call = calls_by_path["insightTags"]
    assert insight_tags_call["params"]["q"] == "account"
    assert insight_tags_call["params"]["account"] == "urn:li:sponsoredAccount:123456"

    insight_tag_domains_call = calls_by_path["insightTagDomains"]
    assert insight_tag_domains_call["params"]["q"] == "account"
    assert insight_tag_domains_call["params"]["account"] == "urn:li:sponsoredAccount:123456"

    insight_tags_permission_call = calls_by_path["insightTagsPermission"]
    assert insight_tags_permission_call["params"]["q"] == "account"
    assert insight_tags_permission_call["params"]["account"] == "urn:li:sponsoredAccount:123456"


def test_fetch_conversions_dedupes_account_ids_and_campaign_ids() -> None:
    client = DummyLinkedInClient()

    fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["123456", "123456", ""],
        campaign_ids_by_account={
            "123456": ["200", "200", "", "201"],
        },
    )

    conversion_calls = [call for call in client.calls if call["path"] == "conversions"]
    campaign_conversion_calls = [call for call in client.calls if call["path"] == "campaignConversions"]

    assert len(conversion_calls) == 1
    assert len(campaign_conversion_calls) == 1
    assert campaign_conversion_calls[0]["params"]["campaigns"] == (
        "List(urn:li:sponsoredCampaign:200,urn:li:sponsoredCampaign:201)"
    )


def test_fetch_conversions_batches_campaign_conversion_requests() -> None:
    client = DummyLinkedInClient()
    campaign_ids = [str(index) for index in range(1, 106)]

    fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        campaign_ids_by_account={
            "123456": campaign_ids,
        },
    )

    campaign_conversion_calls = [call for call in client.calls if call["path"] == "campaignConversions"]

    assert len(campaign_conversion_calls) == 3

    first_batch = campaign_conversion_calls[0]["params"]["campaigns"]
    second_batch = campaign_conversion_calls[1]["params"]["campaigns"]
    third_batch = campaign_conversion_calls[2]["params"]["campaigns"]

    assert "urn:li:sponsoredCampaign:1" in first_batch
    assert "urn:li:sponsoredCampaign:50" in first_batch
    assert "urn:li:sponsoredCampaign:51" in second_batch
    assert "urn:li:sponsoredCampaign:100" in second_batch
    assert "urn:li:sponsoredCampaign:101" in third_batch
    assert "urn:li:sponsoredCampaign:105" in third_batch


def test_fetch_conversions_warns_when_campaign_ids_are_missing() -> None:
    client = DummyLinkedInClient()

    (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        warnings,
    ) = fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        campaign_ids_by_account={},
    )

    assert len(conversions) == 1
    assert campaign_conversions == []
    assert len(insight_tags) == 1
    assert len(insight_tag_domains) == 1
    assert len(insight_tags_permission) == 1

    assert len(warnings) == 1
    assert "Campaign conversion associations pro account 123456 nebyly načteny" in warnings[0]


def test_fetch_conversions_collects_warnings_and_continues_on_endpoint_errors() -> None:
    class PartiallyFailingClient(DummyLinkedInClient):
        def paginate_cursor(
            self,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            page_size: int = 100,
            extra_headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            if path in {"conversions", "campaignConversions", "insightTagsPermission"}:
                raise RuntimeError(f"{path} failed")

            return super().paginate_cursor(
                path,
                params=params,
                page_size=page_size,
                extra_headers=extra_headers,
            )

    client = PartiallyFailingClient()

    (
        conversions,
        campaign_conversions,
        insight_tags,
        insight_tag_domains,
        insight_tags_permission,
        warnings,
    ) = fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["123456"],
        campaign_ids_by_account={
            "123456": ["200"],
        },
    )

    assert conversions == []
    assert campaign_conversions == []
    assert insight_tags
    assert insight_tag_domains
    assert insight_tags_permission == []

    assert len(warnings) == 3
    assert any("Conversions pro account 123456 nebylo možné načíst" in warning for warning in warnings)
    assert any("Campaign conversion associations pro account 123456 nebylo možné načíst" in warning for warning in warnings)
    assert any("Insight tags permission pro account 123456 nebylo možné načíst" in warning for warning in warnings)


def test_fetch_conversions_accepts_account_urn_and_act_prefix() -> None:
    client = DummyLinkedInClient()

    fetch_conversions_for_accounts(
        client,  # type: ignore[arg-type]
        account_ids=["act_123456", "urn:li:sponsoredAccount:789"],
        campaign_ids_by_account={
            "act_123456": ["200"],
            "urn:li:sponsoredAccount:789": ["300"],
        },
    )

    conversion_calls = [call for call in client.calls if call["path"] == "conversions"]

    assert conversion_calls[0]["params"]["account"] == "urn:li:sponsoredAccount:123456"
    assert conversion_calls[1]["params"]["account"] == "urn:li:sponsoredAccount:789"