from __future__ import annotations

from collections import deque
from typing import Any

import pandas as pd

from app.auth.google_ads_client import build_google_ads_client
from app.config.env_settings import GoogleAdsEnvConfig
from app.google_ads.normalizer import extract_path_value
from app.utils.retry import is_retryable_google_ads_exception, run_with_retry


DISCOVERY_COLUMNS = [
    "customer_id",
    "resource_name",
    "descriptive_name",
    "currency_code",
    "time_zone",
    "is_manager",
    "level",
    "status",
]


def _customer_id_from_resource(resource_name: str) -> str:
    return str(resource_name or "").replace("customers/", "").strip()


def _stream_rows(google_ads_service: Any, customer_id: str, query: str) -> list[Any]:
    def _action() -> list[Any]:
        stream = google_ads_service.search_stream(customer_id=customer_id, query=query)
        collected: list[Any] = []
        for batch in stream:
            collected.extend(batch.results)
        return collected

    return run_with_retry(_action, should_retry=is_retryable_google_ads_exception)


def _root_customer_metadata(google_ads_service: Any, customer_id: str) -> dict[str, Any]:
    query = """
        SELECT
          customer.id,
          customer.resource_name,
          customer.descriptive_name,
          customer.currency_code,
          customer.time_zone,
          customer.manager,
          customer.status
        FROM customer
        LIMIT 1
    """
    rows = _stream_rows(google_ads_service, customer_id, query)
    if not rows:
        return {
            "customer_id": customer_id,
            "resource_name": f"customers/{customer_id}",
            "descriptive_name": "",
            "currency_code": "",
            "time_zone": "",
            "is_manager": "",
            "level": 0,
            "status": "",
        }

    row = rows[0]
    return {
        "customer_id": extract_path_value(row, "customer.id") or customer_id,
        "resource_name": extract_path_value(row, "customer.resource_name") or f"customers/{customer_id}",
        "descriptive_name": extract_path_value(row, "customer.descriptive_name") or "",
        "currency_code": extract_path_value(row, "customer.currency_code") or "",
        "time_zone": extract_path_value(row, "customer.time_zone") or "",
        "is_manager": extract_path_value(row, "customer.manager"),
        "level": 0,
        "status": extract_path_value(row, "customer.status") or "",
    }


def _direct_customer_children(google_ads_service: Any, manager_customer_id: str) -> list[dict[str, Any]]:
    query = """
        SELECT
          customer_client.id,
          customer_client.resource_name,
          customer_client.descriptive_name,
          customer_client.currency_code,
          customer_client.time_zone,
          customer_client.manager,
          customer_client.level,
          customer_client.status
        FROM customer_client
        WHERE customer_client.level = 1
    """
    rows = _stream_rows(google_ads_service, manager_customer_id, query)
    children: list[dict[str, Any]] = []
    for row in rows:
        children.append(
            {
                "customer_id": extract_path_value(row, "customer_client.id") or "",
                "resource_name": extract_path_value(row, "customer_client.resource_name") or "",
                "descriptive_name": extract_path_value(row, "customer_client.descriptive_name") or "",
                "currency_code": extract_path_value(row, "customer_client.currency_code") or "",
                "time_zone": extract_path_value(row, "customer_client.time_zone") or "",
                "is_manager": extract_path_value(row, "customer_client.manager"),
                "level": extract_path_value(row, "customer_client.level") or 1,
                "status": extract_path_value(row, "customer_client.status") or "",
            }
        )
    return children


def discover_google_ads_customers(env_config: GoogleAdsEnvConfig) -> pd.DataFrame:
    client = build_google_ads_client(env_config=env_config)
    customer_service = client.get_service("CustomerService")
    google_ads_service = client.get_service("GoogleAdsService")

    response = customer_service.list_accessible_customers()
    resource_names = list(getattr(response, "resource_names", []) or getattr(response, "resource_names_list", []) or [])

    discovered_rows: list[dict[str, Any]] = []
    seen_customer_ids: set[str] = set()
    manager_queue: deque[str] = deque()

    for resource_name in resource_names:
        customer_id = _customer_id_from_resource(str(resource_name))
        try:
            root_row = _root_customer_metadata(google_ads_service, customer_id)
        except Exception:
            root_row = {
                "customer_id": customer_id,
                "resource_name": str(resource_name),
                "descriptive_name": "",
                "currency_code": "",
                "time_zone": "",
                "is_manager": "",
                "level": 0,
                "status": "",
            }
        normalized_id = str(root_row.get("customer_id") or customer_id)
        if normalized_id and normalized_id not in seen_customer_ids:
            seen_customer_ids.add(normalized_id)
            discovered_rows.append(root_row)
        if bool(root_row.get("is_manager")):
            manager_queue.append(normalized_id)

    visited_managers: set[str] = set()
    while manager_queue:
        manager_customer_id = manager_queue.popleft()
        if not manager_customer_id or manager_customer_id in visited_managers:
            continue
        visited_managers.add(manager_customer_id)

        try:
            children = _direct_customer_children(google_ads_service, manager_customer_id)
        except Exception:
            continue

        for child in children:
            child_customer_id = str(child.get("customer_id") or "")
            if child_customer_id and child_customer_id not in seen_customer_ids:
                seen_customer_ids.add(child_customer_id)
                discovered_rows.append(child)
            if bool(child.get("is_manager")) and child_customer_id not in visited_managers:
                manager_queue.append(child_customer_id)

    return pd.DataFrame(discovered_rows, columns=DISCOVERY_COLUMNS)
