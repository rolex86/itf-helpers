from __future__ import annotations

from datetime import date

from app.integrations.linkedin.restli import (
    campaign_urn,
    clean_linkedin_id,
    creative_urn,
    date_range_param,
    encode_urn,
    lead_form_owner_param,
    lead_form_urn,
    lead_type_param,
    organization_urn,
    owner_param_for_organization,
    owner_param_for_sponsored_account,
    restli_list,
    sponsored_account_urn,
    versioned_lead_form_urn,
)


def test_clean_linkedin_id_from_plain_id() -> None:
    assert clean_linkedin_id("123456") == "123456"


def test_clean_linkedin_id_from_act_prefix() -> None:
    assert clean_linkedin_id("act_123456") == "123456"


def test_clean_linkedin_id_from_urn() -> None:
    assert clean_linkedin_id("urn:li:sponsoredAccount:123456") == "123456"


def test_clean_linkedin_id_from_versioned_urn() -> None:
    assert clean_linkedin_id("urn:li:leadGenForm:123456:7") == "7"


def test_encode_urn() -> None:
    assert encode_urn("urn:li:sponsoredAccount:123456") == "urn%3Ali%3AsponsoredAccount%3A123456"


def test_restli_list_encodes_values() -> None:
    assert restli_list(["a", "b"]) == "List(a,b)"


def test_restli_list_dedupes_and_skips_empty_values() -> None:
    assert restli_list(["a", "", "b", "a", "  ", "c"]) == "List(a,b,c)"


def test_sponsored_account_urn() -> None:
    assert sponsored_account_urn("123456") == "urn:li:sponsoredAccount:123456"


def test_sponsored_account_urn_from_act_prefix() -> None:
    assert sponsored_account_urn("act_123456") == "urn:li:sponsoredAccount:123456"


def test_sponsored_account_urn_from_urn() -> None:
    assert sponsored_account_urn("urn:li:sponsoredAccount:123456") == "urn:li:sponsoredAccount:123456"


def test_organization_urn() -> None:
    assert organization_urn("987") == "urn:li:organization:987"


def test_campaign_urn() -> None:
    assert campaign_urn("321") == "urn:li:sponsoredCampaign:321"


def test_creative_urn() -> None:
    assert creative_urn("654") == "urn:li:sponsoredCreative:654"


def test_lead_form_urn() -> None:
    assert lead_form_urn("777") == "urn:li:leadGenForm:777"


def test_versioned_lead_form_urn() -> None:
    assert versioned_lead_form_urn("777", 3) == "urn:li:leadGenForm:777:3"


def test_versioned_lead_form_urn_without_version_returns_base_urn() -> None:
    assert versioned_lead_form_urn("777", "") == "urn:li:leadGenForm:777"


def test_date_range_param() -> None:
    value = date_range_param(date(2026, 1, 1), date(2026, 1, 31))
    assert "year:2026" in value
    assert "month:1" in value
    assert "day:31" in value


def test_lead_form_owner_param_for_sponsored_account() -> None:
    assert (
        lead_form_owner_param("urn:li:sponsoredAccount:123456")
        == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    )


def test_lead_form_owner_param_for_organization() -> None:
    assert (
        lead_form_owner_param("urn:li:organization:987")
        == "(organization:urn:li:organization:987)"
    )


def test_lead_form_owner_param_is_idempotent() -> None:
    assert (
        lead_form_owner_param("(sponsoredAccount:urn:li:sponsoredAccount:123456)")
        == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    )


def test_owner_param_for_sponsored_account() -> None:
    assert (
        owner_param_for_sponsored_account("123456")
        == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    )


def test_owner_param_for_organization() -> None:
    assert owner_param_for_organization("987") == "(organization:urn:li:organization:987)"


def test_lead_type_param_defaults_to_sponsored() -> None:
    assert lead_type_param() == "(leadType:SPONSORED)"


def test_lead_type_param_normalizes_value() -> None:
    assert lead_type_param("organic") == "(leadType:ORGANIC)"