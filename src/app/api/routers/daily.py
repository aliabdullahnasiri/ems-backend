"""Operating expense, cash transfer and daily closing APIs."""

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.dependencies import CurrentOrganizationContext, DBSession
from app.api.routers.finance import RetryKey, ShopID
from app.models.daily import CashTransfer, Expense
from app.schemas.daily.request import (
    CategoryRequest,
    CategoryUpdateRequest,
    ClosingRequest,
    ConfirmClosingRequest,
    ExpenseRequest,
    OpenDayRequest,
    ReverseRequest,
    TransferRequest,
)
from app.schemas.daily.response import (
    CategoriesResponse,
    CategoryResponse,
    ClosingResponse,
    ClosingsResponse,
    DayResponse,
    OperationResponse,
    OperationsResponse,
)
from app.services.daily import ClosingService, DailyService

router = APIRouter(prefix="", tags=["Daily operations"])


@router.get("/expense-categories", response_model=CategoriesResponse)
async def categories(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Read expense categories (report:read)."""
    return CategoriesResponse(
        data=await DailyService.categories(db, context, shop_id, offset, limit)
    )


@router.post(
    "/expense-categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    data: CategoryRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Create a category, including salaries (settings:update)."""
    return CategoryResponse(
        data=await DailyService.category(db, context, shop_id, data)
    )


@router.patch("/expense-categories/{record_uuid}", response_model=CategoryResponse)
async def update_category(
    record_uuid: UUID,
    data: CategoryUpdateRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Rename or deactivate a retained category (settings:update)."""
    return CategoryResponse(
        data=await DailyService.category(db, context, shop_id, data, record_uuid)
    )


@router.post(
    "/expenses", response_model=OperationResponse, status_code=status.HTTP_201_CREATED
)
async def create_expense(
    data: ExpenseRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Post a expense atomically; identical retries replay the original response."""
    return OperationResponse(
        data=await DailyService.create(
            db, context, shop_id, retry_key, data, transfer=False
        )
    )


@router.get("/expenses", response_model=OperationsResponse)
async def list_expense(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Read shop history, including reversal links (report:read)."""
    return OperationsResponse(
        data=await DailyService.operations(db, context, shop_id, Expense, offset, limit)
    )


@router.post(
    "/expenses/{record_uuid}/reverse",
    response_model=OperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse_expense(
    record_uuid: UUID,
    data: ReverseRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner-approved correction with a reason in an open period."""
    return OperationResponse(
        data=await DailyService.reverse(
            db, context, shop_id, record_uuid, retry_key, data.reason, transfer=False
        )
    )


@router.post(
    "/cash-transfers",
    response_model=OperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transfer(
    data: TransferRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Post a transfer atomically; identical retries replay the original response."""
    return OperationResponse(
        data=await DailyService.create(
            db, context, shop_id, retry_key, data, transfer=True
        )
    )


@router.get("/cash-transfers", response_model=OperationsResponse)
async def list_transfer(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Read shop history, including reversal links (report:read)."""
    return OperationsResponse(
        data=await DailyService.operations(
            db, context, shop_id, CashTransfer, offset, limit
        )
    )


@router.post(
    "/cash-transfers/{record_uuid}/reverse",
    response_model=OperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse_transfer(
    record_uuid: UUID,
    data: ReverseRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner-approved correction with a reason in an open period."""
    return OperationResponse(
        data=await DailyService.reverse(
            db, context, shop_id, record_uuid, retry_key, data.reason, transfer=True
        )
    )


@router.post(
    "/business-days/open",
    response_model=DayResponse,
    status_code=status.HTTP_201_CREATED,
)
async def open_day(
    data: OpenDayRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Open today's Kabul business day without reposting balances (day_closing:create)."""
    return DayResponse(
        data=await ClosingService.open(db, context, shop_id, retry_key, data)
    )


@router.get("/business-days/current", response_model=DayResponse)
async def current_day(
    db: DBSession, context: CurrentOrganizationContext, shop_id: ShopID
):
    """Read latest business day and its open/closing/closed state (report:read)."""
    return DayResponse(data=await ClosingService.current(db, context, shop_id))


@router.post(
    "/day-closings", response_model=ClosingResponse, status_code=status.HTTP_201_CREATED
)
async def create_closing(
    data: ClosingRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Freeze a complete cash count at the server cutoff (day_closing:create)."""
    return ClosingResponse(
        data=await ClosingService.create(db, context, shop_id, retry_key, data)
    )


@router.get("/day-closings", response_model=ClosingsResponse)
async def list_closings(
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Read retained closing reports (report:read)."""
    return ClosingsResponse(
        data=await ClosingService.list(db, context, shop_id, offset, limit)
    )


@router.get("/day-closings/{record_uuid}", response_model=ClosingResponse)
async def get_closing(
    record_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Read expected, counted, difference and approval details (report:read)."""
    return ClosingResponse(
        data=await ClosingService.get(db, context, shop_id, record_uuid)
    )


@router.get("/day-closings/{record_uuid}/export", response_class=Response)
async def export_closing(
    record_uuid: UUID,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
):
    """Export the immutable numeric cash snapshot as CSV (report:read)."""
    return Response(
        content=await ClosingService.export(db, context, shop_id, record_uuid),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=closing.csv"},
    )


@router.post(
    "/day-closings/{record_uuid}/confirm",
    response_model=ClosingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_closing(
    record_uuid: UUID,
    data: ConfirmClosingRequest,
    db: DBSession,
    context: CurrentOrganizationContext,
    shop_id: ShopID,
    retry_key: RetryKey,
):
    """Owner approves the closing and explicit discrepancy entries (day_closing:approve)."""
    return ClosingResponse(
        data=await ClosingService.confirm(
            db, context, shop_id, record_uuid, retry_key, data
        )
    )
