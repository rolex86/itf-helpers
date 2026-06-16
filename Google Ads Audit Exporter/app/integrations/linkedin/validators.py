from __future__ import annotations

from urllib.parse import urlsplit

from app.integrations.linkedin.models import LinkedInAccountContextMapping, LinkedInConnection


REQUIRED_CORE_SCOPES = ["r_ads", "r_ads_reporting"]
RECOMMENDED_SCOPES = [
    "r_ads",
    "r_ads_reporting",
    "r_marketing_leadgen_automation",
    "r_organization_lookup",
    "rw_organization_admin",
]


def validate_connection(connection: LinkedInConnection) -> list[str]:
    errors: list[str] = []
    if not connection.key:
        errors.append("Connection key je povinný.")
    if not connection.label:
        errors.append("Název connection je povinný.")
    if not connection.linkedin_api_version:
        errors.append("LinkedIn API version je povinná.")
    return errors


def validate_mapping(mapping: LinkedInAccountContextMapping) -> list[str]:
    errors: list[str] = []
    if not mapping.context_key:
        errors.append("Context key je povinný.")
    if mapping.enabled and not mapping.connection_key:
        errors.append(f"Kontext {mapping.context_key} má LinkedIn zapnutý, ale chybí connection key.")
    return errors


def normalize_domains(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        if "://" in text:
            try:
                text = urlsplit(text).netloc.strip().lower()
            except ValueError:
                pass
        text = text.split(":")[0].strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized

