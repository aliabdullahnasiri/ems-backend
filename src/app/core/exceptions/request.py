from app.core.exceptions.base import AppError
from app.core.i18n.types import T


class BadRequestError(AppError):
    """Raised when a request is invalid."""

    code = 400
    type = "bad_request"
    message = T("request:bad_request")


class ServiceUnavailableError(AppError):
    """Raised when the service is temporarily unavailable."""

    code = 503
    type = "service_unavailable"
    message = T("health.database_unavailable")


class DatabaseContextUnavailableError(AppError):
    type = "database_context_unavailable"
    message = T("errors:database_context_unavailable")


class WorkerLoopUnavailableError(AppError):
    type = "worker_loop_unavailable"
    message = T("worker:event_loop_not_initialized")


class WorkerDatabaseUnavailableError(AppError):
    type = "worker_database_unavailable"
    message = T("worker:database_not_initialized")


class TwoFactorChallengeStorageError(ServiceUnavailableError):
    type = "two_factor_challenge_storage_unavailable"
    message = T("auth:challenge_storage_failed")


class SerializationError(AppError):
    type = "serialization_error"
    message = T("errors:serialization_failed")
