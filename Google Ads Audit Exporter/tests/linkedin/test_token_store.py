from pathlib import Path

from app.integrations.linkedin.token_store import load_token_payload, revoke_local_tokens, save_token_payload


def test_token_store_roundtrip(tmp_path: Path) -> None:
    save_token_payload(
        tmp_path,
        "main",
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "client_secret": "secret",
            "manual_token": "manual",
        },
    )
    payload = load_token_payload(tmp_path, "main")
    assert payload["access_token"] == "access"
    assert payload["refresh_token"] == "refresh"
    assert payload["client_secret"] == "secret"
    assert payload["manual_token"] == "manual"
    revoke_local_tokens(tmp_path, "main")
    revoked = load_token_payload(tmp_path, "main")
    assert revoked["access_token"] == ""

