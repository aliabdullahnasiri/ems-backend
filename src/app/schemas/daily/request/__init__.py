from datetime import date

from pydantic import Field

from app.schemas.finance.request import FinanceRequest


class CategoryRequest(FinanceRequest):
    name: str = Field(min_length=1, max_length=150)
    is_active: bool = Field(default=True, strict=True)


class CategoryUpdateRequest(FinanceRequest):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    is_active: bool | None = Field(default=None, strict=True)


class ExpenseRequest(FinanceRequest):
    category_id: int = Field(gt=0)
    cashbox_id: int = Field(gt=0)
    currency_id: int = Field(gt=0)
    amount: str = Field(max_length=40)
    expense_date: date
    payee: str = Field(min_length=1, max_length=150)
    explanation: str = Field(min_length=1, max_length=500)
    attachment_id: int | None = Field(default=None, gt=0)


class TransferRequest(FinanceRequest):
    source_cashbox_id: int = Field(gt=0)
    destination_cashbox_id: int = Field(gt=0)
    currency_id: int = Field(gt=0)
    amount: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=500)


class OpenDayRequest(FinanceRequest):
    day: date


class CountRequest(FinanceRequest):
    cashbox_id: int = Field(gt=0)
    currency_id: int = Field(gt=0)
    counted: str = Field(max_length=40)
    overage_book_afn: str | None = Field(default=None, max_length=40)


class ClosingRequest(FinanceRequest):
    counts: list[CountRequest] = Field(max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class ConfirmClosingRequest(FinanceRequest):
    owner_approved: bool = Field(strict=True)


class ReverseRequest(FinanceRequest):
    reason: str = Field(min_length=1, max_length=500)
