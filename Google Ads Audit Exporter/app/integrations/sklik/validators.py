from __future__ import annotations

import re

from app.integrations.sklik.models import SklikAccountContextMapping, SklikConnection, ValidationResult
from app.integrations.sklik.normalizers import normalize_domains


CONNECTION_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def normalize_connection_key(value: str) -> str:
    return str(value or "").strip().lower()


def normalize_env_key(value: str) -> str:
    key = normalize_connection_key(value).upper()
    key = re.sub(r"[^A-Z0-9_]+", "_", key)
    return key.strip("_")


def validate_connection(connection: SklikConnection) -> list[str]:
    errors: list[str] = []

    if not connection.key:
        errors.append("Connection key je povinný.")
    elif not CONNECTION_KEY_PATTERN.match(connection.key):
        errors.append("Connection key smí obsahovat jen písmena, čísla, pomlčku a podtržítko.")

    if not connection.label:
        errors.append("Label connection je povinný.")

    if not connection.drak_enabled and not connection.fenix_enabled:
        errors.append("Connection musí mít zapnutý aspoň Drak nebo Fénix.")

    return errors


def validate_mapping(
    mapping: SklikAccountContextMapping,
    *,
    known_context_keys: set[str] | None = None,
    known_connection_keys: set[str] | None = None,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not mapping.context_key:
        errors.append("Context key je povinný.")

    if known_context_keys is not None and mapping.context_key and mapping.context_key not in known_context_keys:
        errors.append(f"Context '{mapping.context_key}' neexistuje v account_contexts.")

    if mapping.enabled and not mapping.connection_key:
        errors.append(f"Kontext {mapping.context_key} má Sklik zapnutý, ale chybí connection key.")

    if known_connection_keys is not None and mapping.connection_key and mapping.connection_key not in known_connection_keys:
        errors.append(f"Connection key '{mapping.connection_key}' neexistuje ve sklik_connections.")

    if mapping.enabled and not mapping.drak_user_ids and not mapping.enable_fenix:
        errors.append(f"Kontext {mapping.context_key} je Drak-only, ale chybí user_id.")

    if mapping.enabled and not mapping.drak_user_ids and mapping.enable_reporting:
        warnings.append(f"Kontext {mapping.context_key} nemá Drak user_id, reporting může být omezený.")

    if mapping.enable_fenix and not mapping.fenix_premise_ids:
        warnings.append(f"Kontext {mapping.context_key} má zapnutý Fénix, ale chybí premise_id.")

    if mapping.enable_web_scan and not mapping.expected_domains:
        errors.append(f"Kontext {mapping.context_key} má zapnutý web scan, ale chybí expected domains.")

    normalized_domains = normalize_domains(mapping.expected_domains)
    if mapping.expected_domains and len(normalized_domains) != len(set(normalized_domains)):
        errors.append(f"Kontext {mapping.context_key} obsahuje duplicitní expected domains.")

    for user_id in list(mapping.drak_user_ids) + list(mapping.fenix_user_ids):
        text = str(user_id or "").strip()
        if text and not text.isdigit():
            errors.append(f"User ID '{text}' v kontextu {mapping.context_key} musí být číselný string.")

    for premise_id in mapping.fenix_premise_ids:
        text = str(premise_id or "").strip()
        if text and not text.isdigit():
            errors.append(f"Premise ID '{text}' v kontextu {mapping.context_key} musí být číselný string.")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)

