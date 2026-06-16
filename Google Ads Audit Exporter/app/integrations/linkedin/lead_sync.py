from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers, records_to_frame
from app.integrations.linkedin.restli import lead_form_owner_param, lead_form_urn, lead_type_param


def _string(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(values: list[str]) -> list[str]:
    normalized: list[str] = []

    for value in values or []:
        text = _string(value)
        if text and text not in normalized:
            normalized.append(text)

    return normalized


def _extract_questions(row: dict[str, Any]) -> list[dict[str, Any]]:
    content = row.get("content", {}) if isinstance(row.get("content"), dict) else {}
    questions = content.get("questions", []) or []

    if not isinstance(questions, list):
        return []

    return [question for question in questions if isinstance(question, dict)]


def _extract_versioned_form_urn(form: dict[str, Any]) -> str:
    candidates = (
        form.get("versioned_lead_form_urn"),
        form.get("versionedLeadGenFormUrn"),
        form.get("versionedLeadGenForm"),
        form.get("versioned_form_urn"),
        form.get("lead_form_urn"),
        form.get("leadGenForm"),
        form.get("form"),
        form.get("id"),
    )

    for candidate in candidates:
        text = _string(candidate)
        if text.startswith("urn:li:versionedLeadGenForm:"):
            return text
        if text.startswith("urn:li:leadGenForm:"):
            return text

    form_id = _string(form.get("lead_form_id") or form.get("id"))
    if form_id:
        return lead_form_urn(form_id)

    return ""


def _normalize_lead_answer(value: Any) -> Any:
    if isinstance(value, dict):
        if "localized" in value:
            return value.get("localized")
        if "text" in value:
            return value.get("text")
        if "answer" in value:
            return _normalize_lead_answer(value.get("answer"))
        return {str(key): _normalize_lead_answer(nested) for key, nested in value.items()}

    if isinstance(value, list):
        return [_normalize_lead_answer(item) for item in value]

    return value


def _flatten_lead_response(row: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_entity_identifiers(row)

    answers = row.get("answers") or row.get("formResponse") or row.get("leadFormResponse") or []
    if isinstance(answers, list):
        for index, answer in enumerate(answers, start=1):
            if not isinstance(answer, dict):
                continue

            question_id = _string(
                answer.get("questionId")
                or answer.get("question")
                or answer.get("field")
                or f"question_{index}"
            )
            answer_value = _normalize_lead_answer(
                answer.get("answerDetails")
                or answer.get("answer")
                or answer.get("value")
                or answer.get("values")
            )

            if question_id:
                normalized[f"answer_{index}_question_id"] = question_id
                normalized[f"answer_{index}_value"] = answer_value

    return normalized


def fetch_lead_forms(
    client: LinkedInRestClient,
    *,
    owner_urns: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    forms: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    warnings: list[str] = []

    for owner_urn in _dedupe(owner_urns):
        owner_param = lead_form_owner_param(owner_urn)

        try:
            rows = list(
                client.paginate_start_count(
                    "leadForms",
                    params={
                        "q": "owner",
                        "owner": owner_param,
                    },
                    count=100,
                )
            )

            for row in rows:
                normalized = normalize_entity_identifiers(row)
                normalized["owner_urn"] = owner_urn
                normalized["owner_param"] = owner_param

                versioned_form_urn = _extract_versioned_form_urn(normalized) or _extract_versioned_form_urn(row)
                normalized["versioned_lead_form_urn"] = versioned_form_urn

                forms.append(normalized)

                for index, question in enumerate(_extract_questions(row), start=1):
                    question_row = normalize_entity_identifiers(dict(question))
                    question_row["owner_urn"] = owner_urn
                    question_row["owner_param"] = owner_param
                    question_row["lead_form_id"] = normalized.get("lead_form_id") or normalized.get("id") or ""
                    question_row["versioned_lead_form_urn"] = versioned_form_urn
                    question_row["question_index"] = index
                    questions.append(question_row)

        except Exception as exc:
            warnings.append(f"Lead forms pro owner {owner_urn} nebylo možné načíst: {exc}")

    return forms, questions, warnings


def fetch_lead_responses(
    client: LinkedInRestClient,
    *,
    forms: list[dict[str, Any]],
    limited_to_test_leads: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    responses: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_request_keys: set[tuple[str, str, bool]] = set()

    for form in forms:
        owner_urn = _string(form.get("owner_urn") or form.get("owner") or "")
        if not owner_urn:
            warnings.append("Lead responses nebylo možné načíst: lead form nemá owner_urn.")
            continue

        owner_param = lead_form_owner_param(owner_urn)
        versioned_form_urn = _extract_versioned_form_urn(form)

        request_key = (owner_param, versioned_form_urn, bool(limited_to_test_leads))
        if request_key in seen_request_keys:
            continue
        seen_request_keys.add(request_key)

        try:
            request_params = {
                "q": "owner",
                "owner": owner_param,
                "leadType": lead_type_param("SPONSORED"),
                "limitedToTestLeads": str(bool(limited_to_test_leads)).lower(),
            }

            if versioned_form_urn:
                request_params["versionedLeadGenFormUrn"] = versioned_form_urn

            rows = list(
                client.paginate_start_count(
                    "leadFormResponses",
                    params=request_params,
                    count=100,
                )
            )

            for row in rows:
                normalized = _flatten_lead_response(row)
                normalized["owner_urn"] = owner_urn
                normalized["owner_param"] = owner_param
                normalized["versioned_lead_form_urn"] = versioned_form_urn
                normalized["limited_to_test_leads"] = bool(limited_to_test_leads)
                responses.append(normalized)

        except Exception as exc:
            warnings.append(f"Lead responses pro owner {owner_urn} nebylo možné načíst: {exc}")

    return responses, warnings


def empty_response_frames() -> dict[str, Any]:
    return {
        "lead_forms": records_to_frame([]),
        "lead_form_questions": records_to_frame([]),
        "lead_form_responses": records_to_frame([]),
        "lead_notifications": records_to_frame([]),
    }