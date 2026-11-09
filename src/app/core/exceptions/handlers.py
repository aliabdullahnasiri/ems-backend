from typing import cast

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions.base import AppError
from app.core.exceptions.resources import NotFoundError
from app.core.exceptions.validation import AppValidationError
from app.core.i18n.types import T


async def app_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    Handle application-level errors.
    """
    error = cast(AppError, exc)

    content = {"type": error.type, "message": error.message}
    if isinstance(error, AppValidationError) and error.errors():
        content["errors"] = jsonable_encoder(error.errors())
    return JSONResponse(status_code=error.code, content=content)


async def validation_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    Handle Pydantic and FastAPI request validation errors.
    """
    if isinstance(exc, AppValidationError):
        error = exc
    elif isinstance(exc, (RequestValidationError, PydanticValidationError)):
        error = AppValidationError(errors=exc.errors())
    else:
        error = AppError()
    return await app_error_handler(request, error)


async def no_result_found_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Translate an uncaught ORM missing-row error into the API's 404 response."""
    return await app_error_handler(
        request, NotFoundError(message=T("errors:not_found"))
    )
