from app.integrations.linkedin.normalizers import normalize_entity_identifiers, urn_to_id


def test_urn_to_id() -> None:
    assert urn_to_id("urn:li:sponsoredCampaign:987654321") == "987654321"


def test_normalize_entity_identifiers() -> None:
    row = normalize_entity_identifiers({"campaign": "urn:li:sponsoredCampaign:123"})
    assert row["campaign_id"] == "123"
    assert row["campaign_urn"] == "urn:li:sponsoredCampaign:123"

