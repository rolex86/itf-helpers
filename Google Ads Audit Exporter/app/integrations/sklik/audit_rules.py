from __future__ import annotations

from typing import Any

import pandas as pd

from app.integrations.sklik.models import SklikAccountContextMapping, SklikAuditFinding, SklikConnection
from app.integrations.sklik.normalizers import normalize_domain


def _frame(datasets: dict[str, Any], key: str) -> pd.DataFrame:
    value = datasets.get(key)
    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, list):
        return pd.DataFrame(value)
    if isinstance(value, dict):
        return pd.DataFrame([value])
    return pd.DataFrame()


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _append_finding(findings: list[SklikAuditFinding], **kwargs: Any) -> None:
    findings.append(SklikAuditFinding(**kwargs))


def _gtm_has_sklik_signal(gtm_crosscheck: dict[str, Any]) -> bool:
    return bool(
        gtm_crosscheck.get("matched")
        or gtm_crosscheck.get("has_valid_sklik_tag")
        or gtm_crosscheck.get("has_sem_tag")
        or gtm_crosscheck.get("has_sklik_conversion_template")
        or gtm_crosscheck.get("has_legacy_conversion_script")
        or gtm_crosscheck.get("has_legacy_retargeting_script")
        or gtm_crosscheck.get("found_tags")
    )


def _classify_gtm_warning(warning_text: str, *, gtm_has_signal: bool) -> tuple[str, str]:
    lowered = warning_text.lower()

    if "legacy" in lowered and "retargeting" in lowered:
        return (
            "SKLIK_GTM_LEGACY_RETARGETING",
            "Legacy Sklik retargeting v GTM",
        )

    if "legacy" in lowered and ("konverz" in lowered or "conversion" in lowered):
        return (
            "SKLIK_GTM_LEGACY_CONVERSION",
            "Legacy Seznam konverzni skript v GTM",
        )

    if "consent" in lowered or "ad_storage" in lowered:
        return (
            "SKLIK_GTM_CONSENT_NEEDS_CHECK",
            "Sklik GTM consent potrebuje kontrolu",
        )

    no_tag_signal = (
        "nenasel zadne" in lowered
        or "nenašel žádné" in lowered
        or "chybi sklik" in lowered
        or "chybí sklik" in lowered
        or "no seznam" in lowered
        or "no sklik" in lowered
    )
    if no_tag_signal and not gtm_has_signal:
        return (
            "SKLIK_GTM_NO_SEZNAM_TAGS",
            "V GTM chybi Sklik tagy",
        )

    return (
        "SKLIK_GTM_TAGS_NEED_CHECK",
        "Sklik GTM tagy potrebuji kontrolu",
    )


def _append_gtm_finding_once(
    findings: list[SklikAuditFinding],
    seen_codes: set[str],
    *,
    code: str,
    title: str,
    message: str,
    mapping: SklikAccountContextMapping,
    gtm_crosscheck: dict[str, Any],
    severity: str = "warning",
) -> None:
    if code in seen_codes:
        return
    seen_codes.add(code)
    _append_finding(
        findings,
        severity=severity,
        category="measurement",
        code=code,
        title=title,
        message=message,
        context_key=mapping.context_key,
        evidence=gtm_crosscheck,
    )


def build_audit_findings(
    *,
    connection: SklikConnection,
    mapping: SklikAccountContextMapping,
    datasets: dict[str, Any],
    gtm_crosscheck: dict[str, Any] | None,
    web_scan_rows: list[dict[str, Any]],
    export_warnings: list[str],
    export_info_notes: list[str],
) -> list[SklikAuditFinding]:
    findings: list[SklikAuditFinding] = []

    if connection.drak_enabled and not mapping.drak_user_ids:
        _append_finding(
            findings,
            severity="error",
            category="mapping",
            code="SKLIK_MAPPING_NO_USER_ID",
            title="Chybi Drak user ID",
            message="Sklik mapping nema vyplneny zadny Drak user ID pro export.",
            context_key=mapping.context_key,
        )

    if mapping.enable_fenix and not mapping.fenix_premise_ids:
        _append_finding(
            findings,
            severity="warning",
            category="mapping",
            code="SKLIK_MAPPING_NO_PREMISE_ID",
            title="Chybi Fenix premise ID",
            message="Fenix export je zapnuty, ale mapping nema premise ID.",
            context_key=mapping.context_key,
        )

    if mapping.enable_web_scan and not mapping.expected_domains:
        _append_finding(
            findings,
            severity="error",
            category="mapping",
            code="SKLIK_MAPPING_NO_EXPECTED_DOMAIN",
            title="Chybi expected domain",
            message="Web scan je zapnuty, ale mapping nema vyplnene expected domains.",
            context_key=mapping.context_key,
        )

    utm_settings = _frame(datasets, "utm_settings_audit")
    if not utm_settings.empty:
        for _, row in utm_settings.iterrows():
            row_dict = row.to_dict()
            entity_id = str(row_dict.get("user_id") or "")

            enabled = _as_bool(row_dict.get("enabled"))
            if enabled is False:
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_AUTOTAGGING_DISABLED",
                    title="Autotagging je vypnuty",
                    message="Drak autotagging neni aktivni pro tento userId.",
                    context_key=mapping.context_key,
                    entity_type="user",
                    entity_id=entity_id,
                    evidence=row_dict,
                )

            utm_campaign_enabled = _as_bool(row_dict.get("utm_campaign_enabled"))
            if utm_campaign_enabled is False:
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_UTM_CAMPAIGN_DISABLED",
                    title="utm_campaign je vypnute",
                    message="Autotagging ma vypnute doplnovani utm_campaign.",
                    context_key=mapping.context_key,
                    entity_type="user",
                    entity_id=entity_id,
                    evidence=row_dict,
                )

            utm_source_value = str(row_dict.get("utm_source_value") or "").strip().lower()
            expected_sources = {
                str(item or "").strip().lower()
                for item in row_dict.get("expected_utm_source") or []
                if str(item or "").strip()
            }
            if utm_source_value and expected_sources and utm_source_value not in expected_sources:
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_UTM_SOURCE_NONSTANDARD",
                    title="Nestandardni utm_source",
                    message="Autotagging vraci utm_source mimo ocekavane hodnoty mappingu.",
                    context_key=mapping.context_key,
                    entity_type="user",
                    entity_id=entity_id,
                    evidence=row_dict,
                )

            utm_medium_value = str(row_dict.get("utm_medium_value") or "").strip().lower()
            expected_mediums = {
                str(item or "").strip().lower()
                for item in row_dict.get("expected_utm_medium") or []
                if str(item or "").strip()
            }
            if utm_medium_value and expected_mediums and utm_medium_value not in expected_mediums:
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_UTM_MEDIUM_NONSTANDARD",
                    title="Nestandardni utm_medium",
                    message="Autotagging vraci utm_medium mimo ocekavane hodnoty mappingu.",
                    context_key=mapping.context_key,
                    entity_type="user",
                    entity_id=entity_id,
                    evidence=row_dict,
                )

    autotagging_default = _frame(datasets, "autotagging_default")
    if not autotagging_default.empty:
        for _, row in autotagging_default.iterrows():
            row_dict = row.to_dict()
            if _as_bool(row_dict.get("enabled")) is False:
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_AUTOTAGGING_DEFAULT_DISABLED",
                    title="Vychozi autotagging je vypnuty",
                    message="autotagging.default.get vraci vypnutou vychozi konfiguraci.",
                    context_key=mapping.context_key,
                    entity_type="user",
                    entity_id=str(row_dict.get("user_id") or ""),
                    evidence=row_dict,
                )

    utm_audit = _frame(datasets, "utm_audit")
    if not utm_audit.empty:
        missing_utm_rows: list[dict[str, Any]] = []
        missing_utm_counts: dict[str, int] = {}
        out_of_context_domains: dict[str, int] = {}

        for _, row in utm_audit.iterrows():
            row_dict = row.to_dict()
            issue_code = str(row_dict.get("issue_code") or "").strip()
            final_domain = normalize_domain(row_dict.get("final_domain") or "")
            entity_id = str(row_dict.get("ad_id") or "")

            if issue_code == "out_of_context_domain":
                if final_domain:
                    out_of_context_domains[final_domain] = out_of_context_domains.get(final_domain, 0) + 1
                continue

            if issue_code == "domain_mismatch":
                _append_finding(
                    findings,
                    severity="error",
                    category="tracking",
                    code="SKLIK_AD_FINAL_URL_WRONG_DOMAIN",
                    title="Reklama miri na spatnou domenu",
                    message="Landing page domena neodpovida expected domains v mappingu.",
                    context_key=mapping.context_key,
                    entity_type="ad",
                    entity_id=entity_id,
                    evidence={"final_domain": final_domain, "row": row_dict},
                )
            elif issue_code in {"missing_utm_source", "missing_utm_medium", "missing_utm_campaign"}:
                missing_utm_rows.append(row_dict)
                missing_utm_counts[issue_code] = missing_utm_counts.get(issue_code, 0) + 1
            elif issue_code == "utm_source_mismatch":
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_UTM_SOURCE_NONSTANDARD",
                    title="Nestandardni utm_source",
                    message="Landing page URL ma neocekavane utm_source pro tento context.",
                    context_key=mapping.context_key,
                    entity_type="ad",
                    entity_id=entity_id,
                    evidence=row_dict,
                )
            elif issue_code == "utm_medium_mismatch":
                _append_finding(
                    findings,
                    severity="warning",
                    category="measurement",
                    code="SKLIK_UTM_MEDIUM_NONSTANDARD",
                    title="Nestandardni utm_medium",
                    message="Landing page URL ma neocekavane utm_medium pro tento context.",
                    context_key=mapping.context_key,
                    entity_type="ad",
                    entity_id=entity_id,
                    evidence=row_dict,
                )

        if out_of_context_domains:
            _append_finding(
                findings,
                severity="info",
                category="tracking",
                code="SKLIK_OUT_OF_CONTEXT_DOMAINS_FOUND",
                title="Sklik ucet obsahuje landing pages mimo tento context",
                message="Web scan nasel reklamy mirici na domeny mimo expected domains. U mixed uctu je to informacni finding, ne chyba ShopID mappingu.",
                context_key=mapping.context_key,
                evidence={
                    "expected_domains": list(mapping.expected_domains),
                    "domains": out_of_context_domains,
                    "row_count": int(sum(out_of_context_domains.values())),
                },
            )

        if missing_utm_rows:
            _append_finding(
                findings,
                severity="warning",
                category="measurement",
                code="SKLIK_UTM_MISSING_ON_ADS",
                title="Nektere reklamy nemaji kompletni UTM v URL",
                message="UTM chyby jsou agregovane do jednoho findingu; detail je v utm_audit.csv.",
                context_key=mapping.context_key,
                evidence={
                    "issue_counts": missing_utm_counts,
                    "row_count": int(len(missing_utm_rows)),
                    "sample": missing_utm_rows[:10],
                },
            )

    if gtm_crosscheck:
        seen_gtm_codes: set[str] = set()
        gtm_has_signal = _gtm_has_sklik_signal(gtm_crosscheck)

        if not gtm_has_signal:
            _append_gtm_finding_once(
                findings,
                seen_gtm_codes,
                code="SKLIK_GTM_NO_SEZNAM_TAGS",
                title="V GTM chybi Sklik tagy",
                message="GTM cross-check nenasel zadny Sklik/Seznam tag.",
                mapping=mapping,
                gtm_crosscheck=gtm_crosscheck,
            )

        for warning in gtm_crosscheck.get("warnings", []) or []:
            warning_text = str(warning or "").strip()
            if not warning_text:
                continue

            code, title = _classify_gtm_warning(warning_text, gtm_has_signal=gtm_has_signal)

            # Safety guard: never report "no Sklik tags" when GTM cross-check found a Sklik signal.
            if code == "SKLIK_GTM_NO_SEZNAM_TAGS" and gtm_has_signal:
                code = "SKLIK_GTM_TAGS_NEED_CHECK"
                title = "Sklik GTM tagy potrebuji kontrolu"

            _append_gtm_finding_once(
                findings,
                seen_gtm_codes,
                code=code,
                title=title,
                message=warning_text,
                mapping=mapping,
                gtm_crosscheck=gtm_crosscheck,
            )

        if bool(gtm_crosscheck.get("has_legacy_retargeting_script")):
            _append_gtm_finding_once(
                findings,
                seen_gtm_codes,
                code="SKLIK_GTM_LEGACY_RETARGETING",
                title="Legacy Sklik retargeting v GTM",
                message="GTM obsahuje legacy Sklik retargeting HTML tag ke kontrole / nahrade.",
                mapping=mapping,
                gtm_crosscheck=gtm_crosscheck,
            )

        if bool(gtm_crosscheck.get("has_legacy_conversion_script")):
            _append_gtm_finding_once(
                findings,
                seen_gtm_codes,
                code="SKLIK_GTM_LEGACY_CONVERSION",
                title="Legacy Seznam konverzni skript v GTM",
                message="GTM obsahuje legacy Seznam konverzni skript ke kontrole / nahrade.",
                mapping=mapping,
                gtm_crosscheck=gtm_crosscheck,
            )

    if web_scan_rows:
        if not any(bool(row.get("has_sem")) for row in web_scan_rows):
            _append_finding(
                findings,
                severity="warning",
                category="measurement",
                code="SKLIK_SEM_NOT_FOUND_ON_WEB",
                title="SEM skript nebyl nalezen na webu",
                message="Web scan nenasel sul.js ani jiny signal Seznam Event Measurement.",
                context_key=mapping.context_key,
            )
        if any(bool(row.get("has_old_retargeting")) for row in web_scan_rows):
            _append_finding(
                findings,
                severity="warning",
                category="measurement",
                code="SKLIK_OLD_RETARGETING_SCRIPT_FOUND",
                title="Na webu je stary retargeting script",
                message="Web scan nasel stary retargeting script bez potvrzeneho SEM.",
                context_key=mapping.context_key,
            )

    fenix_status = datasets.get("fenix_status") if isinstance(datasets.get("fenix_status"), dict) else {}
    fenix_campaigns = _frame(datasets, "fenix_campaigns")

    if fenix_status:
        skipped_reason = str(fenix_status.get("skipped_reason") or "").strip()
        requested_by_ui = bool(fenix_status.get("requested_by_ui"))
        mapping_enabled = bool(fenix_status.get("mapping_enabled"))
        effective_enabled = bool(fenix_status.get("effective_enabled"))

        if requested_by_ui and mapping_enabled and not effective_enabled:
            if skipped_reason == "missing_refresh_token":
                _append_finding(
                    findings,
                    severity="warning",
                    category="fenix",
                    code="SKLIK_FENIX_REFRESH_TOKEN_MISSING",
                    title="Fenix refresh token chybi",
                    message="Fenix je pro context zapnuty, ale chybi refresh token v konfiguraci.",
                    context_key=mapping.context_key,
                    entity_type="context",
                    entity_id=mapping.context_key,
                    evidence=fenix_status,
                )
            elif skipped_reason == "missing_premise_ids":
                _append_finding(
                    findings,
                    severity="warning",
                    category="fenix",
                    code="SKLIK_FENIX_PREMISE_ID_MISSING",
                    title="Fenix premiseId chybi",
                    message="Fenix je pro context zapnuty, ale mapping nema vyplnene premiseId.",
                    context_key=mapping.context_key,
                    entity_type="context",
                    entity_id=mapping.context_key,
                    evidence=fenix_status,
                )
            else:
                _append_finding(
                    findings,
                    severity="info",
                    category="fenix",
                    code="SKLIK_FENIX_SKIPPED",
                    title="Fenix export byl preskocen",
                    message=f"Fenix export nebyl efektivne spusten: {skipped_reason or 'unknown'}.",
                    context_key=mapping.context_key,
                    entity_type="context",
                    entity_id=mapping.context_key,
                    evidence=fenix_status,
                )

        if effective_enabled and mapping.fenix_premise_ids and fenix_campaigns.empty:
            _append_finding(
                findings,
                severity="warning",
                category="fenix",
                code="SKLIK_FENIX_NO_CAMPAIGNS",
                title="Fenix export nenasel kampane",
                message="Fenix cast exportu nevratila zadne shopping kampane pro zadane premiseId.",
                context_key=mapping.context_key,
                entity_type="context",
                entity_id=mapping.context_key,
                evidence=fenix_status,
            )

    for warning in export_warnings:
        text = str(warning or "").strip()
        if not text:
            continue
        if "conversions.list" in text:
            _append_finding(
                findings,
                severity="warning",
                category="measurement",
                code="SKLIK_CONVERSIONS_LIST_UNAVAILABLE_SEM_OR_PERMISSION",
                title="conversions.list neni dostupne",
                message=text,
                context_key=mapping.context_key,
            )
        elif "FENIX_PREMISES_AUTODISCOVERY_NOT_CONFIRMED_BY_PUBLIC_DOCS" in text:
            _append_finding(
                findings,
                severity="info",
                category="fenix",
                code="SKLIK_FENIX_PREMISE_AUTODISCOVERY_UNCONFIRMED",
                title="Fenix premise autodiscovery neni potvrzene",
                message="Fenix premiseId je potreba zadat rucne v mappingu.",
                context_key=mapping.context_key,
            )

    if not findings and export_info_notes:
        _append_finding(
            findings,
            severity="info",
            category="reporting",
            code="SKLIK_EXPORT_INFO",
            title="Export probehl bez heuristickych nalezu",
            message="Sklik export probehl bez explicitnich findings; zkontroluj info poznamky a raw data pro detail.",
            context_key=mapping.context_key,
            evidence={"notes": export_info_notes},
        )

    return findings