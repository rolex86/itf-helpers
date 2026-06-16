from __future__ import annotations

from urllib.parse import urlencode

import requests

from app.integrations.linkedin.errors import LinkedInAuthError
from app.integrations.linkedin.models import LinkedInRuntimeConfig
from app.utils.retry import run_http_request_with_retry


AUTH_BASE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def build_authorize_url(
    *,
    config: LinkedInRuntimeConfig,
    client_id: str,
    state: str,
    scopes: list[str],
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id or config.client_id,
            "redirect_uri": config.redirect_uri,
            "state": state,
            "scope": " ".join(scopes),
        }
    )
    return f"{AUTH_BASE_URL}?{query}"


def exchange_code_for_token(
    *,
    config: LinkedInRuntimeConfig,
    client_id: str,
    client_secret: str,
    code: str,
) -> dict:
    response = run_http_request_with_retry(
        lambda: requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id or config.client_id,
                "client_secret": client_secret or config.client_secret,
                "redirect_uri": config.redirect_uri,
            },
            timeout=config.request_timeout_seconds,
        )
    )
    if response.status_code >= 400:
        raise LinkedInAuthError(
            "Výměna authorization code za token selhala.",
            status_code=response.status_code,
            details=response.text,
        )
    return response.json()


def refresh_access_token(
    *,
    config: LinkedInRuntimeConfig,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    response = run_http_request_with_retry(
        lambda: requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id or config.client_id,
                "client_secret": client_secret or config.client_secret,
            },
            timeout=config.request_timeout_seconds,
        )
    )
    if response.status_code >= 400:
        raise LinkedInAuthError(
            "Obnovení LinkedIn access tokenu selhalo.",
            status_code=response.status_code,
            details=response.text,
        )
    return response.json()

