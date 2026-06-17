from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests


GTM_PATTERN = re.compile(r"GTM-[A-Z0-9]+", re.IGNORECASE)
CANONICAL_PATTERN = re.compile(
    r"""<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)["']""",
    re.IGNORECASE,
)


def _normalize_domain(value: str) -> str:
    domain = str(value or "").strip().lower()

    if "://" in domain:
        domain = urlsplit(domain).netloc.lower()

    domain = domain.split("/")[0].split(":")[0].strip()
    return domain.removeprefix("www.")


def _scan_html(text: str) -> dict[str, Any]:
    html = str(text or "")
    lowered = html.lower()

    gtm_matches = sorted({match.upper() for match in GTM_PATTERN.findall(html)})

    canonical_url = ""
    canonical_match = CANONICAL_PATTERN.search(html)
    if canonical_match:
        canonical_url = canonical_match.group(1).strip()

    return {
        "has_insight_tag": "_linkedin_partner_id" in lowered
        or "snap.licdn.com/li.lms-analytics/insight.min.js" in lowered
        or "linkedin_data_partner_id" in lowered,
        "has_lintrk": "lintrk(" in lowered or "window.lintrk" in lowered,
        "gtm_container_id": ",".join(gtm_matches),
        "canonical_url": canonical_url,
        "canonical_domain": _normalize_domain(urlsplit(canonical_url).netloc) if canonical_url else "",
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
            response = session.get(
                candidate,
                timeout=timeout_seconds,
                allow_redirects=True,
                headers={
                    "User-Agent": "ITFutureLinkedInAudit/1.0",
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
                    "final_domain": _normalize_domain(parsed.netloc),
                    "status_code": response.status_code,
                    "redirected": candidate.rstrip("/") != final_url.rstrip("/"),
                    "redirect_chain": redirect_chain,
                    "redirect_count": max(0, len(redirect_chain) - 1),
                    "has_insight_tag": scan["has_insight_tag"],
                    "has_lintrk": scan["has_lintrk"],
                    "gtm_container_id": scan["gtm_container_id"],
                    "canonical_url": scan["canonical_url"],
                    "canonical_domain": scan["canonical_domain"] or _normalize_domain(parsed.netloc),
                }
            )
        except Exception as exc:
            warnings.append(f"Landing page scan selhal pro {candidate}: {exc}")

            parsed = urlsplit(candidate)
            rows.append(
                {
                    "source_url": candidate,
                    "final_url": "",
                    "final_domain": _normalize_domain(parsed.netloc),
                    "status_code": 0,
                    "redirected": False,
                    "redirect_chain": [],
                    "redirect_count": 0,
                    "has_insight_tag": False,
                    "has_lintrk": False,
                    "gtm_container_id": "",
                    "canonical_url": "",
                    "canonical_domain": _normalize_domain(parsed.netloc),
                    "scan_error": str(exc),
                }
            )

    return rows, warnings


def build_utm_audit_rows(
    landing_rows: list[dict[str, Any]],
    *,
    expected_source: str,
    expected_medium: str,
    expected_domains: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized_expected_domains = {
        _normalize_domain(domain)
        for domain in expected_domains
        if _normalize_domain(domain)
    }

    for row in landing_rows:
        landing_page_url = str(row.get("source_url") or row.get("landing_page_url") or "").strip()
        final_url = str(row.get("final_url") or landing_page_url or "").strip()

        if not landing_page_url and not final_url:
            continue

        parsed_source = urlsplit(landing_page_url)
        parsed_final = urlsplit(final_url)

        query = parse_qs(parsed_source.query or parsed_final.query)

        utm_source = (query.get("utm_source") or [""])[0]
        utm_medium = (query.get("utm_medium") or [""])[0]
        utm_campaign = (query.get("utm_campaign") or [""])[0]
        utm_content = (query.get("utm_content") or [""])[0]
        utm_term = (query.get("utm_term") or [""])[0]

        source_domain = _normalize_domain(parsed_source.netloc)
        final_domain = _normalize_domain(row.get("final_domain") or parsed_final.netloc or parsed_source.netloc)
        status_code = int(row.get("status_code") or 0)

        issues: list[tuple[str, str, str]] = []

        if status_code >= 400 or status_code == 0:
            issues.append(("landing_status_error", "error", "Oprav cílovou URL nebo redirect chain."))

        if not utm_source:
            issues.append(("missing_utm_source", "warning", f"Doplň utm_source={expected_source or 'linkedin'}."))
        elif expected_source and utm_source.lower() != expected_source.lower():
            issues.append(("utm_source_mismatch", "warning", f"Použij utm_source={expected_source}."))

        if not utm_medium:
            issues.append(("missing_utm_medium", "warning", f"Doplň utm_medium={expected_medium}."))
        elif expected_medium and utm_medium.lower() != expected_medium.lower():
            issues.append(("utm_medium_mismatch", "warning", f"Použij utm_medium={expected_medium}."))

        if not utm_campaign:
            issues.append(("missing_utm_campaign", "warning", "Doplň utm_campaign."))

        if normalized_expected_domains and final_domain and final_domain not in normalized_expected_domains:
            issues.append(("domain_mismatch", "error", "Landing page míří mimo očekávané domény kontextu."))

        if source_domain and final_domain and source_domain != final_domain:
            issues.append(("redirect_domain_changed", "warning", "Redirect vede na jinou doménu než původní cílová URL."))

        if not row.get("has_insight_tag") and final_url:
            issues.append(("missing_insight_tag", "warning", "Zkontroluj LinkedIn Insight Tag na landing page."))

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
                    "final_url": final_url,
                    "source_domain": source_domain,
                    "final_domain": final_domain,
                    "status_code": status_code,
                    "redirected": bool(row.get("redirected")),
                    "redirect_count": int(row.get("redirect_count") or 0),
                    "utm_source": utm_source,
                    "utm_medium": utm_medium,
                    "utm_campaign": utm_campaign,
                    "utm_content": utm_content,
                    "utm_term": utm_term,
                    "has_insight_tag": bool(row.get("has_insight_tag")),
                    "has_lintrk": bool(row.get("has_lintrk")),
                    "gtm_container_id": row.get("gtm_container_id", ""),
                    "issue_code": issue_code,
                    "severity": severity,
                    "recommendation": recommendation,
                }
            )

    return rows
