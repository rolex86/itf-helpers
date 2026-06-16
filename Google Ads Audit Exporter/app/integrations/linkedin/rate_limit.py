from __future__ import annotations

from app.integrations.linkedin.errors import LinkedInRateLimitError


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_TEXT_TOKENS = (
    "rate",
    "quota",
    "temporar",
    "unavailable",
    "timeout",
    "too many requests",
    "throttle",
)


def is_retryable_linkedin_error(status_code: int | None, details: str = "") -> bool:
    if status_code in RETRYABLE_STATUS_CODES:
        return True
    lowered = str(details or "").strip().lower()
    return any(token in lowered for token in RETRYABLE_TEXT_TOKENS)


def raise_if_rate_limited(status_code: int | None, message: str, details: str, retry_after_seconds: int | None = None) -> None:
    if is_retryable_linkedin_error(status_code, details or message):
        raise LinkedInRateLimitError(
            message,
            status_code=status_code,
            details=details,
            retry_after_seconds=retry_after_seconds,
        )

