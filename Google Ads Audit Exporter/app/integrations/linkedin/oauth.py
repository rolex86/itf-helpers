from __future__ import annotations

import json
from urllib.parse import urlencode

import requests

from app.integrations.linkedin.errors import LinkedInAuthError
from app.integrations.linkedin.models import LinkedInRuntimeConfig
from app.utils.retry import run_http_request_with_retry


AUTH_BASE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def _string(value: object) -> str:
    return str(value or "").strip()


def _dedupe_scopes(scopes: list[str]) -> list[str]:
    normalized: list[str] = []

    for scope in scopes or []:
        value = _string(scope)
        if value and value not in normalized:
            normalized.append(value)

    return normalized


def _parse_json_response(response: requests.Response) -> dict:
    try:
        payload = response.json() if response.text.strip() else {}
    except ValueError:
        payload = {"message": response.text}

    return payload if isinstance(payload, dict) else {"response": payload}


def _safe_error_details(response: requests.Response) -> str:
    payload = _parse_json_response(response)

    for key in ("access_token", "refresh_token", "client_secret", "authorization", "code"):
        if key in payload:
            payload[key] = "***"

    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _raise_for_token_error(response: requests.Response, message: str) -> None:
    if response.status_code < 400:
        return

    raise LinkedInAuthError(
        message,
        status_code=response.status_code,
        details=_safe_error_details(response),
    )


def build_authorize_url(
    *,
    config: LinkedInRuntimeConfig,
    client_id: str,
    state: str,
    scopes: list[str],
) -> str:
    resolved_client_id = _string(client_id) or _string(config.client_id)
    resolved_state = _string(state)
    resolved_redirect_uri = _string(config.redirect_uri)
    resolved_scopes = _dedupe_scopes(scopes)

    if not resolved_client_id:
        raise LinkedInAuthError("Chybí LinkedIn client_id pro OAuth authorize URL.")

    if not resolved_redirect_uri:
        raise LinkedInAuthError("Chybí LinkedIn redirect_uri pro OAuth authorize URL.")

    if not resolved_state:
        raise LinkedInAuthError("Chybí OAuth state.")

    if not resolved_scopes:
        raise LinkedInAuthError("Chybí LinkedIn OAuth scopes.")

    query = urlencode(
        {
            "response_type": "code",
            "client_id": resolved_client_id,
            "redirect_uri": resolved_redirect_uri,
            "state": resolved_state,
            "scope": " ".join(resolved_scopes),
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
    resolved_client_id = _string(client_id) or _string(config.client_id)
    resolved_client_secret = _string(client_secret) or _string(config.client_secret)
    resolved_code = _string(code)
    resolved_redirect_uri = _string(config.redirect_uri)

    if not resolved_client_id:
        raise LinkedInAuthError("Chybí LinkedIn client_id pro výměnu authorization code.")

    if not resolved_client_secret:
        raise LinkedInAuthError("Chybí LinkedIn client_secret pro výměnu authorization code.")

    if not resolved_code:
        raise LinkedInAuthError("Chybí LinkedIn authorization code.")

    if not resolved_redirect_uri:
        raise LinkedInAuthError("Chybí LinkedIn redirect_uri pro výměnu authorization code.")

    response = run_http_request_with_retry(
        lambda: requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": resolved_code,
                "client_id": resolved_client_id,
                "client_secret": resolved_client_secret,
                "redirect_uri": resolved_redirect_uri,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=config.request_timeout_seconds,
        )
    )

    _raise_for_token_error(response, "Výměna authorization code za token selhala.")

    payload = _parse_json_response(response)
    if not _string(payload.get("access_token")):
        raise LinkedInAuthError(
            "LinkedIn OAuth odpověď neobsahuje access_token.",
            status_code=response.status_code,
            details=_safe_error_details(response),
        )

    return payload


def refresh_access_token(
    *,
    config: LinkedInRuntimeConfig,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    resolved_client_id = _string(client_id) or _string(config.client_id)
    resolved_client_secret = _string(client_secret) or _string(config.client_secret)
    resolved_refresh_token = _string(refresh_token)

    if not resolved_client_id:
        raise LinkedInAuthError("Chybí LinkedIn client_id pro refresh tokenu.")

    if not resolved_client_secret:
        raise LinkedInAuthError("Chybí LinkedIn client_secret pro refresh tokenu.")

    if not resolved_refresh_token:
        raise LinkedInAuthError("Chybí LinkedIn refresh token.")

    response = run_http_request_with_retry(
        lambda: requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": resolved_refresh_token,
                "client_id": resolved_client_id,
                "client_secret": resolved_client_secret,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=config.request_timeout_seconds,
        )
    )

    _raise_for_token_error(response, "Obnovení LinkedIn access tokenu selhalo.")

    payload = _parse_json_response(response)
    if not _string(payload.get("access_token")):
        raise LinkedInAuthError(
            "LinkedIn refresh odpověď neobsahuje access_token.",
            status_code=response.status_code,
            details=_safe_error_details(response),
        )

    return payload