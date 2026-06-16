from __future__ import annotations

from typing import Any

import pandas as pd

from app.integrations.linkedin.lead_sync import empty_response_frames, fetch_lead_forms, fetch_lead_responses


class DummyLinkedInClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def paginate_start_count(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        request_params = dict(params or {})
        self.calls.append(
            {
                "path": path,
                "params": request_params,
                "count": count,
            }
        )

        if path == "leadForms":
            return [
                {
                    "id": "urn:li:leadGenForm:111",
                    "name": "Test lead form",
                    "content": {
                        "questions": [
                            {
                                "questionId": "email",
                                "question": {
                                    "localized": {
                                        "cs_CZ": "E-mail",
                                    },
                                },
                            },
                            {
                                "questionId": "phone",
                                "question": {
                                    "localized": {
                                        "cs_CZ": "Telefon",
                                    },
                                },
                            },
                        ]
                    },
                }
            ]

        if path == "leadFormResponses":
            return [
                {
                    "id": "urn:li:leadGenFormResponse:999",
                    "leadGenForm": "urn:li:leadGenForm:111",
                    "createdAt": 1760000000000,
                    "answers": [
                        {
                            "questionId": "email",
                            "answerDetails": {
                                "text": "test@example.cz",
                            },
                        },
                        {
                            "questionId": "phone",
                            "answerDetails": {
                                "text": "+420 123 456 789",
                            },
                        },
                    ],
                }
            ]

        return []


def test_fetch_lead_forms_uses_owner_finder_and_extracts_questions() -> None:
    client = DummyLinkedInClient()

    forms, questions, warnings = fetch_lead_forms(
        client,  # type: ignore[arg-type]
        owner_urns=["urn:li:sponsoredAccount:123456"],
    )

    assert warnings == []
    assert len(forms) == 1
    assert len(questions) == 2

    call = client.calls[0]
    assert call["path"] == "leadForms"
    assert call["params"]["q"] == "owner"
    assert call["params"]["owner"] == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    assert call["count"] == 100

    assert forms[0]["owner_urn"] == "urn:li:sponsoredAccount:123456"
    assert forms[0]["owner_param"] == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    assert forms[0]["lead_form_id"] == "111"
    assert forms[0]["lead_form_urn"] == "urn:li:leadGenForm:111"
    assert forms[0]["versioned_lead_form_urn"] == "urn:li:leadGenForm:111"

    assert questions[0]["owner_urn"] == "urn:li:sponsoredAccount:123456"
    assert questions[0]["lead_form_id"] == "111"
    assert questions[0]["versioned_lead_form_urn"] == "urn:li:leadGenForm:111"
    assert questions[0]["question_index"] == 1


def test_fetch_lead_forms_accepts_already_wrapped_owner_param() -> None:
    client = DummyLinkedInClient()

    fetch_lead_forms(
        client,  # type: ignore[arg-type]
        owner_urns=["(sponsoredAccount:urn:li:sponsoredAccount:123456)"],
    )

    assert client.calls[0]["params"]["owner"] == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"


def test_fetch_lead_forms_dedupes_owner_urns() -> None:
    client = DummyLinkedInClient()

    fetch_lead_forms(
        client,  # type: ignore[arg-type]
        owner_urns=[
            "urn:li:sponsoredAccount:123456",
            "urn:li:sponsoredAccount:123456",
            "",
        ],
    )

    assert len(client.calls) == 1


def test_fetch_lead_forms_returns_warning_on_endpoint_error() -> None:
    class FailingClient(DummyLinkedInClient):
        def paginate_start_count(
            self,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            count: int = 100,
        ) -> list[dict[str, Any]]:
            raise RuntimeError("LinkedIn API error")

    forms, questions, warnings = fetch_lead_forms(
        FailingClient(),  # type: ignore[arg-type]
        owner_urns=["urn:li:sponsoredAccount:123456"],
    )

    assert forms == []
    assert questions == []
    assert len(warnings) == 1
    assert "Lead forms pro owner urn:li:sponsoredAccount:123456 nebylo možné načíst" in warnings[0]
    assert "LinkedIn API error" in warnings[0]


def test_fetch_lead_responses_uses_owner_lead_type_test_flag_and_versioned_form() -> None:
    client = DummyLinkedInClient()

    responses, warnings = fetch_lead_responses(
        client,  # type: ignore[arg-type]
        forms=[
            {
                "owner_urn": "urn:li:sponsoredAccount:123456",
                "versioned_lead_form_urn": "urn:li:leadGenForm:111:2",
            }
        ],
        limited_to_test_leads=True,
    )

    assert warnings == []
    assert len(responses) == 1

    call = client.calls[0]
    assert call["path"] == "leadFormResponses"
    assert call["params"]["q"] == "owner"
    assert call["params"]["owner"] == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    assert call["params"]["leadType"] == "(leadType:SPONSORED)"
    assert call["params"]["limitedToTestLeads"] == "true"
    assert call["params"]["versionedLeadGenFormUrn"] == "urn:li:leadGenForm:111:2"
    assert call["count"] == 100

    assert responses[0]["owner_urn"] == "urn:li:sponsoredAccount:123456"
    assert responses[0]["owner_param"] == "(sponsoredAccount:urn:li:sponsoredAccount:123456)"
    assert responses[0]["versioned_lead_form_urn"] == "urn:li:leadGenForm:111:2"
    assert responses[0]["limited_to_test_leads"] is True
    assert responses[0]["answer_1_question_id"] == "email"
    assert responses[0]["answer_1_value"] == "test@example.cz"
    assert responses[0]["answer_2_question_id"] == "phone"
    assert responses[0]["answer_2_value"] == "+420 123 456 789"


def test_fetch_lead_responses_can_fetch_non_test_leads() -> None:
    client = DummyLinkedInClient()

    fetch_lead_responses(
        client,  # type: ignore[arg-type]
        forms=[
            {
                "owner_urn": "urn:li:sponsoredAccount:123456",
                "versioned_lead_form_urn": "urn:li:leadGenForm:111",
            }
        ],
        limited_to_test_leads=False,
    )

    assert client.calls[0]["params"]["limitedToTestLeads"] == "false"


def test_fetch_lead_responses_dedupes_identical_requests() -> None:
    client = DummyLinkedInClient()

    responses, warnings = fetch_lead_responses(
        client,  # type: ignore[arg-type]
        forms=[
            {
                "owner_urn": "urn:li:sponsoredAccount:123456",
                "versioned_lead_form_urn": "urn:li:leadGenForm:111",
            },
            {
                "owner_urn": "urn:li:sponsoredAccount:123456",
                "versioned_lead_form_urn": "urn:li:leadGenForm:111",
            },
        ],
        limited_to_test_leads=True,
    )

    assert warnings == []
    assert len(client.calls) == 1
    assert len(responses) == 1


def test_fetch_lead_responses_warns_when_owner_is_missing() -> None:
    client = DummyLinkedInClient()

    responses, warnings = fetch_lead_responses(
        client,  # type: ignore[arg-type]
        forms=[
            {
                "versioned_lead_form_urn": "urn:li:leadGenForm:111",
            }
        ],
    )

    assert responses == []
    assert warnings == ["Lead responses nebylo možné načíst: lead form nemá owner_urn."]
    assert client.calls == []


def test_fetch_lead_responses_returns_warning_on_endpoint_error() -> None:
    class FailingClient(DummyLinkedInClient):
        def paginate_start_count(
            self,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            count: int = 100,
        ) -> list[dict[str, Any]]:
            raise RuntimeError("LinkedIn API error")

    responses, warnings = fetch_lead_responses(
        FailingClient(),  # type: ignore[arg-type]
        forms=[
            {
                "owner_urn": "urn:li:sponsoredAccount:123456",
                "versioned_lead_form_urn": "urn:li:leadGenForm:111",
            }
        ],
    )

    assert responses == []
    assert len(warnings) == 1
    assert "Lead responses pro owner urn:li:sponsoredAccount:123456 nebylo možné načíst" in warnings[0]
    assert "LinkedIn API error" in warnings[0]


def test_empty_response_frames_returns_empty_dataframes() -> None:
    frames = empty_response_frames()

    assert set(frames.keys()) == {
        "lead_forms",
        "lead_form_questions",
        "lead_form_responses",
        "lead_notifications",
    }

    for dataframe in frames.values():
        assert isinstance(dataframe, pd.DataFrame)
        assert dataframe.empty