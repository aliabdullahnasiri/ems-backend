from datetime import datetime
from typing import Literal
from uuid import UUID

from app.schemas.finance.data import FinanceData, SettingsData


class RateData(FinanceData):
    id: int
    uuid: UUID
    shop_id: int
    base_currency_id: int
    quote_currency_id: int
    base_currency: str
    quote_currency: str
    direction: str = "quote_per_base"
    buying_rate: str
    selling_rate: str
    user_id: int
    membership_id: int
    created_at: datetime


class QuoteData(FinanceData):
    side: Literal["buy", "sell"]
    base_currency: str
    quote_currency: str
    direction: str = "quote_per_base"
    amount: str
    rate: str
    incoming_currency: str
    outgoing_currency: str
    incoming_amount: str
    outgoing_amount: str
    unrounded_quote_amount: str
    rounding_adjustment: str
    rounding_mode: str = "ROUND_HALF_UP"
    incoming_afn_rate: str | None
    incoming_book_afn: str
    outgoing_book_afn: str
    profit_afn: str
    fees: str = "0.00"
    fee_currency: str = "AFN"


class ReceiptData(FinanceData):
    reference: str
    created_at: str
    shop: SettingsData
    cashier_id: int
    cashier_name: str
    customer_id: int | None
    customer_name: str | None
    cashbox_id: int
    values: QuoteData


class ExchangeData(FinanceData):
    id: int
    uuid: UUID
    shop_id: int
    entry_id: int
    reference: str
    receipt: ReceiptData
    reversal_entry_id: int | None = None
