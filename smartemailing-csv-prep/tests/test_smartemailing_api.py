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

        if method == "POST":
            if path == "/api/v3/import":
                raise SmartEmailingApiError("not found", status_code=404)

            if path == "/api/v3/imports":
                # accept only the "flat" payload variant
                if isinstance(body, dict) and "settings" in body and "contacts" in body:
                    return {"status": "ok", "sent": len(body.get("contacts", []))}
                raise SmartEmailingApiError("bad payload", status_code=400)

        return {"status": "ok"}


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
        lists_payload = {"data": {"contactlists": [{"id": 1, "name": "Staging"}]}}

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
        self.assertEqual(client.calls[0][1], "/api/v3/customfields")
        self.assertEqual(client.calls[1][1], "/api/v3/custom-fields")

    def test_fetch_custom_field_names_handles_pagination(self) -> None:
        responses = {
            ("/api/v3/customfields", 1): {"data": [{"name": "Pole A"}], "meta": {"total_pages": 2}},
            ("/api/v3/customfields", 2): {"data": [{"name": "Pole B"}], "meta": {"total_pages": 2}},
        }
        client = PagingFakeApiClient(responses)

        names = client.fetch_custom_field_names()

        self.assertEqual(names, ["Pole A", "Pole B"])
        pages = [call[2].get("page") for call in client.calls if call[1] == "/api/v3/customfields"]
        self.assertEqual(pages, [1, 2])

    def test_import_contacts_batch_tries_fallback_endpoint(self) -> None:
        client = ImportFakeApiClient()

        response, endpoint, payload_variant = client.import_contacts_batch(
            contacts=[{"emailaddress": "a@example.com"}],
            endpoint_candidates=["/api/v3/import", "/api/v3/imports"],
        )

        self.assertEqual(endpoint, "/api/v3/imports")
        self.assertEqual(payload_variant, "flat")
        self.assertEqual(response.get("status"), "ok")

    def test_import_contacts_canary_batches(self) -> None:
        client = ImportFakeApiClient()
        contacts = [{"emailaddress": f"user{i}@example.com"} for i in range(0, 120)]

        results = client.import_contacts_canary(
            contacts=contacts,
            canary_size=50,
            batch_size=40,
            endpoint_candidates=["/api/v3/imports"],
        )

        self.assertEqual(len(results), 3)  # 50 + 40 + 30
        self.assertTrue(results[0].canary)
        self.assertFalse(results[1].canary)
        self.assertEqual(sum(x.sent_contacts for x in results), 120)


if __name__ == "__main__":
    unittest.main()
