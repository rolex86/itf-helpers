from __future__ import annotations

from app.integrations.meta.errors import MetaRateLimitError


RETRYABLE_META_ERROR_CODES = {1, 2, 4, 17, 32, 613}


def is_retryable_meta_error(status_code: int | None, error_code: int | None, message: str = "") -> bool:
    if status_code in {429, 500, 502, 503, 504}:
        return True
    if error_code in RETRYABLE_META_ERROR_CODES:
        return True
    lowered = str(message or "").lower()
    return "rate limit" in lowered or "temporarily unavailable" in lowered


def raise_if_rate_limited(status_code: int | None, error_code: int | None, message: str, details: str = "") -> None:
    if is_retryable_meta_error(status_code, error_code, message):
        raise MetaRateLimitError(
            message,
            status_code=status_code,
            error_code=error_code,
            details=details,
        )
