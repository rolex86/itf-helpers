from app.integrations.linkedin.reporting import resolve_date_range


def test_resolve_date_range_from_preset() -> None:
    result = resolve_date_range(preset="last_30_days")
    assert result.start <= result.end


def test_resolve_date_range_from_custom_values() -> None:
    result = resolve_date_range(date_from="2026-01-01", date_to="2026-01-31")
    assert result.start.isoformat() == "2026-01-01"
    assert result.end.isoformat() == "2026-01-31"

