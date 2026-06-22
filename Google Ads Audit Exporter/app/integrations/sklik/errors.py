from __future__ import annotations

from typing import Any


class SklikError(Exception):
    """Base error for Sklik integration."""


class SklikValidationError(SklikError):
    """Raised when Sklik configuration is invalid."""


class SklikApiError(SklikError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload
        self.recoverable = recoverable


class SklikPartialFailure(SklikApiError):
    """Recoverable endpoint failure that should be logged and skipped."""

