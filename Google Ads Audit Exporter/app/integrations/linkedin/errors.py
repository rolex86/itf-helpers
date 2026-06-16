from __future__ import annotations


class LinkedInIntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        details: str = "",
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details
        self.retry_after_seconds = retry_after_seconds


class LinkedInPermissionError(LinkedInIntegrationError):
    pass


class LinkedInRateLimitError(LinkedInIntegrationError):
    pass


class LinkedInAuthError(LinkedInIntegrationError):
    pass

