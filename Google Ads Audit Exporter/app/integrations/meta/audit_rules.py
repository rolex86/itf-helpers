from __future__ import annotations

from typing import Any

import pandas as pd

from app.accounts.context_config import AccountContext
from app.integrations.meta.models import MetaAuditFinding


FINAL_EVENTS = {"purchase", "lead"}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _string(value).lower()


def _row_dict(row: Any) -> dict[str, Any]:
    try:
        return row.to_dict()
    except AttributeError:
        return dict(row or {})


def _expected_pixel_ids(context: AccountContext) -> list[str]:
    return [
        _string(pixel_id)
        for pixel_id in (getattr(context.meta, "pixel_ids", None) or [])
        if _string(pixel_id)
    ]


def _expected_conversion_event(context: AccountContext) -> str:
    return _lower(getattr(context.meta, "expected_conversion_event", "") or "")


def _gtm_tags_available(gtm_tags: pd.DataFrame | None) -> bool:
    return gtm_tags is not None and not gtm_tags.empty


def _split_pipe(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item or "") for item in value]
    else:
        raw_items = str(value or "").replace(",", "|").split("|")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        candidate = item.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _lower(value)
    return text in {"1", "true", "yes", "y", "ano"}


def _landing_targets_available(landing_targets: pd.DataFrame | None) -> bool:
    return landing_targets is not None and not landing_targets.empty


def _event_missing_rule_code(event_name: str) -> str:
    if event_name == "purchase":
        return "META_PIXEL_PURCHASE_MISSING"
    if event_name == "lead":
        return "META_PIXEL_LEAD_MISSING"
    return "META_PIXEL_EVENT_MISSING"


def _event_missing_title(event_name: str) -> str:
    if event_name == "purchase":
        return "V GTM cílové domény chybí Meta Purchase event"
    if event_name == "lead":
        return "V GTM cílové domény chybí Meta Lead event"
    return "V GTM cílové domény chybí očekávaný Meta event"


def _adset_mismatch_severity(expected_event: str) -> str:
    return "high" if expected_event == "purchase" else "medium"


def _adset_event_matches(expected_event: str, combined_text: str) -> bool:
    if not expected_event:
        return True
    lowered = combined_text.lower()
    if expected_event in lowered:
        return True
    if expected_event == "purchase":
        return any(marker in lowered for marker in ["offsite_conversion", "offsite_conversions", "conversions", "catalog_sales", "sales"])
    if expected_event == "lead":
        return any(marker in lowered for marker in ["lead", "complete_registration", "submit_application"])
    return False


def _combined_gtm_frame(
    gtm_tags: pd.DataFrame | None,
    target_gtm_tags: pd.DataFrame | None,
) -> pd.DataFrame | None:
    frames: list[pd.DataFrame] = []
    if gtm_tags is not None and not gtm_tags.empty:
        frames.append(gtm_tags)
    if target_gtm_tags is not None and not target_gtm_tags.empty:
        frames.append(target_gtm_tags)
    if not frames:
        return gtm_tags if gtm_tags is not None else target_gtm_tags
    return pd.concat(frames, ignore_index=True, sort=False)


def _domain_target_groups(landing_targets: pd.DataFrame) -> list[dict[str, Any]]:
    if landing_targets.empty:
        return []

    group_cols = ["landing_domain", "target_context_key", "expected_event"]
    rows: list[dict[str, Any]] = []
    for group_values, group in landing_targets.groupby(group_cols, dropna=False):
        landing_domain, target_context_key, expected_event = group_values
        first = group.iloc[0]

        web_pixel_ids: set[str] = set()
        web_detected_events: set[str] = set()
        web_scan_errors: set[str] = set()
        web_status_codes: set[str] = set()
        for _, item in group.iterrows():
            web_pixel_ids.update(_split_pipe(item.get("web_pixel_ids")))
            web_detected_events.update(_lower(event) for event in _split_pipe(item.get("web_detected_events")))
            if _string(item.get("web_scan_error")):
                web_scan_errors.add(_string(item.get("web_scan_error")))
            if _string(item.get("web_scan_status_code")):
                web_status_codes.add(_string(item.get("web_scan_status_code")))

        rows.append(
            {
                "landing_domain": _string(landing_domain),
                "target_context_key": _string(target_context_key),
                "target_context_label": _string(first.get("target_context_label")),
                "expected_event": _lower(expected_event),
                "ad_count": int(len(group)),
                "ad_ids": sorted({_string(value) for value in group.get("ad_id", []) if _string(value)}),
                "adset_ids": sorted({_string(value) for value in group.get("adset_id", []) if _string(value)}),
                "campaign_ids": sorted({_string(value) for value in group.get("campaign_id", []) if _string(value)}),
                "landing_urls": sorted({_string(value) for value in group.get("landing_url", []) if _string(value)})[:10],
                "target_known": bool(first.get("target_known")),
                "target_is_current_context": bool(first.get("target_is_current_context")),
                "gtm_account_id": _string(first.get("gtm_account_id")),
                "gtm_container_id": _string(first.get("gtm_container_id")),
                "raw_gtm_tags_count": int(first.get("raw_gtm_tags_count") or 0),
                "meta_gtm_tags_count": int(first.get("meta_gtm_tags_count") or 0),
                "gtm_pixel_ids": _split_pipe(first.get("gtm_pixel_ids")),
                "gtm_event_names": [_lower(item) for item in _split_pipe(first.get("gtm_event_names"))],
                "web_scan_count": int(len(group)),
                "web_scan_ok": any(_truthy(value) for value in group.get("web_scan_ok", [])),
                "web_pixel_present": any(_truthy(value) for value in group.get("web_pixel_present", [])),
                "web_expected_pixel_found": any(_truthy(value) for value in group.get("web_expected_pixel_found", [])),
                "web_pixel_ids": sorted(web_pixel_ids),
                "web_detected_events": sorted(web_detected_events),
                "web_scan_status_codes": sorted(web_status_codes),
                "web_scan_errors": sorted(web_scan_errors),
            }
        )
    return rows

def _build_domain_aware_findings(
    *,
    context: AccountContext,
    adsets: pd.DataFrame,
    landing_targets: pd.DataFrame,
) -> list[MetaAuditFinding]:
    findings: list[MetaAuditFinding] = []
    expected_pixel_ids = _expected_pixel_ids(context)

    for target in _domain_target_groups(landing_targets):
        landing_domain = target["landing_domain"]
        if not landing_domain:
            continue

        if not target["target_known"]:
            findings.append(
                MetaAuditFinding(
                    source="meta_ads",
                    severity="medium",
                    rule_code="META_AD_LANDING_DOMAIN_UNMAPPED",
                    title="Cílová doména reklamy není namapovaná v account contextech",
                    description=(
                        "Reklama vede na doménu, kterou nelze přiřadit k žádnému account contextu podle source_domains. "
                        "Audit proto neví, proti kterému GTM kontejneru má kontrolovat Meta Pixel/eventy."
                    ),
                    affected_object_type="domain",
                    affected_object_id=landing_domain,
                    affected_object_name=landing_domain,
                    evidence_json=target,
                    recommended_fix="Doplň doménu do source_domains správného account contextu a nastav mu GTM container.",
                )
            )
            continue

        if not target["gtm_container_id"]:
            findings.append(
                MetaAuditFinding(
                    source="gtm",
                    severity="high",
                    rule_code="META_TARGET_CONTEXT_GTM_MISSING",
                    title="Cílový context nemá GTM kontejner",
                    description=(
                        "Reklamy vedou na známou doménu/context, ale tento context nemá vyplněný GTM account/container. "
                        "Nelze spolehlivě zkontrolovat Meta Pixel ani konverzní event."
                    ),
                    affected_object_type="domain",
                    affected_object_id=landing_domain,
                    affected_object_name=landing_domain,
                    evidence_json=target,
                    recommended_fix="Doplň GTM_ACCOUNT_ID/GTM_CONTAINER_ID pro account context cílové domény.",
                )
            )
            continue

        found_pixel_ids = set(target["gtm_pixel_ids"])
        found_event_names = set(target["gtm_event_names"])
        expected_event = target["expected_event"]

        web_pixel_present = bool(target.get("web_pixel_present"))
        web_expected_pixel_found = bool(target.get("web_expected_pixel_found"))
        web_event_names = set(target.get("web_detected_events") or [])

        for pixel_id in expected_pixel_ids:
            if not pixel_id or pixel_id in found_pixel_ids:
                continue

            if web_expected_pixel_found:
                findings.append(
                    MetaAuditFinding(
                        source="web",
                        severity="medium",
                        rule_code="META_PIXEL_FOUND_ON_WEB_NOT_IN_TARGET_GTM",
                        title="Meta Pixel běží na webu, ale není v GTM cílové domény",
                        description=(
                            "Očekávané Pixel ID z Meta mappingu nebylo nalezeno v GTM kontejneru cílového contextu, "
                            "ale live scan landing URL ho našel přímo ve webovém HTML. Pixel tedy pravděpodobně běží mimo kontrolovaný GTM kontejner."
                        ),
                        affected_object_type="pixel",
                        affected_object_id=pixel_id,
                        affected_object_name=pixel_id,
                        pixel_id=pixel_id,
                        evidence_json={
                            **target,
                            "expected_pixel_ids": expected_pixel_ids,
                            "gtm_pixel_ids": sorted(found_pixel_ids),
                            "web_pixel_ids": sorted(target.get("web_pixel_ids") or []),
                        },
                        recommended_fix=(
                            "Rozhodni, jestli je nasazení mimo GTM záměr. Pro auditovatelnost doporučeně přesuň Meta Pixel do GTM, "
                            "nebo nech audit brát live-web scan jako potvrzení základního pixelu."
                        ),
                    )
                )
                continue

            if web_pixel_present:
                findings.append(
                    MetaAuditFinding(
                        source="web",
                        severity="high",
                        rule_code="META_PIXEL_DIFFERENT_OR_UNVERIFIED_ON_WEB",
                        title="Na webu je Meta Pixel, ale očekávané Pixel ID nebylo potvrzeno",
                        description=(
                            "GTM cílové domény očekávaný Pixel ID neobsahuje. Live scan sice našel Meta Pixel signály, "
                            "ale nepotvrdil očekávané Pixel ID z mappingu."
                        ),
                        affected_object_type="pixel",
                        affected_object_id=pixel_id,
                        affected_object_name=pixel_id,
                        pixel_id=pixel_id,
                        evidence_json={
                            **target,
                            "expected_pixel_ids": expected_pixel_ids,
                            "gtm_pixel_ids": sorted(found_pixel_ids),
                            "web_pixel_ids": sorted(target.get("web_pixel_ids") or []),
                        },
                        recommended_fix="Zkontroluj Pixel ID na live webu a v Meta mappingu.",
                    )
                )
                continue

            findings.append(
                MetaAuditFinding(
                    source="gtm",
                    severity="critical",
                    rule_code="META_PIXEL_NOT_FOUND_IN_TARGET_GTM_OR_WEB",
                    title="Meta Pixel nebyl nalezen v GTM ani na live webu cílové domény",
                    description=(
                        "Reklamy z Meta contextu vedou na tuto doménu, ale očekávané Pixel ID z Meta mappingu "
                        "nebylo nalezeno v GTM kontejneru cílového contextu ani jednoduchým live scanem landing URL."
                    ),
                    affected_object_type="pixel",
                    affected_object_id=pixel_id,
                    affected_object_name=pixel_id,
                    pixel_id=pixel_id,
                    evidence_json={
                        **target,
                        "expected_pixel_ids": expected_pixel_ids,
                        "gtm_pixel_ids": sorted(found_pixel_ids),
                        "web_pixel_ids": sorted(target.get("web_pixel_ids") or []),
                    },
                    recommended_fix="Doplň Meta Pixel na cílový web nebo oprav mapování pixelů/domény/contextu.",
                )
            )

        if expected_event in FINAL_EVENTS and expected_event not in found_event_names:
            if web_expected_pixel_found or web_pixel_present:
                findings.append(
                    MetaAuditFinding(
                        source="web",
                        severity="high",
                        rule_code="META_PIXEL_EVENT_NOT_VERIFIED_IN_TARGET_GTM",
                        title="Meta konverzní event není ověřitelný v GTM cílové domény",
                        description=(
                            "Na live webu byl nalezen Meta Pixel, ale očekávaný finální event nebyl nalezen mezi GTM Meta tagy cílové domény. "
                            "U landing stránek to nemusí dokazovat, že finální event reálně chybí; Purchase/Lead je potřeba ověřit na děkovací stránce, "
                            "v Meta Events Manageru nebo přes Pixel Helper/Test Events."
                        ),
                        affected_object_type="domain",
                        affected_object_id=landing_domain,
                        affected_object_name=landing_domain,
                        evidence_json={
                            **target,
                            "expected_conversion_event": expected_event,
                            "gtm_event_names": sorted(found_event_names),
                            "web_event_names": sorted(web_event_names),
                        },
                        recommended_fix=(
                            "Ověř skutečný finální event v Meta Events Manageru/Test Events nebo jej přesuň do GTM, "
                            "aby byl auditovatelný z exportu kontejneru."
                        ),
                    )
                )
            else:
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="critical",
                        rule_code=_event_missing_rule_code(expected_event),
                        title=_event_missing_title(expected_event),
                        description=(
                            "Podle cílové domény reklamy audit očekává tento Meta event v GTM kontejneru cílového contextu, "
                            "ale mezi rozpoznanými Meta tagy/eventy nebyl nalezen a live scan nepotvrdil ani základní Meta Pixel."
                        ),
                        affected_object_type="domain",
                        affected_object_id=landing_domain,
                        affected_object_name=landing_domain,
                        evidence_json={
                            **target,
                            "expected_conversion_event": expected_event,
                            "event_names": sorted(found_event_names),
                        },
                        recommended_fix="Doplň odpovídající Meta Pixel/event do cílového webu nebo GTM, případně oprav mapování domény/contextu.",
                    )
                )

    if not adsets.empty:
        adset_targets: dict[str, list[dict[str, Any]]] = {}
        for _, row in landing_targets.iterrows():
            adset_id = _string(row.get("adset_id"))
            if not adset_id:
                continue
            adset_targets.setdefault(adset_id, []).append(_row_dict(row))

        for _, row in adsets.iterrows():
            adset_id = _string(row.get("id"))
            optimization_goal = _string(row.get("optimization_goal"))
            promoted_object = row.get("promoted_object")

            if not optimization_goal:
                findings.append(
                    MetaAuditFinding(
                        source="meta_ads",
                        severity="high",
                        rule_code="META_ADSET_NO_OPTIMIZATION_EVENT",
                        title="Ad set nemá optimization goal",
                        description="Ad set nemá nastavený optimization goal nebo conversion event.",
                        affected_object_type="adset",
                        affected_object_id=adset_id,
                        affected_object_name=_string(row.get("name")),
                        ad_account_id=_string(row.get("account_id")),
                        adset_id=adset_id,
                        evidence_json={
                            "optimization_goal": optimization_goal,
                            "promoted_object": promoted_object,
                        },
                        recommended_fix="Zkontroluj optimization goal a promoted object v Meta Ads.",
                    )
                )
                continue

            expected_events = sorted(
                {
                    _lower(item.get("expected_event"))
                    for item in adset_targets.get(adset_id, [])
                    if _lower(item.get("expected_event")) in FINAL_EVENTS
                }
            )
            if not expected_events:
                continue

            combined_text = " ".join(
                [
                    optimization_goal,
                    _string(promoted_object),
                    _string(row.get("billing_event")),
                    _string(row.get("destination_type")),
                ]
            ).lower()

            for expected_event in expected_events:
                if _adset_event_matches(expected_event, combined_text):
                    continue
                findings.append(
                    MetaAuditFinding(
                        source="meta_ads",
                        severity=_adset_mismatch_severity(expected_event),
                        rule_code="META_ADSET_EXPECTED_EVENT_BY_LANDING_DOMAIN_MISMATCH",
                        title="Ad set neodpovídá očekávanému eventu podle cílové domény",
                        description=(
                            "Očekávaný event je odvozený z landing domény reklam v ad setu, ne napevno z Meta contextu. "
                            "Ad set ale nemá zjevnou vazbu na tento event v optimization goal / promoted object / billing event."
                        ),
                        affected_object_type="adset",
                        affected_object_id=adset_id,
                        affected_object_name=_string(row.get("name")),
                        ad_account_id=_string(row.get("account_id")),
                        adset_id=adset_id,
                        evidence_json={
                            "expected_conversion_event": expected_event,
                            "optimization_goal": optimization_goal,
                            "promoted_object": promoted_object,
                            "billing_event": row.get("billing_event"),
                            "destination_type": row.get("destination_type"),
                            "landing_targets": adset_targets.get(adset_id, []),
                        },
                        recommended_fix="Zkontroluj cíl kampaně/ad setu vůči tomu, kam reklamy reálně vedou a co se na cílové doméně měří.",
                    )
                )

    return findings


def build_meta_audit_findings(
    *,
    context: AccountContext,
    campaigns: pd.DataFrame,
    adsets: pd.DataFrame,
    ads: pd.DataFrame,
    creatives: pd.DataFrame,
    catalogs: pd.DataFrame,
    product_feeds: pd.DataFrame,
    gtm_tags: pd.DataFrame | None = None,
    landing_targets: pd.DataFrame | None = None,
    target_gtm_tags: pd.DataFrame | None = None,
    raw_gtm_tags_count: int = 0,
) -> list[MetaAuditFinding]:
    findings: list[MetaAuditFinding] = []

    expected_event = _expected_conversion_event(context)
    expected_pixel_ids = _expected_pixel_ids(context)

    if _landing_targets_available(landing_targets):
        assert landing_targets is not None
        findings.extend(
            _build_domain_aware_findings(
                context=context,
                adsets=adsets,
                landing_targets=landing_targets,
            )
        )
    else:
        if not adsets.empty:
            for _, row in adsets.iterrows():
                optimization_goal = _string(row.get("optimization_goal"))
                promoted_object = row.get("promoted_object")

                if not optimization_goal:
                    findings.append(
                        MetaAuditFinding(
                            source="meta_ads",
                            severity="high",
                            rule_code="META_ADSET_NO_OPTIMIZATION_EVENT",
                            title="Ad set nemá optimization goal",
                            description="Ad set nemá nastavený optimization goal nebo conversion event.",
                            affected_object_type="adset",
                            affected_object_id=_string(row.get("id")),
                            affected_object_name=_string(row.get("name")),
                            ad_account_id=_string(row.get("account_id")),
                            adset_id=_string(row.get("id")),
                            evidence_json={
                                "optimization_goal": optimization_goal,
                                "promoted_object": promoted_object,
                            },
                            recommended_fix="Zkontroluj optimization goal a promoted object v Meta Ads.",
                        )
                    )

                if expected_event:
                    combined_text = " ".join(
                        [
                            optimization_goal,
                            _string(promoted_object),
                            _string(row.get("billing_event")),
                        ]
                    ).lower()

                    if not _adset_event_matches(expected_event, combined_text):
                        findings.append(
                            MetaAuditFinding(
                                source="meta_ads",
                                severity="high",
                                rule_code="META_ADSET_EXPECTED_CONVERSION_EVENT_MISMATCH",
                                title="Ad set neodpovídá očekávanému konverznímu eventu",
                                description="Ad set podle mapování nemá zjevnou vazbu na očekávaný konverzní event.",
                                affected_object_type="adset",
                                affected_object_id=_string(row.get("id")),
                                affected_object_name=_string(row.get("name")),
                                ad_account_id=_string(row.get("account_id")),
                                adset_id=_string(row.get("id")),
                                evidence_json={
                                    "expected_conversion_event": expected_event,
                                    "optimization_goal": optimization_goal,
                                    "promoted_object": promoted_object,
                                    "billing_event": row.get("billing_event"),
                                },
                                recommended_fix="Zkontroluj optimization goal, promoted object a mapování kontextu.",
                            )
                        )

    if not catalogs.empty and product_feeds.empty:
        for _, row in catalogs.iterrows():
            findings.append(
                MetaAuditFinding(
                    source="meta_catalog",
                    severity="high",
                    rule_code="META_CATALOG_NO_ACTIVE_FEED",
                    title="Katalog nemá aktivní feed",
                    description="Katalog byl nalezen, ale nejsou k němu dostupné žádné product feedy.",
                    affected_object_type="catalog",
                    affected_object_id=_string(row.get("id")),
                    affected_object_name=_string(row.get("name")),
                    catalog_id=_string(row.get("id")),
                    evidence_json=_row_dict(row),
                    recommended_fix="Zkontroluj napojení feedu nebo Catalog Batch API.",
                )
            )

    combined_gtm_tags = _combined_gtm_frame(gtm_tags, target_gtm_tags)
    gtm_has_tags = _gtm_tags_available(combined_gtm_tags)

    found_pixel_ids: set[str] = set()
    event_names: set[str] = set()

    if gtm_has_tags:
        assert combined_gtm_tags is not None
        found_pixel_ids = {
            _string(row.get("pixel_id"))
            for _, row in combined_gtm_tags.iterrows()
            if _string(row.get("pixel_id"))
        }
        event_names = {
            _lower(row.get("event_name"))
            for _, row in combined_gtm_tags.iterrows()
            if _string(row.get("event_name"))
        }

    # Fallback global GTM checks only when domain-aware targets are not available.
    # When landing_targets exists, GTM checks are evaluated against each landing domain/context above.
    if not _landing_targets_available(landing_targets):
        if expected_pixel_ids:
            for pixel_id in expected_pixel_ids:
                if pixel_id not in found_pixel_ids:
                    findings.append(
                        MetaAuditFinding(
                            source="gtm",
                            severity="critical",
                            rule_code="META_PIXEL_NOT_FOUND_IN_GTM",
                            title="Meta Pixel nebyl nalezen v GTM",
                            description=(
                                "Pixel namapovaný v Meta kontextu nebyl nalezen mezi Meta tagy v GTM. "
                                "Pokud raw GTM tagy existují, problém bude spíš v chybějícím Meta Pixel tagu nebo parseru, "
                                "ne v načtení GTM kontejneru."
                            ),
                            affected_object_type="pixel",
                            affected_object_id=pixel_id,
                            affected_object_name=pixel_id,
                            pixel_id=pixel_id,
                            evidence_json={
                                "expected_pixel_ids": expected_pixel_ids,
                                "gtm_pixel_ids": sorted(found_pixel_ids),
                                "gtm_tags_available": gtm_has_tags,
                                "gtm_meta_tags_count": int(len(combined_gtm_tags)) if combined_gtm_tags is not None else 0,
                                "raw_gtm_tags_count": int(raw_gtm_tags_count),
                            },
                            recommended_fix="Zkontroluj GTM kontejner, Meta Pixel tagy a Meta mapping pixelů.",
                        )
                    )

            unexpected_pixel_ids = sorted(found_pixel_ids.difference(set(expected_pixel_ids)))
            if unexpected_pixel_ids:
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="medium",
                        rule_code="META_PIXEL_ID_MISMATCH",
                        title="V GTM jsou i jiná Meta Pixel ID",
                        description="GTM obsahuje Pixel ID, která nejsou součástí mapování Meta kontextu.",
                        affected_object_type="pixel",
                        affected_object_id=",".join(unexpected_pixel_ids),
                        affected_object_name="Meta Pixel mismatch",
                        evidence_json={
                            "expected_pixel_ids": expected_pixel_ids,
                            "gtm_pixel_ids": sorted(found_pixel_ids),
                        },
                        recommended_fix="Zkontroluj, zda GTM kontejner a Meta mapping patří ke stejné doméně a účtu.",
                    )
                )

        if expected_event == "purchase" and "purchase" not in event_names:
            findings.append(
                MetaAuditFinding(
                    source="gtm",
                    severity="critical",
                    rule_code="META_PIXEL_PURCHASE_MISSING",
                    title="V GTM chybí Meta Purchase event",
                    description="Kontext očekává Purchase event, ale mezi Meta tagy v GTM nebyl nalezen.",
                    affected_object_type="pixel",
                    affected_object_id="purchase",
                    affected_object_name="Purchase",
                    evidence_json={
                        "expected_conversion_event": expected_event,
                        "event_names": sorted(event_names),
                        "gtm_tags_available": gtm_has_tags,
                        "gtm_meta_tags_count": int(len(combined_gtm_tags)) if combined_gtm_tags is not None else 0,
                        "raw_gtm_tags_count": int(raw_gtm_tags_count),
                    },
                    recommended_fix="Doplň Purchase event do GTM nebo oprav mapování kontextu.",
                )
            )

        if expected_event == "lead" and "lead" not in event_names:
            findings.append(
                MetaAuditFinding(
                    source="gtm",
                    severity="critical",
                    rule_code="META_PIXEL_LEAD_MISSING",
                    title="V GTM chybí Meta Lead event",
                    description="Kontext očekává Lead event, ale mezi Meta tagy v GTM nebyl nalezen.",
                    affected_object_type="pixel",
                    affected_object_id="lead",
                    affected_object_name="Lead",
                    evidence_json={
                        "expected_conversion_event": expected_event,
                        "event_names": sorted(event_names),
                        "gtm_tags_available": gtm_has_tags,
                        "gtm_meta_tags_count": int(len(combined_gtm_tags)) if combined_gtm_tags is not None else 0,
                        "raw_gtm_tags_count": int(raw_gtm_tags_count),
                    },
                    recommended_fix="Doplň Lead event do GTM nebo oprav mapování kontextu.",
                )
            )

    if gtm_has_tags:
        assert combined_gtm_tags is not None
        for _, row in combined_gtm_tags.iterrows():
            event_name = _lower(row.get("event_name"))
            row_evidence = _row_dict(row)

            if event_name == "purchase" and not bool(row.get("value_present")):
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="high",
                        rule_code="META_PIXEL_VALUE_MISSING",
                        title="Meta Purchase event nemá value",
                        description="Purchase tag nemá zjevné value pole v GTM parametrech.",
                        affected_object_type="tag",
                        affected_object_id=_string(row.get("tag_id")),
                        affected_object_name=_string(row.get("tag_name")),
                        evidence_json=row_evidence,
                        recommended_fix="Doplň do Purchase eventu parametr value.",
                    )
                )

            if event_name == "purchase" and not bool(row.get("currency_present")):
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="high",
                        rule_code="META_PIXEL_CURRENCY_MISSING",
                        title="Meta Purchase event nemá currency",
                        description="Purchase tag nemá zjevné currency pole v GTM parametrech.",
                        affected_object_type="tag",
                        affected_object_id=_string(row.get("tag_id")),
                        affected_object_name=_string(row.get("tag_name")),
                        evidence_json=row_evidence,
                        recommended_fix="Doplň do Purchase eventu parametr currency.",
                    )
                )

            if event_name == "purchase" and not bool(row.get("content_ids_present")):
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="medium",
                        rule_code="META_PIXEL_CONTENT_IDS_MISSING",
                        title="Meta Purchase event nemá content_ids",
                        description="Purchase tag nemá zjevné content_ids pole v GTM parametrech.",
                        affected_object_type="tag",
                        affected_object_id=_string(row.get("tag_id")),
                        affected_object_name=_string(row.get("tag_name")),
                        evidence_json=row_evidence,
                        recommended_fix="Doplň do Purchase eventu parametr content_ids.",
                    )
                )

            if event_name in {"purchase", "lead"} and not bool(row.get("event_id_present")):
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="high",
                        rule_code="META_PIXEL_CAPI_NO_EVENT_ID",
                        title="Meta event nemá event_id pro deduplikaci",
                        description=(
                            "Meta tag nemá zjevné event_id pole, takže hrozí problém "
                            "s deduplikací browser/server eventu."
                        ),
                        affected_object_type="tag",
                        affected_object_id=_string(row.get("tag_id")),
                        affected_object_name=_string(row.get("tag_name")),
                        evidence_json=row_evidence,
                        recommended_fix="Doplň event_id a sjednoť ho mezi browser a server eventem.",
                    )
                )

            if not _string(row.get("consent_settings")):
                findings.append(
                    MetaAuditFinding(
                        source="gtm",
                        severity="medium",
                        rule_code="META_PIXEL_CONSENT_RISK",
                        title="Meta tag nemá zjevné consent nastavení",
                        description="U Meta tagu nebylo nalezeno consent nastavení v exportu GTM.",
                        affected_object_type="tag",
                        affected_object_id=_string(row.get("tag_id")),
                        affected_object_name=_string(row.get("tag_name")),
                        evidence_json=row_evidence,
                        recommended_fix="Zkontroluj consent settings a firing podmínky Meta tagu.",
                    )
                )

    if not ads.empty:
        for _, row in ads.iterrows():
            landing_url = _string(row.get("landing_page_url") or row.get("link_url"))
            if not landing_url:
                findings.append(
                    MetaAuditFinding(
                        source="meta_ads",
                        severity="medium",
                        rule_code="META_AD_MISSING_LANDING_URL",
                        title="Reklama nemá landing URL",
                        description="U reklamy chybí cílová URL nebo ji nebylo možné z kreativy přečíst.",
                        affected_object_type="ad",
                        affected_object_id=_string(row.get("id")),
                        affected_object_name=_string(row.get("name")),
                        ad_id=_string(row.get("id")),
                        adset_id=_string(row.get("adset_id")),
                        campaign_id=_string(row.get("campaign_id")),
                        evidence_json=_row_dict(row),
                        recommended_fix="Zkontroluj creative a object story spec.",
                    )
                )

    return findings
