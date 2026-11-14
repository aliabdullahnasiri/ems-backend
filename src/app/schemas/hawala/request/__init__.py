from typing import Literal

from pydantic import Field

from app.schemas.finance.request import FinanceRequest
from app.schemas.payment.request import AllocationRequest, PaymentReverseRequest


class HawalaCreateRequest(FinanceRequest):
    direction: Literal["incoming", "outgoing"]
    agent_id: int = Field(gt=0)
    collection_currency_id: int = Field(gt=0)
    payout_currency_id: int = Field(gt=0)
    cashbox_id: int | None = Field(default=None, gt=0)
    sender_name: str | None = Field(default=None, min_length=1, max_length=150)
    recipient_name: str | None = Field(default=None, min_length=1, max_length=150)
    destination: str | None = Field(default=None, min_length=1, max_length=500)
    collection_amount: str | None = Field(default=None, strict=True, max_length=40)
    payout_amount: str | None = Field(default=None, strict=True, max_length=40)
    rate: str | None = Field(default=None, strict=True, max_length=40)
    collection_afn_rate: str | None = Field(default=None, strict=True, max_length=40)
    payout_afn_rate: str | None = Field(default=None, strict=True, max_length=40)
    customer_fee: str = Field(default="0", strict=True, max_length=40)
    agent_fee: str = Field(default="0", strict=True, max_length=40)
    customer_fee_earned_at: Literal["confirm", "paid"] = "paid"
    agent_fee_earned_at: Literal["confirm", "paid"] = "paid"
    customer_fee_refundable: bool = Field(default=True, strict=True)
    agent_fee_refundable: bool = Field(default=True, strict=True)


class HawalaUpdateRequest(FinanceRequest):
    """Only supplied fields change; direction is fixed when the draft is created."""

    agent_id: int | None = Field(default=None, gt=0)
    collection_currency_id: int | None = Field(default=None, gt=0)
    payout_currency_id: int | None = Field(default=None, gt=0)
    cashbox_id: int | None = Field(default=None, gt=0)
    sender_name: str | None = Field(default=None, min_length=1, max_length=150)
    recipient_name: str | None = Field(default=None, min_length=1, max_length=150)
    destination: str | None = Field(default=None, min_length=1, max_length=500)
    collection_amount: str | None = Field(default=None, strict=True, max_length=40)
    payout_amount: str | None = Field(default=None, strict=True, max_length=40)
    rate: str | None = Field(default=None, strict=True, max_length=40)
    collection_afn_rate: str | None = Field(default=None, strict=True, max_length=40)
    payout_afn_rate: str | None = Field(default=None, strict=True, max_length=40)
    customer_fee: str | None = Field(default=None, strict=True, max_length=40)
    agent_fee: str | None = Field(default=None, strict=True, max_length=40)
    customer_fee_earned_at: Literal["confirm", "paid"] | None = None
    agent_fee_earned_at: Literal["confirm", "paid"] | None = None
    customer_fee_refundable: bool | None = Field(default=None, strict=True)
    agent_fee_refundable: bool | None = Field(default=None, strict=True)


class HawalaActionRequest(FinanceRequest):
    reason: str = Field(min_length=1, max_length=500)


class HawalaConfirmRequest(HawalaActionRequest):
    instruction_verified: bool = Field(default=False, strict=True)
    instruction_reference: str | None = Field(
        default=None, min_length=1, max_length=500
    )


class HawalaPayRequest(HawalaActionRequest):
    mode: Literal["local", "partner"]
    cashbox_id: int | None = Field(default=None, gt=0)
    beneficiary_verified: bool = Field(strict=True)
    verification_reference: str = Field(min_length=1, max_length=500)
    evidence_reference: str = Field(min_length=1, max_length=500)


class HawalaCancelRequest(HawalaActionRequest):
    partner_not_paid: bool = Field(default=False, strict=True)
    partner_confirmation_reference: str | None = Field(
        default=None, min_length=1, max_length=500
    )


class HawalaRefundRequest(HawalaActionRequest):
    cashbox_id: int = Field(gt=0)
    amount: str = Field(strict=True, max_length=40)
    evidence_reference: str = Field(min_length=1, max_length=500)


class HawalaReviewRequest(PaymentReverseRequest):
    decision: Literal["investigate", "resolved_offline"]
    evidence_reference: str = Field(min_length=1, max_length=500)


class AgentSettlementCreateRequest(HawalaActionRequest):
    agent_id: int = Field(gt=0)
    currency_id: int = Field(gt=0)
    cashbox_id: int | None = Field(default=None, gt=0)
    direction: Literal["receive", "pay"]
    method: Literal["cash", "bank", "other"]
    amount: str = Field(strict=True, max_length=40)
    external_reference: str = Field(min_length=1, max_length=500)
    evidence_reference: str = Field(min_length=1, max_length=500)
    allocations: list[AllocationRequest] = Field(min_length=1, max_length=50)
