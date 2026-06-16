from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers, records_to_frame


def fetch_lead_forms(
    client: LinkedInRestClient,
    *,
    owner_urns: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    forms: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    warnings: list[str] = []

    for owner_urn in owner_urns:
        try:
            rows = list(
                client.paginate(
                    "leadForms",
                    params={"q": "owner", "owner": owner_urn, "count": 100},
                    count=100,
                )
            )
            for row in rows:
                normalized = normalize_entity_identifiers(row)
                forms.append(normalized)
                for question in row.get("questions", []) or []:
                    if isinstance(question, dict):
                        question_row = dict(question)
                        question_row["owner_urn"] = owner_urn
                        question_row["lead_form_id"] = normalized.get("lead_form_id", "")
                        questions.append(question_row)
        except Exception as exc:
            warnings.append(f"Lead forms pro owner {owner_urn} nebylo možné načíst: {exc}")

    return forms, questions, warnings


def fetch_lead_responses(
    client: LinkedInRestClient,
    *,
    form_urns: list[str],
    limited_to_test_leads: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    responses: list[dict[str, Any]] = []
    warnings: list[str] = []

    for form_urn in form_urns:
        try:
            rows = list(
                client.paginate(
                    "leadFormResponses",
                    params={
                        "q": "leadForm",
                        "leadForm": form_urn,
                        "limitedToTestLeads": str(bool(limited_to_test_leads)).lower(),
                        "count": 100,
                    },
                    count=100,
                )
            )
            for row in rows:
                normalized = normalize_entity_identifiers(row)
                normalized["versioned_lead_form_urn"] = form_urn
                responses.append(normalized)
        except Exception as exc:
            warnings.append(f"Lead responses pro form {form_urn} nebylo možné načíst: {exc}")

    return responses, warnings


def empty_response_frames() -> dict[str, Any]:
    return {
        "lead_forms": records_to_frame([]),
        "lead_form_questions": records_to_frame([]),
        "lead_form_responses": records_to_frame([]),
        "lead_notifications": records_to_frame([]),
    }

