from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

from app.integrations.sklik.normalizers import normalize_domain


CANONICAL_PATTERN = re.compile(
    r"""<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)["']""",
    re.IGNORECASE,
)


def _scan_html(text: str) -> dict[str, Any]:
    html = str(text or "")
    lowered = html.lower()
    canonical_url = ""
    match = CANONICAL_PATTERN.search(html)
    if match:
        canonical_url = match.group(1).strip()
    return {
        "has_sem": "sul.js" in lowered or "seznam event measurement" in lowered,
        "has_sklik_conversion": "c.seznam.cz" in lowered or "h.seznam.cz" in lowered,
        "has_old_retargeting": "retargeting" in lowered and "sul.js" not in lowered,
        "robots_noindex": "noindex" in lowered and "robots" in lowered,
        "canonical_url": canonical_url,
        "canonical_domain": normalize_domain(canonical_url) if canonical_url else "",
    }


def _domain_matches_expected(final_domain: str, expected_domains: set[str]) -> bool:
    domain = normalize_domain(final_domain)
    if not expected_domains:
        return True
    if not domain:
        return False

    for expected in expected_domains:
        expected = normalize_domain(expected)
        if not expected:
            continue
        if domain == expected or domain.endswith("." + expected):
            return True

    return False


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
            response = session.get(
                candidate,
                timeout=timeout_seconds,
                allow_redirects=True,
                headers={
                    "User-Agent": "ITFutureSklikAudit/1.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            scan = _scan_html(response.text)
            final_url = response.url
            parsed = urlsplit(final_url)
            redirect_chain = [item.url for item in response.history] + [final_url]
            rows.append(
                {
                    "source_url": candidate,
                    "final_url": final_url,
                    "final_domain": normalize_domain(parsed.netloc),
                    "status_code": response.status_code,
                    "redirected": candidate.rstrip("/") != final_url.rstrip("/"),
                    "redirect_chain": redirect_chain,
                    "redirect_count": max(0, len(redirect_chain) - 1),
                    "has_sem": scan["has_sem"],
                    "has_sklik_conversion": scan["has_sklik_conversion"],
                    "has_old_retargeting": scan["has_old_retargeting"],
                    "robots_noindex": scan["robots_noindex"],
                    "canonical_url": scan["canonical_url"],
                    "canonical_domain": scan["canonical_domain"] or normalize_domain(parsed.netloc),
                }
            )
        except Exception as exc:
            warnings.append(f"Landing page scan selhal pro {candidate}: {exc}")
            parsed = urlsplit(candidate)
            rows.append(
                {
                    "source_url": candidate,
                    "final_url": "",
                    "final_domain": normalize_domain(parsed.netloc),
                    "status_code": 0,
                    "redirected": False,
                    "redirect_chain": [],
                    "redirect_count": 0,
                    "has_sem": False,
                    "has_sklik_conversion": False,
                    "has_old_retargeting": False,
                    "robots_noindex": False,
                    "canonical_url": "",
                    "canonical_domain": normalize_domain(parsed.netloc),
                    "scan_error": str(exc),
                }
            )

    return rows, warnings


def build_utm_audit_rows(
    landing_rows: list[dict[str, Any]],
    *,
    expected_sources: list[str],
    expected_mediums: list[str],
    expected_domains: list[str],
) -> list[dict[str, Any]]:
    normalized_expected_domains = {
        normalize_domain(value)
        for value in expected_domains
        if normalize_domain(value)
    }
    normalized_sources = {
        str(value or "").strip().lower()
        for value in expected_sources
        if str(value or "").strip()
    }
    normalized_mediums = {
        str(value or "").strip().lower()
        for value in expected_mediums
        if str(value or "").strip()
    }

    rows: list[dict[str, Any]] = []

    for row in landing_rows:
        source_url = str(row.get("source_url") or row.get("landing_page_url") or "").strip()
        final_url = str(row.get("final_url") or source_url or "").strip()
        if not source_url and not final_url:
            continue

        parsed_source = urlsplit(source_url)
        parsed_final = urlsplit(final_url)

        final_domain = normalize_domain(row.get("final_domain") or parsed_final.netloc or parsed_source.netloc)
        is_expected_domain = _domain_matches_expected(final_domain, normalized_expected_domains)

        query = parse_qs(parsed_source.query or parsed_final.query)
        utm_source = (query.get("utm_source") or [""])[0]
        utm_medium = (query.get("utm_medium") or [""])[0]
        utm_campaign = (query.get("utm_campaign") or [""])[0]

        base_record = {
            "campaign_id": row.get("campaign_id", ""),
            "group_id": row.get("group_id", ""),
            "ad_id": row.get("ad_id", row.get("banner_id", "")),
            "landing_page_url": source_url,
            "final_url": final_url,
            "final_domain": final_domain,
            "status_code": int(row.get("status_code") or 0),
            "utm_source": utm_source,
            "utm_medium": utm_medium,
            "utm_campaign": utm_campaign,
            "is_expected_domain": is_expected_domain,
            "expected_domains": sorted(normalized_expected_domains),
        }

        # Smíšený Sklik účet:
        # Pokud reklama míří na jinou doménu než domény aktuálního contextu,
        # nehlásíme k ní 3x missing UTM. Vypíšeme jeden čistý "out of context" řádek.
        if normalized_expected_domains and final_domain and not is_expected_domain:
            rows.append(
                {
                    **base_record,
                    "issue_code": "out_of_context_domain",
                    "severity": "info",
                    "recommendation": "Landing page patří mimo expected domains tohoto contextu; UTM audit pro tento řádek byl přeskočen.",
                }
            )
            continue

        issues: list[tuple[str, str, str]] = []

        if not utm_source:
            issues.append(("missing_utm_source", "warning", "Doplň utm_source."))
        elif normalized_sources and utm_source.lower() not in normalized_sources:
            issues.append(("utm_source_mismatch", "warning", "Použij očekávaný utm_source pro Sklik/Seznam."))

        if not utm_medium:
            issues.append(("missing_utm_medium", "warning", "Doplň utm_medium."))
        elif normalized_mediums and utm_medium.lower() not in normalized_mediums:
            issues.append(("utm_medium_mismatch", "warning", "Použij očekávaný utm_medium pro Sklik/Seznam."))

        if not utm_campaign:
            issues.append(("missing_utm_campaign", "warning", "Doplň utm_campaign."))

        if not issues:
            issues.append(("ok", "info", "UTM i doména vypadají v pořádku."))

        for issue_code, severity, recommendation in issues:
            rows.append(
                {
                    **base_record,
                    "issue_code": issue_code,
                    "severity": severity,
                    "recommendation": recommendation,
                }
            )

    return rows