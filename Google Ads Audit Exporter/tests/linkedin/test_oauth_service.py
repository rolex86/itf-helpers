from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.integrations.linkedin.models import LinkedInConnection, LinkedInRuntimeConfig
from app.web.services import linkedin_oauth_service


def runtime_config() -> LinkedInRuntimeConfig:
    return LinkedInRuntimeConfig(
        client_id="runtime-client-id",
        client_secret="runtime-client-secret",
        redirect_uri="https://example.test/linkedin/oauth/callback",
        api_version="202606",
        user_agent="TestLinkedInAudit/1.0",
        request_timeout_seconds=10,
        max_retries=0,
    )


def connection(**overrides: Any) -> LinkedInConnection:
    payload: dict[str, Any] = {
        "key": "linkedin-main",
        "label": "LinkedIn Main",
        "auth_type": "oauth",
        "client_id": "connection-client-id",
        "requested_scopes": ["r_ads", "r_ads_reporting"],
        "granted_scopes": [],
        "status": "draft",
        "token_expires_at": "",
        "refresh_token_expires_at": "",
        "last_error": "",
    }
    payload.update(overrides)
    return LinkedInConnection(**payload)


def test_build_oauth_start_returns_state_authorize_url_and_sanitized_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_runtime_config", lambda project_root: runtime_config())

    result = linkedin_oauth_service.build_oauth_start(
        Path("/tmp/project"),
        {
            "key": "linkedin-main",
            "label": "LinkedIn Main",
            "auth_type": "oauth",
            "client_id": "connection-client-id",
            "requested_scopes": ["r_ads", "r_ads_reporting"],
        },
    )

    assert result["state"]
    assert "https://www.linkedin.com/oauth/v2/authorization" in result["authorize_url"]
    assert "client_id=connection-client-id" in result["authorize_url"]
    assert "response_type=code" in result["authorize_url"]
    assert result["connection"]["key"] == "linkedin-main"
    assert result["connection"]["auth_type"] == "oauth"


def test_handle_oauth_callback_exchanges_code_saves_connection_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    saved_connections: list[LinkedInConnection] = []
    saved_tokens: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_runtime_config", lambda project_root: runtime_config())
    monkeypatch.setattr(
        linkedin_oauth_service,
        "_hydrate_secrets",
        lambda project_root, conn, payload: ("", "", "connection-client-secret"),
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "exchange_code_for_token",
        lambda **kwargs: {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "refresh_token_expires_in": 7200,
            "scope": "r_ads r_ads_reporting",
        },
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "upsert_linkedin_connection",
        lambda project_root, conn: saved_connections.append(conn),
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "save_token_payload",
        lambda project_root, key, payload: saved_tokens.append((key, payload)),
    )

    result = linkedin_oauth_service.handle_oauth_callback(
        Path("/tmp/project"),
        payload={
            "key": "linkedin-main",
            "label": "LinkedIn Main",
            "auth_type": "oauth",
            "client_id": "connection-client-id",
            "requested_scopes": ["r_ads", "r_ads_reporting"],
            "code": "authorization-code",
        },
    )

    assert result["ok"] is True
    assert saved_connections
    assert saved_connections[-1].status == "active"
    assert saved_connections[-1].granted_scopes == ["r_ads", "r_ads_reporting"]

    assert saved_tokens == [
        (
            "linkedin-main",
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "client_secret": "connection-client-secret",
                "manual_token": "",
            },
        )
    ]


def test_handle_oauth_callback_requires_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_runtime_config", lambda project_root: runtime_config())
    monkeypatch.setattr(
        linkedin_oauth_service,
        "_hydrate_secrets",
        lambda project_root, conn, payload: ("", "", "connection-client-secret"),
    )

    with pytest.raises(ValueError, match="authorization code"):
        linkedin_oauth_service.handle_oauth_callback(
            Path("/tmp/project"),
            payload={
                "key": "linkedin-main",
                "label": "LinkedIn Main",
                "auth_type": "oauth",
                "client_id": "connection-client-id",
                "code": "",
            },
        )


def test_refresh_linkedin_connection_token_updates_connection_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_connection = connection(
        token_expires_at="2026-01-01T00:00:00+00:00",
        refresh_token_expires_at="2026-02-01T00:00:00+00:00",
    )
    saved_connections: list[LinkedInConnection] = []
    saved_tokens: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_runtime_config", lambda project_root: runtime_config())
    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_connections", lambda project_root: [existing_connection])
    monkeypatch.setattr(
        linkedin_oauth_service,
        "load_token_payload",
        lambda project_root, key: {
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "client_secret": "stored-client-secret",
        },
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "refresh_access_token",
        lambda **kwargs: {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "refresh_token_expires_in": 7200,
            "scope": "r_ads r_ads_reporting",
        },
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "upsert_linkedin_connection",
        lambda project_root, conn: saved_connections.append(conn),
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "save_token_payload",
        lambda project_root, key, payload: saved_tokens.append((key, payload)),
    )

    result = linkedin_oauth_service.refresh_linkedin_connection_token(Path("/tmp/project"), "linkedin-main")

    assert result["ok"] is True
    assert saved_connections
    assert saved_connections[-1].status == "active"
    assert saved_connections[-1].granted_scopes == ["r_ads", "r_ads_reporting"]

    assert saved_tokens == [
        (
            "linkedin-main",
            {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "client_secret": "stored-client-secret",
                "manual_token": "",
            },
        )
    ]


def test_refresh_linkedin_connection_token_marks_needs_reauth_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_connection = connection()
    saved_connections: list[LinkedInConnection] = []

    def raise_refresh_error(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_runtime_config", lambda project_root: runtime_config())
    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_connections", lambda project_root: [existing_connection])
    monkeypatch.setattr(
        linkedin_oauth_service,
        "load_token_payload",
        lambda project_root, key: {
            "refresh_token": "refresh-token",
            "client_secret": "stored-client-secret",
        },
    )
    monkeypatch.setattr(linkedin_oauth_service, "refresh_access_token", raise_refresh_error)
    monkeypatch.setattr(
        linkedin_oauth_service,
        "upsert_linkedin_connection",
        lambda project_root, conn: saved_connections.append(conn),
    )

    with pytest.raises(RuntimeError, match="refresh failed"):
        linkedin_oauth_service.refresh_linkedin_connection_token(Path("/tmp/project"), "linkedin-main")

    assert saved_connections
    assert saved_connections[-1].status == "needs_reauth"
    assert saved_connections[-1].last_error == "refresh failed"


def test_ensure_connection_access_token_returns_existing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_connection = connection(token_expires_at="2999-01-01T00:00:00+00:00")

    monkeypatch.setattr(
        linkedin_oauth_service,
        "load_token_payload",
        lambda project_root, key: {
            "access_token": "access-token",
        },
    )

    token = linkedin_oauth_service.ensure_connection_access_token(Path("/tmp/project"), existing_connection)

    assert token == "access-token"


def test_ensure_connection_access_token_refreshes_expiring_token(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_connection = connection(token_expires_at="2000-01-01T00:00:00+00:00")
    token_payloads = [
        {
            "access_token": "old-access-token",
            "refresh_token": "refresh-token",
        },
        {
            "access_token": "new-access-token",
            "refresh_token": "refresh-token",
        },
    ]
    refreshed_connection = connection(
        status="active",
        granted_scopes=["r_ads", "r_ads_reporting"],
        token_expires_at="2999-01-01T00:00:00+00:00",
    )

    def load_token_payload(project_root: Path, key: str) -> dict[str, Any]:
        return token_payloads.pop(0)

    monkeypatch.setattr(linkedin_oauth_service, "load_token_payload", load_token_payload)
    monkeypatch.setattr(
        linkedin_oauth_service,
        "refresh_linkedin_connection_token",
        lambda project_root, connection_key: {"ok": True},
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "load_linkedin_connections",
        lambda project_root: [refreshed_connection],
    )

    token = linkedin_oauth_service.ensure_connection_access_token(Path("/tmp/project"), existing_connection)

    assert token == "new-access-token"
    assert existing_connection.status == "active"
    assert existing_connection.granted_scopes == ["r_ads", "r_ads_reporting"]


def test_ensure_connection_access_token_marks_needs_reauth_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_connection = connection()
    saved_connections: list[LinkedInConnection] = []

    monkeypatch.setattr(linkedin_oauth_service, "load_token_payload", lambda project_root, key: {})
    monkeypatch.setattr(
        linkedin_oauth_service,
        "upsert_linkedin_connection",
        lambda project_root, conn: saved_connections.append(conn),
    )

    with pytest.raises(ValueError, match="access token"):
        linkedin_oauth_service.ensure_connection_access_token(Path("/tmp/project"), existing_connection)

    assert saved_connections
    assert saved_connections[-1].status == "needs_reauth"


def test_revoke_local_linkedin_connection_revokes_tokens_and_marks_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_connection = connection()
    revoked: list[str] = []
    saved_connections: list[LinkedInConnection] = []

    monkeypatch.setattr(linkedin_oauth_service, "load_linkedin_connections", lambda project_root: [existing_connection])
    monkeypatch.setattr(
        linkedin_oauth_service,
        "revoke_local_tokens",
        lambda project_root, connection_key: revoked.append(connection_key),
    )
    monkeypatch.setattr(
        linkedin_oauth_service,
        "upsert_linkedin_connection",
        lambda project_root, conn: saved_connections.append(conn),
    )

    result = linkedin_oauth_service.revoke_local_linkedin_connection(Path("/tmp/project"), "linkedin-main")

    assert result["ok"] is True
    assert revoked == ["linkedin-main"]
    assert saved_connections
    assert saved_connections[-1].status == "needs_reauth"