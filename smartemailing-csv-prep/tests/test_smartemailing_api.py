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
