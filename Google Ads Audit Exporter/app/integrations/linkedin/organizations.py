from __future__ import annotations

from typing import Any

from app.integrations.linkedin.client import LinkedInRestClient


def fetch_organizations(client: LinkedInRestClient) -> list[dict[str, Any]]:
    organizations: list[dict[str, Any]] = []
    try:
        for row in client.paginate("organizationAcls", params={"q": "roleAssignee"}):
            organizations.append(row)
    except Exception:
        return []
    return organizations

