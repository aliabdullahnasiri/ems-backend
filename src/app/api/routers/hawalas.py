"""Manual hawala instructions; partner actions never imply automatic transmission."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.api.routers.finance import RetryKey, ShopID
from app.core.exceptions import ConflictError
from app.schemas.contact.response import ContactStatementResponse
from app.schemas.hawala.request import (
    AgentSettlementCreateRequest,
    HawalaCancelRequest,
    HawalaConfirmRequest,
    HawalaCreateRequest,
    HawalaPayRequest,
    HawalaRefundRequest,
    HawalaReviewRequest,
    HawalaUpdateRequest,
)
from app.schemas.hawala.response import (
    AgentObligationsResponse,
    AgentReversalResponse,
    AgentSettlementResponse,
    AgentSettlementsResponse,
    HawalaReceiptResponse,
    HawalaResponse,
    HawalasResponse,
)
from app.schemas.payment.request import PaymentReverseRequest
from app.services.agent_settlement import AgentSettlementService
from app.services.contact_ledger import ContactLedgerService
from app.services.hawala import HawalaService

router = APIRouter(prefix="/hawalas", tags=["Hawala transfers"])
agent_router = APIRouter(prefix="/agent-settlements", tags=["Agent settlements"])
contact_router = APIRouter(prefix="/contacts", tags=["Agent statements"])


@router.post("", response_model=HawalaResponse, status_code=status.HTTP_201_CREATED)
async def create(
    data: HawalaCreateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Create an unfunded draft with a unique reference (hawala:create)."""
    return HawalaResponse(data=await HawalaService.create(db, context, shop_id, data))


@router.get("", response_model=HawalasResponse)
async def hawalas(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    direction: str | None = Query(None, pattern="^(incoming|outgoing)$"),
    agent_id: int | None = Query(None, gt=0),
    reference: str | None = Query(None, max_length=40),
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Search shop transfer history (hawala:read); inclusive Kabul creation dates."""
    return HawalasResponse(
        data=await HawalaService.list(
            db,
            context,
            shop_id,
            offset,
            limit,
            direction,
            agent_id,
            reference,
            date_from,
            date_to,
        )
    )


@router.get("/{hawala_uuid}", response_model=HawalaResponse)
async def hawala(
    hawala_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Read lifecycle, payout evidence, refunds and independent settlement state."""
    return HawalaResponse(
        data=await HawalaService.get(db, context, shop_id, hawala_uuid)
    )


@router.patch("/{hawala_uuid}", response_model=HawalaResponse)
async def update(
    hawala_uuid: UUID,
    data: HawalaUpdateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Patch draft details only (hawala:create); confirmed records are frozen."""
    return HawalaResponse(
        data=await HawalaService.update(db, context, shop_id, hawala_uuid, data)
    )


@router.get("/{hawala_uuid}/receipt", response_model=HawalaReceiptResponse)
async def receipt(
    hawala_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Return original confirmation receipt (hawala:read), without another posting."""
    data = await HawalaService.get(db, context, shop_id, hawala_uuid)
    if data.receipt is None:
        raise ConflictError()
    return HawalaReceiptResponse(data=data.receipt)


@router.post("/{hawala_uuid}/confirm", response_model=HawalaResponse)
async def confirm(
    hawala_uuid: UUID,
    data: HawalaConfirmRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Fund outgoing or verify incoming instruction (hawala:confirm); atomic retry."""
    return HawalaResponse(
        data=await HawalaService.transition(
            db, context, shop_id, hawala_uuid, retry_key, "confirm", data
        )
    )


@router.post("/{hawala_uuid}/pay", response_model=HawalaResponse)
async def pay(
    hawala_uuid: UUID,
    data: HawalaPayRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Record verified local incoming or manual partner outgoing payout (hawala:pay)."""
    return HawalaResponse(
        data=await HawalaService.transition(
            db, context, shop_id, hawala_uuid, retry_key, "pay", data
        )
    )


@router.post("/{hawala_uuid}/cancel", response_model=HawalaResponse)
async def cancel(
    hawala_uuid: UUID,
    data: HawalaCancelRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Cancel only unpaid transfers and record refund due, without spending cash."""
    return HawalaResponse(
        data=await HawalaService.transition(
            db, context, shop_id, hawala_uuid, retry_key, "cancel", data
        )
    )


@router.post("/{hawala_uuid}/refund", response_model=HawalaResponse)
async def refund(
    hawala_uuid: UUID,
    data: HawalaRefundRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Pay actual local refund against stored refund due (hawala:refund)."""
    return HawalaResponse(
        data=await HawalaService.transition(
            db, context, shop_id, hawala_uuid, retry_key, "refund", data
        )
    )


@router.post("/{hawala_uuid}/reviews", response_model=HawalaResponse)
async def review(
    hawala_uuid: UUID,
    data: HawalaReviewRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Retain current owner's paid/cancelled dispute review; no financial reversal implied."""
    return HawalaResponse(
        data=await HawalaService.transition(
            db, context, shop_id, hawala_uuid, retry_key, "review", data
        )
    )


@agent_router.post(
    "", response_model=AgentSettlementResponse, status_code=status.HTTP_201_CREATED
)
async def settle(
    data: AgentSettlementCreateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Allocate cash or manually completed external settlement (agent_settlement:create)."""
    return AgentSettlementResponse(
        data=await AgentSettlementService.create(db, context, shop_id, retry_key, data)
    )


@agent_router.get("", response_model=AgentSettlementsResponse)
async def settlements(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    agent_id: int | None = Query(None, gt=0),
    date_from: date | None = None,
    date_to: date | None = None,
):
    """List settlement evidence/history (contact:read), with inclusive Kabul dates."""
    return AgentSettlementsResponse(
        data=await AgentSettlementService.list(
            db, context, shop_id, offset, limit, agent_id, date_from, date_to
        )
    )


@agent_router.get("/statement", response_model=ContactStatementResponse)
async def statement(
    agent_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    date_from: date | None = None,
    date_to: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Agent-only ledger statement (contact:read); complete CSV also requires report:read."""
    await AgentSettlementService.outstanding(db, context, agent_uuid, 0, 1)
    if format == "csv":
        content = await ContactLedgerService.export(
            db, context, agent_uuid, date_from, date_to, role="agent"
        )
        return Response(
            content,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="agent-statement.csv"',
                "Cache-Control": "no-store",
            },
        )
    return ContactStatementResponse(
        data=await ContactLedgerService.statement(
            db, context, agent_uuid, date_from, date_to, offset, limit, role="agent"
        )
    )


@agent_router.get("/{settlement_uuid}", response_model=AgentSettlementResponse)
async def settlement(
    settlement_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Read original settlement evidence and current reversal status (contact:read)."""
    return AgentSettlementResponse(
        data=await AgentSettlementService.get(db, context, shop_id, settlement_uuid)
    )


@agent_router.post(
    "/{settlement_uuid}/reverse",
    response_model=AgentReversalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse(
    settlement_uuid: UUID,
    data: PaymentReverseRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner-approved exact linked reversal restores agent allocations once."""
    return AgentReversalResponse(
        data=await AgentSettlementService.reverse(
            db, context, shop_id, settlement_uuid, retry_key, data
        )
    )


@contact_router.get(
    "/{agent_uuid}/agent-outstanding", response_model=AgentObligationsResponse
)
async def outstanding(
    agent_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_settled: bool = False,
):
    """Read agent principal/fee/opening obligations by original currency (contact:read)."""
    return AgentObligationsResponse(
        data=await AgentSettlementService.outstanding(
            db, context, agent_uuid, offset, limit, include_settled
        )
    )
