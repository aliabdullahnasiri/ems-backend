"""Customer payments, immutable receipts and allocation restoration."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.api.routers.finance import RetryKey, ShopID
from app.schemas.payment.request import PaymentCreateRequest, PaymentReverseRequest
from app.schemas.payment.response import (
    OutstandingResponse,
    PaymentReceiptResponse,
    PaymentResponse,
    PaymentResultResponse,
    PaymentReversalResponse,
    PaymentsResponse,
)
from app.services.obligation import ObligationService
from app.services.payment import PaymentService

router = APIRouter(prefix="/payments", tags=["Customer payments"])
contact_router = APIRouter(prefix="/contacts", tags=["Customers and agents"])


@router.post(
    "", response_model=PaymentResultResponse, status_code=status.HTTP_201_CREATED
)
async def create_payment(
    data: PaymentCreateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Record received/paid cash and allocations atomically (payment:create)."""
    return PaymentResultResponse(
        data=await PaymentService.create(db, context, shop_id, retry_key, data)
    )


@router.get("", response_model=PaymentsResponse)
async def payments(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    contact_id: int | None = Query(None, gt=0),
    currency_id: int | None = Query(None, gt=0),
    direction: str | None = Query(None, pattern="^(receive|pay)$"),
    reference: str | None = Query(None, max_length=40),
):
    """Search shop payment history (payment:read); dates are inclusive Kabul days."""
    return PaymentsResponse(
        data=await PaymentService.list(
            db,
            context,
            shop_id,
            offset,
            limit,
            date_from,
            date_to,
            contact_id,
            currency_id,
            direction,
            reference,
        )
    )


@router.get("/{payment_uuid}", response_model=PaymentResponse)
async def get_payment(
    payment_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Read original payment and current reversal status (payment:read)."""
    return PaymentResponse(
        data=await PaymentService.get(db, context, shop_id, payment_uuid)
    )


@router.get("/{payment_uuid}/receipt", response_model=PaymentReceiptResponse)
async def receipt(
    payment_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Return the immutable original payment receipt, without posting (payment:read)."""
    return PaymentReceiptResponse(
        data=(await PaymentService.get(db, context, shop_id, payment_uuid)).receipt
    )


@router.post(
    "/{payment_uuid}/reverse",
    response_model=PaymentReversalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse(
    payment_uuid: UUID,
    data: PaymentReverseRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner-approved linked reversal restores allocations and cash (payment:reverse)."""
    return PaymentReversalResponse(
        data=await PaymentService.reverse(
            db, context, shop_id, payment_uuid, retry_key, data
        )
    )


@contact_router.get("/{contact_uuid}/outstanding", response_model=OutstandingResponse)
async def outstanding(
    contact_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_settled: bool = False,
):
    """Return original, paid, remaining amounts and settlement history (contact:read)."""
    return OutstandingResponse(
        data=await ObligationService.outstanding(
            db, context, contact_uuid, offset, limit, include_settled
        )
    )
