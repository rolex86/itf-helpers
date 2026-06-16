from app.integrations.linkedin.restli import (
    date_range_param,
    restli_list,
    sponsored_account_urn,
)
from datetime import date


def test_restli_list_encodes_values() -> None:
    assert restli_list(["a", "b"]) == "List(a,b)"


def test_sponsored_account_urn() -> None:
    assert sponsored_account_urn("123456") == "urn:li:sponsoredAccount:123456"


def test_date_range_param() -> None:
    value = date_range_param(date(2026, 1, 1), date(2026, 1, 31))
    assert "year:2026" in value
    assert "month:1" in value
    assert "day:31" in value

