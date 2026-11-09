from typing import Any, Self

from pydantic import BaseModel, model_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_async_validation.mixins import AsyncValidationModelMixin

from app.core.exceptions import AppValidationError
from app.schemas.base.meta import BaseModelMeta


class Model(AsyncValidationModelMixin, BaseModel, metaclass=BaseModelMeta):
    """
    Core base model for all application schemas.

    Extends Pydantic's BaseModel and integrates asynchronous validation
    capabilities via AsyncValidationModelMixin and customized metaclass
    behavior via BaseModelMeta.
    """

    @model_validator(mode="wrap")
    @classmethod
    def _validate(cls, values: Any, handler) -> Any:
        try:
            return handler(values)
        except PydanticValidationError as exc:
            raise AppValidationError(errors=exc.errors()) from None

    async def model_async_validate(self) -> None:
        try:
            await super().model_async_validate()
        except PydanticValidationError as exc:
            raise AppValidationError(errors=exc.errors()) from None

    @classmethod
    def model_validate(cls, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(*args, **kwargs)
        except PydanticValidationError as exc:
            raise AppValidationError(errors=exc.errors()) from None

    @classmethod
    def model_validate_json(cls, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate_json(*args, **kwargs)
        except PydanticValidationError as exc:
            raise AppValidationError(errors=exc.errors()) from None

    @classmethod
    def model_validate_strings(cls, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate_strings(*args, **kwargs)
        except PydanticValidationError as exc:
            raise AppValidationError(errors=exc.errors()) from None

    def __setattr__(self, name: str, value: Any) -> None:
        try:
            super().__setattr__(name, value)
        except PydanticValidationError as exc:
            raise AppValidationError(errors=exc.errors()) from None
