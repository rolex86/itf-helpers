from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.integrations.sklik.client_drak import SklikDrakClient
from app.integrations.sklik.client_fenix import SklikFenixClient
from app.integrations.sklik.errors import SklikApiError, SklikPartialFailure
from app.integrations.sklik.models import SklikConnection, SklikDiscoverySnapshot
from app.integrations.sklik.normalizers import extract_rows, sanitize_payload


DISCOVERY_LIST_CHECKS: dict[str, list[dict[str, Any]]] = {
    "campaigns.list": [
        {"isDeleted": False},
        {
            "offset": 0,
            "limit": 1,
            "displayColumns": ["id", "name", "status", "statusId", "type"],
        },
    ],
    "groups.list": [
        {"isDeleted": False},
        {
            "offset": 0,
            "limit": 1,
            "displayColumns": ["id", "name", "status", "statusId", "campaign.id", "campaign.name"],
        },
    ],
    "ads.list": [
        {"isDeleted": False},
        {
            "offset": 0,
            "limit": 1,
            "displayColumns": ["id", "name", "adStatus", "adType", "finalUrl", "group.id", "campaign.id"],
        },
    ],
    "keywords.list": [
        {"isDeleted": False},
        {
            "offset": 0,
            "limit": 1,
            "displayColumns": ["id", "name", "status", "statusId", "matchType", "group.id", "campaign.id"],
        },
    ],
}


def _warning_text(prefix: str, exc: Exception) -> str:
    return f"{prefix}: {exc}"


def run_sklik_discovery(
    *,
    connection: SklikConnection,
    drak_client: SklikDrakClient | None = None,
    fenix_client: SklikFenixClient | None = None,
) -> SklikDiscoverySnapshot:
    snapshot = SklikDiscoverySnapshot(connection_key=connection.key)
    drak_payload: dict[str, Any] = {
        "account": {},
        "foreign_accounts": [],
        "api_limits": {},
        "api_version": {},
        "endpoint_checks": [],
    }
    fenix_payload: dict[str, Any] = {
        "api_home": {},
        "user": {},
        "premises": [],
        "campaigns_by_premise": {},
        "feed_statuses_by_premise": {},
    }

    if drak_client is not None and connection.drak_enabled:
        try:
            drak_client.login_by_token()
            limits = drak_client.api_limits()
            version = drak_client.api_version()
            client_info = drak_client.client_get()
            drak_payload["api_limits"] = sanitize_payload(limits)
            drak_payload["api_version"] = sanitize_payload(version)
            drak_payload["account"] = sanitize_payload(client_info)

            user = client_info.get("user") if isinstance(client_info.get("user"), dict) else {}
            foreign_accounts = client_info.get("foreignAccounts")
            if not isinstance(foreign_accounts, list):
                foreign_accounts = []
            drak_payload["foreign_accounts"] = sanitize_payload(foreign_accounts if isinstance(foreign_accounts, list) else [])

            own_user_id = str(
                (user or {}).get("userId")
                or connection.default_user_id
                or ""
            ).strip()
            candidate_user_ids: list[str] = []
            if own_user_id:
                candidate_user_ids.append(own_user_id)

            for item in foreign_accounts if isinstance(foreign_accounts, list) else []:
                if not isinstance(item, dict):
                    continue
                relation_status = str(item.get("relationStatus") or item.get("status") or "").strip().lower()
                user_id = str(item.get("userId") or "").strip()
                if user_id and relation_status in {"live", "active", ""} and user_id not in candidate_user_ids:
                    candidate_user_ids.append(user_id)

            for user_id in candidate_user_ids:
                endpoint_summary = {"user_id": user_id, "checks": []}
                for method, params in DISCOVERY_LIST_CHECKS.items():
                    try:
                        payload = drak_client.call(method, params, user_id=int(user_id))
                        endpoint_summary["checks"].append(
                            {
                                "method": method,
                                "ok": True,
                                "count": len(extract_rows(payload)),
                            }
                        )
                    except SklikPartialFailure as exc:
                        snapshot.warnings.append(_warning_text(f"Discovery {method} userId={user_id}", exc))
                        endpoint_summary["checks"].append({"method": method, "ok": False, "warning": str(exc)})
                    except Exception as exc:
                        snapshot.warnings.append(_warning_text(f"Discovery {method} userId={user_id}", exc))
                        endpoint_summary["checks"].append({"method": method, "ok": False, "warning": str(exc)})

                try:
                    stats = drak_client.client_stats(
                        int(user_id),
                        datetime.now(timezone.utc).date().replace(day=1).isoformat(),
                        datetime.now(timezone.utc).date().isoformat(),
                        "daily",
                    )
                    endpoint_summary["checks"].append(
                        {
                            "method": "client.stats",
                            "ok": True,
                            "count": len(extract_rows(stats)),
                        }
                    )
                except Exception as exc:
                    snapshot.warnings.append(_warning_text(f"Discovery client.stats userId={user_id}", exc))
                    endpoint_summary["checks"].append({"method": "client.stats", "ok": False, "warning": str(exc)})
                drak_payload["endpoint_checks"].append(endpoint_summary)
        except Exception as exc:
            snapshot.errors.append(_warning_text("Drak discovery selhalo", exc))

    if fenix_client is not None and connection.fenix_enabled:
        try:
            # Keep discovery read-only and conservative.
            # Do not call unconfirmed premise/feed/stat endpoints here; context-level
            # Fenix export handles manual premise IDs from mapping.
            fenix_payload["api_home"] = sanitize_payload(fenix_client.get_api_home())
            fenix_payload["user"] = sanitize_payload(fenix_client.get_user_me())
            fenix_payload["premises"] = []
            fenix_payload["campaigns_by_premise"] = {}
            fenix_payload["feed_statuses_by_premise"] = {}
        except SklikApiError as exc:
            snapshot.errors.append(_warning_text("Fenix discovery selhalo", exc))
        except Exception as exc:
            snapshot.errors.append(_warning_text("Fenix discovery selhalo", exc))

    snapshot.drak = drak_payload
    snapshot.fenix = fenix_payload
    snapshot.status = "failed" if snapshot.errors else ("partial" if snapshot.warnings else "success")
    return snapshot
