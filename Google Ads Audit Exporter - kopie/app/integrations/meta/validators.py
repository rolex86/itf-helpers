from __future__ import annotations

from typing import Any

from app.accounts.context_config import AccountContext
from app.integrations.meta.models import MetaDiscoverySnapshot


def validate_context_meta_mapping(context: AccountContext, snapshot: MetaDiscoverySnapshot | None = None) -> list[str]:
    warnings: list[str] = []
    meta = context.meta
    if not meta.enabled:
        return warnings
    if not meta.connection_key:
        warnings.append(f"Kontext {context.key} nema vybranou Meta connection.")
    if not meta.ad_account_ids:
        warnings.append(f"Kontext {context.key} nema namapovany zadny Meta reklamni ucet.")
    if not meta.pixel_ids:
        warnings.append(f"Kontext {context.key} nema namapovany zadny Meta Pixel/Dataset.")
    if snapshot is not None and meta.business_id:
        known_business_ids = {str(item.get("id") or "") for item in snapshot.businesses}
        if meta.business_id not in known_business_ids:
            warnings.append(f"Kontext {context.key} odkazuje na neznamy Meta business {meta.business_id}.")
    return warnings


def catalog_match_warnings(context: AccountContext, record: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    meta = context.meta
    catalog_id = str(record.get("catalog_id") or record.get("id") or "")
    if meta.catalog_ids and catalog_id and catalog_id not in meta.catalog_ids:
        warnings.append("Katalog v nalezu neni mezi namapovanymi katalogy kontextu.")
    return warnings
