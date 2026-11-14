from uuid import UUID

from app.schemas.contact.data import ContactBalanceData, ObligationData
from app.schemas.exchange.data import ReceiptData
from app.schemas.finance.data import EntryData, FinanceData, SettingsData


class AllocationData(FinanceData):
    obligation_id: int
    amount: str
    currency: str
    direction: str
    book_afn: str


class PaymentReceiptData(FinanceData):
    reference: str
    created_at: str
    shop: SettingsData
    cashier_id: int
    cashier_name: str
    customer_id: int
    customer_name: str
    cashbox_id: int
    direction: str
    currency: str
    amount: str
    book_afn: str
    profit_afn: str
    reason: str
    settlement_exchange: ReceiptData | None
    allocations: list[AllocationData]


class PaymentData(FinanceData):
    id: int
    uuid: UUID
    shop_id: int
    entry_id: int
    reference: str
    receipt: PaymentReceiptData
    reversal_entry_id: int | None


class PaymentResultData(FinanceData):
    payment: PaymentData
    balances: list[ContactBalanceData]
    obligations: list[ObligationData]


class PaymentReversalData(PaymentResultData):
    entry: EntryData
