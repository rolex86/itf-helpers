from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse

from src.smartemailing_api import SmartEmailingApiClient, SmartEmailingCredentials


class MockSeHandler(BaseHTTPRequestHandler):
    server_version = "MockSE/1.0"

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0:
            return {}
        raw = self.rfile.read(size)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = parse.urlparse(self.path)
        if parsed.path == "/api/v3/ping":
            self._write_json(200, {"status": "ok"})
            return

        if parsed.path == "/api/v3/customfields":
            query = parse.parse_qs(parsed.query)
            page = int(query.get("page", ["1"])[0])
            if page == 1:
                self._write_json(
                    200,
                    {
                        "data": [{"id": 1, "name": "Field A"}],
                        "meta": {"total_pages": 2},
                    },
                )
                return
            self._write_json(
                200,
                {
                    "data": [{"id": 2, "name": "Field B"}],
                    "meta": {"total_pages": 2},
                },
            )
            return

        if parsed.path == "/api/v3/contactlists":
            self._write_json(200, {"data": [{"id": 100, "name": "Staging List"}], "meta": {"total_pages": 1}})
            return

        self._write_json(404, {"status": "error", "message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = parse.urlparse(self.path)
        if parsed.path == "/api/v3/import":
            self._write_json(404, {"status": "error", "message": "unknown endpoint"})
            return

        if parsed.path == "/api/v3/imports":
            body = self._read_json_body()
            contacts = body.get("contacts", [])
            if not isinstance(contacts, list):
                self._write_json(400, {"status": "error", "message": "invalid payload"})
                return
            self._write_json(200, {"status": "ok", "imported": len(contacts)})
            return

        self._write_json(404, {"status": "error", "message": "not found"})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Silence mock server logs in tests.
        return


class SmartEmailingApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockSeHandler)
        except PermissionError as exc:
            raise unittest.SkipTest(f"Socket bind not permitted in this environment: {exc}")
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.server_thread.join(timeout=3)

    def test_end_to_end_mock_flow(self) -> None:
        client = SmartEmailingApiClient(
            SmartEmailingCredentials(
                username="user",
                api_key="key",
                base_url=self.base_url,
            )
        )

        ping = client.ping()
        custom_fields = client.fetch_custom_fields()
        lists = client.fetch_contact_lists()
        list_id = client.resolve_contact_list_id("Staging List")
        batches = client.import_contacts_canary(
            contacts=[{"emailaddress": "a@example.com"}, {"emailaddress": "b@example.com"}],
            canary_size=1,
            batch_size=10,
            endpoint_candidates=["/api/v3/import", "/api/v3/imports"],
        )

        self.assertEqual(ping.get("status"), "ok")
        self.assertEqual([x["name"] for x in custom_fields], ["Field A", "Field B"])
        self.assertEqual(len(lists), 1)
        self.assertEqual(list_id, "100")
        self.assertEqual(len(batches), 2)
        self.assertEqual(sum(x.sent_contacts for x in batches), 2)


if __name__ == "__main__":
    unittest.main()
