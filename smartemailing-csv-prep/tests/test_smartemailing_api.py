from __future__ import annotations

import base64
import unittest
from typing import Any

from src.smartemailing_api import (
    SmartEmailingApiClient,
    SmartEmailingApiError,
    SmartEmailingCredentials,
    build_api_contacts_from_import_df,
    build_basic_auth_header,
    combine_schema_columns,
    extract_contacts,
    extract_contact_custom_field_values,
    extract_contact_lists,
    extract_custom_fields,
    extract_custom_field_names,
)


class MiniImportFrame:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.columns = list(rows[0].keys()) if rows else []

    def iterrows(self):
        for idx, row in enumerate(self.rows):
            yield idx, row


class PagingFakeApiClient(SmartEmailingApiClient):
    def __init__(self, responses: dict[tuple[str, int], Any]) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))
        page = int((query or {}).get("page", 1))
        response = self.responses.get((path, page))
        if isinstance(response, Exception):
            raise response
        if response is None:
            return {"data": []}
        return response


class ContactDetailEnrichmentFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path == "/api/v3/contactlists/422/contacts":
            return {
                "data": {
                    "items": [
                        {
                            "id": 101,
                            "emailaddress": "user@example.com",
                            "name": "User",
                        }
                    ]
                }
            }

        if method == "GET" and path in {"/api/v3/contacts/101", "/api/v3/contact/101"}:
            return {
                "data": {
                    "contact": {
                        "id": 101,
                        "emailaddress": "user@example.com",
                        "name": "User",
                        "customfields": [{"id": 10, "value": "A"}],
                    }
                }
            }

        if method == "GET" and path == "/api/v3/contact-customfield-values":
            return {
                "data": [
                    {
                        "id": 5001,
                        "contact_id": 101,
                        "customfield_id": 10,
                        "value": "A",
                    }
                ],
                "status": "ok",
                "meta": {"total_count": 1, "displayed_count": 1, "offset": 0},
            }

        raise SmartEmailingApiError("not found", status_code=404)


class ContactFallbackByEmailFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path == "/api/v3/contactlists/422/contacts":
            return {
                "data": {
                    "items": [
                        {
                            "emailaddress": "user@example.com",
                            "name": "User",
                        }
                    ]
                }
            }

        if method == "GET" and path == "/api/v3/contacts":
            return {
                "data": {
                    "items": [
                        {
                            "id": 101,
                            "emailaddress": "user@example.com",
                            "name": "User",
                            "customfields": [{"id": 10, "value": "A"}],
                        }
                    ]
                }
            }

        raise SmartEmailingApiError("not found", status_code=404)


class ContactByEmailSearchFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path == "/api/v3/contacts":
            raise SmartEmailingApiError("not found", status_code=404)

        if method == "POST" and path == "/api/v3/contacts/search":
            email = ""
            if isinstance(body, dict):
                if isinstance(body.get("emailaddress"), str):
                    email = body.get("emailaddress", "")
                elif isinstance(body.get("search"), dict):
                    email = str(body.get("search", {}).get("emailaddress", ""))
                elif isinstance(body.get("filter"), dict):
                    email = str(body.get("filter", {}).get("emailaddress", ""))
            email = str(email).strip().casefold()
            if email == "user@example.com":
                return {
                    "data": {
                        "items": [
                            {
                                "id": 101,
                                "emailaddress": "user@example.com",
                                "name": "User",
                                "customfields": [{"id": 10, "value": "A"}],
                            }
                        ]
                    }
                }
            return {"data": {"items": []}}

        raise SmartEmailingApiError("not found", status_code=404)


class ContactBlacklistedLookupFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path == "/api/v3/contacts":
            email = str((query or {}).get("emailaddress", "")).strip().casefold()
            if email == "black@example.com":
                return {"data": {"items": [{"emailaddress": "black@example.com", "blacklisted": 1}]}}
            if email == "ok@example.com":
                return {"data": {"items": [{"emailaddress": "ok@example.com", "blacklisted": 0}]}}
            return {"data": {"items": []}}

        raise SmartEmailingApiError("not found", status_code=404)


class ContactCustomFieldValuesFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path == "/api/v3/contact-customfield-values":
            return {
                "data": [
                    {
                        "id": 5001,
                        "contact_id": 101,
                        "customfield_id": 10,
                        "value": "A",
                    },
                    {
                        "id": 5002,
                        "contact_id": 101,
                        "customfield_id": 11,
                        "value": "B",
                    },
                    {
                        "id": 5003,
                        "contact_id": 999,
                        "customfield_id": 11,
                        "value": "X",
                    },
                ],
                "status": "ok",
                "meta": {"total_count": 3, "displayed_count": 3, "offset": 0},
            }

        raise SmartEmailingApiError("not found", status_code=404)


class TargetedListSearchPartialFailureFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    @staticmethod
    def _extract_requested_emails_from_body(body: dict[str, Any] | None) -> set[str]:
        if not isinstance(body, dict):
            return set()
        candidates: list[Any] = []
        for key in ["search", "filter", "where"]:
            nested = body.get(key)
            if isinstance(nested, dict):
                candidates.append(nested.get("emailaddress"))
                candidates.append(nested.get("email"))
        candidates.append(body.get("emailaddress"))
        candidates.append(body.get("email"))
        candidates.append(body.get("emails"))
        out: set[str] = set()
        for raw in candidates:
            if isinstance(raw, list):
                for item in raw:
                    value = str(item).strip().casefold()
                    if value:
                        out.add(value)
            else:
                value = str(raw).strip().casefold()
                if value:
                    out.add(value)
        return out

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "POST" and path == "/api/v3/contactlists/422/contacts/search":
            requested = self._extract_requested_emails_from_body(body)
            if "broken@example.com" in requested:
                raise SmartEmailingApiError("not found", status_code=404)
            items = []
            if "ok@example.com" in requested:
                items.append(
                    {
                        "id": 101,
                        "emailaddress": "ok@example.com",
                        "customfields": [{"id": 10, "value": "A"}],
                    }
                )
            return {"data": {"items": items}}

        if method == "GET" and path == "/api/v3/contactlists/422/contacts":
            return {
                "data": {
                    "items": [
                        {
                            "id": 101,
                            "emailaddress": "ok@example.com",
                            "customfields": [{"id": 10, "value": "A"}],
                        },
                        {
                            "id": 102,
                            "emailaddress": "broken@example.com",
                            "customfields": [{"id": 11, "value": "B"}],
                        },
                    ]
                }
            }

        raise SmartEmailingApiError("not found", status_code=404)


class ImportFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "POST" and path == "/api/v3/import":
            if isinstance(body, dict) and isinstance(body.get("data"), list):
                rows = body.get("data", [])
                if rows and isinstance(rows[0], dict) and str(rows[0].get("emailaddress", "")).strip():
                    return {"status": "ok", "sent": len(rows)}
            raise SmartEmailingApiError("Missing key: emailaddress", status_code=422)

        raise SmartEmailingApiError("not found", status_code=404)


class ImportDataFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))
        if method == "POST" and path == "/api/v3/import":
            if isinstance(body, dict) and isinstance(body.get("data"), list):
                rows = body.get("data", [])
                if rows and isinstance(rows[0], dict) and str(rows[0].get("emailaddress", "")).strip():
                    contactlists = rows[0].get("contactlists", [])
                    if not isinstance(contactlists, list) or not contactlists:
                        raise SmartEmailingApiError("Missing key: contactlists", status_code=422)
                    return {"status": "ok", "sent": len(rows)}
            raise SmartEmailingApiError("Missing key: emailaddress", status_code=422)
        raise SmartEmailingApiError("not found", status_code=404)


class PostSearchFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path in {"/api/v3/customfields", "/api/v3/custom-fields"}:
            raise SmartEmailingApiError("not found", status_code=404)

        if method == "POST" and path == "/api/v3/customfields/search":
            page = int((body or {}).get("page", 1))
            if page == 1:
                return {"data": {"customfields": [{"id": 1, "name": "Field 1"}]}}
            if page == 2:
                return {"data": {"customfields": [{"id": 2, "name": "Field 2"}]}}
            return {"data": {"customfields": []}}

        raise SmartEmailingApiError("unexpected call", status_code=400)


class ContactListPostSearchFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))

        if method == "GET" and path in {"/api/v3/contactlists", "/api/v3/contact-lists"}:
            raise SmartEmailingApiError("not found", status_code=404)

        if method == "POST" and path == "/api/v3/contactlists/search":
            page = int((body or {}).get("page", 1))
            if page == 1:
                return {"data": {"contactlists": [{"id": 101, "contactlist_name": "Stage A"}]}}
            return {"data": {"contactlists": []}}

        raise SmartEmailingApiError("unexpected call", status_code=400)


class CreateCustomFieldFakeApiClient(SmartEmailingApiClient):
    def __init__(self) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query, body))
        if method == "POST" and path == "/api/v3/customfields":
            if isinstance(body, dict) and str(body.get("name", "")).strip():
                return {"data": {"id": 999, "name": body.get("name"), "type": body.get("type", "text")}}
            raise SmartEmailingApiError("bad payload", status_code=400)
        raise SmartEmailingApiError("not found", status_code=404)


class SmartEmailingApiTests(unittest.TestCase):
    def test_build_basic_auth_header(self) -> None:
        header = build_basic_auth_header("my-user", "my-key")
        self.assertTrue(header.startswith("Basic "))
        decoded = base64.b64decode(header.replace("Basic ", "")).decode("utf-8")
        self.assertEqual(decoded, "my-user:my-key")

    def test_extract_custom_fields_and_contact_lists(self) -> None:
        custom_payload = {
            "data": {"customfields": [{"id": 10, "name": "Pole A"}, {"attributes": {"id": "11", "label": "Pole B"}}]}
        }
        lists_payload = {"data": {"contactlists": [{"id": 1, "contactlist_name": "Staging"}]}}

        custom_fields = extract_custom_fields(custom_payload)
        lists = extract_contact_lists(lists_payload)

        self.assertEqual(custom_fields, [{"id": "10", "name": "Pole A", "type": ""}, {"id": "11", "name": "Pole B", "type": ""}])
        self.assertEqual(lists, [{"id": "1", "name": "Staging"}])

    def test_extract_contacts(self) -> None:
        payload = {
            "data": {
                "items": [
                    {
                        "id": 55,
                        "emailaddress": "a@example.com",
                        "name": "Jan",
                        "surname": "Novák",
                        "blacklisted": 1,
                        "town": "Brno",
                        "customfields": [{"id": 10, "value": "A"}, {"id": "11", "value": ["X", "Y"]}],
                        "tags": ["SYNC"],
                    }
                ]
            }
        }

        contacts = extract_contacts(payload)

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["id"], "55")
        self.assertEqual(contacts[0]["emailaddress"], "a@example.com")
        self.assertEqual(contacts[0]["name"], "Jan")
        self.assertEqual(contacts[0]["surname"], "Novák")
        self.assertEqual(contacts[0]["blacklisted"], "1")
        self.assertEqual(contacts[0]["town"], "Brno")
        self.assertEqual(
            contacts[0]["customfields"],
            [{"id": "10", "value": "A"}, {"id": "11", "value": ["X", "Y"]}],
        )
        self.assertEqual(contacts[0]["tags"], ["SYNC"])

    def test_extract_contact_custom_field_values(self) -> None:
        payload = {
            "data": [
                {
                    "contact_id": 101,
                    "customfield_id": 10,
                    "value": "A",
                },
                {
                    "contact_id": 101,
                    "customfield_id": 11,
                    "customfield_options_id": 777,
                    "value": None,
                },
            ]
        }
        rows = extract_contact_custom_field_values(payload)
        self.assertEqual(
            rows,
            [
                {"pair_id": "", "contact_id": "101", "customfield_id": "10", "value": "A"},
                {"pair_id": "", "contact_id": "101", "customfield_id": "11", "value": "777"},
            ],
        )

    def test_extract_custom_field_names_from_multiple_shapes(self) -> None:
        payload = {
            "data": {
                "customfields": [
                    {"name": "Pole A"},
                    {"attributes": {"label": "Pole B"}},
                    {"title": "Pole C"},
                ]
            }
        }
        self.assertEqual(extract_custom_field_names(payload), ["Pole A", "Pole B", "Pole C"])

    def test_combine_schema_columns_preserves_order_and_deduplicates(self) -> None:
        out = combine_schema_columns(
            ["E-mail", "Společnost", "E-mail"],
            ["Aplikace X", "Společnost", "Aplikace Y"],
        )
        self.assertEqual(out, ["E-mail", "Společnost", "Aplikace X", "Aplikace Y"])

    def test_build_api_contacts_from_import_df(self) -> None:
        frame = MiniImportFrame(
            [
                {"E-mail": "a@example.com", "Jméno": "Jan", "Unknown X": "V1", "Known CF": "CF1"},
                {"E-mail": "", "Jméno": "NoEmail", "Known CF": "CF2"},
            ]
        )
        contacts, issues = build_api_contacts_from_import_df(
            import_df=frame,
            api_system_field_map={"E-mail": "emailaddress", "Jméno": "name"},
            custom_fields=[{"id": "7", "name": "Known CF", "type": ""}],
            list_id="123",
            tag="TAG_TEST",
            strict_custom_fields=True,
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["emailaddress"], "a@example.com")
        self.assertEqual(contacts[0]["name"], "Jan")
        self.assertEqual(contacts[0]["customfields"], [{"id": "7", "value": "CF1"}])
        self.assertEqual(contacts[0]["contactlists"], [{"id": "123", "status": "confirmed"}])
        self.assertEqual(contacts[0]["tags"], ["TAG_TEST"])
        self.assertTrue(any(x["issue"] == "missing_custom_field" for x in issues))
        self.assertTrue(any(x["issue"] == "missing_emailaddress" for x in issues))

    def test_build_api_contacts_maps_titles_to_system_keys(self) -> None:
        frame = MiniImportFrame(
            [
                {
                    "E-mail": "a@example.com",
                    "Jméno": "Jan",
                    "Příjmení": "Novák",
                    "Tituly před jménem": "Ing.",
                    "Tituly za jménem": "Ph.D.",
                }
            ]
        )
        contacts, issues = build_api_contacts_from_import_df(
            import_df=frame,
            api_system_field_map={
                "E-mail": "emailaddress",
                "Jméno": "name",
                "Příjmení": "surname",
                "Tituly před jménem": "titlesbefore",
                "Tituly za jménem": "titlesafter",
            },
            custom_fields=[],
            strict_custom_fields=True,
        )

        self.assertEqual(len(issues), 0)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["titlesbefore"], "Ing.")
        self.assertEqual(contacts[0]["titlesafter"], "Ph.D.")

    def test_build_api_contacts_maps_city_alias_to_town(self) -> None:
        frame = MiniImportFrame(
            [
                {
                    "E-mail": "a@example.com",
                    "Město": "Brno",
                    "Společnost": "Acme",
                    "Země": "CZ",
                }
            ]
        )
        contacts, issues = build_api_contacts_from_import_df(
            import_df=frame,
            api_system_field_map={
                "E-mail": "emailaddress",
                "Město": "city",
                "Společnost": "company",
                "Země": "country",
            },
            custom_fields=[],
            strict_custom_fields=True,
        )

        self.assertEqual(len(issues), 0)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["town"], "Brno")
        self.assertEqual(contacts[0]["company"], "Acme")
        self.assertEqual(contacts[0]["country"], "CZ")

    def test_build_api_contacts_managed_ids_respect_allowlist(self) -> None:
        frame = MiniImportFrame(
            [
                {
                    "E-mail": "a@example.com",
                    "Program A": "",
                    "Neprogramove pole": "X",
                }
            ]
        )
        contacts, issues = build_api_contacts_from_import_df(
            import_df=frame,
            api_system_field_map={"E-mail": "emailaddress"},
            custom_fields=[
                {"id": "7", "name": "Program A", "type": "text"},
                {"id": "8", "name": "Neprogramove pole", "type": "text"},
            ],
            strict_custom_fields=True,
            managed_custom_field_ids_allowlist={"7"},
        )

        self.assertEqual(len(issues), 0)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].get("__managed_custom_field_ids"), ["7"])
        self.assertEqual(contacts[0].get("customfields"), [{"id": "8", "value": "X"}])

    def test_build_api_contacts_managed_ids_empty_allowlist_disables_managed(self) -> None:
        frame = MiniImportFrame(
            [
                {
                    "E-mail": "a@example.com",
                    "Program A": "",
                }
            ]
        )
        contacts, issues = build_api_contacts_from_import_df(
            import_df=frame,
            api_system_field_map={"E-mail": "emailaddress"},
            custom_fields=[
                {"id": "7", "name": "Program A", "type": "text"},
            ],
            strict_custom_fields=True,
            managed_custom_field_ids_allowlist=set(),
        )

        self.assertEqual(len(issues), 0)
        self.assertEqual(len(contacts), 1)
        self.assertNotIn("__managed_custom_field_ids", contacts[0])

    def test_build_api_contacts_ignores_selected_missing_custom_fields(self) -> None:
        frame = MiniImportFrame(
            [
                {
                    "E-mail": "a@example.com",
                    "Jméno": "Jan",
                    "Tituly před jménem": "Ing.",
                    "Unknown X": "V1",
                },
            ]
        )
        contacts, issues = build_api_contacts_from_import_df(
            import_df=frame,
            api_system_field_map={"E-mail": "emailaddress", "Jméno": "name"},
            custom_fields=[],
            strict_custom_fields=True,
            ignore_missing_custom_for_columns=["Tituly před jménem"],
        )

        self.assertEqual(len(contacts), 1)
        missing_custom_details = [str(x.get("detail", "")) for x in issues if x.get("issue") == "missing_custom_field"]
        self.assertIn("Unknown X", missing_custom_details)
        self.assertNotIn("Tituly před jménem", missing_custom_details)

    def test_fetch_custom_field_names_uses_fallback_endpoint(self) -> None:
        responses = {
            ("/api/v3/customfields", 1): SmartEmailingApiError("404", status_code=404),
            (
                "/api/v3/custom-fields",
                1,
            ): {
                "data": [{"name": "Pole A"}, {"name": "Pole B"}],
                "meta": {"total_pages": 1},
            },
        }
        client = PagingFakeApiClient(responses)

        names = client.fetch_custom_field_names()

        self.assertEqual(names, ["Pole A", "Pole B"])
        called_paths = [call[1] for call in client.calls]
        self.assertIn("/api/v3/customfields", called_paths)
        self.assertIn("/api/v3/custom-fields", called_paths)

    def test_fetch_custom_field_names_handles_pagination(self) -> None:
        responses = {
            ("/api/v3/customfields", 1): {"data": [{"name": "Pole A"}], "meta": {"total_pages": 2}},
            ("/api/v3/customfields", 2): {"data": [{"name": "Pole B"}], "meta": {"total_pages": 2}},
        }
        client = PagingFakeApiClient(responses)

        names = client.fetch_custom_field_names()

        self.assertEqual(names, ["Pole A", "Pole B"])
        pages = [
            call[2].get("page")
            for call in client.calls
            if call[1] == "/api/v3/customfields" and isinstance(call[2], dict) and "page" in call[2]
        ]
        self.assertEqual(pages, [1, 2])

    def test_fetch_custom_field_names_allows_empty_result(self) -> None:
        responses = {
            ("/api/v3/customfields", 1): {"data": [], "meta": {"total_pages": 1}},
        }
        client = PagingFakeApiClient(responses)

        names = client.fetch_custom_field_names()

        self.assertEqual(names, [])
        called_paths = [call[1] for call in client.calls]
        self.assertIn("/api/v3/customfields", called_paths)

    def test_import_contacts_batch_tries_fallback_endpoint(self) -> None:
        client = ImportFakeApiClient()

        response, endpoint, payload_variant = client.import_contacts_batch(
            contacts=[{"emailaddress": "a@example.com"}],
            endpoint_candidates=["/api/v3/import"],
        )

        self.assertEqual(endpoint, "/api/v3/import")
        self.assertEqual(payload_variant, "import_data")
        self.assertEqual(response.get("status"), "ok")

    def test_import_contacts_canary_batches(self) -> None:
        client = ImportFakeApiClient()
        contacts = [{"emailaddress": f"user{i}@example.com"} for i in range(0, 120)]

        results = client.import_contacts_canary(
            contacts=contacts,
            canary_size=50,
            batch_size=40,
            endpoint_candidates=["/api/v3/import"],
        )

        self.assertEqual(len(results), 3)  # 50 + 40 + 30
        self.assertTrue(results[0].canary)
        self.assertFalse(results[1].canary)
        self.assertEqual(sum(x.sent_contacts for x in results), 120)

    def test_import_contacts_batch_prefers_import_data_payload(self) -> None:
        client = ImportDataFakeApiClient()

        response, endpoint, payload_variant = client.import_contacts_batch(
            contacts=[
                {
                    "emailaddress": "a@example.com",
                    "name": "Alice",
                    "customfields": [{"id": "7", "value": "CF1"}],
                    "contactlists": [{"id": "123", "status": "confirmed"}],
                }
            ],
            endpoint_candidates=["/api/v3/import"],
        )

        self.assertEqual(endpoint, "/api/v3/import")
        self.assertEqual(payload_variant, "import_data")
        self.assertEqual(response.get("status"), "ok")

    def test_import_contacts_batch_ignores_internal_keys(self) -> None:
        client = ImportDataFakeApiClient()

        response, endpoint, payload_variant = client.import_contacts_batch(
            contacts=[
                {
                    "emailaddress": "a@example.com",
                    "name": "Alice",
                    "__managed_custom_field_ids": ["10", "11"],
                    "customfields": [{"id": "10", "value": "X"}],
                    "contactlists": [{"id": "123", "status": "confirmed"}],
                }
            ],
            endpoint_candidates=["/api/v3/import"],
        )

        self.assertEqual(endpoint, "/api/v3/import")
        self.assertEqual(payload_variant, "import_data")
        self.assertEqual(response.get("status"), "ok")
        post_calls = [call for call in client.calls if call[0] == "POST" and call[1] == "/api/v3/import"]
        self.assertTrue(post_calls)
        sent_body = post_calls[0][3] or {}
        sent_rows = sent_body.get("data", [])
        self.assertTrue(sent_rows)
        self.assertNotIn("__managed_custom_field_ids", sent_rows[0])

    def test_fetch_custom_fields_falls_back_to_post_search(self) -> None:
        client = PostSearchFakeApiClient()

        fields = client.fetch_custom_fields()

        self.assertEqual([x["name"] for x in fields], ["Field 1", "Field 2"])
        called = [(m, p) for m, p, _, _ in client.calls]
        self.assertIn(("POST", "/api/v3/customfields/search"), called)

    def test_fetch_contact_lists_falls_back_to_post_search(self) -> None:
        client = ContactListPostSearchFakeApiClient()

        lists = client.fetch_contact_lists()

        self.assertEqual(lists, [{"id": "101", "name": "Stage A"}])
        called = [(m, p) for m, p, _, _ in client.calls]
        self.assertIn(("POST", "/api/v3/contactlists/search"), called)

    def test_fetch_contacts_in_list(self) -> None:
        responses = {
            (
                "/api/v3/contactlists/422/contacts",
                1,
            ): {
                "data": {
                    "items": [
                        {
                            "emailaddress": "user@example.com",
                            "name": "User",
                            "customfields": [{"id": 10, "value": "A"}],
                        }
                    ]
                }
            },
        }
        client = PagingFakeApiClient(responses)

        contacts = client.fetch_contacts_in_list(
            list_id="422",
            endpoint_templates=["/api/v3/contactlists/{list_id}/contacts"],
            search_endpoint_templates=[],
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["emailaddress"], "user@example.com")
        called_paths = [call[1] for call in client.calls]
        self.assertIn("/api/v3/contactlists/422/contacts", called_paths)

    def test_fetch_contacts_in_list_enriches_custom_fields_from_contact_detail(self) -> None:
        client = ContactDetailEnrichmentFakeApiClient()

        contacts = client.fetch_contacts_in_list(
            list_id="422",
            endpoint_templates=["/api/v3/contactlists/{list_id}/contacts"],
            search_endpoint_templates=[],
            detail_endpoint_templates=["/api/v3/contacts/{contact_id}", "/api/v3/contact/{contact_id}"],
            enrich_only_email_keys={"user@example.com"},
            custom_field_values_endpoint_candidates=["/api/v3/contact-customfield-values"],
            custom_field_values_search_endpoint_candidates=[],
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["emailaddress"], "user@example.com")
        self.assertEqual(contacts[0]["customfields"], [{"id": "10", "value": "A"}])
        called_paths = [call[1] for call in client.calls]
        self.assertIn("/api/v3/contacts/101", called_paths)

    def test_fetch_contacts_in_list_enriches_custom_fields_from_contacts_fallback(self) -> None:
        client = ContactFallbackByEmailFakeApiClient()

        contacts = client.fetch_contacts_in_list(
            list_id="422",
            endpoint_templates=["/api/v3/contactlists/{list_id}/contacts"],
            search_endpoint_templates=[],
            detail_endpoint_templates=["/api/v3/contacts/{contact_id}"],
            enrich_only_email_keys={"user@example.com"},
            contacts_endpoint_candidates=["/api/v3/contacts"],
            contacts_search_endpoint_candidates=[],
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["emailaddress"], "user@example.com")
        self.assertEqual(contacts[0]["customfields"], [{"id": "10", "value": "A"}])
        called_paths = [call[1] for call in client.calls]
        self.assertIn("/api/v3/contacts", called_paths)

    def test_fetch_contacts_by_emails_uses_search_fallback(self) -> None:
        client = ContactByEmailSearchFakeApiClient()

        found = client.fetch_contacts_by_emails(
            email_keys={"user@example.com"},
            endpoint_candidates=["/api/v3/contacts"],
            search_endpoint_candidates=["/api/v3/contacts/search"],
        )

        self.assertIn("user@example.com", found)
        self.assertEqual(found["user@example.com"]["customfields"], [{"id": "10", "value": "A"}])
        called = [(method, path) for method, path, _, _ in client.calls]
        self.assertIn(("POST", "/api/v3/contacts/search"), called)

    def test_fetch_blacklisted_email_keys(self) -> None:
        client = ContactBlacklistedLookupFakeApiClient()

        blacklisted = client.fetch_blacklisted_email_keys(
            email_keys={"black@example.com", "ok@example.com"},
            endpoint_candidates=["/api/v3/contacts"],
            search_endpoint_candidates=[],
            max_workers=1,
        )

        self.assertEqual(blacklisted, {"black@example.com"})

    def test_fetch_contacts_in_list_targeted_partial_failure_falls_back_to_full_list(self) -> None:
        client = TargetedListSearchPartialFailureFakeApiClient()

        contacts = client.fetch_contacts_in_list(
            list_id="422",
            endpoint_templates=["/api/v3/contactlists/{list_id}/contacts"],
            search_endpoint_templates=["/api/v3/contactlists/{list_id}/contacts/search"],
            detail_endpoint_templates=[],
            enrich_only_email_keys={"ok@example.com", "broken@example.com"},
            target_email_batch_size=1,
            read_parallel_workers=1,
            prefer_targeted_search=True,
        )

        emails = sorted([str(x.get("emailaddress", "")).strip().casefold() for x in contacts])
        self.assertEqual(emails, ["broken@example.com", "ok@example.com"])
        called = [(method, path) for method, path, _, _ in client.calls]
        self.assertIn(("POST", "/api/v3/contactlists/422/contacts/search"), called)
        self.assertIn(("GET", "/api/v3/contactlists/422/contacts"), called)

    def test_fetch_custom_field_values_for_contacts(self) -> None:
        client = ContactCustomFieldValuesFakeApiClient()
        values = client.fetch_custom_field_values_for_contacts(
            contact_ids={"101"},
            endpoint_candidates=["/api/v3/contact-customfield-values"],
            search_endpoint_candidates=[],
        )
        self.assertIn("101", values)
        self.assertEqual(
            values["101"],
            [
                {"id": "10", "value": "A", "pair_id": "5001"},
                {"id": "11", "value": "B", "pair_id": "5002"},
            ],
        )

    def test_create_custom_field(self) -> None:
        client = CreateCustomFieldFakeApiClient()
        created = client.create_custom_field(
            name="PNEW_APP",
            field_type="text",
            endpoint_candidates=["/api/v3/customfields"],
        )
        self.assertEqual(created.get("name"), "PNEW_APP")
        self.assertEqual(created.get("type"), "text")


if __name__ == "__main__":
    unittest.main()
