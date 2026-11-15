from datetime import date, datetime
from uuid import UUID

from pydantic import ConfigDict

from app.schemas.base.model import Model


class CategoryData(Model):
    model_config = ConfigDict(from_attributes=True)
    id: int
    uuid: UUID
    name: str
    is_active: bool


class OperationData(Model):
    model_config = ConfigDict(from_attributes=True)
    id: int
    uuid: UUID
    shop_id: int
    entry_id: int
    currency_id: int
    currency: str
    amount: str
    book_afn: str
    category_id: int | None = None
    cashbox_id: int | None = None
    source_cashbox_id: int | None = None
    destination_cashbox_id: int | None = None
    expense_date: date | None = None
    payee: str | None = None
    explanation: str | None = None
    attachment_id: int | None = None
    reason: str | None = None
    reversal_entry_id: int | None = None


class CashCountData(Model):
    cashbox_id: int
    currency_id: int
    currency: str
    expected: str
    book_afn: str
    counted: str | None = None
    difference: str | None = None
    adjustment_book_afn: str | None = None


class DayData(Model):
    id: int
    uuid: UUID
    day: date
    opening: list[CashCountData]
    status: str


class ClosingData(Model):
    id: int
    uuid: UUID
    business_day_id: int
    cutoff: datetime
    counts: list[CashCountData]
    reason: str | None
    confirmed: bool
    adjustment_entry_id: int | None = None
