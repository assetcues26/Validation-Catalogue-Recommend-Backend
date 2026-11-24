"""Domain exceptions for the validation pipeline."""


class ValidationPipelineError(Exception):
    """Base error for pipeline failures."""


class RequestValidationError(ValidationPipelineError):
    """Invalid request payload (maps to HTTP 400)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
