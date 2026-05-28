from __future__ import annotations

import time
from typing import Any, Callable, TypeVar


T = TypeVar("T")

RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_TEXT_TOKENS = (
    "rate",
    "quota",
    "temporar",
    "unavailable",
    "deadline",
    "timeout",
    "too many requests",
)


def _text_contains_retry_token(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return any(token in lowered for token in RETRYABLE_TEXT_TOKENS)


def is_retryable_http_status(status_code: int | None, details: str = "") -> bool:
    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        return True
    return _text_contains_retry_token(details)


def is_retryable_google_ads_exception(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in RETRYABLE_HTTP_STATUS_CODES:
        return True

    text_parts = [str(exc)]
    failure = getattr(exc, "failure", None)
    if failure:
        for error in getattr(failure, "errors", []):
            message = getattr(error, "message", None)
            if message:
                text_parts.append(str(message))
    return _text_contains_retry_token(" | ".join(text_parts))


def run_with_retry(
    action: Callable[[], T],
    *,
    should_retry: Callable[[Exception], bool],
    max_attempts: int = 4,
    base_delay_seconds: float = 1.0,
) -> T:
    last_exception: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return action()
        except Exception as exc:
            last_exception = exc
            if attempt >= max_attempts or not should_retry(exc):
                raise
            time.sleep(base_delay_seconds * (2 ** (attempt - 1)))
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("Retry execution ended without result.")


def run_http_request_with_retry(
    action: Callable[[], Any],
    *,
    max_attempts: int = 4,
    base_delay_seconds: float = 1.0,
) -> Any:
    last_response: Any = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = action()
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            time.sleep(base_delay_seconds * (2 ** (attempt - 1)))
            continue

        last_response = response
        status_code = getattr(response, "status_code", None)
        details = getattr(response, "text", "")
        if not is_retryable_http_status(status_code, details) or attempt >= max_attempts:
            return response
        time.sleep(base_delay_seconds * (2 ** (attempt - 1)))

    return last_response
