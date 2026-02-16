from __future__ import annotations

import base64
import unittest
from typing import Any

from src.smartemailing_api import (
    SmartEmailingApiClient,
    SmartEmailingApiError,
    SmartEmailingCredentials,
    build_basic_auth_header,
    combine_schema_columns,
    extract_custom_field_names,
)


class FakeApiClient(SmartEmailingApiClient):
    def __init__(self, responses: dict[tuple[str, int], Any]) -> None:
        super().__init__(SmartEmailingCredentials(username="user", api_key="key"))
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, query))
        page = int((query or {}).get("page", 1))
        response = self.responses.get((path, page))
        if isinstance(response, Exception):
            raise response
        if response is None:
            return {"data": []}
        return response


class SmartEmailingApiTests(unittest.TestCase):
    def test_build_basic_auth_header(self) -> None:
        header = build_basic_auth_header("my-user", "my-key")
        self.assertTrue(header.startswith("Basic "))
        decoded = base64.b64decode(header.replace("Basic ", "")).decode("utf-8")
        self.assertEqual(decoded, "my-user:my-key")

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
        client = FakeApiClient(responses)

        names = client.fetch_custom_field_names()

        self.assertEqual(names, ["Pole A", "Pole B"])
        self.assertEqual(client.calls[0][1], "/api/v3/customfields")
        self.assertEqual(client.calls[1][1], "/api/v3/custom-fields")

    def test_fetch_custom_field_names_handles_pagination(self) -> None:
        responses = {
            ("/api/v3/customfields", 1): {"data": [{"name": "Pole A"}], "meta": {"total_pages": 2}},
            ("/api/v3/customfields", 2): {"data": [{"name": "Pole B"}], "meta": {"total_pages": 2}},
        }
        client = FakeApiClient(responses)

        names = client.fetch_custom_field_names()

        self.assertEqual(names, ["Pole A", "Pole B"])
        pages = [call[2].get("page") for call in client.calls if call[1] == "/api/v3/customfields"]
        self.assertEqual(pages, [1, 2])


if __name__ == "__main__":
    unittest.main()
