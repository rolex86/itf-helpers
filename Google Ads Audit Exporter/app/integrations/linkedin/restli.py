from __future__ import annotations

from datetime import date
from urllib.parse import quote


def encode_urn(urn: str) -> str:
    return quote(str(urn or "").strip(), safe="")


def sponsored_account_urn(account_id: str) -> str:
    return f"urn:li:sponsoredAccount:{str(account_id or '').replace('act_', '').strip()}"


def organization_urn(org_id: str) -> str:
    return f"urn:li:organization:{str(org_id or '').strip()}"


def campaign_urn(campaign_id: str) -> str:
    return f"urn:li:sponsoredCampaign:{str(campaign_id or '').strip()}"


def creative_urn(creative_id: str) -> str:
    return f"urn:li:sponsoredCreative:{str(creative_id or '').strip()}"


def lead_form_urn(form_id: str) -> str:
    return f"urn:li:leadGenForm:{str(form_id or '').strip()}"


def versioned_lead_form_urn(form_id: str, version: int) -> str:
    return f"{lead_form_urn(form_id)}:{int(version)}"


def restli_list(values: list[str]) -> str:
    normalized = [str(item or "").strip() for item in values if str(item or "").strip()]
    return f"List({','.join(normalized)})"


def date_range_param(start_date: date, end_date: date) -> str:
    return (
        f"(start:(year:{start_date.year},month:{start_date.month},day:{start_date.day}),"
        f"end:(year:{end_date.year},month:{end_date.month},day:{end_date.day}))"
    )


def owner_param_for_sponsored_account(account_id: str) -> str:
    return sponsored_account_urn(account_id)


def owner_param_for_organization(org_id: str) -> str:
    return organization_urn(org_id)

