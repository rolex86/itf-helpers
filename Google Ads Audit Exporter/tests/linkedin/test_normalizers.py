from __future__ import annotations

import pandas as pd

from app.integrations.linkedin.normalizers import (
    iso_from_epoch_millis,
    json_text,
    normalize_entity_identifiers,
    records_to_frame,
    sanitize_pii_for_report,
    urn_to_id,
)


def test_urn_to_id_from_numeric_urn() -> None:
    assert urn_to_id("urn:li:sponsoredCampaign:987654321") == "987654321"


def test_urn_to_id_from_act_prefix() -> None:
    assert urn_to_id("act_123456") == "123456"


def test_urn_to_id_from_versioned_urn_returns_base_entity_id() -> None:
    assert urn_to_id("urn:li:leadGenForm:123456:7") == "123456"


def test_urn_to_id_from_non_numeric_urn() -> None:
    assert urn_to_id("urn:li:seniority:senior") == "senior"


def test_urn_to_id_returns_plain_value_when_not_urn() -> None:
    assert urn_to_id("plain-id") == "plain-id"


def test_urn_to_id_handles_empty_values() -> None:
    assert urn_to_id("") == ""
    assert urn_to_id(None) == ""


def test_records_to_frame_returns_dataframe_for_records() -> None:
    frame = records_to_frame(
        [
            {
                "id": "1",
                "name": "Test",
            }
        ]
    )

    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 1
    assert frame.iloc[0]["id"] == "1"
    assert frame.iloc[0]["name"] == "Test"


def test_records_to_frame_returns_empty_dataframe_for_empty_records() -> None:
    frame = records_to_frame([])

    assert isinstance(frame, pd.DataFrame)
    assert frame.empty


def test_iso_from_epoch_millis() -> None:
    result = iso_from_epoch_millis(1760000000000)

    assert result.startswith("2025-10-09T")
    assert result.endswith("+00:00")


def test_iso_from_epoch_millis_handles_invalid_values() -> None:
    assert iso_from_epoch_millis("") == ""
    assert iso_from_epoch_millis(None) == ""
    assert iso_from_epoch_millis("not-a-number") == ""


def test_normalize_entity_identifiers_from_campaign_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "campaign": "urn:li:sponsoredCampaign:123",
        }
    )

    assert row["campaign_id"] == "123"
    assert row["campaign_urn"] == "urn:li:sponsoredCampaign:123"


def test_normalize_entity_identifiers_from_account_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "account": "urn:li:sponsoredAccount:123456",
        }
    )

    assert row["account_id"] == "123456"
    assert row["account_urn"] == "urn:li:sponsoredAccount:123456"


def test_normalize_entity_identifiers_from_creative_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "creative": "urn:li:sponsoredCreative:300",
        }
    )

    assert row["creative_id"] == "300"
    assert row["creative_urn"] == "urn:li:sponsoredCreative:300"


def test_normalize_entity_identifiers_from_organization_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "organization": "urn:li:organization:987",
        }
    )

    assert row["organization_id"] == "987"
    assert row["organization_urn"] == "urn:li:organization:987"


def test_normalize_entity_identifiers_from_lead_form_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "leadGenForm": "urn:li:leadGenForm:111",
        }
    )

    assert row["lead_form_id"] == "111"
    assert row["lead_form_urn"] == "urn:li:leadGenForm:111"


def test_normalize_entity_identifiers_from_versioned_lead_form_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "versionedLeadGenFormUrn": "urn:li:leadGenForm:111:2",
        }
    )

    assert row["lead_form_id"] == "111"
    assert row["lead_form_urn"] == "urn:li:leadGenForm:111:2"


def test_normalize_entity_identifiers_from_campaign_group_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "campaignGroup": "urn:li:sponsoredCampaignGroup:555",
        }
    )

    assert row["campaign_group_id"] == "555"
    assert row["campaign_group_urn"] == "urn:li:sponsoredCampaignGroup:555"


def test_normalize_entity_identifiers_detects_entity_type_from_id_urn() -> None:
    row = normalize_entity_identifiers(
        {
            "id": "urn:li:sponsoredCreative:300",
        }
    )

    assert row["creative_id"] == "300"
    assert row["creative_urn"] == "urn:li:sponsoredCreative:300"


def test_normalize_entity_identifiers_adds_generic_id_for_urn_string_values() -> None:
    row = normalize_entity_identifiers(
        {
            "owner": "urn:li:sponsoredAccount:123456",
        }
    )

    assert row["owner_id"] == "123456"


def test_normalize_entity_identifiers_does_not_overwrite_existing_ids() -> None:
    row = normalize_entity_identifiers(
        {
            "campaign": "urn:li:sponsoredCampaign:123",
            "campaign_id": "already-set",
            "campaign_urn": "already-set-urn",
        }
    )

    assert row["campaign_id"] == "already-set"
    assert row["campaign_urn"] == "already-set-urn"


def test_sanitize_pii_for_report_redacts_sensitive_keys_recursively() -> None:
    sanitized = sanitize_pii_for_report(
        {
            "email": "test@example.cz",
            "phone": "+420 123 456 789",
            "first_name": "Jan",
            "lastName": "Novák",
            "company": "Example s.r.o.",
            "job-title": "CEO",
            "safe_metric": 123,
            "nested": {
                "mobile": "+420 987 654 321",
                "utm_campaign": "leadgen",
            },
            "items": [
                {
                    "full_name": "Jan Novák",
                    "campaign_id": "200",
                }
            ],
        }
    )

    assert sanitized["email"] == "***"
    assert sanitized["phone"] == "***"
    assert sanitized["first_name"] == "***"
    assert sanitized["lastName"] == "***"
    assert sanitized["company"] == "***"
    assert sanitized["job-title"] == "***"
    assert sanitized["safe_metric"] == 123
    assert sanitized["nested"]["mobile"] == "***"
    assert sanitized["nested"]["utm_campaign"] == "leadgen"
    assert sanitized["items"][0]["full_name"] == "***"
    assert sanitized["items"][0]["campaign_id"] == "200"


def test_json_text_serializes_dicts_and_lists() -> None:
    assert json_text({"b": 2, "a": 1}) == '{"a": 1, "b": 2}'
    assert json_text(["a", "b"]) == '["a", "b"]'


def test_json_text_returns_empty_string_for_empty_values() -> None:
    assert json_text(None) == ""
    assert json_text("") == ""
    assert json_text([]) == ""
    assert json_text({}) == ""


def test_json_text_falls_back_to_str_for_non_json_serializable_values() -> None:
    assert "object" in json_text(object())