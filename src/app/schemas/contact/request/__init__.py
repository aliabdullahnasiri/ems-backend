from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.schemas.base.request import BaseRequestModel


class ContactCreateRequest(BaseRequestModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    shop_uuid: UUID
    name: str = Field(min_length=1, max_length=150)
    phone: str | None = Field(None, max_length=50)
    location: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=2000)
    is_customer: bool = True
    is_agent: bool = False
    identification: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def require_role(self):
        if not (self.is_customer or self.is_agent):
            raise ValueError("At least one contact role is required")
        return self


class ContactUpdateRequest(BaseRequestModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=150)
    phone: str | None = Field(None, max_length=50)
    location: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=2000)
    is_customer: bool | None = None
    is_agent: bool | None = None
    is_active: bool | None = None
    identification: str | None = Field(None, max_length=2000)


class ContactOpeningRequest(BaseRequestModel):
    """Owner-approved obligation, offset to equity rather than income or cash."""

    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(customer|agent)$")
    direction: str = Field(pattern="^(receivable|payable)$")
    currency_id: int = Field(gt=0)
    amount: str = Field(strict=True, min_length=1, max_length=30)
    book_afn: str = Field(strict=True, min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=500)
    owner_approved: bool
