"""Shop-scoped currency rates, cash exchanges and immutable receipts."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.api.routers.finance import RetryKey, ShopID
from app.schemas.exchange.request import (
    ExchangeCreateRequest,
    ExchangeQuoteRequest,
    ExchangeReverseRequest,
    RateCreateRequest,
)
from app.schemas.exchange.response import (
    ExchangeResponse,
    ExchangesResponse,
    QuoteResponse,
    RateResponse,
    RatesResponse,
    ReceiptResponse,
)
from app.schemas.finance.data import EntryData
from app.schemas.finance.response import EntryResponse
from app.services.exchange import ExchangeService

router = APIRouter(prefix="/rates", tags=["Exchange rates"])
exchange_router = APIRouter(prefix="/exchanges", tags=["Currency exchanges"])


@router.post("", response_model=RateResponse, status_code=status.HTTP_201_CREATED)
async def create_rate(
    data: RateCreateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Append buying/selling quote-per-base rates (settings:update)."""
    return RateResponse(
        data=await ExchangeService.create_rate(db, context, shop_id, data)
    )


@router.get("", response_model=RatesResponse)
async def rates(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    base_currency_id: int | None = Query(None, gt=0),
    quote_currency_id: int | None = Query(None, gt=0),
):
    """Read rate history with inclusive Kabul dates (exchange:read)."""
    return RatesResponse(
        data=await ExchangeService.rates(
            db,
            context,
            shop_id,
            offset,
            limit,
            date_from,
            date_to,
            base_currency_id,
            quote_currency_id,
        )
    )


@exchange_router.post("/quote", response_model=QuoteResponse)
async def quote(
    data: ExchangeQuoteRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Calculate exact nonbinding amounts, rounding and costs (exchange:create)."""
    return QuoteResponse(data=await ExchangeService.quote(db, context, shop_id, data))


@exchange_router.post(
    "", response_model=ExchangeResponse, status_code=status.HTTP_201_CREATED
)
async def create_exchange(
    data: ExchangeCreateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Post a fully paid cash exchange; identical Idempotency-Key retries replay."""
    return ExchangeResponse(
        data=await ExchangeService.create(db, context, shop_id, retry_key, data)
    )


@exchange_router.get("", response_model=ExchangesResponse)
async def exchanges(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    reference: str | None = Query(None, max_length=40),
    cashier_id: int | None = Query(None, gt=0),
    contact_id: int | None = Query(None, gt=0),
    currency_id: int | None = Query(None, gt=0),
):
    """Search shop exchanges by date, reference, cashier, customer or currency."""
    return ExchangesResponse(
        data=await ExchangeService.list(
            db,
            context,
            shop_id,
            offset,
            limit,
            date_from,
            date_to,
            reference,
            cashier_id,
            contact_id,
            currency_id,
        )
    )


@exchange_router.get("/{exchange_uuid}", response_model=ExchangeResponse)
async def exchange(
    exchange_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Read original transaction details and reversal status (exchange:read)."""
    return ExchangeResponse(
        data=await ExchangeService.get(db, context, shop_id, exchange_uuid)
    )


@exchange_router.get("/{exchange_uuid}/receipt", response_model=ReceiptResponse)
async def receipt(
    exchange_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Retrieve the unchanged receipt snapshot without posting (exchange:read)."""
    return ReceiptResponse(
        data=(await ExchangeService.get(db, context, shop_id, exchange_uuid)).receipt
    )


@exchange_router.post(
    "/{exchange_uuid}/reverse",
    response_model=EntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse(
    exchange_uuid: UUID,
    data: ExchangeReverseRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner-approved linked reversal with reason (exchange:reverse); cash rules apply."""
    return EntryResponse(
        data=EntryData.model_validate(
            await ExchangeService.reverse(
                db, context, shop_id, exchange_uuid, retry_key, data
            )
        )
    )
