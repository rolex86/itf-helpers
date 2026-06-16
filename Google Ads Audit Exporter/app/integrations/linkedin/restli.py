from __future__ import annotations

from datetime import date
from urllib.parse import quote


def encode_urn(urn: str) -> str:
    return quote(str(urn or "").strip(), safe="")


def clean_linkedin_id(value: str) -> str:
    text = str(value or "").strip()

    if text.startswith("urn:li:"):
        return text.rsplit(":", 1)[-1].strip()

    if text.startswith("act_"):
        return text.replace("act_", "", 1).strip()

    return text


def sponsored_account_urn(account_id: str) -> str:
    return f"urn:li:sponsoredAccount:{clean_linkedin_id(account_id)}"


def organization_urn(org_id: str) -> str:
    return f"urn:li:organization:{clean_linkedin_id(org_id)}"


def campaign_urn(campaign_id: str) -> str:
    return f"urn:li:sponsoredCampaign:{clean_linkedin_id(campaign_id)}"


def creative_urn(creative_id: str) -> str:
    return f"urn:li:sponsoredCreative:{clean_linkedin_id(creative_id)}"


def lead_form_urn(form_id: str) -> str:
    return f"urn:li:leadGenForm:{clean_linkedin_id(form_id)}"


def versioned_lead_form_urn(form_id: str, version: int | str) -> str:
    normalized_version = str(version or "").strip()
    if not normalized_version:
        return lead_form_urn(form_id)
    return f"{lead_form_urn(form_id)}:{normalized_version}"


def restli_list(values: list[str] | tuple[str, ...] | set[str]) -> str:
    normalized: list[str] = []

    for item in values or []:
        value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)

    return f"List({','.join(normalized)})"


def date_range_param(start_date: date, end_date: date) -> str:
    return (
        f"(start:(year:{start_date.year},month:{start_date.month},day:{start_date.day}),"
        f"end:(year:{end_date.year},month:{end_date.month},day:{end_date.day}))"
    )


def owner_param_for_sponsored_account(account_id: str) -> str:
    return lead_form_owner_param(sponsored_account_urn(account_id))


def owner_param_for_organization(org_id: str) -> str:
    return lead_form_owner_param(organization_urn(org_id))


def lead_form_owner_param(owner_urn: str) -> str:
    normalized = str(owner_urn or "").strip()

    if normalized.startswith("(sponsoredAccount:") or normalized.startswith("(organization:"):
        return normalized

    if normalized.startswith("urn:li:sponsoredAccount:"):
        return f"(sponsoredAccount:{normalized})"

    if normalized.startswith("urn:li:organization:"):
        return f"(organization:{normalized})"

    return normalized


def lead_type_param(lead_type: str = "SPONSORED") -> str:
    normalized = str(lead_type or "SPONSORED").strip().upper() or "SPONSORED"
    return f"(leadType:{normalized})"