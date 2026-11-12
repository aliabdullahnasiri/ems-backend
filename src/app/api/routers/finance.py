"""Phase 6 endpoints; shop_id is checked within the selected organization."""

from datetime import date
from typing import Annotated, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Header, Query, status
from pydantic import Field

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.schemas.finance.data import EntryData
from app.schemas.finance.request import (
    CashboxCreateRequest,
    CashboxUpdateRequest,
    CurrencyCreateRequest,
    CurrencyUpdateRequest,
    FinanceRequest,
    OpeningBalanceRequest,
    SettingsUpdateRequest,
)
from app.schemas.finance.response import (
    CashboxesResponse,
    CashboxResponse,
    CurrenciesResponse,
    CurrencyResponse,
    EntriesResponse,
    EntryResponse,
    SettingsResponse,
)
from app.services.finance import FinanceService, LedgerService
from app.services.financial_values import day_cutoffs

router = APIRouter(tags=["Financial foundation"])
ShopID: TypeAlias = Annotated[int, Query(gt=0)]  # noqa: UP040 (FastAPI runtime alias)
RetryKey: TypeAlias = Annotated[  # noqa: UP040 (FastAPI runtime alias)
    str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
]


@router.get("/settings", response_model=SettingsResponse)
async def settings(db: DBSession, context: CurrentOrganizationContext, shop_id: ShopID):
    """Read shop settings (settings:read); business timezone is Asia/Kabul."""
    return SettingsResponse(data=await FinanceService.settings(db, context, shop_id))


@router.patch("/settings", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Update Unicode shop details and preferences (settings:update)."""
    return SettingsResponse(
        data=await FinanceService.update_settings(db, context, shop_id, data)
    )


@router.get("/currencies", response_model=CurrenciesResponse)
async def currencies(
    db: DBSession,
    context: CurrentOrganizationContext,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List configured organization currencies (settings:read)."""
    return CurrenciesResponse(
        data=await FinanceService.currencies(db, context, offset, limit)
    )


@router.post(
    "/currencies", response_model=CurrencyResponse, status_code=status.HTTP_201_CREATED
)
async def create_currency(
    data: CurrencyCreateRequest, db: DBSession, context: CurrentOrganizationContext
):
    """Configure code/precision (settings:update); IRR=rial, IRT=toman=10 rial."""
    return CurrencyResponse(
        data=await FinanceService.create_currency(db, context, data)
    )


@router.patch("/currencies/{currency_uuid}", response_model=CurrencyResponse)
async def update_currency(
    currency_uuid: UUID,
    data: CurrencyUpdateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
):
    """Activate/deactivate a unit (settings:update); historical precision stays fixed."""
    return CurrencyResponse(
        data=await FinanceService.update_currency(db, context, currency_uuid, data)
    )


@router.post(
    "/cashboxes", response_model=CashboxResponse, status_code=status.HTTP_201_CREATED
)
async def create_cashbox(
    data: CashboxCreateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Create an empty shop cashbox (cashbox:manage)."""
    return CashboxResponse(
        data=await FinanceService.create_cashbox(db, context, shop_id, data)
    )


@router.get("/cashboxes", response_model=CashboxesResponse)
async def cashboxes(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Return per-currency ledger cash and AFN book costs (cashbox:read)."""
    return CashboxesResponse(
        data=await FinanceService.cashboxes(db, context, shop_id, offset, limit)
    )


@router.patch("/cashboxes/{cashbox_uuid}", response_model=CashboxResponse)
async def update_cashbox(
    cashbox_uuid: UUID,
    data: CashboxUpdateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Rename/deactivate without deleting financial history (cashbox:manage)."""
    return CashboxResponse(
        data=await FinanceService.update_cashbox(
            db, context, shop_id, cashbox_uuid, data
        )
    )


@router.post(
    "/opening-balances",
    response_model=EntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def opening(
    data: OpeningBalanceRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner-approved opening against equity, once per box/currency; identical retries replay."""
    result = await LedgerService.opening(db, context, shop_id, retry_key, data)
    return EntryResponse(data=EntryData.model_validate(result))


class ReversalRequest(FinanceRequest):
    reason: str = Field(min_length=1, max_length=500)


@router.post(
    "/opening-balances/{entry_uuid}/reverse",
    response_model=EntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse_opening(
    entry_uuid: UUID,
    data: ReversalRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner correction through a linked reversal, subject to available cash."""
    result = await LedgerService.reverse(
        db, context, shop_id, entry_uuid, retry_key, data.reason
    )
    return EntryResponse(data=EntryData.model_validate(result))


def cutoffs(day):
    return day_cutoffs(day) if day is not None else (None, None)


@router.get("/ledger/entries", response_model=EntriesResponse)
async def entries(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    currency_id: int | None = Query(None, gt=0),
    day: date | None = None,
):
    """Read posted journals (report:read), optionally filtered by currency and Kabul day."""
    start, end = cutoffs(day)
    return EntriesResponse(
        data=await LedgerService.entries(
            db,
            context,
            shop_id,
            offset,
            limit,
            currency_id=currency_id,
            start=start,
            end=end,
        )
    )


@router.get("/cashboxes/{cashbox_uuid}/movements", response_model=EntriesResponse)
async def movements(
    cashbox_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    currency_id: int | None = Query(None, gt=0),
    day: date | None = None,
):
    """Read cashbox journals including openings/reversals (cashbox:read)."""
    start, end = cutoffs(day)
    return EntriesResponse(
        data=await LedgerService.entries(
            db, context, shop_id, offset, limit, cashbox_uuid, currency_id, start, end
        )
    )
