import pandas as pd

from app.integrations.linkedin.gtm_crosscheck import build_gtm_crosscheck, parse_linkedin_tags_from_gtm


def test_parse_linkedin_tags_from_gtm() -> None:
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
    parsed = parse_linkedin_tags_from_gtm(df)
    assert parsed.iloc[0]["partner_id"] == "123456"


def test_build_gtm_crosscheck_warns_on_missing_expected_id() -> None:
    df = pd.DataFrame()
    result = build_gtm_crosscheck(
        context_key="ctx",
        expected_domains=["example.cz"],
        expected_conversion_ids=["111"],
        expected_insight_tag_ids=["222"],
        gtm_tags=df,
    )
    assert result["warnings"]

