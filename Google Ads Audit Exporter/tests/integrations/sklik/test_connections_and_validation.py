from __future__ import annotations

import json

from app.integrations.sklik.auth import get_secret
from app.integrations.sklik.models import SklikAccountContextMapping
from app.integrations.sklik.normalizers import normalize_halers_to_czk, sanitize_payload
from app.integrations.sklik.validators import validate_mapping
from app.web.services.sklik_connection_service import save_sklik_connection


def test_connections_do_not_store_tokens_in_json(tmp_path) -> None:
    save_sklik_connection(
        tmp_path,
        {
            "key": "itfuture",
            "label": "ITFuture",
            "drak_enabled": True,
            "fenix_enabled": True,
            "drak_token": "drak-secret",
            "fenix_refresh_token": "fenix-refresh",
        },
    )

    payload = json.loads((tmp_path / "app_state" / "sklik_connections.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload)
    assert "drak-secret" not in serialized
    assert "fenix-refresh" not in serialized
    assert get_secret(tmp_path, "SKLIK_DRAK_TOKEN__ITFUTURE") == "drak-secret"
    assert get_secret(tmp_path, "SKLIK_FENIX_REFRESH_TOKEN__ITFUTURE") == "fenix-refresh"


def test_fenix_access_token_not_persisted(tmp_path) -> None:
    save_sklik_connection(
        tmp_path,
        {
            "key": "itfuture",
            "label": "ITFuture",
            "drak_enabled": False,
            "fenix_enabled": True,
            "fenix_refresh_token": "fenix-refresh",
        },
    )
    env_text = (tmp_path / ".env.sklik.local").read_text(encoding="utf-8")
    assert "fenix-refresh" in env_text
    assert "ACCESS_TOKEN" not in env_text


def test_sklik_mapping_validation() -> None:
    mapping = SklikAccountContextMapping(
        context_key="shopid_cz",
        enabled=True,
        connection_key="itfuture",
        drak_user_ids=["123456"],
        fenix_premise_ids=["987"],
        expected_domains=["shopid.cz"],
        enable_fenix=True,
        enable_web_scan=True,
    )
    validation = validate_mapping(
        mapping,
        known_context_keys={"shopid_cz"},
        known_connection_keys={"itfuture"},
    )
    assert validation.ok is True
    assert validation.errors == []


def test_money_normalization_halers_to_czk() -> None:
    assert normalize_halers_to_czk(12345) == 123.45


def test_raw_payload_sanitization() -> None:
    sanitized = sanitize_payload(
        {
            "session": "abc",
            "access_token": "def",
            "nested": {"refresh_token": "ghi", "value": 1},
        }
    )
    assert sanitized["session"] == "***"
    assert sanitized["access_token"] == "***"
    assert sanitized["nested"]["refresh_token"] == "***"

