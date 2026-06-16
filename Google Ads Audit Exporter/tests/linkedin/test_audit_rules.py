import pandas as pd

from app.integrations.linkedin.audit_rules import build_audit_findings
from app.integrations.linkedin.models import LinkedInAccountContextMapping, LinkedInConnection


def test_audit_rules_detect_missing_scope() -> None:
    connection = LinkedInConnection(key="main", label="Main", granted_scopes=["r_ads"])
    mapping = LinkedInAccountContextMapping(context_key="ctx", enabled=True)
    findings = build_audit_findings(
        connection=connection,
        mapping=mapping,
        datasets={
            "campaigns": pd.DataFrame(),
            "creatives": pd.DataFrame(),
            "lead_forms": pd.DataFrame(),
            "insights_campaign_all": pd.DataFrame(),
            "utm_audit": pd.DataFrame(),
        },
        gtm_crosscheck={"warnings": []},
        web_scan_rows=[],
        export_warnings=[],
    )
    assert any(f.code == "LINKEDIN_SCOPE_MISSING" for f in findings)

