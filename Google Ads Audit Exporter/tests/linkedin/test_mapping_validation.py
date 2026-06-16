from app.integrations.linkedin.models import LinkedInAccountContextMapping
from app.integrations.linkedin.validators import normalize_domains, validate_mapping


def test_normalize_domains() -> None:
    assert normalize_domains(["https://www.example.cz/", "example.cz"]) == ["www.example.cz", "example.cz"]


def test_validate_mapping_requires_connection_when_enabled() -> None:
    mapping = LinkedInAccountContextMapping(context_key="ctx", enabled=True)
    errors = validate_mapping(mapping)
    assert errors

