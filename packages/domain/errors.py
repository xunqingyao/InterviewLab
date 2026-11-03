from __future__ import annotations


class DomainError(Exception):
    code = "DOMAIN_ERROR"
    http_status = 400
    retryable = False

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthRequired(DomainError):
    code = "AUTH_REQUIRED"
    http_status = 401


class ActionNotAllowed(DomainError):
    code = "ACTION_NOT_ALLOWED"
    http_status = 403


class ResourceNotFound(DomainError):
    code = "RESOURCE_NOT_FOUND"
    http_status = 404


class StateConflict(DomainError):
    code = "STATE_CONFLICT"
    http_status = 409


class IdempotencyMismatch(DomainError):
    code = "IDEMPOTENCY_MISMATCH"
    http_status = 409


class EventCursorExpired(DomainError):
    code = "EVENT_CURSOR_EXPIRED"
    http_status = 410


class FileTooLarge(DomainError):
    code = "FILE_TOO_LARGE"
    http_status = 413


class InvalidAnswer(DomainError):
    code = "INVALID_ANSWER"
    http_status = 422


class InvalidAgentOutput(DomainError):
    code = "INVALID_AGENT_OUTPUT"
    http_status = 422


class UnreadableResume(DomainError):
    code = "UNREADABLE_RESUME"
    http_status = 422


class QuotaExceeded(DomainError):
    code = "QUOTA_EXCEEDED"
    http_status = 429


class DependencyUnavailable(DomainError):
    code = "DEPENDENCY_UNAVAILABLE"
    http_status = 503
