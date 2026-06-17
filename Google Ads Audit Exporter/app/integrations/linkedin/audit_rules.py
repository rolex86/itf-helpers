from __future__ import annotations

import json
from typing import Any

import pandas as pd

from app.integrations.linkedin.auth import token_expires_within
from app.integrations.linkedin.models import LinkedInAccountContextMapping, LinkedInAuditFinding, LinkedInConnection
from app.integrations.linkedin.normalizers import sanitize_pii_for_report


LOW_CTR_THRESHOLD = 0.002
HIGH_CPC_THRESHOLD = 150.0
HIGH_CPL_THRESHOLD = 3000.0
MIN_SPEND_FOR_ZERO_CONVERSION_WARNING = 500.0
MIN_CLICKS_FOR_ZERO_CONVERSION_WARNING = 20
MIN_IMPRESSIONS_FOR_LOW_CTR_WARNING = 1000


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(value: object) -> str:
    return str(value or "").strip()


def _is_truthy_status(value: object) -> bool:
    return _safe_str(value).upper() in {"ACTIVE", "ENABLED", "RUNNING"}


def _column_value(row: pd.Series, *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return ""


def _row_evidence(row: pd.Series, allowed_keys: list[str] | None = None) -> dict[str, Any]:
    payload = row.to_dict()

    if allowed_keys:
        payload = {key: payload.get(key) for key in allowed_keys if key in payload}

    return sanitize_pii_for_report(payload)


def _row_text(row: pd.Series | dict[str, Any]) -> str:
    try:
        payload = row.to_dict() if isinstance(row, pd.Series) else dict(row)
        return json.dumps(payload, ensure_ascii=False, default=str).lower()
    except Exception:
        return str(row).lower()


def _append_finding(
    findings: list[LinkedInAuditFinding],
    *,
    severity: str,
    category: str,
    code: str,
    title: str,
    detail: str,
    recommendation: str = "",
    entity_type: str = "",
    entity_id: str = "",
    entity_name: str = "",
    evidence: dict[str, Any] | None = None,
) -> None:
    findings.append(
        LinkedInAuditFinding(
            severity=severity,
            category=category,
            code=code,
            title=title,
            detail=detail,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            recommendation=recommendation,
            evidence=sanitize_pii_for_report(evidence or {}),
        )
    )


def _total_conversions(row: pd.Series) -> float:
    return sum(
        _safe_float(_column_value(row, key))
        for key in (
            "externalWebsiteConversions",
            "externalWebsitePostClickConversions",
            "externalWebsitePostViewConversions",
            "oneClickLeads",
            "leads_or_conversions",
            "conversions",
        )
    )


def _campaign_entity_id(row: pd.Series) -> str:
    return _safe_str(
        _column_value(
            row,
            "campaign_id",
            "pivot_value_1_id",
            "pivotValues_id",
            "id",
        )
    )


def _campaign_entity_name(row: pd.Series) -> str:
    return _safe_str(
        _column_value(
            row,
            "campaignName",
            "campaign_name",
            "name",
            "pivot_value_1",
        )
    )


def _is_dataframe(value: Any) -> bool:
    return isinstance(value, pd.DataFrame)


def _dataframe(datasets: dict[str, pd.DataFrame], key: str) -> pd.DataFrame:
    value = datasets.get(key, pd.DataFrame())
    return value if _is_dataframe(value) else pd.DataFrame()


def _creative_id_from_row(row: pd.Series) -> str:
    return _safe_str(
        _column_value(
            row,
            "creative_id",
            "creative",
            "creative_urn",
            "id",
        )
    )


def _looks_like_lead_gen_creative(row: pd.Series) -> bool:
    text = _row_text(row)

    return any(
        marker in text
        for marker in (
            "leadgen",
            "lead_gen",
            "lead form",
            "leadform",
            "oneclicklead",
            "adform",
            "urn:li:adform",
            "leadgencalltoaction",
        )
    )


def _lead_gen_creative_ids(creatives: pd.DataFrame) -> set[str]:
    if creatives.empty:
        return set()

    ids: set[str] = set()

    for _, row in creatives.iterrows():
        if not _looks_like_lead_gen_creative(row):
            continue

        creative_id = _creative_id_from_row(row)
        if creative_id:
            ids.add(creative_id)

    return ids


def _is_lead_gen_utm_false_positive(row: pd.Series, *, lead_gen_creative_ids: set[str]) -> bool:
    landing_page_url = _safe_str(_column_value(row, "landing_page_url", "source_url", "final_url", "url"))
    if landing_page_url:
        return False

    creative_id = _safe_str(_column_value(row, "creative_id", "creative", "creative_urn", "id"))
    if creative_id and creative_id in lead_gen_creative_ids:
        return True

    text = _row_text(row)
    return any(
        marker in text
        for marker in (
            "leadgen",
            "lead_gen",
            "lead form",
            "leadform",
            "oneclicklead",
            "adform",
            "urn:li:adform",
        )
    )


def _audit_access(
    findings: list[LinkedInAuditFinding],
    *,
    connection: LinkedInConnection,
    mapping: LinkedInAccountContextMapping,
) -> None:
    if connection.status in {"error", "needs_reauth", "disabled"}:
        _append_finding(
            findings,
            severity="critical" if connection.status == "needs_reauth" else "warning",
            category="access",
            code="LINKEDIN_CONNECTION_NOT_ACTIVE",
            title="LinkedIn connection není aktivní",
            detail=f"Connection má stav `{connection.status}`.",
            recommendation="Spusť test connection, OAuth refresh nebo reautorizaci.",
            evidence={
                "connection_key": connection.key,
                "status": connection.status,
                "last_error": connection.last_error,
            },
        )

    if token_expires_within(connection.token_expires_at, days=7):
        _append_finding(
            findings,
            severity="warning",
            category="access",
            code="LINKEDIN_ACCESS_TOKEN_EXPIRES_SOON",
            title="LinkedIn access token brzy expiruje",
            detail="Access token expiruje do 7 dnů.",
            recommendation="Ověř refresh token nebo proveď reautorizaci connection.",
            evidence={
                "token_expires_at": connection.token_expires_at,
                "refresh_token_expires_at": connection.refresh_token_expires_at,
            },
        )

    if token_expires_within(connection.refresh_token_expires_at, days=30):
        _append_finding(
            findings,
            severity="warning",
            category="access",
            code="LINKEDIN_REFRESH_TOKEN_EXPIRES_SOON",
            title="LinkedIn refresh token brzy expiruje",
            detail="Refresh token expiruje do 30 dnů.",
            recommendation="Naplánuj reautorizaci LinkedIn connection.",
            evidence={
                "refresh_token_expires_at": connection.refresh_token_expires_at,
            },
        )

    if not connection.granted_scopes:
        _append_finding(
            findings,
            severity="warning",
            category="access",
            code="LINKEDIN_SCOPES_UNKNOWN",
            title="Scopes nejsou detekované",
            detail="Connection nemá uložené detekované scopes z posledního testu nebo OAuth callbacku.",
            recommendation="Spusť znovu test connection nebo OAuth refresh.",
        )
        return

    for required_scope in ("r_ads", "r_ads_reporting"):
        if required_scope not in connection.granted_scopes:
            _append_finding(
                findings,
                severity="critical",
                category="access",
                code="LINKEDIN_SCOPE_MISSING",
                title=f"Chybí scope {required_scope}",
                detail="Bez tohoto scope nebude export kompletní nebo nemusí fungovat vůbec.",
                recommendation="Rozšiř oprávnění LinkedIn appky a proveď reautorizaci.",
                evidence={"scope": required_scope},
            )

    if mapping.lead_sync_enabled and "r_marketing_leadgen_automation" not in connection.granted_scopes:
        _append_finding(
            findings,
            severity="warning",
            category="lead_sync",
            code="LINKEDIN_LEAD_SYNC_SCOPE_MISSING",
            title="Lead Sync je zapnutý, ale chybí potřebný scope",
            detail="Lead Sync data budou přeskočená nebo neúplná.",
            recommendation="Požádej o scope r_marketing_leadgen_automation nebo vypni Lead Sync pro tento kontext.",
        )


def _audit_structure(
    findings: list[LinkedInAuditFinding],
    *,
    mapping: LinkedInAccountContextMapping,
    ad_accounts: pd.DataFrame,
    campaign_groups: pd.DataFrame,
    campaigns: pd.DataFrame,
    creatives: pd.DataFrame,
) -> None:
    if mapping.enabled and ad_accounts.empty:
        _append_finding(
            findings,
            severity="critical",
            category="access",
            code="LINKEDIN_NO_AD_ACCOUNTS_FOUND",
            title="Export nenašel žádný LinkedIn ad account",
            detail="Kontext má LinkedIn zapnutý, ale export ad accountů je prázdný.",
            recommendation="Zkontroluj mapping ad account IDs, developer app mapping a role uživatele v Campaign Manageru.",
        )

    if campaigns.empty and mapping.enabled:
        _append_finding(
            findings,
            severity="warning",
            category="access",
            code="LINKEDIN_NO_CAMPAIGNS_FOUND",
            title="Discovery/export nenašel žádné kampaně",
            detail="Může jít o prázdný účet, chybějící oprávnění nebo nepřesné mapování ad accountů.",
            recommendation="Zkontroluj namapované ad accounty, q=search export a role uživatele v Campaign Manageru.",
        )

    if not campaigns.empty and creatives.empty:
        _append_finding(
            findings,
            severity="warning",
            category="structure",
            code="LINKEDIN_ACTIVE_CAMPAIGNS_WITHOUT_CREATIVES",
            title="Kampaně bez kreativ",
            detail="Byly nalezeny kampaně, ale export kreativ je prázdný.",
            recommendation="Zkontroluj přístup ke kreativám nebo stav kampaní.",
        )

    if not campaign_groups.empty and not campaigns.empty:
        group_ids_with_campaigns = {
            _safe_str(value)
            for value in campaigns.get("campaign_group_id", pd.Series(dtype=str)).tolist()
            if _safe_str(value)
        }
        group_ids_with_campaigns.update(
            _safe_str(value)
            for value in campaigns.get("campaignGroup_id", pd.Series(dtype=str)).tolist()
            if _safe_str(value)
        )

        for _, row in campaign_groups.iterrows():
            group_status = _column_value(row, "status", "servingStatus")
            group_id = _safe_str(_column_value(row, "campaign_group_id", "campaignGroup_id", "id"))

            if _is_truthy_status(group_status) and group_id and group_id not in group_ids_with_campaigns:
                _append_finding(
                    findings,
                    severity="warning",
                    category="structure",
                    code="LINKEDIN_ACTIVE_GROUP_WITHOUT_CAMPAIGNS",
                    title="Aktivní campaign group bez kampaní",
                    detail="Campaign group je aktivní, ale export nenašel žádné kampaně uvnitř.",
                    entity_type="campaign_group",
                    entity_id=group_id,
                    entity_name=_safe_str(_column_value(row, "name", "campaignGroupName")),
                    recommendation="Zkontroluj stav kampaní nebo API export pro campaign groups/campaigns.",
                    evidence=_row_evidence(row),
                )


def _audit_tracking(
    findings: list[LinkedInAuditFinding],
    *,
    connection: LinkedInConnection,
    mapping: LinkedInAccountContextMapping,
    lead_sync_active: bool,
    datasets: dict[str, pd.DataFrame],
    gtm_crosscheck: dict[str, Any],
    web_scan_rows: list[dict[str, Any]],
) -> None:
    lead_forms = _dataframe(datasets, "lead_forms")
    insight_tags = _dataframe(datasets, "insight_tags")
    insight_tag_domains = _dataframe(datasets, "insight_tag_domains")
    campaign_conversions = _dataframe(datasets, "campaign_conversions")

    lead_sync_scope_available = "r_marketing_leadgen_automation" in connection.granted_scopes

    if (
        mapping.expected_conversion_type == "lead"
        and lead_sync_active
        and lead_sync_scope_available
        and lead_forms.empty
    ):
        _append_finding(
            findings,
            severity="warning",
            category="tracking",
            code="LINKEDIN_LEAD_FORMS_MISSING",
            title="Kontext očekává lead flow, ale lead form metadata chybí",
            detail="Nejsou dostupná žádná lead form metadata, i když je kontext nastavený jako leadový.",
            recommendation="Ověř typ kampaní, Lead Sync oprávnění a mapování lead form ID.",
        )

    if insight_tags.empty and mapping.expected_domains:
        _append_finding(
            findings,
            severity="warning",
            category="tracking",
            code="LINKEDIN_INSIGHT_TAGS_MISSING",
            title="Chybí Insight Tag metadata",
            detail="Export nenašel Insight Tag metadata pro namapovaný LinkedIn account.",
            recommendation="Ověř conversion tracking nastavení v Campaign Manageru a API oprávnění.",
        )

    if campaign_conversions.empty and mapping.expected_conversion_type in {"lead", "purchase", "mixed"}:
        _append_finding(
            findings,
            severity="warning",
            category="tracking",
            code="LINKEDIN_CAMPAIGN_CONVERSIONS_MISSING",
            title="Chybí campaign conversion associations",
            detail="Export nenašel propojení kampaní s conversion rules.",
            recommendation="Zkontroluj, zda kampaně mají připojené konverze a jestli API vrací campaignConversions.",
        )

    if mapping.expected_domains and not insight_tag_domains.empty:
        expected_domains = {domain.lower().removeprefix("www.") for domain in mapping.expected_domains}
        exported_domains: set[str] = set()

        for _, row in insight_tag_domains.iterrows():
            for key in ("domain", "domainName", "domain_name", "url"):
                value = _safe_str(row.get(key)).lower()
                if value:
                    exported_domains.add(value.removeprefix("www."))

        if exported_domains and expected_domains.isdisjoint(exported_domains):
            _append_finding(
                findings,
                severity="warning",
                category="tracking",
                code="LINKEDIN_INSIGHT_TAG_DOMAIN_MISMATCH",
                title="Insight Tag domains neodpovídají contextu",
                detail="Domény vrácené přes LinkedIn Insight Tag Domains neodpovídají expected domains v mappingu.",
                recommendation="Zkontroluj mapping contextu a nastavení Insight Tag domén.",
                evidence={
                    "expected_domains": sorted(expected_domains),
                    "exported_domains": sorted(exported_domains),
                },
            )

    if gtm_crosscheck.get("warnings"):
        for warning in gtm_crosscheck.get("warnings", []):
            _append_finding(
                findings,
                severity="warning",
                category="tracking",
                code="LINKEDIN_GTM_CROSSCHECK_WARNING",
                title="GTM cross-check našel nesoulad",
                detail=str(warning),
                recommendation="Porovnej GTM export s očekávaným LinkedIn Insight Tag / conversion ID mappingem.",
                evidence=sanitize_pii_for_report(gtm_crosscheck),
            )

    if gtm_crosscheck and not bool(gtm_crosscheck.get("matched")) and mapping.expected_domains:
        _append_finding(
            findings,
            severity="warning",
            category="tracking",
            code="LINKEDIN_GTM_NOT_MATCHED",
            title="GTM cross-check nepotvrdil LinkedIn měření",
            detail="GTM cross-check nenašel shodu s očekávaným LinkedIn Insight Tag / conversion mappingem.",
            recommendation="Zkontroluj GTM kontejner, trigger a conversion_id.",
            evidence=sanitize_pii_for_report(gtm_crosscheck),
        )

    for row in web_scan_rows:
        status_code = int(_safe_float(row.get("status_code")))

        if status_code >= 400:
            _append_finding(
                findings,
                severity="error",
                category="landing_page",
                code="LINKEDIN_LANDING_NON_200",
                title="Landing page vrací chybový HTTP status",
                detail="LinkedIn reklama míří na stránku, která nevrací 200.",
                entity_type="landing_page",
                entity_id=_safe_str(row.get("source_url")),
                entity_name=_safe_str(row.get("source_url")),
                recommendation="Oprav cílovou URL nebo redirect chain.",
                evidence=sanitize_pii_for_report(row),
            )

        if row.get("source_url") and not bool(row.get("has_insight_tag")):
            _append_finding(
                findings,
                severity="warning",
                category="tracking",
                code="LINKEDIN_INSIGHT_TAG_NOT_FOUND_ON_PAGE",
                title="Landing page neobsahuje Insight Tag",
                detail="Při HTML scanu landing page nebyl nalezen LinkedIn Insight Tag.",
                entity_type="landing_page",
                entity_id=_safe_str(row.get("source_url")),
                entity_name=_safe_str(row.get("source_url")),
                recommendation="Zkontroluj Insight Tag v GTM nebo přímo ve webu.",
                evidence=sanitize_pii_for_report(row),
            )


def _audit_performance(
    findings: list[LinkedInAuditFinding],
    *,
    insights_campaign_all: pd.DataFrame,
) -> None:
    if insights_campaign_all.empty:
        return

    for _, row in insights_campaign_all.iterrows():
        spend = _safe_float(
            _column_value(
                row,
                "costInLocalCurrency",
                "cost_in_local_currency",
                "spend",
            )
        )
        clicks = _safe_float(_column_value(row, "clicks"))
        impressions = _safe_float(_column_value(row, "impressions"))
        ctr = _safe_float(_column_value(row, "ctr"))
        conversions = _total_conversions(row)
        cpc = _safe_float(_column_value(row, "averageCpc", "cpc"))
        cpl = spend / conversions if conversions else 0.0

        entity_id = _campaign_entity_id(row)
        entity_name = _campaign_entity_name(row)

        if spend >= MIN_SPEND_FOR_ZERO_CONVERSION_WARNING and conversions == 0:
            _append_finding(
                findings,
                severity="warning",
                category="performance",
                code="LINKEDIN_SPEND_NO_CONVERSIONS",
                title="Kampaň utrácí bez konverzí",
                detail="Kampaň má spend nad prahem a zároveň 0 konverzí / leadů.",
                entity_type="campaign",
                entity_id=entity_id,
                entity_name=entity_name,
                recommendation="Zkontroluj cíl kampaně, tracking a relevanci landing page.",
                evidence={
                    "spend": spend,
                    "clicks": clicks,
                    "conversions": conversions,
                },
            )

        if clicks >= MIN_CLICKS_FOR_ZERO_CONVERSION_WARNING and conversions == 0:
            _append_finding(
                findings,
                severity="warning",
                category="performance",
                code="LINKEDIN_CLICKS_NO_CONVERSIONS",
                title="Kampaň má kliky bez konverzí",
                detail="Kampaň má dost kliků, ale žádné konverze.",
                entity_type="campaign",
                entity_id=entity_id,
                entity_name=entity_name,
                recommendation="Prověř landing page, formulář a konverzní tracking.",
                evidence={
                    "clicks": clicks,
                    "conversions": conversions,
                },
            )

        if impressions >= MIN_IMPRESSIONS_FOR_LOW_CTR_WARNING and ctr and ctr < LOW_CTR_THRESHOLD:
            _append_finding(
                findings,
                severity="warning",
                category="performance",
                code="LINKEDIN_LOW_CTR",
                title="CTR je pod prahem",
                detail="Kampaň má dost impresí, ale CTR je velmi nízké.",
                entity_type="campaign",
                entity_id=entity_id,
                entity_name=entity_name,
                recommendation="Otestuj jinou kreativu, headline nebo přesnější targeting.",
                evidence={
                    "impressions": impressions,
                    "ctr": ctr,
                },
            )

        if cpc >= HIGH_CPC_THRESHOLD:
            _append_finding(
                findings,
                severity="info",
                category="performance",
                code="LINKEDIN_HIGH_CPC",
                title="CPC je nad prahem",
                detail="Kampaň má vysokou cenu za proklik.",
                entity_type="campaign",
                entity_id=entity_id,
                entity_name=entity_name,
                recommendation="Prověř bidding, audience a relevanci kreativy.",
                evidence={
                    "cpc": cpc,
                },
            )

        if conversions > 0 and cpl >= HIGH_CPL_THRESHOLD:
            _append_finding(
                findings,
                severity="warning",
                category="performance",
                code="LINKEDIN_HIGH_CPL",
                title="CPL je nad prahem",
                detail="Kampaň konvertuje, ale cena za lead je vysoká.",
                entity_type="campaign",
                entity_id=entity_id,
                entity_name=entity_name,
                recommendation="Porovnej kvalitu leadů a zvaž úpravu targetingu nebo kreativy.",
                evidence={
                    "spend": spend,
                    "conversions": conversions,
                    "cpl": cpl,
                },
            )


def _audit_utm(
    findings: list[LinkedInAuditFinding],
    *,
    utm_audit: pd.DataFrame,
    creatives: pd.DataFrame,
) -> None:
    if utm_audit.empty:
        return

    lead_gen_ids = _lead_gen_creative_ids(creatives)

    for _, row in utm_audit.iterrows():
        issue_code = _safe_str(row.get("issue_code"))
        if issue_code == "ok":
            continue

        if _is_lead_gen_utm_false_positive(row, lead_gen_creative_ids=lead_gen_ids):
            continue

        _append_finding(
            findings,
            severity=_safe_str(row.get("severity")) or "warning",
            category="utm",
            code=f"LINKEDIN_UTM_{issue_code.upper()}",
            title="UTM nebo landing page nesoulad",
            detail=f"Nález pro URL {_safe_str(row.get('landing_page_url'))}",
            entity_type="landing_page",
            entity_id=_safe_str(row.get("landing_page_url")),
            entity_name=_safe_str(row.get("landing_page_url")),
            recommendation=_safe_str(row.get("recommendation")),
            evidence=_row_evidence(row),
        )


def build_audit_findings(
    *,
    connection: LinkedInConnection,
    mapping: LinkedInAccountContextMapping,
    lead_sync_active: bool,
    datasets: dict[str, pd.DataFrame],
    gtm_crosscheck: dict[str, Any],
    web_scan_rows: list[dict[str, Any]],
    export_warnings: list[str],
    export_info_notes: list[str],
) -> list[LinkedInAuditFinding]:
    findings: list[LinkedInAuditFinding] = []

    ad_accounts = _dataframe(datasets, "ad_accounts")
    campaign_groups = _dataframe(datasets, "campaign_groups")
    campaigns = _dataframe(datasets, "campaigns")
    creatives = _dataframe(datasets, "creatives")
    insights_campaign_all = _dataframe(datasets, "insights_campaign_all")
    utm_audit = _dataframe(datasets, "utm_audit")

    _audit_access(
        findings,
        connection=connection,
        mapping=mapping,
    )
    _audit_structure(
        findings,
        mapping=mapping,
        ad_accounts=ad_accounts,
        campaign_groups=campaign_groups,
        campaigns=campaigns,
        creatives=creatives,
    )
    _audit_tracking(
        findings,
        connection=connection,
        mapping=mapping,
        lead_sync_active=lead_sync_active,
        datasets=datasets,
        gtm_crosscheck=gtm_crosscheck,
        web_scan_rows=web_scan_rows,
    )
    _audit_performance(
        findings,
        insights_campaign_all=insights_campaign_all,
    )
    _audit_utm(
        findings,
        utm_audit=utm_audit,
        creatives=creatives,
    )

    for warning in export_warnings:
        _append_finding(
            findings,
            severity="warning",
            category="export",
            code="LINKEDIN_EXPORT_WARNING",
            title="Export doběhl s warningem",
            detail=str(warning),
            recommendation="Zkontroluj manifest a konkrétní přeskočené endpointy.",
        )

    for note in export_info_notes:
        _append_finding(
            findings,
            severity="info",
            category="export",
            code="LINKEDIN_EXPORT_INFO",
            title="Export poznámka",
            detail=str(note),
            recommendation="Jen informativní poznámka, export není považovaný za částečně neúspěšný.",
        )

    return findings
