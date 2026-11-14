from typing import Literal

from pydantic import Field

from app.schemas.exchange.request import ExchangeReverseRequest
from app.schemas.finance.request import FinanceRequest


class AllocationRequest(FinanceRequest):
    obligation_id: int = Field(gt=0)
    amount: str = Field(strict=True, max_length=40)


class PaymentCreateRequest(FinanceRequest):
    contact_id: int = Field(gt=0)
    cashbox_id: int = Field(gt=0)
    currency_id: int = Field(gt=0)
    direction: Literal["receive", "pay"]
    amount: str = Field(strict=True, max_length=40)
    allocations: list[AllocationRequest] = Field(min_length=1, max_length=50)
    settlement_exchange_id: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=500)


class PaymentReverseRequest(ExchangeReverseRequest):
    pass
