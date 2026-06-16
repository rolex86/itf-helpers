from __future__ import annotations

from typing import Any

from app.integrations.linkedin import web_scan


class DummyHistoryItem:
    def __init__(self, url: str) -> None:
        self.url = url


class DummyResponse:
    def __init__(
        self,
        *,
        text: str,
        url: str,
        status_code: int = 200,
        history: list[DummyHistoryItem] | None = None,
    ) -> None:
        self.text = text
        self.url = url
        self.status_code = status_code
        self.history = history or []


class DummySession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        timeout: int,
        allow_redirects: bool,
        headers: dict[str, str],
    ) -> DummyResponse:
        self.calls.append(
            {
                "url": url,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
                "headers": headers,
            }
        )

        if "fail" in url:
            raise RuntimeError("request failed")

        html = """
        <html>
          <head>
            <script>_linkedin_partner_id = "123456";</script>
            <script src="https://snap.licdn.com/li.lms-analytics/insight.min.js"></script>
            <script>window.lintrk('track', { conversion_id: 987654 });</script>
            <script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>
            <link rel="canonical" href="https://www.example.cz/canonical-page/" />
          </head>
          <body>OK</body>
        </html>
        """

        if "redirect" in url:
            return DummyResponse(
                text=html,
                url="https://www.example.cz/final-page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
                status_code=200,
                history=[
                    DummyHistoryItem(
                        "https://www.example.cz/redirect/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test"
                    )
                ],
            )

        if "missing-tag" in url:
            return DummyResponse(
                text="<html><head><title>No tag</title></head><body>No tag</body></html>",
                url=url,
                status_code=200,
            )

        if "not-found" in url:
            return DummyResponse(
                text="<html><body>404</body></html>",
                url=url,
                status_code=404,
            )

        return DummyResponse(
            text=html,
            url=url,
            status_code=200,
        )


def test_scan_landing_pages_detects_tags_gtm_canonical_and_redirects(monkeypatch) -> None:
    dummy_session = DummySession()
    monkeypatch.setattr(web_scan.requests, "Session", lambda: dummy_session)

    rows, warnings = web_scan.scan_landing_pages(
        [
            "https://www.example.cz/redirect/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
        ],
        timeout_seconds=15,
    )

    assert warnings == []
    assert len(rows) == 1

    row = rows[0]
    assert row["source_url"] == "https://www.example.cz/redirect/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test"
    assert row["final_url"] == "https://www.example.cz/final-page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test"
    assert row["final_domain"] == "example.cz"
    assert row["status_code"] == 200
    assert row["redirected"] is True
    assert row["redirect_count"] == 1
    assert row["redirect_chain"] == [
        "https://www.example.cz/redirect/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
        "https://www.example.cz/final-page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
    ]
    assert row["has_insight_tag"] is True
    assert row["has_lintrk"] is True
    assert row["gtm_container_id"] == "GTM-ABC123"
    assert row["canonical_url"] == "https://www.example.cz/canonical-page/"
    assert row["canonical_domain"] == "example.cz"

    assert dummy_session.calls[0]["timeout"] == 15
    assert dummy_session.calls[0]["allow_redirects"] is True
    assert dummy_session.calls[0]["headers"]["User-Agent"] == "ITFutureLinkedInAudit/1.0"


def test_scan_landing_pages_dedupes_urls(monkeypatch) -> None:
    dummy_session = DummySession()
    monkeypatch.setattr(web_scan.requests, "Session", lambda: dummy_session)

    rows, warnings = web_scan.scan_landing_pages(
        [
            "https://www.example.cz/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
            "https://www.example.cz/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
            "",
        ]
    )

    assert warnings == []
    assert len(rows) == 1
    assert len(dummy_session.calls) == 1


def test_scan_landing_pages_adds_error_row_when_request_fails(monkeypatch) -> None:
    dummy_session = DummySession()
    monkeypatch.setattr(web_scan.requests, "Session", lambda: dummy_session)

    rows, warnings = web_scan.scan_landing_pages(["https://www.example.cz/fail/"])

    assert len(rows) == 1
    assert len(warnings) == 1
    assert "Landing page scan selhal pro https://www.example.cz/fail/" in warnings[0]
    assert "request failed" in warnings[0]

    row = rows[0]
    assert row["source_url"] == "https://www.example.cz/fail/"
    assert row["final_url"] == ""
    assert row["final_domain"] == "example.cz"
    assert row["status_code"] == 0
    assert row["has_insight_tag"] is False
    assert row["has_lintrk"] is False
    assert row["scan_error"] == "request failed"


def test_build_utm_audit_rows_returns_ok_for_valid_utm_domain_and_tag() -> None:
    rows = web_scan.build_utm_audit_rows(
        [
            {
                "source_url": "https://www.example.cz/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=leadgen&utm_content=ad1&utm_term=test",
                "final_url": "https://www.example.cz/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=leadgen&utm_content=ad1&utm_term=test",
                "final_domain": "example.cz",
                "status_code": 200,
                "redirected": False,
                "redirect_count": 0,
                "has_insight_tag": True,
                "has_lintrk": True,
                "gtm_container_id": "GTM-ABC123",
                "account_id": "123456",
                "campaign_id": "200",
                "campaign_name": "Lead campaign",
                "creative_id": "300",
                "creative_name": "Creative",
            }
        ],
        expected_source="linkedin",
        expected_medium="paid-social",
        expected_domains=["www.example.cz"],
    )

    assert len(rows) == 1
    assert rows[0]["issue_code"] == "ok"
    assert rows[0]["severity"] == "info"
    assert rows[0]["utm_source"] == "linkedin"
    assert rows[0]["utm_medium"] == "paid-social"
    assert rows[0]["utm_campaign"] == "leadgen"
    assert rows[0]["utm_content"] == "ad1"
    assert rows[0]["utm_term"] == "test"
    assert rows[0]["final_domain"] == "example.cz"
    assert rows[0]["has_insight_tag"] is True
    assert rows[0]["gtm_container_id"] == "GTM-ABC123"


def test_build_utm_audit_rows_reports_missing_and_mismatched_utm_values() -> None:
    rows = web_scan.build_utm_audit_rows(
        [
            {
                "source_url": "https://www.example.cz/page/?utm_source=facebook&utm_medium=cpc",
                "final_url": "https://www.example.cz/page/?utm_source=facebook&utm_medium=cpc",
                "final_domain": "example.cz",
                "status_code": 200,
                "has_insight_tag": True,
            }
        ],
        expected_source="linkedin",
        expected_medium="paid-social",
        expected_domains=["example.cz"],
    )

    issue_codes = {row["issue_code"] for row in rows}

    assert "utm_source_mismatch" in issue_codes
    assert "utm_medium_mismatch" in issue_codes
    assert "missing_utm_campaign" in issue_codes


def test_build_utm_audit_rows_reports_domain_redirect_status_and_missing_tag_issues() -> None:
    rows = web_scan.build_utm_audit_rows(
        [
            {
                "source_url": "https://www.example.cz/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
                "final_url": "https://other.example/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
                "final_domain": "other.example",
                "status_code": 404,
                "redirected": True,
                "redirect_count": 1,
                "has_insight_tag": False,
                "has_lintrk": False,
            }
        ],
        expected_source="linkedin",
        expected_medium="paid-social",
        expected_domains=["example.cz"],
    )

    issue_codes = {row["issue_code"] for row in rows}

    assert "landing_status_error" in issue_codes
    assert "domain_mismatch" in issue_codes
    assert "redirect_domain_changed" in issue_codes
    assert "missing_insight_tag" in issue_codes

    status_issue = [row for row in rows if row["issue_code"] == "landing_status_error"][0]
    assert status_issue["severity"] == "error"
    assert status_issue["status_code"] == 404
    assert status_issue["redirected"] is True
    assert status_issue["redirect_count"] == 1


def test_build_utm_audit_rows_uses_final_url_query_when_source_query_is_empty() -> None:
    rows = web_scan.build_utm_audit_rows(
        [
            {
                "source_url": "https://www.example.cz/page/",
                "final_url": "https://www.example.cz/page/?utm_source=linkedin&utm_medium=paid-social&utm_campaign=test",
                "final_domain": "example.cz",
                "status_code": 200,
                "has_insight_tag": True,
            }
        ],
        expected_source="linkedin",
        expected_medium="paid-social",
        expected_domains=["example.cz"],
    )

    assert len(rows) == 1
    assert rows[0]["issue_code"] == "ok"
    assert rows[0]["utm_source"] == "linkedin"
    assert rows[0]["utm_medium"] == "paid-social"
    assert rows[0]["utm_campaign"] == "test"