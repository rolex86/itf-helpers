from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from app.config.env_settings import GoogleAdsEnvConfig
from app.config.settings import FlagsConfig, PageSpeedConfig
from app.google_ads.report_definitions import get_report_definition
from app.pagespeed.cache import PageSpeedCache
from app.pagespeed.client import PageSpeedApiClient, PageSpeedApiError, PageSpeedClientConfig


LOGGER = logging.getLogger("google_ads_audit_exporter")


@dataclass(slots=True)
class PageSpeedExportResult:
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    report_notes: dict[str, list[str]] = field(default_factory=dict)
    report_warning_keys: set[str] = field(default_factory=set)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_report() -> pd.DataFrame:
    report = get_report_definition("pagespeed_landing_pages")
    return pd.DataFrame(columns=report.aliases)


def _safe_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_PARAMS = {
    "gad_source",
    "gad_campaignid",
    "gclid",
    "gbraid",
    "wbraid",
    "fbclid",
    "msclkid",
}


def _normalize_pagespeed_url(url: str) -> str:
    """Return a stable URL for Lighthouse testing while keeping the landing page path intact."""
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return str(url or "").strip()

    kept_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in _TRACKING_QUERY_PARAMS:
            continue
        if any(lower_key.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES):
            continue
        kept_params.append((key, value))

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            urlencode(kept_params, doseq=True),
            "",
        )
    )


def _top_landing_pages(landing_pages: pd.DataFrame, max_urls: int) -> list[str]:
    if landing_pages.empty or "expanded_final_url" not in landing_pages.columns:
        return []
    normalized = landing_pages.copy()
    for column in ["cost_micros", "clicks", "conversions"]:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0)
    grouped = (
        normalized.groupby("expanded_final_url", dropna=False, as_index=False)[["cost_micros", "clicks", "conversions"]]
        .sum()
        .sort_values(by="cost_micros", ascending=False)
    )
    urls = [
        str(value).strip()
        for value in grouped["expanded_final_url"].tolist()
        if str(value).strip()
    ]
    return urls[:max_urls]


def _score(categories: dict[str, Any], category_name: str) -> float | None:
    category = categories.get(category_name, {}) or {}
    score = category.get("score")
    if score is None:
        return None
    return round(float(score) * 100, 2)


def _audit_value(audits: dict[str, Any], audit_key: str, value_key: str = "numericValue") -> float | None:
    audit = audits.get(audit_key, {}) or {}
    value = audit.get(value_key)
    if value is None:
        return None
    return round(float(value), 2)


def _collect_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    lighthouse = payload.get("lighthouseResult", {}) or {}
    audits = lighthouse.get("audits", {}) or {}
    loading = payload.get("loadingExperience", {}) or {}
    origin_loading = payload.get("originLoadingExperience", {}) or {}
    return {
        "requested_url": payload.get("id", ""),
        "final_url": payload.get("lighthouseResult", {}).get("finalDisplayedUrl", ""),
        "environment": lighthouse.get("environment", {}),
        "loading_experience": loading,
        "origin_loading_experience": origin_loading,
        "audits": {
            "largest-contentful-paint": audits.get("largest-contentful-paint", {}),
            "cumulative-layout-shift": audits.get("cumulative-layout-shift", {}),
            "interaction-to-next-paint": audits.get("interaction-to-next-paint", {}),
            "first-contentful-paint": audits.get("first-contentful-paint", {}),
            "speed-index": audits.get("speed-index", {}),
        },
    }


def _opportunities_json(
    *,
    url: str,
    strategy: str,
    performance_score: float | None,
    lcp: float | None,
    cls: float | None,
    cost_micros: float,
    recommendations: list[str],
) -> str:
    payload = {
        "url": url,
        "strategy": strategy,
        "cost_micros": cost_micros,
        "rules": recommendations,
        "note": "Nejdřív opravit stránku, ne vypnout kampaň." if recommendations else "",
        "performance_score": performance_score,
        "lcp": lcp,
        "cls": cls,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_pagespeed_export(
    *,
    env_config: GoogleAdsEnvConfig,
    pagespeed_config: PageSpeedConfig,
    flags_config: FlagsConfig,
    reports_enabled: dict[str, bool],
    landing_pages: pd.DataFrame,
    cache_dir: Path,
    currency_code: str | None = None,
) -> PageSpeedExportResult:
    result = PageSpeedExportResult()
    if not reports_enabled.get("pagespeed_landing_pages", False):
        return result

    if not env_config.pagespeed_enabled or not pagespeed_config.enabled:
        result.datasets["pagespeed_landing_pages"] = _empty_report()
        result.report_notes["pagespeed_landing_pages"] = [
            "PageSpeed modul je vypnuty v .env nebo configu."
        ]
        result.report_warning_keys.add("pagespeed_landing_pages")
        return result

    top_urls = _top_landing_pages(landing_pages, pagespeed_config.max_urls_per_export)
    if not top_urls:
        result.datasets["pagespeed_landing_pages"] = _empty_report()
        result.report_notes["pagespeed_landing_pages"] = [
            "Nebyly nalezeny zadne landing pages pro technickou analyzu."
        ]
        result.report_warning_keys.add("pagespeed_landing_pages")
        return result

    client = PageSpeedApiClient(
        PageSpeedClientConfig(
            api_key=env_config.pagespeed_api_key,
            enabled=env_config.pagespeed_enabled,
        )
    )
    cache = PageSpeedCache(base_dir=cache_dir, ttl_days=pagespeed_config.cache_days)

    cost_lookup: dict[str, float] = {}
    if not landing_pages.empty and "expanded_final_url" in landing_pages.columns:
        grouped = (
            landing_pages.groupby("expanded_final_url", dropna=False, as_index=False)[["cost_micros"]]
            .sum()
        )
        cost_lookup = {
            str(row["expanded_final_url"]).strip(): _safe_float(row["cost_micros"])
            for _, row in grouped.iterrows()
            if str(row["expanded_final_url"]).strip()
        }

    total_requests = len(top_urls) * len(pagespeed_config.strategies)
    LOGGER.info(
        "PageSpeed selected %s URL(s), strategies=%s, total_requests=%s, source=%s, cache_days=%s",
        len(top_urls),
        ",".join(pagespeed_config.strategies),
        total_requests,
        pagespeed_config.source,
        pagespeed_config.cache_days,
    )
    for index, url in enumerate(top_urls, start=1):
        url_cost_micros = cost_lookup.get(url, 0)
        url_cost = round(url_cost_micros / 1_000_000, 2)
        LOGGER.info(
            "PageSpeed selected URL %s/%s cost_micros=%s cost=%.2f currency=%s source_url=%s",
            index,
            len(top_urls),
            int(url_cost_micros),
            url_cost,
            currency_code or "",
            url,
        )

    rows: list[dict[str, Any]] = []
    had_error = False
    request_index = 0
    for url in top_urls:
        tested_url = _normalize_pagespeed_url(url)
        url_cost_micros = cost_lookup.get(url, 0)
        if tested_url != url:
            LOGGER.info(
                "PageSpeed normalized URL source_url=%s tested_url=%s",
                url,
                tested_url,
            )

        for strategy in pagespeed_config.strategies:
            request_index += 1
            request_payload = {
                "url": tested_url,
                "strategy": strategy,
                "has_api_key": bool(env_config.pagespeed_api_key),
            }
            payload = cache.load(request_payload)
            if payload is not None:
                LOGGER.info(
                    "PageSpeed %s/%s cache HIT strategy=%s url=%s",
                    request_index,
                    total_requests,
                    strategy,
                    tested_url,
                )
            else:
                LOGGER.info(
                    "PageSpeed %s/%s API start strategy=%s url=%s",
                    request_index,
                    total_requests,
                    strategy,
                    tested_url,
                )
                started_at = perf_counter()
                try:
                    payload = client.run_pagespeed(url=tested_url, strategy=strategy)
                    cache.save(request_payload, payload)
                    LOGGER.info(
                        "PageSpeed %s/%s API finished strategy=%s seconds=%.1f url=%s",
                        request_index,
                        total_requests,
                        strategy,
                        perf_counter() - started_at,
                        tested_url,
                    )
                except PageSpeedApiError as exc:
                    had_error = True
                    LOGGER.warning(
                        "PageSpeed %s/%s API failed strategy=%s seconds=%.1f url=%s source_url=%s error=%s",
                        request_index,
                        total_requests,
                        strategy,
                        perf_counter() - started_at,
                        tested_url,
                        url,
                        exc.message,
                    )
                    result.errors.append(
                        {
                            "report": "pagespeed_api",
                            "message": f"{tested_url} [{strategy}]: {exc.message}",
                            "details": exc.details,
                            "timestamp": _timestamp(),
                        }
                    )
                    continue

            lighthouse = payload.get("lighthouseResult", {}) or {}
            categories = lighthouse.get("categories", {}) or {}
            audits = lighthouse.get("audits", {}) or {}

            performance_score = _score(categories, "performance")
            accessibility_score = _score(categories, "accessibility")
            seo_score = _score(categories, "seo")
            best_practices_score = _score(categories, "best-practices")
            lcp = _audit_value(audits, "largest-contentful-paint")
            cls = _audit_value(audits, "cumulative-layout-shift")
            inp = _audit_value(audits, "interaction-to-next-paint")
            fcp = _audit_value(audits, "first-contentful-paint")
            speed_index = _audit_value(audits, "speed-index")

            recommendations: list[str] = []
            high_spend = url_cost_micros >= flags_config.min_spend_micros
            if high_spend and strategy == "mobile" and (performance_score or 0) < 60:
                recommendations.append("high_spend_slow_mobile")
            if high_spend and (lcp or 0) > 4000:
                recommendations.append("high_spend_bad_lcp")
            if high_spend and (cls or 0) > 0.25:
                recommendations.append("high_spend_bad_cls")
            if high_spend and (performance_score or 0) < 50:
                recommendations.append("high_spend_low_pagespeed_score")

            rows.append(
                {
                    "url": tested_url,
                    "strategy": strategy,
                    "performance_score": performance_score,
                    "accessibility_score": accessibility_score,
                    "seo_score": seo_score,
                    "best_practices_score": best_practices_score,
                    "lcp": lcp,
                    "cls": cls,
                    "inp": inp,
                    "fcp": fcp,
                    "speed_index": speed_index,
                    "diagnostics_json": json.dumps(_collect_diagnostics(payload), ensure_ascii=False),
                    "opportunities_json": _opportunities_json(
                        url=tested_url,
                        strategy=strategy,
                        performance_score=performance_score,
                        lcp=lcp,
                        cls=cls,
                        cost_micros=url_cost_micros,
                        recommendations=recommendations,
                    ),
                }
            )

    frame = pd.DataFrame(rows, columns=get_report_definition("pagespeed_landing_pages").aliases)
    if not frame.empty:
        frame = frame.sort_values(by=["url", "strategy"], ascending=[True, True]).reset_index(drop=True)
    result.datasets["pagespeed_landing_pages"] = frame

    notes = [
        f"Analyza je omezena na top {pagespeed_config.max_urls_per_export} landing pages podle nakladu.",
        f"Cache PageSpeed vysledku je nastavena na {pagespeed_config.cache_days} dni.",
    ]
    if not env_config.pagespeed_api_key:
        notes.append("Bezi bez API keye, takze modul muze byt vic omezeny kvotami.")
    result.report_notes["pagespeed_landing_pages"] = notes
    if had_error:
        result.report_warning_keys.add("pagespeed_landing_pages")
    return result
