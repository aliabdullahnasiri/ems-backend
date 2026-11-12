from uuid import UUID

from pydantic import ConfigDict

from app.schemas.base.model import Model


class FinanceData(Model):
    model_config = ConfigDict(from_attributes=True)


class SettingsData(FinanceData):
    shop_id: int
    name: str
    address: str | None
    phone: str | None
    logo_reference: str | None
    preferred_language: str
    receipt_footer: str | None
    timezone: str = "Asia/Kabul"


class CurrencyData(FinanceData):
    id: int
    uuid: UUID
    code: str
    precision: int
    is_active: bool
    rial_per_unit: int | None


class BalanceData(FinanceData):
    currency: str
    amount: str
    book_afn: str


class CashboxData(FinanceData):
    id: int
    uuid: UUID
    shop_id: int
    name: str
    is_active: bool
    balances: list[BalanceData]


class LineData(FinanceData):
    account_id: int
    account_code: str
    kind: str
    currency: str
    amount: str
    book_afn: str


class EntryData(FinanceData):
    id: int
    uuid: UUID
    shop_id: int
    action: str
    reason: str
    user_id: int
    membership_id: int
    created_at: str
    reversal_of_id: int | None
    lines: list[LineData]
