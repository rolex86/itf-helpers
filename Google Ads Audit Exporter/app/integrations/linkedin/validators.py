from __future__ import annotations

import re
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

SUPPORTED_AUTH_TYPES = {"manual_token", "oauth"}
SUPPORTED_EXPECTED_CONVERSION_TYPES = {"lead", "purchase", "engagement", "mixed"}
API_VERSION_PATTERN = re.compile(r"^\d{6}$")


def validate_connection(connection: LinkedInConnection) -> list[str]:
    errors: list[str] = []

    if not connection.key:
        errors.append("Connection key je povinný.")

    if connection.key and not re.match(r"^[a-zA-Z0-9_-]+$", connection.key):
        errors.append("Connection key smí obsahovat jen písmena, čísla, pomlčku a podtržítko.")

    if not connection.label:
        errors.append("Název connection je povinný.")

    if connection.auth_type not in SUPPORTED_AUTH_TYPES:
        errors.append("Auth type musí být manual_token nebo oauth.")

    if not connection.linkedin_api_version:
        errors.append("LinkedIn API version je povinná.")

    if connection.linkedin_api_version and not API_VERSION_PATTERN.match(connection.linkedin_api_version):
        errors.append("LinkedIn API version musí být ve formátu YYYYMM, např. 202606.")

    return errors


def validate_mapping(mapping: LinkedInAccountContextMapping) -> list[str]:
    errors: list[str] = []

    if not mapping.context_key:
        errors.append("Context key je povinný.")

    if mapping.enabled and not mapping.connection_key:
        errors.append(f"Kontext {mapping.context_key} má LinkedIn zapnutý, ale chybí connection key.")

    if mapping.enabled and not mapping.ad_account_ids:
        errors.append(f"Kontext {mapping.context_key} má LinkedIn zapnutý, ale chybí LinkedIn ad account ID.")

    if mapping.expected_conversion_type and mapping.expected_conversion_type not in SUPPORTED_EXPECTED_CONVERSION_TYPES:
        errors.append(
            f"Kontext {mapping.context_key} má neplatný expected conversion type: {mapping.expected_conversion_type}."
        )

    if mapping.expected_domains:
        normalized_domains = normalize_domains(mapping.expected_domains)
        if len(normalized_domains) != len({domain.lower() for domain in normalized_domains}):
            errors.append(f"Kontext {mapping.context_key} obsahuje duplicitní expected domains.")

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

        text = text.split("/")[0].strip()
        text = text.split(":")[0].strip()
        text = text.removeprefix("www.")

        if not text or text in seen:
            continue

        seen.add(text)
        normalized.append(text)

    return normalized