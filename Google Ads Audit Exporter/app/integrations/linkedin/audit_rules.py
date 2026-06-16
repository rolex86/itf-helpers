from __future__ import annotations

from typing import Any

import pandas as pd

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


def build_audit_findings(
    *,
    connection: LinkedInConnection,
    mapping: LinkedInAccountContextMapping,
    datasets: dict[str, pd.DataFrame],
    gtm_crosscheck: dict[str, Any],
    web_scan_rows: list[dict[str, Any]],
    export_warnings: list[str],
) -> list[LinkedInAuditFinding]:
    findings: list[LinkedInAuditFinding] = []

    if not connection.granted_scopes:
        findings.append(
            LinkedInAuditFinding(
                severity="warning",
                category="access",
                code="LINKEDIN_SCOPES_UNKNOWN",
                title="Scopes nejsou detekované",
                detail="Connection nemá uložené detekované scopes z posledního testu nebo OAuth callbacku.",
                recommendation="Spusť znovu test connection nebo OAuth refresh.",
            )
        )
    else:
        for required_scope in ("r_ads", "r_ads_reporting"):
            if required_scope not in connection.granted_scopes:
                findings.append(
                    LinkedInAuditFinding(
                        severity="critical",
                        category="access",
                        code="LINKEDIN_SCOPE_MISSING",
                        title=f"Chybí scope {required_scope}",
                        detail="Bez tohoto scope nebude export kompletní nebo nemusí fungovat vůbec.",
                        recommendation="Rozšiř oprávnění LinkedIn appky a proveď reautorizaci.",
                        evidence={"scope": required_scope},
                    )
                )
        if mapping.lead_sync_enabled and "r_marketing_leadgen_automation" not in connection.granted_scopes:
            findings.append(
                LinkedInAuditFinding(
                    severity="warning",
                    category="lead_sync",
                    code="LINKEDIN_LEAD_SYNC_SCOPE_MISSING",
                    title="Lead Sync je zapnutý, ale chybí potřebný scope",
                    detail="Lead Sync data budou přeskočená nebo neúplná.",
                    recommendation="Požádej o scope r_marketing_leadgen_automation nebo vypni Lead Sync pro tento kontext.",
                )
            )

    campaigns = datasets.get("campaigns", pd.DataFrame())
    creatives = datasets.get("creatives", pd.DataFrame())
    lead_forms = datasets.get("lead_forms", pd.DataFrame())
    insights_campaign_all = datasets.get("insights_campaign_all", pd.DataFrame())
    utm_audit = datasets.get("utm_audit", pd.DataFrame())

    if campaigns.empty and mapping.enabled:
        findings.append(
            LinkedInAuditFinding(
                severity="warning",
                category="access",
                code="LINKEDIN_NO_CAMPAIGNS_FOUND",
                title="Discovery/export nenašel žádné kampaně",
                detail="Může jít o prázdný účet, chybějící oprávnění nebo nepřesné mapování ad accountů.",
                recommendation="Zkontroluj namapované ad accounty a role uživatele v Campaign Manageru.",
            )
        )

    if not campaigns.empty and creatives.empty:
        findings.append(
            LinkedInAuditFinding(
                severity="warning",
                category="structure",
                code="LINKEDIN_ACTIVE_CAMPAIGNS_WITHOUT_CREATIVES",
                title="Kampaně bez kreativ",
                detail="Byly nalezeny kampaně, ale export kreativ je prázdný.",
                recommendation="Zkontroluj přístup ke kreativám nebo stav kampaní.",
            )
        )

    if not insights_campaign_all.empty:
        for _, row in insights_campaign_all.iterrows():
            spend = _safe_float(row.get("costInLocalCurrency") or row.get("cost_in_local_currency") or row.get("spend"))
            clicks = _safe_float(row.get("clicks"))
            impressions = _safe_float(row.get("impressions"))
            ctr = _safe_float(row.get("ctr"))
            conversions = _safe_float(
                row.get("externalWebsiteConversions")
                or row.get("oneClickLeads")
                or row.get("conversions")
            )
            cpc = _safe_float(row.get("averageCpc") or row.get("cpc"))
            cpl = spend / conversions if conversions else 0.0

            if spend >= MIN_SPEND_FOR_ZERO_CONVERSION_WARNING and conversions == 0:
                findings.append(
                    LinkedInAuditFinding(
                        severity="warning",
                        category="performance",
                        code="LINKEDIN_SPEND_NO_CONVERSIONS",
                        title="Kampaň utrácí bez konverzí",
                        detail="Kampaň má spend nad prahem a zároveň 0 konverzí / leadů.",
                        entity_type="campaign",
                        entity_id=str(row.get("campaign_id") or ""),
                        entity_name=str(row.get("campaignName") or row.get("campaign_name") or ""),
                        recommendation="Zkontroluj cíl kampaně, tracking a relevanci landing page.",
                        evidence={"spend": spend, "clicks": clicks, "conversions": conversions},
                    )
                )
            if clicks >= MIN_CLICKS_FOR_ZERO_CONVERSION_WARNING and conversions == 0:
                findings.append(
                    LinkedInAuditFinding(
                        severity="warning",
                        category="performance",
                        code="LINKEDIN_CLICKS_NO_CONVERSIONS",
                        title="Kampaň má kliky bez konverzí",
                        detail="Kampaň má dost kliků, ale žádné konverze.",
                        entity_type="campaign",
                        entity_id=str(row.get("campaign_id") or ""),
                        entity_name=str(row.get("campaignName") or row.get("campaign_name") or ""),
                        recommendation="Prověř landing page, formulář a konverzní tracking.",
                        evidence={"clicks": clicks, "conversions": conversions},
                    )
                )
            if impressions >= MIN_IMPRESSIONS_FOR_LOW_CTR_WARNING and ctr and ctr < LOW_CTR_THRESHOLD:
                findings.append(
                    LinkedInAuditFinding(
                        severity="warning",
                        category="performance",
                        code="LINKEDIN_LOW_CTR",
                        title="CTR je pod prahem",
                        detail="Kampaň má dost impresí, ale CTR je velmi nízké.",
                        entity_type="campaign",
                        entity_id=str(row.get("campaign_id") or ""),
                        entity_name=str(row.get("campaignName") or row.get("campaign_name") or ""),
                        recommendation="Otestuj jinou kreativu, headline nebo přesnější targeting.",
                        evidence={"impressions": impressions, "ctr": ctr},
                    )
                )
            if cpc >= HIGH_CPC_THRESHOLD:
                findings.append(
                    LinkedInAuditFinding(
                        severity="info",
                        category="performance",
                        code="LINKEDIN_HIGH_CPC",
                        title="CPC je nad prahem",
                        detail="Kampaň má vysokou cenu za proklik.",
                        entity_type="campaign",
                        entity_id=str(row.get("campaign_id") or ""),
                        entity_name=str(row.get("campaignName") or row.get("campaign_name") or ""),
                        recommendation="Prověř bidding, audience a relevanci kreativy.",
                        evidence={"cpc": cpc},
                    )
                )
            if conversions > 0 and cpl >= HIGH_CPL_THRESHOLD:
                findings.append(
                    LinkedInAuditFinding(
                        severity="warning",
                        category="performance",
                        code="LINKEDIN_HIGH_CPL",
                        title="CPL je nad prahem",
                        detail="Kampaň konvertuje, ale cena za lead je vysoká.",
                        entity_type="campaign",
                        entity_id=str(row.get("campaign_id") or ""),
                        entity_name=str(row.get("campaignName") or row.get("campaign_name") or ""),
                        recommendation="Porovnej kvalitu leadů a zvaž úpravu targetingu nebo kreativy.",
                        evidence={"spend": spend, "conversions": conversions, "cpl": cpl},
                    )
                )

    if mapping.expected_conversion_type == "lead" and lead_forms.empty:
        findings.append(
            LinkedInAuditFinding(
                severity="warning",
                category="tracking",
                code="LINKEDIN_LEAD_FORMS_MISSING",
                title="Kontext očekává lead flow, ale lead form metadata chybí",
                detail="Nejsou dostupná žádná lead form metadata, i když je kontext nastavený jako leadový.",
                recommendation="Ověř typ kampaní, Lead Sync oprávnění a mapování lead form ID.",
            )
        )

    for warning in export_warnings:
        findings.append(
            LinkedInAuditFinding(
                severity="info",
                category="export",
                code="LINKEDIN_EXPORT_WARNING",
                title="Export doběhl s warningem",
                detail=str(warning),
                recommendation="Zkontroluj manifest a konkrétní přeskočené endpointy.",
            )
        )

    for warning in gtm_crosscheck.get("warnings", []):
        findings.append(
            LinkedInAuditFinding(
                severity="warning",
                category="tracking",
                code="LINKEDIN_GTM_CROSSCHECK_WARNING",
                title="GTM cross-check našel nesoulad",
                detail=str(warning),
                recommendation="Porovnej GTM export s očekávaným LinkedIn Insight Tag / conversion ID mappingem.",
                evidence=sanitize_pii_for_report(gtm_crosscheck),
            )
        )

    for row in web_scan_rows:
        if int(row.get("status_code") or 0) >= 400:
            findings.append(
                LinkedInAuditFinding(
                    severity="error",
                    category="landing_page",
                    code="LINKEDIN_LANDING_NON_200",
                    title="Landing page vrací chybový HTTP status",
                    detail="LinkedIn reklama míří na stránku, která nevrací 200.",
                    entity_type="landing_page",
                    entity_id=str(row.get("source_url") or ""),
                    entity_name=str(row.get("source_url") or ""),
                    recommendation="Oprav cílovou URL nebo redirect chain.",
                    evidence=row,
                )
            )
        if not bool(row.get("has_insight_tag")):
            findings.append(
                LinkedInAuditFinding(
                    severity="warning",
                    category="tracking",
                    code="LINKEDIN_INSIGHT_TAG_NOT_FOUND_ON_PAGE",
                    title="Landing page neobsahuje Insight Tag",
                    detail="Při HTML scanu landing page nebyl nalezen LinkedIn Insight Tag.",
                    entity_type="landing_page",
                    entity_id=str(row.get("source_url") or ""),
                    entity_name=str(row.get("source_url") or ""),
                    recommendation="Zkontroluj Insight Tag v GTM nebo přímo ve webu.",
                    evidence=row,
                )
            )

    if not utm_audit.empty:
        for _, row in utm_audit.iterrows():
            if str(row.get("issue_code") or "") == "ok":
                continue
            findings.append(
                LinkedInAuditFinding(
                    severity=str(row.get("severity") or "warning"),
                    category="utm",
                    code=f"LINKEDIN_UTM_{str(row.get('issue_code') or '').upper()}",
                    title="UTM nebo landing page nesoulad",
                    detail=f"Nález pro URL {row.get('landing_page_url') or ''}",
                    entity_type="landing_page",
                    entity_id=str(row.get("landing_page_url") or ""),
                    entity_name=str(row.get("landing_page_url") or ""),
                    recommendation=str(row.get("recommendation") or ""),
                    evidence=row.to_dict(),
                )
            )

    return findings

