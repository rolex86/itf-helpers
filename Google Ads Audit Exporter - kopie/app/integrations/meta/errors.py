from __future__ import annotations


class MetaIntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        error_subcode: int | None = None,
        details: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.error_subcode = error_subcode
        self.details = details


class MetaPermissionError(MetaIntegrationError):
    pass


class MetaRateLimitError(MetaIntegrationError):
    pass
