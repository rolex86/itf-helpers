#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ModuleNotFoundError:
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.smartemailing_api import (  # noqa: E402
    DEFAULT_BASE_URL,
    SmartEmailingApiClient,
    SmartEmailingCredentials,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is not None:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data

    # Minimal fallback parser for root-level "key: value" files.
    # Enough for credentials .local even without PyYAML installed.
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Ignore nested YAML when running without PyYAML.
        if raw_line.startswith(" ") or raw_line.startswith("\t"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostika vyčištění customfield hodnoty přes SmartEmailing import API."
    )
    parser.add_argument("--email", default="", help="Email kontaktu (pro dohledání contact_id).")
    parser.add_argument("--contact-id", default="", help="Contact ID ve SmartEmailingu.")
    parser.add_argument("--field-id", default="", help="Custom field ID, který chceš na kontaktu vyčistit.")
    parser.add_argument("--pair-id", default="", help="Volitelné pair_id (jen pro dohledání field_id).")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Provede skutečné vyčištění hodnoty přes POST /api/v3/import. Bez tohoto přepínače běží jen diagnostika.",
    )
    parser.add_argument("--mappings", default=str(ROOT / "config" / "mappings.yaml"))
    parser.add_argument("--credentials", default=str(ROOT / "config" / "se_api_credentials.local"))
    args = parser.parse_args()

    mappings = _load_yaml(Path(args.mappings))
    creds = _load_yaml(Path(args.credentials))
    api_cfg = ((mappings.get("smartemailing") or {}).get("api") or {}) if isinstance(mappings, dict) else {}
    if yaml is None:
        print(
            "Upozornění: PyYAML není nainstalovaný. "
            "Používám fallback parser (pokročilé API endpointy z mappings.yaml mohou být ignorovány)."
        )

    username = str(creds.get("username", "")).strip()
    api_key = str(creds.get("api_key", "")).strip()
    base_url = str(creds.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL
    if not username or not api_key:
        print("CHYBA: v credentials chybí username nebo api_key.")
        return 2

    contacts_endpoint_candidates = _unique([str(x).strip() for x in api_cfg.get("contacts_endpoint_candidates", [])])
    contacts_search_endpoint_candidates = _unique(
        [str(x).strip() for x in api_cfg.get("contacts_search_endpoint_candidates", [])]
    )
    cf_values_endpoint_candidates = _unique(
        [str(x).strip() for x in api_cfg.get("contact_custom_field_values_endpoint_candidates", [])]
    )
    cf_values_search_endpoint_candidates = _unique(
        [str(x).strip() for x in api_cfg.get("contact_custom_field_values_search_endpoint_candidates", [])]
    )
    import_endpoint_candidates = _unique(
        [str(x).strip() for x in api_cfg.get("import_endpoint_candidates", ["/api/v3/import"])]
    )

    client = SmartEmailingApiClient(
        SmartEmailingCredentials(username=username, api_key=api_key, base_url=base_url)
    )

    print(f"Používám účet: {username}")
    print(f"Base URL: {base_url}")

    contact_id = str(args.contact_id).strip()
    email_key = str(args.email).strip().casefold()
    field_id = str(args.field_id).strip()
    pair_id = str(args.pair_id).strip()

    if not contact_id and email_key:
        found = client.fetch_contacts_by_emails(
            email_keys={email_key},
            endpoint_candidates=contacts_endpoint_candidates or None,
            search_endpoint_candidates=contacts_search_endpoint_candidates or None,
        )
        contact = found.get(email_key)
        if contact:
            contact_id = str(contact.get("id", "")).strip()
        print(f"Dohledaný kontakt pro {email_key}: {json.dumps(contact or {}, ensure_ascii=False)}")

    values: list[dict[str, Any]] = []
    if contact_id:
        values_by_contact = client.fetch_custom_field_values_for_contacts(
            contact_ids={contact_id},
            endpoint_candidates=cf_values_endpoint_candidates or None,
            search_endpoint_candidates=cf_values_search_endpoint_candidates or None,
        )
        values = values_by_contact.get(contact_id, [])
        print("Načtené custom field values pro kontakt:")
        print(json.dumps(values, ensure_ascii=False, indent=2))
        if field_id:
            for item in values:
                if str(item.get("id", "")).strip() == field_id:
                    pair_id = str(item.get("pair_id", "")).strip()
                    break
        elif pair_id:
            for item in values:
                if str(item.get("pair_id", "")).strip() == pair_id:
                    field_id = str(item.get("id", "")).strip()
                    break
        elif len(values) == 1:
            pair_id = str(values[0].get("pair_id", "")).strip()
            field_id = str(values[0].get("id", "")).strip()

    if not field_id:
        print("CHYBA: nepodařilo se určit field_id. Zadej --field-id nebo --pair-id + --contact-id/--email.")
        return 3

    if pair_id:
        print(f"Resolved pair_id: {pair_id}")
    print(f"Resolved field_id: {field_id}")

    if not args.delete:
        print("Dry-run OK (vyčištění se neprovedlo). Přidej --delete pro skutečné vyčištění.")
        return 0

    resolved_email = str(args.email).strip()
    if not resolved_email and contact_id:
        details = client.fetch_contact_details_by_ids([contact_id])
        detail = details.get(contact_id, {})
        resolved_email = str(detail.get("emailaddress", "")).strip()
    if not resolved_email:
        print("CHYBA: nepodařilo se určit email kontaktu. Zadej --email nebo --contact-id.")
        return 4

    clear_contact_payload = {
        "emailaddress": resolved_email,
        "customfields": [{"id": field_id, "value": ""}],
    }
    try:
        response, endpoint, payload_variant = client.import_contacts_batch(
            contacts=[clear_contact_payload],
            update_existing=True,
            skip_invalid_contacts=True,
            endpoint_candidates=import_endpoint_candidates or None,
        )
    except Exception as exc:
        print(f"CHYBA: clear přes import selhal: {exc}")
        return 5

    print(f"CLEAR OK přes endpoint: {endpoint}")
    print(f"Payload variant: {payload_variant}")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
