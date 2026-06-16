from __future__ import annotations

import pandas as pd

from app.integrations.linkedin.gtm_crosscheck import build_gtm_crosscheck, parse_linkedin_tags_from_gtm


def test_parse_linkedin_tags_from_gtm_detects_insight_tag_partner_id() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "LinkedIn Insight Tag",
                "type": "html",
                "parameter_json": "{\"customHtml\":\"window._linkedin_partner_id='123456';\"}",
                "notes": "",
                "firing_trigger_ids": "10",
                "consent_settings": "ad_storage,ad_user_data,ad_personalization",
            }
        ]
    )

    parsed = parse_linkedin_tags_from_gtm(df)

    assert len(parsed) == 1
    assert parsed.iloc[0]["tag_id"] == "1"
    assert parsed.iloc[0]["tag_name"] == "LinkedIn Insight Tag"
    assert parsed.iloc[0]["tag_type"] == "html"
    assert parsed.iloc[0]["partner_id"] == "123456"
    assert parsed.iloc[0]["all_partner_ids"] == "123456"
    assert parsed.iloc[0]["conversion_id"] == ""
    assert parsed.iloc[0]["trigger_name"] == "10"
    assert parsed.iloc[0]["has_consent"] is True
    assert parsed.iloc[0]["is_insight_tag"] is True
    assert parsed.iloc[0]["is_conversion_tag"] is False


def test_parse_linkedin_tags_from_gtm_detects_conversion_id_from_lintrk() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "2",
                "name": "LinkedIn conversion",
                "type": "html",
                "parameter_json": "",
                "notes": "window.lintrk('track', { conversion_id: 987654 });",
                "firing_trigger_ids": "20",
                "consent_settings": "ad_storage",
            }
        ]
    )

    parsed = parse_linkedin_tags_from_gtm(df)

    assert len(parsed) == 1
    assert parsed.iloc[0]["partner_id"] == ""
    assert parsed.iloc[0]["conversion_id"] == "987654"
    assert parsed.iloc[0]["all_conversion_ids"] == "987654"
    assert parsed.iloc[0]["is_insight_tag"] is False
    assert parsed.iloc[0]["is_conversion_tag"] is True
    assert parsed.iloc[0]["has_consent"] is True


def test_parse_linkedin_tags_from_gtm_detects_multiple_partner_and_conversion_ids() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "3",
                "name": "LinkedIn combined tag",
                "type": "html",
                "parameter_json": "",
                "notes": """
                    window._linkedin_partner_id = "123456";
                    var partnerId = "654321";
                    window.lintrk('track', { conversion_id: 111222 });
                    window.lintrk('track', { conversionId: 333444 });
                """,
                "firing_trigger_ids": "30",
                "consent_settings": "ad_storage",
            }
        ]
    )

    parsed = parse_linkedin_tags_from_gtm(df)

    assert len(parsed) == 1
    assert parsed.iloc[0]["partner_id"] == "123456"
    assert parsed.iloc[0]["conversion_id"] == "111222"
    assert parsed.iloc[0]["all_partner_ids"] == "123456,654321"
    assert parsed.iloc[0]["all_conversion_ids"] == "111222,333444"
    assert parsed.iloc[0]["is_insight_tag"] is True
    assert parsed.iloc[0]["is_conversion_tag"] is True


def test_parse_linkedin_tags_from_gtm_ignores_non_linkedin_tags() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "GA4 event",
                "type": "gaawe",
                "parameter_json": "{\"eventName\":\"purchase\"}",
                "notes": "",
                "firing_trigger_ids": "10",
                "consent_settings": "analytics_storage",
            },
            {
                "tag_id": "2",
                "name": "Meta Pixel",
                "type": "html",
                "parameter_json": "{\"customHtml\":\"fbq('track','Lead');\"}",
                "notes": "",
                "firing_trigger_ids": "20",
                "consent_settings": "ad_storage",
            },
        ]
    )

    parsed = parse_linkedin_tags_from_gtm(df)

    assert parsed.empty
    assert list(parsed.columns) == [
        "tag_id",
        "tag_name",
        "tag_type",
        "partner_id",
        "conversion_id",
        "all_partner_ids",
        "all_conversion_ids",
        "trigger_name",
        "consent_settings",
        "has_consent",
        "is_insight_tag",
        "is_conversion_tag",
        "custom_html",
    ]


def test_parse_linkedin_tags_from_gtm_handles_empty_dataframe() -> None:
    parsed = parse_linkedin_tags_from_gtm(pd.DataFrame())

    assert parsed.empty
    assert "partner_id" in parsed.columns
    assert "conversion_id" in parsed.columns
    assert "has_consent" in parsed.columns


def test_build_gtm_crosscheck_matches_expected_partner_and_conversion_ids() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "LinkedIn Insight Tag",
                "type": "html",
                "parameter_json": "{\"customHtml\":\"window._linkedin_partner_id='123456';\"}",
                "notes": "",
                "firing_trigger_ids": "10",
                "consent_settings": "ad_storage",
            },
            {
                "tag_id": "2",
                "name": "LinkedIn conversion",
                "type": "html",
                "parameter_json": "",
                "notes": "window.lintrk('track', { conversion_id: 987654 });",
                "firing_trigger_ids": "20",
                "consent_settings": "ad_storage",
            },
        ]
    )

    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=["example.cz"],
        expected_conversion_ids=["987654"],
        expected_insight_tag_ids=["123456"],
        gtm_tags=df,
    )

    assert result["context_key"] == "ctx"
    assert result["expected_domains"] == ["example.cz"]
    assert result["expected_conversion_ids"] == ["987654"]
    assert result["expected_insight_tag_ids"] == ["123456"]
    assert result["found_partner_ids"] == ["123456"]
    assert result["found_conversion_ids"] == ["987654"]
    assert result["matched"] is True
    assert result["warnings"] == []
    assert result["errors"] == []

    assert len(result["found_insight_tags"]) == 1
    assert len(result["found_conversion_tags"]) == 1
    assert result["found_insight_tags"][0]["partner_id"] == "123456"
    assert result["found_conversion_tags"][0]["conversion_id"] == "987654"


def test_build_gtm_crosscheck_warns_on_missing_expected_partner_id() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "LinkedIn Insight Tag",
                "type": "html",
                "parameter_json": "{\"customHtml\":\"window._linkedin_partner_id='123456';\"}",
                "notes": "",
                "firing_trigger_ids": "10",
                "consent_settings": "ad_storage",
            }
        ]
    )

    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=["example.cz"],
        expected_conversion_ids=[],
        expected_insight_tag_ids=["222"],
        gtm_tags=df,
    )

    assert result["matched"] is False
    assert "V GTM nebyl nalezen očekávaný LinkedIn Insight Tag / partner ID." in result["warnings"]


def test_build_gtm_crosscheck_warns_on_missing_expected_conversion_id() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "LinkedIn conversion",
                "type": "html",
                "parameter_json": "",
                "notes": "window.lintrk('track', { conversion_id: 987654 });",
                "firing_trigger_ids": "10",
                "consent_settings": "ad_storage",
            }
        ]
    )

    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=["example.cz"],
        expected_conversion_ids=["111"],
        expected_insight_tag_ids=[],
        gtm_tags=df,
    )

    assert result["matched"] is False
    assert "V GTM nebyl nalezen očekávaný LinkedIn conversion_id." in result["warnings"]


def test_build_gtm_crosscheck_warns_on_missing_consent_settings() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "LinkedIn Insight Tag",
                "type": "html",
                "parameter_json": "{\"customHtml\":\"window._linkedin_partner_id='123456';\"}",
                "notes": "",
                "firing_trigger_ids": "10",
                "consent_settings": "",
            }
        ]
    )

    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=["example.cz"],
        expected_conversion_ids=[],
        expected_insight_tag_ids=["123456"],
        gtm_tags=df,
    )

    assert result["matched"] is False
    assert any("nemají detekované consent nastavení" in warning for warning in result["warnings"])
    assert "LinkedIn Insight Tag" in result["warnings"][0]


def test_build_gtm_crosscheck_warns_when_gtm_tags_are_empty_and_expected_ids_exist() -> None:
    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=["example.cz"],
        expected_conversion_ids=["111"],
        expected_insight_tag_ids=["222"],
        gtm_tags=pd.DataFrame(),
    )

    assert result["matched"] is False
    assert result["found_insight_tags"] == []
    assert result["found_conversion_tags"] == []
    assert result["found_partner_ids"] == []
    assert result["found_conversion_ids"] == []
    assert "V GTM nebyl nalezen očekávaný LinkedIn Insight Tag / partner ID." in result["warnings"]
    assert "V GTM nebyl nalezen očekávaný LinkedIn conversion_id." in result["warnings"]
    assert result["errors"] == []


def test_build_gtm_crosscheck_matches_when_no_expected_ids_are_configured_and_no_warnings_exist() -> None:
    df = pd.DataFrame(
        [
            {
                "tag_id": "1",
                "name": "LinkedIn Insight Tag",
                "type": "html",
                "parameter_json": "{\"customHtml\":\"window._linkedin_partner_id='123456';\"}",
                "notes": "",
                "firing_trigger_ids": "10",
                "consent_settings": "ad_storage",
            }
        ]
    )

    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=[],
        expected_conversion_ids=[],
        expected_insight_tag_ids=[],
        gtm_tags=df,
    )

    assert result["matched"] is True
    assert result["warnings"] == []
    assert result["found_partner_ids"] == ["123456"]