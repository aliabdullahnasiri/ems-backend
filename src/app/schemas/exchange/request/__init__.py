from typing import Literal

from pydantic import Field, field_validator

from app.schemas.finance.request import FinanceRequest, OpeningBalanceRequest


class RateCreateRequest(FinanceRequest):
    base_currency_id: int = Field(gt=0)
    quote_currency_id: int = Field(gt=0)
    buying_rate: str = Field(strict=True, max_length=40)
    selling_rate: str = Field(strict=True, max_length=40)


class ExchangeQuoteRequest(FinanceRequest):
    cashbox_id: int = Field(gt=0)
    contact_id: int | None = Field(default=None, gt=0)
    base_currency_id: int = Field(gt=0)
    quote_currency_id: int = Field(gt=0)
    side: Literal["buy", "sell"]
    amount: str = Field(strict=True, max_length=40)
    rate: str = Field(strict=True, max_length=40)
    # Explicit AFN valuation per incoming unit for a pair without AFN.
    incoming_afn_rate: str | None = Field(default=None, strict=True, max_length=40)


class ExchangeCreateRequest(ExchangeQuoteRequest):
    reason: str = Field(min_length=1, max_length=500)


class ExchangeReverseRequest(FinanceRequest):
    owner_approved: Literal[True]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("owner_approved", mode="before")
    @classmethod
    def explicit_approval(cls, value):
        return OpeningBalanceRequest.explicit_approval(value)
