from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pandas as pd

from app.accounts.context_config import (
    AccountContext,
    load_account_contexts,
    resolve_context_env_config,
)
from app.config.env_settings import load_env_config
from app.gtm.export import build_gtm_exports
from app.integrations.meta.audit_rules import build_meta_audit_findings
from app.integrations.meta.client import MetaGraphClient
from app.integrations.meta.exporters import export_meta_bundle
from app.integrations.meta.gtm_crosscheck import parse_meta_tags_from_gtm
from app.integrations.meta.normalizers import flatten_insights_actions, records_to_frame


@dataclass(slots=True)
class MetaSyncResult:
    context_key: str
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    raw_payloads: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    export_dir: str = ""


@dataclass(slots=True)
class TargetGtmInfo:
    context: AccountContext
    raw_tags: pd.DataFrame
    meta_tags: pd.DataFrame
    warnings: list[str] = field(default_factory=list)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


def _act_id(ad_account_id: str) -> str:
    return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"


def _store_raw_list(result: MetaSyncResult, key: str, rows: list[dict[str, Any]]) -> None:
    bucket = result.raw_payloads.setdefault(key, [])
    if isinstance(bucket, list):
        bucket.extend(rows)
    else:
        result.raw_payloads[key] = list(rows)


def _store_raw_item(result: MetaSyncResult, key: str, row: dict[str, Any]) -> None:
    bucket = result.raw_payloads.setdefault(key, [])
    if isinstance(bucket, list):
        bucket.append(row)
    else:
        result.raw_payloads[key] = [row]


def _safe_get(
    *,
    result: MetaSyncResult,
    client: MetaGraphClient,
    endpoint: str,
    params: dict[str, Any],
    warning_context: str,
    raw_key: str | None = None,
) -> dict[str, Any]:
    try:
        row = client.get(endpoint, params=params)
    except Exception as exc:
        result.warnings.append(f"{warning_context} failed: {exc}")
        return {}

    if raw_key:
        _store_raw_item(result, raw_key, row)
    return row


def _safe_paginate(
    *,
    result: MetaSyncResult,
    client: MetaGraphClient,
    endpoint: str,
    params: dict[str, Any],
    warning_context: str,
    raw_key: str | None = None,
) -> list[dict[str, Any]]:
    try:
        rows = list(client.paginate(endpoint, params=params))
    except Exception as exc:
        result.warnings.append(f"{warning_context} failed: {exc}")
        return []

    if raw_key:
        _store_raw_list(result, raw_key, rows)
    return rows


def _walk_first_url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith(("http://", "https://")):
            return candidate
        return ""

    if isinstance(value, dict):
        preferred_keys = (
            "link_url",
            "url",
            "website_url",
            "href",
            "value",
            "uri",
        )
        for key in preferred_keys:
            nested = value.get(key)
            found = _walk_first_url(nested)
            if found:
                return found

        for nested in value.values():
            found = _walk_first_url(nested)
            if found:
                return found

        return ""

    if isinstance(value, list):
        for nested in value:
            found = _walk_first_url(nested)
            if found:
                return found

    return ""


def _extract_creative_id(ad_row: dict[str, Any]) -> str:
    creative = ad_row.get("creative")
    if isinstance(creative, dict):
        return str(creative.get("id") or "").strip()
    return ""


def _extract_landing_url_from_creative(creative_row: dict[str, Any]) -> str:
    direct_url = str(creative_row.get("link_url") or "").strip()
    if direct_url:
        return direct_url

    object_story_spec = creative_row.get("object_story_spec")
    found = _walk_first_url(object_story_spec)
    if found:
        return found

    asset_feed_spec = creative_row.get("asset_feed_spec")
    found = _walk_first_url(asset_feed_spec)
    if found:
        return found

    return ""


def _landing_domain(url: str) -> str:
    try:
        hostname = urlsplit(url).netloc.strip().lower()
    except ValueError:
        return ""
    return hostname.split("@").pop().split(":")[0].strip().rstrip(".")


def _domain_from_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("sc-domain:"):
        text = text.split(":", 1)[1]
    if text.startswith(("http://", "https://")):
        return _landing_domain(text)
    text = text.split("/")[0]
    text = text.split("@").pop().split(":")[0]
    return text.strip().rstrip(".")


def _canonical_domain(value: object) -> str:
    domain = _domain_from_text(value)
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _context_source_domains(context: AccountContext) -> list[str]:
    domains: list[str] = []
    for value in getattr(context, "effective_source_domains", []) or []:
        domain = _domain_from_text(value)
        if domain:
            domains.append(domain)
    if not domains:
        domain = _domain_from_text(getattr(context, "gsc_site_url", ""))
        if domain:
            domains.append(domain)
    result: list[str] = []
    seen: set[str] = set()
    for domain in domains:
        for candidate in {domain, _canonical_domain(domain)}:
            if candidate and candidate not in seen:
                seen.add(candidate)
                result.append(candidate)
    return result


def _build_domain_context_lookup(contexts: list[AccountContext]) -> dict[str, AccountContext]:
    lookup: dict[str, AccountContext] = {}
    for context in contexts:
        for domain in _context_source_domains(context):
            canonical = _canonical_domain(domain)
            if canonical and canonical not in lookup:
                lookup[canonical] = context
    return lookup


def _find_context_for_domain(
    domain: str,
    lookup: dict[str, AccountContext],
) -> AccountContext | None:
    canonical = _canonical_domain(domain)
    if not canonical:
        return None
    if canonical in lookup:
        return lookup[canonical]
    for source_domain, context in lookup.items():
        if canonical == source_domain or canonical.endswith(f".{source_domain}"):
            return context
    return None


def _infer_expected_event_for_landing_context(
    *,
    target_context: AccountContext | None,
    source_context: AccountContext,
) -> str:
    # Universal default: contexts with Merchant Center/e-shop expect Purchase,
    # lead/service/product-web contexts expect Lead. This avoids hard-coding domains.
    if target_context is not None:
        if str(getattr(target_context, "merchant_account_id", "") or "").strip():
            return "purchase"
        return "lead"

    fallback = str(getattr(source_context.meta, "expected_conversion_event", "") or "").strip().lower()
    return fallback if fallback in {"purchase", "lead"} else ""


def _pipe_join(values: list[str] | set[str]) -> str:
    return " | ".join(sorted({str(value or "").strip() for value in values if str(value or "").strip()}))


PIXEL_SCAN_NEEDLES = {
    "fbq": "fbq(",
    "fbevents_js": "fbevents.js",
    "connect_facebook_net": "connect.facebook.net",
    "facebook_tr": "facebook.com/tr",
}


def _unique_urls_from_ads(enriched_ads: list[dict[str, Any]], limit: int = 25) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for ad in enriched_ads:
        url = str(ad.get("landing_page_url") or ad.get("link_url") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _extract_meta_pixel_ids_from_html(html: str) -> set[str]:
    ids: set[str] = set()
    patterns = [
        r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](?P<id>\d+)['\"]",
        r"facebook\.com/tr\?[^'\"<>\s]*\bid=(?P<id>\d+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            pixel_id = str(match.group("id") or "").strip()
            if pixel_id:
                ids.add(pixel_id)
    return ids


def _extract_meta_pixel_events_from_html(html: str) -> set[str]:
    events: set[str] = set()
    patterns = [
        r"fbq\(\s*['\"]track['\"]\s*,\s*['\"](?P<event>[A-Za-z0-9_]+)['\"]",
        r"fbq\(\s*['\"]trackCustom['\"]\s*,\s*['\"](?P<event>[A-Za-z0-9_]+)['\"]",
        r"fbq\(\s*['\"]trackSingle['\"]\s*,\s*['\"]?\d+['\"]?\s*,\s*['\"](?P<event>[A-Za-z0-9_]+)['\"]",
        r"fbq\(\s*['\"]trackSingleCustom['\"]\s*,\s*['\"]?\d+['\"]?\s*,\s*['\"](?P<event>[A-Za-z0-9_]+)['\"]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            event_name = str(match.group("event") or "").strip()
            if event_name:
                events.add(event_name)
    return events


def _scan_live_meta_pixel_url(url: str, expected_pixel_ids: list[str]) -> dict[str, Any]:
    landing_domain = _landing_domain(url)
    row: dict[str, Any] = {
        "url": url,
        "landing_domain": landing_domain,
        "scan_ok": False,
        "status_code": "",
        "error": "",
        "fbq_present": False,
        "fbevents_js_present": False,
        "connect_facebook_net_present": False,
        "facebook_tr_present": False,
        "pixel_present": False,
        "pixel_ids": "",
        "expected_pixel_ids": _pipe_join(expected_pixel_ids),
        "expected_pixel_id_found": False,
        "detected_events": "",
    }
    if not url.startswith(("http://", "https://")):
        row["error"] = "unsupported_url"
        return row

    try:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ITFutureMetaAudit/1.0; +https://itfuture.cz)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=12) as response:
            row["status_code"] = str(getattr(response, "status", "") or response.getcode() or "")
            content = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            html = content.decode(charset, errors="ignore")
    except HTTPError as exc:
        row["status_code"] = str(exc.code)
        row["error"] = f"http_error: {exc}"
        return row
    except URLError as exc:
        row["error"] = f"url_error: {exc}"
        return row
    except Exception as exc:
        row["error"] = f"scan_failed: {exc}"
        return row

    lowered = html.lower()
    row["scan_ok"] = True
    row["fbq_present"] = PIXEL_SCAN_NEEDLES["fbq"] in lowered
    row["fbevents_js_present"] = PIXEL_SCAN_NEEDLES["fbevents_js"] in lowered
    row["connect_facebook_net_present"] = PIXEL_SCAN_NEEDLES["connect_facebook_net"] in lowered
    row["facebook_tr_present"] = PIXEL_SCAN_NEEDLES["facebook_tr"] in lowered

    pixel_ids = _extract_meta_pixel_ids_from_html(html)
    events = _extract_meta_pixel_events_from_html(html)
    expected = {str(pixel_id or "").strip() for pixel_id in expected_pixel_ids if str(pixel_id or "").strip()}

    row["pixel_ids"] = _pipe_join(pixel_ids)
    row["detected_events"] = _pipe_join(events)
    row["pixel_present"] = bool(
        pixel_ids
        or row["fbq_present"]
        or row["fbevents_js_present"]
        or row["connect_facebook_net_present"]
        or row["facebook_tr_present"]
    )
    row["expected_pixel_id_found"] = bool(expected and pixel_ids.intersection(expected))
    return row


def _build_web_pixel_scan_rows(
    *,
    enriched_ads: list[dict[str, Any]],
    expected_pixel_ids: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for url in _unique_urls_from_ads(enriched_ads):
        rows.append(_scan_live_meta_pixel_url(url, expected_pixel_ids))
    return rows


def _enrich_ads_with_creatives(
    ads: list[dict[str, Any]],
    creatives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    creative_lookup = {
        str(item.get("id") or "").strip(): item
        for item in creatives
        if str(item.get("id") or "").strip()
    }

    enriched: list[dict[str, Any]] = []
    for row in ads:
        creative_id = _extract_creative_id(row)
        creative = creative_lookup.get(creative_id, {})
        landing_url = _extract_landing_url_from_creative(creative)

        merged = dict(row)
        merged["creative_id"] = creative_id
        merged["link_url"] = str(creative.get("link_url") or "").strip()
        merged["landing_page_url"] = landing_url
        merged["landing_domain"] = _landing_domain(landing_url)
        enriched.append(merged)

    return enriched


def _build_feed_upload_rows(
    *,
    catalog_id: str,
    product_feed: dict[str, Any],
    uploads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    feed_id = str(product_feed.get("id") or "").strip()
    feed_name = str(product_feed.get("name") or "").strip()

    for upload in uploads:
        item = dict(upload)
        item["catalog_id"] = catalog_id
        item["product_feed_id"] = feed_id
        item["product_feed_name"] = feed_name
        rows.append(item)

    if rows:
        return rows

    latest_upload = product_feed.get("latest_upload")
    if isinstance(latest_upload, dict) and latest_upload:
        fallback_row = dict(latest_upload)
        fallback_row["catalog_id"] = catalog_id
        fallback_row["product_feed_id"] = feed_id
        fallback_row["product_feed_name"] = feed_name
        fallback_row.setdefault("status", latest_upload.get("status") or "latest_upload_only")
        fallback_row.setdefault("source", "product_feed.latest_upload")
        rows.append(fallback_row)

    return rows


def _empty_raw_gtm_tags_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "tag_id",
            "name",
            "type",
            "parameter_json",
            "notes",
            "firing_trigger_ids",
            "consent_settings",
        ]
    )


def _load_target_gtm_info(
    *,
    project_root: Path,
    target_context: AccountContext,
    current_context: AccountContext,
    current_raw_gtm_tags: pd.DataFrame | None,
    base_env_config,
) -> TargetGtmInfo:
    warnings: list[str] = []

    if target_context.key == current_context.key:
        raw_tags = current_raw_gtm_tags if current_raw_gtm_tags is not None else _empty_raw_gtm_tags_frame()
        parsed_tags = parse_meta_tags_from_gtm(raw_tags)
        return TargetGtmInfo(context=target_context, raw_tags=raw_tags, meta_tags=parsed_tags)

    if not (target_context.gtm_account_id and target_context.gtm_container_id):
        return TargetGtmInfo(
            context=target_context,
            raw_tags=_empty_raw_gtm_tags_frame(),
            meta_tags=parse_meta_tags_from_gtm(_empty_raw_gtm_tags_frame()),
            warnings=[f"Target context '{target_context.key}' has no GTM account/container."],
        )

    try:
        target_env = resolve_context_env_config(base_env_config, target_context)
        export_result = build_gtm_exports(
            env_config=target_env,
            reports_enabled={"gtm_tags": True},
        )
    except Exception as exc:
        return TargetGtmInfo(
            context=target_context,
            raw_tags=_empty_raw_gtm_tags_frame(),
            meta_tags=parse_meta_tags_from_gtm(_empty_raw_gtm_tags_frame()),
            warnings=[f"GTM load failed for target context '{target_context.key}': {exc}"],
        )

    for error in export_result.errors:
        warnings.append(f"GTM load error for target context '{target_context.key}': {error}")
    for key, notes in export_result.report_notes.items():
        if key in export_result.report_warning_keys:
            for note in notes:
                warnings.append(f"GTM warning for target context '{target_context.key}'/{key}: {note}")

    raw_tags = export_result.datasets.get("gtm_tags")
    if raw_tags is None:
        raw_tags = _empty_raw_gtm_tags_frame()
        warnings.append(f"GTM tags dataset missing for target context '{target_context.key}'.")
    if not isinstance(raw_tags, pd.DataFrame):
        warnings.append(
            f"GTM tags dataset has unexpected type for target context '{target_context.key}': {type(raw_tags).__name__}."
        )
        raw_tags = _empty_raw_gtm_tags_frame()

    parsed_tags = parse_meta_tags_from_gtm(raw_tags)
    return TargetGtmInfo(context=target_context, raw_tags=raw_tags, meta_tags=parsed_tags, warnings=warnings)


def _target_gtm_rows(target_infos: dict[str, TargetGtmInfo]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for info in target_infos.values():
        context = info.context
        if info.meta_tags.empty:
            continue
        for _, row in info.meta_tags.iterrows():
            item = row.to_dict()
            item["target_context_key"] = context.key
            item["target_context_label"] = context.label
            item["gtm_account_id"] = context.gtm_account_id
            item["gtm_container_id"] = context.gtm_container_id
            item["source_domains"] = _pipe_join(_context_source_domains(context))
            rows.append(item)
    return rows


def _build_landing_target_rows(
    *,
    enriched_ads: list[dict[str, Any]],
    all_contexts: list[AccountContext],
    current_context: AccountContext,
    target_infos: dict[str, TargetGtmInfo],
    web_pixel_scan_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    domain_lookup = _build_domain_context_lookup(all_contexts)
    rows: list[dict[str, Any]] = []
    web_scan_by_url = {
        str(row.get("url") or "").strip(): row
        for row in web_pixel_scan_rows
        if str(row.get("url") or "").strip()
    }

    for ad in enriched_ads:
        landing_domain = _domain_from_text(ad.get("landing_domain"))
        target_context = _find_context_for_domain(landing_domain, domain_lookup)
        target_info = target_infos.get(target_context.key) if target_context is not None else None
        meta_tags = target_info.meta_tags if target_info is not None else pd.DataFrame()
        raw_tags = target_info.raw_tags if target_info is not None else pd.DataFrame()
        found_pixel_ids = {
            str(row.get("pixel_id") or "").strip()
            for _, row in meta_tags.iterrows()
            if str(row.get("pixel_id") or "").strip()
        }
        found_event_names = {
            str(row.get("event_name") or "").strip().lower()
            for _, row in meta_tags.iterrows()
            if str(row.get("event_name") or "").strip()
        }
        expected_event = _infer_expected_event_for_landing_context(
            target_context=target_context,
            source_context=current_context,
        )
        landing_url = str(ad.get("landing_page_url") or ad.get("link_url") or "").strip()
        web_scan = web_scan_by_url.get(landing_url, {})

        rows.append(
            {
                "ad_id": str(ad.get("id") or "").strip(),
                "ad_name": str(ad.get("name") or "").strip(),
                "adset_id": str(ad.get("adset_id") or "").strip(),
                "campaign_id": str(ad.get("campaign_id") or "").strip(),
                "landing_url": landing_url,
                "landing_domain": landing_domain,
                "target_known": bool(target_context is not None),
                "target_is_current_context": bool(target_context is not None and target_context.key == current_context.key),
                "target_context_key": target_context.key if target_context is not None else "",
                "target_context_label": target_context.label if target_context is not None else "",
                "target_source_domains": _pipe_join(_context_source_domains(target_context)) if target_context is not None else "",
                "expected_event": expected_event,
                "expected_event_source": "merchant_or_lead_context" if target_context is not None else "source_context_fallback",
                "gtm_account_id": target_context.gtm_account_id if target_context is not None else "",
                "gtm_container_id": target_context.gtm_container_id if target_context is not None else "",
                "raw_gtm_tags_count": int(len(raw_tags)) if target_info is not None else 0,
                "meta_gtm_tags_count": int(len(meta_tags)) if target_info is not None else 0,
                "gtm_pixel_ids": _pipe_join(found_pixel_ids),
                "gtm_event_names": _pipe_join(found_event_names),
                "web_scan_ok": bool(web_scan.get("scan_ok")),
                "web_scan_status_code": str(web_scan.get("status_code") or ""),
                "web_scan_error": str(web_scan.get("error") or ""),
                "web_pixel_present": bool(web_scan.get("pixel_present")),
                "web_expected_pixel_found": bool(web_scan.get("expected_pixel_id_found")),
                "web_pixel_ids": str(web_scan.get("pixel_ids") or ""),
                "web_detected_events": str(web_scan.get("detected_events") or ""),
                "web_fbq_present": bool(web_scan.get("fbq_present")),
                "web_fbevents_js_present": bool(web_scan.get("fbevents_js_present")),
                "web_connect_facebook_net_present": bool(web_scan.get("connect_facebook_net_present")),
                "web_facebook_tr_present": bool(web_scan.get("facebook_tr_present")),
            }
        )

    return rows


def _collect_target_infos(
    *,
    project_root: Path,
    current_context: AccountContext,
    current_raw_gtm_tags: pd.DataFrame | None,
    enriched_ads: list[dict[str, Any]],
    result: MetaSyncResult,
) -> tuple[list[AccountContext], dict[str, TargetGtmInfo]]:
    try:
        all_contexts = load_account_contexts(project_root / "config.accounts.yaml")
    except Exception as exc:
        result.warnings.append(f"Account contexts load failed for landing-domain audit: {exc}")
        all_contexts = [current_context]

    if not any(item.key == current_context.key for item in all_contexts):
        all_contexts.append(current_context)

    domain_lookup = _build_domain_context_lookup(all_contexts)
    needed_contexts: dict[str, AccountContext] = {current_context.key: current_context}
    for ad in enriched_ads:
        domain = _domain_from_text(ad.get("landing_domain"))
        target_context = _find_context_for_domain(domain, domain_lookup)
        if target_context is not None:
            needed_contexts[target_context.key] = target_context

    try:
        base_env_config = load_env_config(project_root / ".env")
    except Exception as exc:
        result.warnings.append(f"Base .env load failed for landing-domain GTM audit: {exc}")
        base_env_config = None

    target_infos: dict[str, TargetGtmInfo] = {}
    for target_context in needed_contexts.values():
        if base_env_config is None:
            target_infos[target_context.key] = TargetGtmInfo(
                context=target_context,
                raw_tags=_empty_raw_gtm_tags_frame(),
                meta_tags=parse_meta_tags_from_gtm(_empty_raw_gtm_tags_frame()),
                warnings=["Base .env could not be loaded."],
            )
            continue
        info = _load_target_gtm_info(
            project_root=project_root,
            target_context=target_context,
            current_context=current_context,
            current_raw_gtm_tags=current_raw_gtm_tags,
            base_env_config=base_env_config,
        )
        target_infos[target_context.key] = info
        result.warnings.extend(info.warnings)

    return all_contexts, target_infos


def run_meta_context_sync(
    *,
    project_root: Path,
    context: AccountContext,
    connection,
    gtm_tags: pd.DataFrame | None = None,
) -> MetaSyncResult:
    client = MetaGraphClient(connection)
    meta = context.meta
    result = MetaSyncResult(context_key=context.key)

    campaigns: list[dict[str, Any]] = []
    ad_accounts: list[dict[str, Any]] = []
    adsets: list[dict[str, Any]] = []
    ads: list[dict[str, Any]] = []
    creatives: list[dict[str, Any]] = []
    insights_campaign_daily: list[dict[str, Any]] = []
    insights_adset_daily: list[dict[str, Any]] = []
    insights_ad_daily: list[dict[str, Any]] = []
    custom_conversions: list[dict[str, Any]] = []
    pixels: list[dict[str, Any]] = []
    catalogs: list[dict[str, Any]] = []
    product_sets: list[dict[str, Any]] = []
    product_feeds: list[dict[str, Any]] = []
    feed_uploads: list[dict[str, Any]] = []

    if meta.business_id:
        result.raw_payloads["business_assets"] = [
            {
                "business_id": meta.business_id,
                "connection_key": meta.connection_key,
                "business_name": getattr(connection, "business_name", ""),
            }
        ]

    for ad_account_id in meta.ad_account_ids:
        act_id = _act_id(ad_account_id)

        account_row = _safe_get(
            result=result,
            client=client,
            endpoint=act_id,
            params={"fields": "id,name,account_id,account_status,currency,timezone_name,business"},
            warning_context=f"Ad account export for {act_id}",
            raw_key="ad_accounts",
        )
        if account_row:
            ad_accounts.append(account_row)
        else:
            fallback_account_row = {
                "id": act_id,
                "source_ad_account_id": ad_account_id,
                "export_status": "detail_failed",
            }
            ad_accounts.append(fallback_account_row)
            _store_raw_item(result, "ad_accounts", fallback_account_row)

        campaign_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/campaigns",
            params={
                "fields": (
                    "id,name,status,effective_status,objective,buying_type,"
                    "special_ad_categories,created_time,updated_time,start_time,stop_time,"
                    "daily_budget,lifetime_budget,budget_remaining,bid_strategy,configured_status"
                )
            },
            warning_context=f"Campaign export for {act_id}",
            raw_key="campaigns",
        )
        campaigns.extend(campaign_rows)

        adset_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/adsets",
            params={
                "fields": (
                    "id,name,campaign_id,account_id,status,effective_status,configured_status,"
                    "optimization_goal,billing_event,bid_strategy,bid_amount,daily_budget,"
                    "lifetime_budget,start_time,end_time,targeting,promoted_object,"
                    "attribution_spec,destination_type,created_time,updated_time"
                )
            },
            warning_context=f"Ad set export for {act_id}",
            raw_key="adsets",
        )
        adsets.extend(adset_rows)

        ad_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/ads",
            params={
                "fields": (
                    "id,name,adset_id,campaign_id,account_id,status,effective_status,"
                    "configured_status,creative,tracking_specs,conversion_specs,created_time,"
                    "updated_time,preview_shareable_link"
                )
            },
            warning_context=f"Ads export for {act_id}",
            raw_key="ads",
        )
        ads.extend(ad_rows)

        creative_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/adcreatives",
            params={
                "fields": (
                    "id,name,object_story_spec,asset_feed_spec,url_tags,body,title,link_url,"
                    "call_to_action_type,thumbnail_url,image_hash,video_id,"
                    "instagram_permalink_url,effective_object_story_id"
                )
            },
            warning_context=f"Creative export for {act_id}",
            raw_key="creatives",
        )
        creatives.extend(creative_rows)

        campaign_insights = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/insights",
            params={
                "level": "campaign",
                "time_increment": 1,
                "date_preset": "last_90d",
                "fields": (
                    "date_start,date_stop,account_id,campaign_id,campaign_name,objective,"
                    "optimization_goal,buying_type,impressions,reach,frequency,spend,clicks,"
                    "inline_link_clicks,outbound_clicks,landing_page_view,ctr,cpc,cpm,cpp,"
                    "actions,action_values,cost_per_action_type,purchase_roas,"
                    "website_purchase_roas,conversions,conversion_values"
                ),
            },
            warning_context=f"Campaign insights export for {act_id}",
            raw_key="insights_campaign_daily",
        )

        adset_insights = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/insights",
            params={
                "level": "adset",
                "time_increment": 1,
                "date_preset": "last_90d",
                "fields": (
                    "date_start,date_stop,account_id,campaign_id,campaign_name,adset_id,"
                    "adset_name,objective,optimization_goal,buying_type,impressions,reach,"
                    "frequency,spend,clicks,inline_link_clicks,outbound_clicks,"
                    "landing_page_view,ctr,cpc,cpm,cpp,actions,action_values,"
                    "cost_per_action_type,purchase_roas,website_purchase_roas,conversions,"
                    "conversion_values"
                ),
            },
            warning_context=f"Ad set insights export for {act_id}",
            raw_key="insights_adset_daily",
        )

        ad_insights = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/insights",
            params={
                "level": "ad",
                "time_increment": 1,
                "date_preset": "last_90d",
                "fields": (
                    "date_start,date_stop,account_id,campaign_id,campaign_name,adset_id,"
                    "adset_name,ad_id,ad_name,objective,optimization_goal,buying_type,"
                    "impressions,reach,frequency,spend,clicks,inline_link_clicks,"
                    "outbound_clicks,landing_page_view,ctr,cpc,cpm,cpp,actions,action_values,"
                    "cost_per_action_type,purchase_roas,website_purchase_roas,conversions,"
                    "conversion_values"
                ),
            },
            warning_context=f"Ad insights export for {act_id}",
            raw_key="insights_ad_daily",
        )

        insights_campaign_daily.extend([flatten_insights_actions(row) for row in campaign_insights])
        insights_adset_daily.extend([flatten_insights_actions(row) for row in adset_insights])
        insights_ad_daily.extend([flatten_insights_actions(row) for row in ad_insights])

        pixel_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/adspixels",
            params={"fields": "id,name,owner_ad_account,creation_time"},
            warning_context=f"Pixel discovery for {act_id}",
            raw_key="pixels",
        )
        pixels.extend(pixel_rows)

        custom_conversion_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{act_id}/customconversions",
            params={"fields": "id,name,event_source_type,rule,status"},
            warning_context=f"Custom conversions discovery for {act_id}",
            raw_key="custom_conversions",
        )
        custom_conversions.extend(custom_conversion_rows)

    for catalog_id in meta.catalog_ids:
        catalog_row = _safe_get(
            result=result,
            client=client,
            endpoint=catalog_id,
            params={"fields": "id,name,vertical,product_count"},
            warning_context=f"Catalog export for {catalog_id}",
            raw_key="catalogs",
        )
        if not catalog_row:
            continue

        catalogs.append(catalog_row)

        product_set_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{catalog_id}/product_sets",
            params={"fields": "id,name,filter,product_count"},
            warning_context=f"Product sets export for catalog {catalog_id}",
            raw_key="product_sets",
        )
        product_sets.extend(product_set_rows)

        product_feed_rows = _safe_paginate(
            result=result,
            client=client,
            endpoint=f"{catalog_id}/product_feeds",
            params={"fields": "id,name,schedule,latest_upload,created_time,update_schedule"},
            warning_context=f"Product feeds export for catalog {catalog_id}",
            raw_key="product_feeds",
        )
        product_feeds.extend(product_feed_rows)

        for product_feed in product_feed_rows:
            feed_id = str(product_feed.get("id") or "").strip()
            if not feed_id:
                continue

            upload_rows = _safe_paginate(
                result=result,
                client=client,
                endpoint=f"{feed_id}/uploads",
                params={"fields": "id,start_time,end_time,status,url,warning_count,error_count,warnings,errors"},
                warning_context=f"Feed uploads export for {feed_id}",
                raw_key=None,
            )

            enriched_upload_rows = _build_feed_upload_rows(
                catalog_id=catalog_id,
                product_feed=product_feed,
                uploads=upload_rows,
            )
            feed_uploads.extend(enriched_upload_rows)

            if enriched_upload_rows:
                _store_raw_list(result, "feed_uploads", enriched_upload_rows)

    current_raw_gtm_tags = gtm_tags if gtm_tags is not None else _empty_raw_gtm_tags_frame()
    parsed_gtm_tags = parse_meta_tags_from_gtm(current_raw_gtm_tags)
    enriched_ads = _enrich_ads_with_creatives(ads, creatives)

    all_contexts, target_infos = _collect_target_infos(
        project_root=project_root,
        current_context=context,
        current_raw_gtm_tags=current_raw_gtm_tags,
        enriched_ads=enriched_ads,
        result=result,
    )
    web_pixel_scan_rows = _build_web_pixel_scan_rows(
        enriched_ads=enriched_ads,
        expected_pixel_ids=[str(pixel_id or "").strip() for pixel_id in (meta.pixel_ids or []) if str(pixel_id or "").strip()],
    )
    landing_target_rows = _build_landing_target_rows(
        enriched_ads=enriched_ads,
        all_contexts=all_contexts,
        current_context=context,
        target_infos=target_infos,
        web_pixel_scan_rows=web_pixel_scan_rows,
    )
    target_gtm_meta_tag_rows = _target_gtm_rows(target_infos)

    datasets = {
        "business_assets": records_to_frame(
            [
                {
                    "business_id": meta.business_id,
                    "business_name": getattr(connection, "business_name", ""),
                    "connection_key": meta.connection_key,
                }
            ]
        ),
        "ad_accounts": records_to_frame(ad_accounts),
        "campaigns": records_to_frame(campaigns),
        "adsets": records_to_frame(adsets),
        "ads": records_to_frame(enriched_ads),
        "creatives": records_to_frame(creatives),
        "insights_campaign_daily": records_to_frame(insights_campaign_daily),
        "insights_adset_daily": records_to_frame(insights_adset_daily),
        "insights_ad_daily": records_to_frame(insights_ad_daily),
        "pixels": records_to_frame(pixels),
        "custom_conversions": records_to_frame(custom_conversions),
        "catalogs": records_to_frame(catalogs),
        "product_sets": records_to_frame(product_sets),
        "product_feeds": records_to_frame(product_feeds),
        "feed_uploads": records_to_frame(feed_uploads),
        "gtm_meta_tags": parsed_gtm_tags,
        "landing_targets": records_to_frame(landing_target_rows),
        "target_gtm_meta_tags": records_to_frame(target_gtm_meta_tag_rows),
        "web_pixel_scan": records_to_frame(web_pixel_scan_rows),
    }
    result.datasets = datasets

    findings = build_meta_audit_findings(
        context=context,
        campaigns=datasets["campaigns"],
        adsets=datasets["adsets"],
        ads=datasets["ads"],
        creatives=datasets["creatives"],
        catalogs=datasets["catalogs"],
        product_feeds=datasets["product_feeds"],
        gtm_tags=datasets["gtm_meta_tags"],
        landing_targets=datasets["landing_targets"],
        target_gtm_tags=datasets["target_gtm_meta_tags"],
        raw_gtm_tags_count=int(len(current_raw_gtm_tags)),
    )
    result.findings = [finding.to_dict() for finding in findings]

    export_root = project_root / "exports" / "meta" / context.key / _timestamp()

    report_lines = [
        f"# Meta audit report - {context.label}",
        "",
        f"- Context key: `{context.key}`",
        f"- Connection: `{meta.connection_key}`",
        f"- Business ID: `{meta.business_id}`",
        f"- Ad accounts: {', '.join(meta.ad_account_ids) or '-'}",
        f"- Pixel IDs: {', '.join(meta.pixel_ids) or '-'}",
        f"- Catalog IDs: {', '.join(meta.catalog_ids) or '-'}",
        "",
        "## Landing domain targets",
        "",
    ]

    if landing_target_rows:
        seen_targets: set[tuple[str, str, str]] = set()
        for target in landing_target_rows:
            key = (
                str(target.get("landing_domain") or ""),
                str(target.get("target_context_key") or ""),
                str(target.get("expected_event") or ""),
            )
            if key in seen_targets:
                continue
            seen_targets.add(key)
            report_lines.append(
                "- "
                f"`{target.get('landing_domain') or '-'}` -> "
                f"{target.get('target_context_label') or 'unknown context'} "
                f"/ expected `{target.get('expected_event') or '-'}` "
                f"/ GTM `{target.get('gtm_container_id') or '-'}` "
                f"/ Meta tags `{target.get('meta_gtm_tags_count')}` "
                f"/ live pixel `{target.get('web_pixel_present')}` "
                f"/ live pixel IDs `{target.get('web_pixel_ids') or '-'}`"
            )
    else:
        report_lines.append("- Bez landing URL targetů.")

    report_lines.extend(["", "## Findings", ""])
    if result.findings:
        for finding in result.findings:
            report_lines.append(f"- [{finding['severity']}] {finding['rule_code']}: {finding['title']}")
    else:
        report_lines.append("- Bez nálezů.")

    if result.warnings:
        report_lines.extend(["", "## Warnings", ""])
        for warning in result.warnings:
            report_lines.append(f"- {warning}")

    export_meta_bundle(
        export_root=export_root,
        datasets=datasets,
        raw_payloads=result.raw_payloads,
        findings=findings,
        report_markdown="\n".join(report_lines),
    )
    result.export_dir = str(export_root)
    return result
