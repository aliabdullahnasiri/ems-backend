from datetime import datetime
from uuid import UUID

from app.schemas.finance.data import FinanceData, SettingsData
from app.schemas.hawala.request import HawalaCreateRequest


class HawalaTermsData(HawalaCreateRequest):
    """Validated input terms frozen with canonical values at confirmation."""

    collection_amount: str
    payout_amount: str
    rate: str
    direction_label: str = "payout_per_collection"
    collection_book_afn: str
    payout_book_afn: str
    customer_fee_book_afn: str
    agent_fee_book_afn: str
    unrounded_payout_amount: str
    rounding_adjustment: str
    rounding_mode: str = "ROUND_HALF_UP"
    collection_currency: str
    payout_currency: str


class HawalaEventData(FinanceData):
    id: int
    kind: str
    user_id: int
    membership_id: int
    created_at: datetime
    entry_id: int | None
    details: dict


class AgentObligationData(FinanceData):
    id: int
    uuid: UUID
    agent_id: int
    hawala_id: int | None
    source_entry_id: int
    currency_id: int
    currency: str
    direction: str
    kind: str
    original_amount: str
    settled_amount: str
    remaining_amount: str
    remaining_book_afn: str
    is_cancelled: bool
    settlements: list[dict]


class HawalaReceiptData(FinanceData):
    reference: str
    confirmed_at: str
    operator_id: int
    operator_name: str
    agent_name: str
    shop: SettingsData
    terms: HawalaTermsData


class HawalaData(FinanceData):
    id: int
    uuid: UUID
    reference: str
    shop_id: int
    state: str
    payout_status: str
    settlement_status: str
    draft: HawalaCreateRequest
    receipt: HawalaReceiptData | None
    refund_due: str
    refund_paid: str
    refund_remaining: str
    refund_currency: str | None
    obligations: list[AgentObligationData]
    history: list[HawalaEventData]


class AgentSettlementData(FinanceData):
    id: int
    uuid: UUID
    shop_id: int
    entry_id: int
    reference: str
    details: dict
    reversal_entry_id: int | None
