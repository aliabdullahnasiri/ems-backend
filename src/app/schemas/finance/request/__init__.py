from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from app.core.exceptions import AppValidationError
from app.schemas.base.request import BaseRequestModel


class FinanceRequest(BaseRequestModel):
    model_config = ConfigDict(extra="forbid")


class SettingsUpdateRequest(FinanceRequest):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    logo_reference: str | None = Field(default=None, max_length=500)
    preferred_language: Literal["fa", "ps", "en"] | None = None
    receipt_footer: str | None = Field(default=None, max_length=1000)


class CurrencyCreateRequest(FinanceRequest):
    code: str = Field(pattern=r"^[A-Z]{3}$")
    precision: int = Field(ge=0, le=8, strict=True)


class CurrencyUpdateRequest(FinanceRequest):
    is_active: bool = Field(strict=True)


class CashboxCreateRequest(FinanceRequest):
    name: str = Field(min_length=1, max_length=150)


class CashboxUpdateRequest(FinanceRequest):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    is_active: bool | None = Field(default=None, strict=True)


class OpeningBalanceRequest(FinanceRequest):
    cashbox_id: int = Field(gt=0)
    currency_id: int = Field(gt=0)
    amount: str = Field(strict=True, max_length=40)
    book_afn: str = Field(strict=True, max_length=40)
    owner_approved: Literal[True]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("owner_approved", mode="before")
    @classmethod
    def explicit_approval(cls, value):
        """Approval must be the literal JSON boolean true."""
        if value is not True:
            raise AppValidationError()
        return value
