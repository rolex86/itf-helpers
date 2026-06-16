from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient
from app.integrations.linkedin.normalizers import normalize_entity_identifiers
from app.integrations.linkedin.restli import organization_urn


ORGANIZATION_ROLE_STATES = (
    "APPROVED",
    "REQUESTED",
    "REJECTED",
)


ORGANIZATION_ROLE_TYPES = (
    "ADMINISTRATOR",
    "DIRECT_SPONSORED_CONTENT_POSTER",
    "RECRUITING_POSTER",
    "LEAD_CAPTURE_ADMINISTRATOR",
    "LEAD_GEN_FORMS_MANAGER",
    "ANALYST",
    "CURATOR",
    "CONTENT_ADMINISTRATOR",
)


def _dedupe_rows(rows: list[dict[str, Any]], *, key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[dict[str, Any]] = []

    for row in rows:
        key = tuple(str(row.get(field) or "").strip() for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped


def _normalize_acl_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_entity_identifiers(row)

    organization = row.get("organization") or row.get("organization_urn")
    role = row.get("role") or row.get("roleType") or row.get("role_type")
    state = row.get("state") or row.get("roleState") or row.get("role_state")

    if organization:
        normalized["organization_urn"] = str(organization)
        normalized["organization_id"] = str(organization).rsplit(":", 1)[-1]

    if role:
        normalized["role_type"] = str(role)

    if state:
        normalized["role_state"] = str(state)

    return normalized


def fetch_organization_acls(client: LinkedInRestClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    try:
        fetched = list(
            client.paginate_start_count(
                "organizationAcls",
                params={
                    "q": "roleAssignee",
                },
                count=100,
            )
        )
        rows.extend(_normalize_acl_row(row) for row in fetched)
    except Exception:
        pass

    if rows:
        return _dedupe_rows(rows, key_fields=("organization_id", "role_type", "role_state"))

    for role_state in ORGANIZATION_ROLE_STATES:
        try:
            fetched = list(
                client.paginate_start_count(
                    "organizationAcls",
                    params={
                        "q": "roleAssignee",
                        "state": role_state,
                    },
                    count=100,
                )
            )
            rows.extend(_normalize_acl_row(row) for row in fetched)
        except Exception:
            continue

    return _dedupe_rows(rows, key_fields=("organization_id", "role_type", "role_state"))


def fetch_organization_details(
    client: LinkedInRestClient,
    *,
    organization_ids: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for organization_id in organization_ids:
        clean_id = str(organization_id or "").strip()
        if not clean_id or clean_id in seen:
            continue

        seen.add(clean_id)

        try:
            payload = client.get(f"organizations/{clean_id}")
            if isinstance(payload, dict):
                normalized = normalize_entity_identifiers(payload)
                normalized["organization_id"] = clean_id
                normalized["organization_urn"] = organization_urn(clean_id)
                rows.append(normalized)
        except Exception:
            continue

    return rows


def fetch_organization_authorizations(client: LinkedInRestClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    authorization_actions = (
        "(authorizationAction:(organizationRoleAuthorizationAction:(actionType:ADMINISTRATOR_READ)))",
        "(authorizationAction:(organizationRoleAuthorizationAction:(actionType:ADMINISTRATION_PAGE_VIEW)))",
        "(authorizationAction:(organizationAnalyticsAuthorizationAction:(actionType:VISITOR_ANALYTICS_READ)))",
        "(authorizationAction:(organizationAnalyticsAuthorizationAction:(actionType:FOLLOWER_ANALYTICS_READ)))",
    )

    try:
        payload = client.get(
            "organizationAuthorizations",
            params={
                "bq": "authorizationActionsAndImpersonator",
                "authorizationActions": "List(" + ",".join(authorization_actions) + ")",
            },
        )
    except Exception:
        return rows

    elements = payload.get("elements", []) if isinstance(payload, dict) else []
    if not isinstance(elements, list):
        return rows

    for outer in elements:
        if not isinstance(outer, dict):
            continue

        inner_elements = outer.get("elements", [])
        if not isinstance(inner_elements, list):
            continue

        for item in inner_elements:
            if not isinstance(item, dict):
                continue
            rows.append(normalize_entity_identifiers(item))

    return rows


def fetch_organizations(client: LinkedInRestClient) -> list[dict[str, Any]]:
    acl_rows = fetch_organization_acls(client)

    organization_ids = [
        str(row.get("organization_id") or row.get("id") or "").strip()
        for row in acl_rows
        if str(row.get("organization_id") or row.get("id") or "").strip()
    ]

    detail_rows = fetch_organization_details(
        client,
        organization_ids=organization_ids,
    )

    authorization_rows = fetch_organization_authorizations(client)

    detail_by_id = {
        str(row.get("organization_id") or row.get("id") or "").strip(): row
        for row in detail_rows
        if str(row.get("organization_id") or row.get("id") or "").strip()
    }

    merged: list[dict[str, Any]] = []

    for acl in acl_rows:
        organization_id = str(acl.get("organization_id") or acl.get("id") or "").strip()
        detail = detail_by_id.get(organization_id, {})
        row = dict(detail)
        row.update(acl)
        row["source"] = "organizationAcls"
        merged.append(row)

    if not merged:
        merged.extend(detail_rows)

    if authorization_rows:
        for row in merged:
            row["authorization_rows_available"] = True

    return _dedupe_rows(merged, key_fields=("organization_id", "role_type", "role_state"))