from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, JsonValue, StringConstraints, model_validator

from app.core.exceptions import NullFieldError
from app.schemas.base.request import BaseRequestModel

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
]
Slug = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=150, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    ),
]


class CreateOrganizationRequest(BaseRequestModel):
    name: Name
    slug: Slug
    settings: dict[str, JsonValue] = Field(default_factory=dict)


class UpdateOrganizationRequest(BaseRequestModel):
    name: Name | None = None
    slug: Slug | None = None
    settings: dict[str, JsonValue] | None = None
    is_active: bool | None = None
    owner_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def reject_null_fields(self) -> Self:
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise NullFieldError(field)
        return self


class AddMemberRequest(BaseRequestModel):
    user_id: int | None = Field(default=None, gt=0)
    user_uuid: UUID | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> Self:
        if (self.user_id is None) == (self.user_uuid is None):
            raise ValueError("Provide exactly one of user_uuid or user_id")
        return self

    @property
    def user_identifier(self) -> UUID | int:
        if self.user_uuid is not None:
            return self.user_uuid
        assert self.user_id is not None
        return self.user_id


class UpdateMemberRequest(BaseRequestModel):
    is_active: bool
