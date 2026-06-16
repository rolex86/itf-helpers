from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests


def _scan_html(text: str) -> dict[str, bool]:
    lowered = str(text or "").lower()
    return {
        "has_insight_tag": "_linkedin_partner_id" in lowered or "snap.licdn.com/li.lms-analytics/insight.min.js" in lowered,
        "has_lintrk": "lintrk(" in lowered or "window.lintrk" in lowered,
    }


def scan_landing_pages(urls: list[str], *, timeout_seconds: int = 20) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    session = requests.Session()
    seen: set[str] = set()

    for url in urls:
        candidate = str(url or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            response = session.get(candidate, timeout=timeout_seconds, allow_redirects=True)
            scan = _scan_html(response.text)
            final_url = response.url
            parsed = urlsplit(final_url)
            rows.append(
                {
                    "source_url": candidate,
                    "final_url": final_url,
                    "final_domain": parsed.netloc.lower(),
                    "status_code": response.status_code,
                    "redirected": candidate != final_url,
                    "has_insight_tag": scan["has_insight_tag"],
                    "has_lintrk": scan["has_lintrk"],
                    "gtm_container_id": "",
                    "canonical_domain": parsed.netloc.lower(),
                }
            )
        except Exception as exc:
            warnings.append(f"Landing page scan selhal pro {candidate}: {exc}")

    return rows, warnings


def build_utm_audit_rows(
    landing_rows: list[dict[str, Any]],
    *,
    expected_source: str,
    expected_medium: str,
    expected_domains: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in landing_rows:
        landing_page_url = str(row.get("source_url") or row.get("landing_page_url") or "")
        parsed = urlsplit(landing_page_url)
        query = parse_qs(parsed.query)
        utm_source = (query.get("utm_source") or [""])[0]
        utm_medium = (query.get("utm_medium") or [""])[0]
        utm_campaign = (query.get("utm_campaign") or [""])[0]
        utm_content = (query.get("utm_content") or [""])[0]
        utm_term = (query.get("utm_term") or [""])[0]
        final_domain = parsed.netloc.lower()
        issues: list[tuple[str, str, str]] = []
        if not utm_source:
            issues.append(("missing_utm_source", "warning", "Doplň utm_source=linkedin."))
        elif expected_source and utm_source.lower() != expected_source.lower():
            issues.append(("utm_source_mismatch", "warning", f"Použij utm_source={expected_source}."))
        if not utm_medium:
            issues.append(("missing_utm_medium", "warning", f"Doplň utm_medium={expected_medium}."))
        elif expected_medium and utm_medium.lower() != expected_medium.lower():
            issues.append(("utm_medium_mismatch", "warning", f"Použij utm_medium={expected_medium}."))
        if not utm_campaign:
            issues.append(("missing_utm_campaign", "warning", "Doplň utm_campaign."))
        if expected_domains and final_domain and final_domain not in expected_domains:
            issues.append(("domain_mismatch", "error", "Landing page míří mimo očekávané domény kontextu."))
        if not issues:
            issues = [("ok", "info", "UTM i doména vypadají v pořádku.")]
        for issue_code, severity, recommendation in issues:
            rows.append(
                {
                    "account_id": row.get("account_id", ""),
                    "campaign_id": row.get("campaign_id", ""),
                    "campaign_name": row.get("campaign_name", ""),
                    "creative_id": row.get("creative_id", ""),
                    "creative_name": row.get("creative_name", ""),
                    "landing_page_url": landing_page_url,
                    "final_domain": final_domain,
                    "utm_source": utm_source,
                    "utm_medium": utm_medium,
                    "utm_campaign": utm_campaign,
                    "utm_content": utm_content,
                    "utm_term": utm_term,
                    "issue_code": issue_code,
                    "severity": severity,
                    "recommendation": recommendation,
                }
            )
    return rows

